import re
import asyncio
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

import models
import schemas
from database import get_db
from services.auth import get_current_user, decrypt_smtp_pass
from services.credits import InsufficientCreditsError, consume_credits, refund_credits
from services.followup_engine import analyze_reply_intent, draft_followup_email
from services.email_sender import send_email
from services.email_threads import reply_thread_context
from services.suppression import generate_unsubscribe_token, owner_id_for_lead, suppression_reason


class SendReplyRequest(BaseModel):
    draft: str

router = APIRouter(prefix="/api/replies", tags=["replies"])


def _insufficient_credits_http(exc: InsufficientCreditsError) -> HTTPException:
    return HTTPException(
        status_code=402,
        detail={
            "message": "Insufficient credits",
            "action": exc.action,
            "required": exc.required,
            "balance": exc.balance,
        },
    )


def _verify_reply_lead(lead_id: int, db: Session, user: models.User) -> models.Lead:
    query = db.query(models.Lead).outerjoin(
        models.Workflow, models.Workflow.id == models.Lead.workflow_id
    ).outerjoin(
        models.ClientPool, models.ClientPool.id == models.Lead.client_pool_id
    ).filter(models.Lead.id == lead_id)
    if not user.is_admin:
        query = query.filter(or_(models.Workflow.user_id == user.id, models.ClientPool.user_id == user.id))
    lead = query.first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


