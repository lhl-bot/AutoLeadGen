import re
import asyncio
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

import models
import schemas
from database import get_db
from services.auth import get_current_user, decrypt_smtp_pass
from services.followup_engine import analyze_reply_intent, draft_followup_email
from services.email_sender import send_email


class SendReplyRequest(BaseModel):
    draft: str

router = APIRouter(prefix="/api/replies", tags=["replies"])


@router.get("/", response_model=List[schemas.Lead])
def read_replies(
    include_handoff: bool = Query(True),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    query = db.query(models.Lead).outerjoin(models.Workflow, models.Workflow.id == models.Lead.workflow_id)
    if not user.is_admin:
        query = query.filter(models.Workflow.user_id == user.id)

    # Only show leads that have actually replied
    query = query.filter(models.Lead.status == "replied")

    return query.order_by(
        models.Lead.last_reply_at.is_(None),
        models.Lead.last_reply_at.desc(),
        models.Lead.updated_at.desc(),
    ).limit(limit).all()


@router.post("/{lead_id}/generate-draft")
def generate_ai_draft(
    lead_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    lead = db.query(models.Lead).filter(models.Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    if not lead.reply_snippet:
        raise HTTPException(status_code=400, detail="No reply content to analyze")

    # Analyze intent
    analysis = analyze_reply_intent(lead.reply_snippet)
    intent = analysis.get("intent", "other")
    summary = analysis.get("summary", "")

    if intent == "not_interested":
        return {
            "intent": intent,
            "summary": summary,
            "draft": "",
            "message": "Lead is not interested. No draft generated."
        }

    # Get conversation history from email logs
    from models import EmailLog
    logs = db.query(EmailLog).filter(
        EmailLog.lead_id == lead_id
    ).order_by(EmailLog.sent_at.asc()).all()

    conversation_history = [
        {
            "direction": log.direction,
            "subject": log.subject,
            "body": log.body,
            "sent_at": str(log.sent_at) if log.sent_at else None,
        }
        for log in logs[-10:]  # Last 10 messages
    ]

    followup_round = len([l for l in logs if l.direction == "outbound"]) + 1

    draft = draft_followup_email(
        lead_data={
            "first_name": lead.first_name,
            "company_name": lead.company_name,
        },
        reply_text=lead.reply_snippet,
        intent=intent,
        conversation_history=conversation_history,
        followup_round=followup_round,
    )

    # Save draft to lead
    lead.ai_draft = draft
    lead.intent = intent
    db.commit()

    return {
        "intent": intent,
        "summary": summary,
        "draft": draft,
        "followup_round": followup_round,
    }


@router.post("/{lead_id}/send")
async def send_ai_reply(
    lead_id: int,
    req: SendReplyRequest,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    lead = db.query(models.Lead).filter(models.Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    if not req.draft.strip():
        raise HTTPException(status_code=400, detail="Draft is empty")

    # Find email account via workflow
    if not lead.workflow_id:
        raise HTTPException(status_code=400, detail="Lead has no associated workflow")

    workflow_email = db.query(models.WorkflowEmail).filter(
        models.WorkflowEmail.workflow_id == lead.workflow_id
    ).first()
    if not workflow_email:
        raise HTTPException(status_code=400, detail="No email account configured for this workflow")

    account = workflow_email.email_account
    if not account:
        raise HTTPException(status_code=400, detail="Email account not found")

    # Parse subject and body from draft
    lines = req.draft.split('\n')
    subject = ""
    body = req.draft

    for i, line in enumerate(lines):
        line_stripped = line.strip()
        m = re.match(r'^(?:\*{0,2})(?:subject|SUBJECT|主题)\s*[：:]\s*(.+)', line_stripped, re.IGNORECASE)
        if m:
            subject = m.group(1).strip().strip('*').strip()
            raw_body = "\n".join(lines[i+1:]).strip()
            raw_body = re.sub(r'^(?:\*{0,2})(?:body|BODY)\s*[：:]\s*\n?', '', raw_body).strip()
            body = raw_body
            break

    if not subject:
        subject = f"Re: {lead.company_name or 'your company'}"

    # Clean placeholders
    first_n = lead.first_name or "there"
    comp_n = lead.company_name or "your company"
    body = re.sub(r'\[First Name\]|\[first name\]', first_n, body, flags=re.IGNORECASE)
    body = re.sub(r'\[Company\]|\[Company Name\]|\[Target Company\]', comp_n, body, flags=re.IGNORECASE)
    body = re.sub(r'\[.*?\]', '', body)

    # Signature cleanup
    sign_off_words = r'(?:Best regards|Kind regards|Warm regards|Regards|Cheers|Thanks|Thank you|Best|Sincerely|Yours truly|Looking forward)'
    body = re.sub(
        r'\n\s*' + sign_off_words + r',?\s*(?:\n.{0,60}){0,3}\s*$',
        '', body, flags=re.IGNORECASE
    )
    body = "\n".join([line for line in body.split("\n") if line.strip() not in ('|', '', '  |  ', '|  ')])

    # Build HTML
    body_html = _build_email_html(body, account.display_name or account.email)

    # Send
    res = await asyncio.to_thread(
        send_email,
        smtp_host=account.smtp_host,
        smtp_port=account.smtp_port,
        smtp_user=account.smtp_user,
        smtp_pass=decrypt_smtp_pass(account.smtp_pass),
        use_ssl=account.use_ssl,
        use_tls=account.use_tls,
        from_email=account.email,
        to_email=lead.email,
        subject=subject,
        body_html=body_html,
        body_text=body,
        sender_name=account.display_name or account.email.split('@')[0],
        reply_to=account.email,
    )

    if not res.get("success"):
        raise HTTPException(status_code=500, detail=f"Failed to send: {res.get('message', 'Unknown error')}")

    # Log and update lead
    log_entry = models.EmailLog(
        lead_id=lead.id,
        direction="outbound",
        from_email=account.email,
        to_email=lead.email,
        subject=subject,
        body=body,
        message_id=res.get("message_id"),
    )
    db.add(log_entry)
    lead.ai_draft = req.draft
    lead.status = "sent"
    db.commit()

    return {"ok": True, "message_id": res.get("message_id")}


def _build_email_html(body_text: str, sender_name: str) -> str:
    body_paragraphs = ""
    for para in body_text.strip().split("\n\n"):
        cleaned = para.strip().replace("\n", "<br>")
        if cleaned:
            body_paragraphs += f"<p style='margin:0 0 12px 0;line-height:1.6;color:#333333;'>{cleaned}</p>\n"
    if not body_paragraphs:
        body_paragraphs = f"<p style='margin:0 0 12px 0;line-height:1.6;color:#333333;'>{body_text.replace(chr(10), '<br>')}</p>"

    signature_block = f"<p style='margin:0;'>Best regards,<br><strong style='color:#555;'>{sender_name}</strong></p>"

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="margin:0;padding:0;background-color:#f4f4f7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background-color:#f4f4f7;padding:24px 0;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="background-color:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,0.06);">
<tr><td style="padding:32px 40px;">
{body_paragraphs}
</td></tr>
<tr><td style="padding:16px 40px 24px;border-top:1px solid #eee;font-size:13px;color:#999;">
{signature_block}
<p style="margin:8px 0 0;font-size:11px;color:#bbb;">If you no longer wish to receive these emails, please reply with \"unsubscribe\".</p>
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""