@router.get("/", response_model=List[schemas.Lead])
def read_replies(
    include_handoff: bool = Query(True),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """
    Returns replied leads from the real database.
    Enriches reply_snippet from actual inbound EmailLog records.
    """
    # Reply history is a durable milestone, independent of the current
    # automation status (which may already be drafted/sent again).
    query = db.query(models.Lead).outerjoin(
        models.Workflow, models.Workflow.id == models.Lead.workflow_id
    ).outerjoin(
        models.ClientPool, models.ClientPool.id == models.Lead.client_pool_id
    ).filter(or_(
        models.Lead.has_replied.is_(True),
        models.Lead.status == "replied",  # compatibility before v15 backfill
    ))
    if not user.is_admin:
        query = query.filter(or_(models.Workflow.user_id == user.id, models.ClientPool.user_id == user.id))
    replied_leads = query.order_by(models.Lead.updated_at.desc()).limit(limit).all()

    # Build lookup: lead_id → latest inbound email body
    lead_ids = [l.id for l in replied_leads]
    inbound_map: dict = {}
    if lead_ids:
        inbound_logs = (
            db.query(models.EmailLog)
            .join(models.Lead, models.Lead.id == models.EmailLog.lead_id)
            .filter(
                models.EmailLog.lead_id.in_(lead_ids),
                models.EmailLog.direction == "inbound",
                func.lower(models.EmailLog.from_email) == func.lower(models.Lead.email),
            )
            .order_by(models.EmailLog.sent_at.desc())
            .all()
        )
        for log in inbound_logs:
            if log.lead_id not in inbound_map:
                inbound_map[log.lead_id] = log.body

    results = []
    for lead in replied_leads:
        # Use the real inbound email body if richer than the stored snippet
        snippet = lead.reply_snippet or ""
        real_body = inbound_map.get(lead.id, "")
        if real_body and len(real_body) > len(snippet):
            snippet = real_body

        results.append(_lead_to_dict(lead, snippet_override=snippet))

    return results


def _lead_to_dict(
    lead,
    snippet_override: str = "",
    status_override: str = "",
    reply_time_override=None,
) -> dict:
    """Convert a Lead ORM object into a serialisable dict for the replies endpoint."""
    reply_at = reply_time_override or lead.last_reply_at or lead.updated_at
    return {
        "id": lead.id,
        "workflow_id": lead.workflow_id,
        "client_pool_id": lead.client_pool_id,
        "domain": lead.domain,
        "company_name": lead.company_name,
        "email": lead.email,
        "first_name": lead.first_name,
        "last_name": lead.last_name,
        "job_title": lead.job_title,
        "linkedin_url": lead.linkedin_url,
        "status": status_override or lead.status or "replied",
        "ai_draft": lead.ai_draft,
        "followup_count": lead.followup_count or 0,
        "last_reply_at": reply_at,
        "reply_snippet": snippet_override or lead.reply_snippet or "",
        "has_replied": bool(lead.has_replied or lead.status == "replied"),
        "reply_intent": lead.reply_intent,
        "user_rating": lead.user_rating,
        "email_verified": lead.email_verified,
        "email_validation_status": lead.email_validation_status,
        "timezone": lead.timezone,
        "fit_score": lead.fit_score,
        "fit_grade": lead.fit_grade,
        "qualification_notes": lead.qualification_notes,
        "handoff_recommended": lead.handoff_recommended,
        "source_channel": lead.source_channel or "search",
        "data_sources": lead.data_sources or "website, snovio",
        "created_at": lead.created_at,
        "updated_at": lead.updated_at,
    }


@router.post("/{lead_id}/generate-draft")
def generate_ai_draft(
    lead_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    lead = _verify_reply_lead(lead_id, db, user)
    credit_owner_id = owner_id_for_lead(db, lead)

    if not lead.reply_snippet:
        raise HTTPException(status_code=400, detail="No reply content to analyze")

    charged = False
    try:
        consume_credits(
            db,
            credit_owner_id,
            "ai_reply_draft",
            description=f"AI reply draft for lead #{lead.id}",
            reference_type="lead",
            reference_id=lead.id,
        )
        charged = True

        # Analyze intent
        analysis = analyze_reply_intent(lead.reply_snippet)
        intent = analysis.get("intent", "other")
        summary = analysis.get("summary", "")
        lead.has_replied = True
        lead.reply_intent = intent

        if intent == "not_interested":
            lead.status = "rejected"
            db.commit()
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
        db.commit()

        return {
            "intent": intent,
            "summary": summary,
            "draft": draft,
            "followup_round": followup_round,
        }
    except InsufficientCreditsError as exc:
        raise _insufficient_credits_http(exc) from exc
    except Exception:
        if charged:
            refund_credits(
                db,
                credit_owner_id,
                "ai_reply_draft",
                description=f"Refund failed AI reply draft for lead #{lead.id}",
                reference_type="lead",
                reference_id=lead.id,
            )
        raise


@router.post("/{lead_id}/send")
async def send_ai_reply(
    lead_id: int,
    req: SendReplyRequest,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    lead = _verify_reply_lead(lead_id, db, user)
    if not req.draft.strip():
        raise HTTPException(status_code=400, detail="Draft is empty")
    if not lead.email:
        raise HTTPException(status_code=400, detail="Lead has no recipient email")
    if lead.status in {"unsubscribed", "rejected"}:
        raise HTTPException(status_code=400, detail=f"Lead is {lead.status}; sending is blocked")
    owner_id = owner_id_for_lead(db, lead)
    blocked_reason = suppression_reason(db, email=lead.email, domain=lead.domain, user_id=owner_id)
    if blocked_reason:
        raise HTTPException(status_code=400, detail=f"Recipient is suppressed: {blocked_reason}")

    # Resolve the exact mailbox and RFC thread used by the conversation.
    if not lead.workflow_id:
        raise HTTPException(status_code=400, detail="Lead has no associated workflow")

    thread = reply_thread_context(db, lead)
    if thread.original_sender_missing:
        raise HTTPException(
            status_code=409,
            detail="The original sender account is no longer bound to this workflow. Rebind it before replying.",
        )
    account = thread.account
    if not account:
        raise HTTPException(status_code=400, detail="No email account configured for this workflow")

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
    unsubscribe_url = None
    public_base = _public_base_url()
    if public_base and lead.email:
        unsubscribe_url = f"{public_base}/api/unsubscribe/{generate_unsubscribe_token(lead.id, lead.email)}"
    body_html = _build_email_html(body, account.display_name or account.email, unsubscribe_url)

    charged = False
    try:
        consume_credits(
            db,
            owner_id,
            "email_send",
            description=f"Reply email send to {lead.email}",
            reference_type="lead",
            reference_id=lead.id,
            metadata={"workflow_id": lead.workflow_id},
        )
        charged = True

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
            in_reply_to=thread.in_reply_to,
            references=thread.references,
            list_unsubscribe_url=unsubscribe_url,
        )
    except InsufficientCreditsError as exc:
        raise _insufficient_credits_http(exc) from exc
    except Exception:
        if charged:
            refund_credits(
                db,
                owner_id,
                "email_send",
                description=f"Refund failed reply email send to {lead.email}",
                reference_type="lead",
                reference_id=lead.id,
            )
        raise

    if not res.get("success"):
        if charged:
            refund_credits(
                db,
                owner_id,
                "email_send",
                description=f"Refund failed reply email send to {lead.email}",
                reference_type="lead",
                reference_id=lead.id,
            )
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
    lead.has_replied = True
    db.commit()

    return {
        "ok": True,
        "message_id": res.get("message_id"),
        "from_email": account.email,
        "in_reply_to": thread.in_reply_to,
    }


def _public_base_url() -> str:
    import os
    for name in ("PUBLIC_APP_URL", "FRONTEND_BASE_URL", "APP_BASE_URL"):
        value = os.environ.get(name, "").strip().rstrip("/")
        if value:
            return value
    return ""


def _build_email_html(body_text: str, sender_name: str, unsubscribe_url: str = "") -> str:
    body_paragraphs = ""
    for para in body_text.strip().split("\n\n"):
        cleaned = para.strip().replace("\n", "<br>")
        if cleaned:
            body_paragraphs += f"<p style='margin:0 0 12px 0;line-height:1.6;color:#333333;'>{cleaned}</p>\n"
    if not body_paragraphs:
        body_paragraphs = f"<p style='margin:0 0 12px 0;line-height:1.6;color:#333333;'>{body_text.replace(chr(10), '<br>')}</p>"

    signature_block = f"<p style='margin:0;'>Best regards,<br><strong style='color:#555;'>{sender_name}</strong></p>"

    unsubscribe_copy = "If you no longer wish to receive these emails, please reply with \"unsubscribe\"."
    if unsubscribe_url:
        unsubscribe_copy = f'If you no longer wish to receive these emails, <a href="{unsubscribe_url}" style="color:#94a3b8;">unsubscribe here</a>.'

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
<p style="margin:8px 0 0;font-size:11px;color:#bbb;">{unsubscribe_copy}</p>
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""
