import os
import json
import asyncio
import re
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks
from sqlalchemy import or_
from sqlalchemy.orm import Session
from database import get_db
import models
from models import ChannelAccount, Lead, User
from services.unipile_client import UnipileClient
from services.auth import get_current_user
from services.credits import InsufficientCreditsError, consume_credits, refund_credits
from services.suppression import owner_id_for_lead, suppression_reason, suppress_lead
import logging

logger = logging.getLogger("channels_api")
router = APIRouter(prefix="/api/channels", tags=["channels"])
_ACCOUNT_OWNER_MARKER = re.compile(r"\[autoleadgen-user:(\d+)\]")


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


def _verify_lead_ownership(lead_id: int, db: Session, user: User) -> Lead:
    query = db.query(Lead).outerjoin(
        models.Workflow, models.Workflow.id == Lead.workflow_id
    ).outerjoin(
        models.ClientPool, models.ClientPool.id == Lead.client_pool_id
    ).filter(Lead.id == lead_id)
    if not user.is_admin:
        query = query.filter(or_(models.Workflow.user_id == user.id, models.ClientPool.user_id == user.id))
    lead = query.first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


def _owned_unipile_account_name(name: str, user_id: int) -> str:
    visible_name = _ACCOUNT_OWNER_MARKER.sub("", name).strip() or "Channel Account"
    return f"{visible_name} [autoleadgen-user:{user_id}]"


def _unipile_account_owner_id(account: dict) -> Optional[int]:
    match = _ACCOUNT_OWNER_MARKER.search(str(account.get("name") or ""))
    return int(match.group(1)) if match else None


def _visible_unipile_account_name(account: dict, provider: str) -> str:
    raw_name = str(account.get("name") or f"{provider} Account")
    return _ACCOUNT_OWNER_MARKER.sub("", raw_name).strip() or f"{provider} Account"


def _sync_unipile_accounts(db: Session, user: User, unipile_accounts: list[dict]) -> None:
    """Synchronize only accounts the current user is allowed to manage."""
    remote_by_id = {
        str(account["id"]): account
        for account in unipile_accounts
        if account.get("id")
    }

    local_query = db.query(ChannelAccount)
    if not user.is_admin:
        local_query = local_query.filter(ChannelAccount.user_id == user.id)
    local_accounts = local_query.all()
    local_by_remote_id = {account.unipile_account_id: account for account in local_accounts}

    for remote_id, db_account in local_by_remote_id.items():
        remote_account = remote_by_id.get(remote_id)
        if not remote_account:
            if db_account.status != "DISCONNECTED":
                db_account.status = "DISCONNECTED"
            continue

        provider = (
            remote_account.get("provider")
            or remote_account.get("type")
            or "UNKNOWN"
        ).upper()
        db_account.status = remote_account.get("status", "OK")
        db_account.name = _visible_unipile_account_name(remote_account, provider)
        if provider != "UNKNOWN":
            db_account.account_type = provider

    for remote_id, remote_account in remote_by_id.items():
        if remote_id in local_by_remote_id:
            continue

        owner_id = _unipile_account_owner_id(remote_account)
        if owner_id is None:
            if not (
                user.is_admin
                and os.environ.get("UNIPILE_SYNC_ALLOW_UNOWNED_AUTOCREATE", "").lower()
                in {"1", "true", "yes", "on"}
            ):
                continue
            owner_id = user.id
        elif not user.is_admin and owner_id != user.id:
            continue

        owner_exists = db.query(User.id).filter(User.id == owner_id).first()
        if not owner_exists:
            logger.warning(
                "Skipping Unipile account %s because owner user #%s does not exist",
                remote_id,
                owner_id,
            )
            continue

        provider = (
            remote_account.get("provider")
            or remote_account.get("type")
            or "UNKNOWN"
        ).upper()
        db.add(ChannelAccount(
            user_id=owner_id,
            account_type=provider,
            unipile_account_id=remote_id,
            name=_visible_unipile_account_name(remote_account, provider),
            status=remote_account.get("status", "OK"),
        ))

    db.commit()


def _find_owned_lead_for_message(
    db: Session,
    *,
    channel_account_id: Optional[str],
    sender_id: str,
) -> Optional[Lead]:
    if not channel_account_id:
        return None

    account = db.query(ChannelAccount).filter(
        ChannelAccount.unipile_account_id == channel_account_id
    ).first()
    if not account:
        return None

    return (
        db.query(Lead)
        .outerjoin(models.Workflow, models.Workflow.id == Lead.workflow_id)
        .outerjoin(models.ClientPool, models.ClientPool.id == Lead.client_pool_id)
        .filter(
            or_(
                models.Workflow.user_id == account.user_id,
                models.ClientPool.user_id == account.user_id,
            ),
            or_(
                Lead.linkedin_url.contains(sender_id),
                Lead.whatsapp_number == sender_id,
            ),
        )
        .first()
    )


@router.post("/auth-link")
async def create_auth_link(request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """
    Called by the frontend to get a Hosted Auth Wizard link for Unipile.
    Expects JSON: {"type": "LINKEDIN" | "WHATSAPP", "name": "User's Name"}
    """
    data = await request.json()
    account_type = data.get("type", "LINKEDIN")
    name = _owned_unipile_account_name(
        data.get("name", f"Account - {account_type}"),
        user.id,
    )
    
    frontend_base_url = os.environ.get("FRONTEND_BASE_URL", "http://localhost:3000").rstrip("/")
    success_redirect_url = f"{frontend_base_url}/dashboard/settings?unipile_success=true"

    client = UnipileClient()
    url = await client.generate_hosted_auth_link(account_type, name, success_redirect_url)
    
    if url:
        return {"url": url}
    else:
        raise HTTPException(status_code=500, detail="Failed to generate Unipile auth link.")

@router.get("/accounts")
async def list_accounts(sync: bool = False, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """
    Returns all connected Unipile accounts.
    Returns local DB accounts by default; pass sync=true to refresh from Unipile.
    """
    if sync:
        try:
            client = UnipileClient()
            try:
                unipile_accounts = await asyncio.wait_for(client.get_all_accounts(timeout=2.0), timeout=2.5)
            except asyncio.TimeoutError:
                client.last_error_body = "Timed out syncing Unipile accounts"
                unipile_accounts = None

            if unipile_accounts is None:
                logger.info("Skipping Unipile account sync: %s", client.last_error_body or "unknown error")
            else:
                _sync_unipile_accounts(db, user, unipile_accounts)
        except Exception as e:
            logger.error(f"Error syncing accounts with Unipile: {e}")

    # Return local DB accounts after sync (user-filtered)
    if user.is_admin:
        accounts = db.query(ChannelAccount).all()
    else:
        accounts = db.query(ChannelAccount).filter(ChannelAccount.user_id == user.id).all()
    return accounts


@router.delete("/accounts/{account_id}")
def delete_account(account_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Remove a channel account from the local AutoLeadGen account list."""
    query = db.query(ChannelAccount).filter(ChannelAccount.id == account_id)
    if not user.is_admin:
        query = query.filter(ChannelAccount.user_id == user.id)
    account = query.first()
    if not account:
        raise HTTPException(status_code=404, detail="Channel account not found")

    db.delete(account)
    db.commit()
    return {"ok": True}


def _is_production_env() -> bool:
    return os.environ.get("APP_ENV", os.environ.get("ENVIRONMENT", "")).lower() in {"prod", "production"}


def _verify_unipile_signature(request: Request, raw_body: bytes) -> bool:
    """Verify the X-Unipile-Signature header if a webhook secret is configured."""
    secret = os.environ.get("UNIPILE_WEBHOOK_SECRET", "")
    if not secret:
        return not _is_production_env()
    import hmac, hashlib
    signature = request.headers.get("X-Unipile-Signature", "")
    if not signature:
        return False
    if signature.startswith("sha256="):
        signature = signature.removeprefix("sha256=")
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


@router.post("/webhooks/unipile")
async def unipile_webhook(request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """
    Receives webhooks from Unipile.
    Examples: account status changes, new incoming messages.
    """
    raw_body = await request.body()
    if not _verify_unipile_signature(request, raw_body):
        logger.warning("Unipile webhook signature verification failed")
        raise HTTPException(status_code=403, detail="Invalid signature")

    try:
        payload = json.loads(raw_body.decode("utf-8") or "{}")
        event_type = payload.get("type")
        
        logger.info(f"Received Unipile Webhook: {event_type}")

        if event_type == "account_status":
            account_id = payload.get("account_id")
            new_status = payload.get("status") # OK, CREDENTIALS, etc.
            provider_type = (payload.get("provider") or payload.get("account_type") or "UNKNOWN").upper()
            
            # Update DB
            acc = db.query(ChannelAccount).filter(ChannelAccount.unipile_account_id == account_id).first()
            if acc:
                acc.status = new_status
                if (acc.account_type == "UNKNOWN" or acc.account_type != provider_type) and provider_type != "UNKNOWN":
                    acc.account_type = provider_type
                db.commit()
                logger.info(f"Updated ChannelAccount {account_id} status to {new_status} (type: {acc.account_type})")
            elif new_status == "OK" and os.environ.get("UNIPILE_WEBHOOK_ALLOW_ACCOUNT_AUTOCREATE", "").lower() in {"1", "true", "yes", "on"}:
                # Legacy fallback for single-tenant installs only.
                admin_user_id = int(os.environ.get("DEFAULT_ADMIN_USER_ID", "1"))
                new_acc = ChannelAccount(
                    user_id=admin_user_id,
                    account_type=provider_type,
                    unipile_account_id=account_id,
                    name=payload.get("name", f"Account {account_id}"),
                    status=new_status
                )
                db.add(new_acc)
                db.commit()
                logger.info(f"Created new ChannelAccount {account_id} with status {new_status} (type: {provider_type})")
            elif new_status == "OK":
                logger.warning(
                    "Received OK account_status webhook for unknown Unipile account %s, "
                    "but webhook account autocreate is disabled.",
                    account_id,
                )

        elif event_type == "message":
            # Incoming message (LinkedIn or WhatsApp)
            message_data = payload.get("message", {})
            sender_id = message_data.get("sender_id")
            text = message_data.get("text")
            if not sender_id:
                return {"status": "ok"}

            account_id = message_data.get("account_id") or payload.get("account_id")
            lead = _find_owned_lead_for_message(
                db,
                channel_account_id=account_id,
                sender_id=sender_id,
            )
            if not account_id:
                logger.warning("Ignoring Unipile message without account_id")
            elif not lead:
                logger.info(
                    "No lead owned by Unipile account %s matched sender %s",
                    account_id,
                    sender_id,
                )
            
            if lead:
                # We have a reply from a known lead!
                reply_lower = (text or "").lower()
                unsub_keywords = [
                    "unsubscribe", "opt out", "opt-out", "stop messaging",
                    "stop emailing", "remove me", "do not contact", "don't contact",
                    "退订", "取消订阅", "不要联系",
                ]
                if any(keyword in reply_lower for keyword in unsub_keywords):
                    lead.has_replied = True
                    lead.reply_intent = "unsubscribe"
                    suppress_lead(db, lead, reason="unsubscribe", source="unipile_message")
                else:
                    lead.status = "replied"
                    lead.has_replied = True
                    lead.reply_intent = "other"
                lead.reply_snippet = text
                from datetime import datetime, timezone
                lead.last_reply_at = datetime.now(timezone.utc)
                db.commit()
                logger.info(f"Lead {lead.id} replied via Unipile: {text[:50]}...")
                
                # Background task to evaluate intent and draft follow-up
                # background_tasks.add_task(evaluate_and_draft_omnichannel_reply, lead.id)

        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Error processing Unipile webhook: {e}")
        return {"status": "error", "message": str(e)}

@router.post("/send-linkedin")
async def send_linkedin_manually(request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Manually send a LinkedIn invitation to a specific lead."""
    data = await request.json()
    lead_id = data.get("lead_id")
    message = data.get("message", "")
    
    if not lead_id:
        raise HTTPException(status_code=400, detail="lead_id is required")
    
    lead = _verify_lead_ownership(lead_id, db, user)
    if not lead.linkedin_url:
        raise HTTPException(status_code=404, detail="Lead not found or has no LinkedIn URL")
    blocked_reason = suppression_reason(db, email=lead.email, domain=lead.domain, user_id=owner_id_for_lead(db, lead))
    if blocked_reason:
        raise HTTPException(status_code=400, detail=f"Recipient is suppressed: {blocked_reason}")
    
    # Find a connected LinkedIn account owned by this user
    linkedin_account = db.query(ChannelAccount).filter(
        ChannelAccount.account_type == "LINKEDIN",
        ChannelAccount.status == "OK",
        ChannelAccount.user_id == user.id,
    ).first()
    
    if not linkedin_account:
        raise HTTPException(status_code=400, detail="No connected LinkedIn account found. Please connect one in Omnichannel Settings.")
    
    client = UnipileClient()
    
    # Resolve provider_id
    provider_id = await client.get_linkedin_provider_id(linkedin_account.unipile_account_id, lead.linkedin_url)
    if not provider_id:
        raise HTTPException(status_code=400, detail=f"Could not resolve LinkedIn profile: {lead.linkedin_url}")
    
    # If no message provided, generate one with AI
    if not message:
        from services.ai_writer import generate_linkedin_invite
        brief_summary = ""
        from models import LeadBrief
        brief = db.query(LeadBrief).filter(LeadBrief.lead_id == lead_id).first()
        if brief:
            brief_summary = f"{brief.company_overview or ''}"
        message = generate_linkedin_invite(
            first_name=lead.first_name or "",
            company_name=lead.company_name or lead.domain,
            job_title=lead.job_title or "",
            brief_summary=brief_summary,
            template="I'd like to connect and explore potential collaboration."
        )
    
    if not message:
        raise HTTPException(status_code=500, detail="Failed to generate LinkedIn invite message")
    
    charged = False
    try:
        consume_credits(
            db,
            user.id,
            "linkedin_invite",
            description=f"LinkedIn invite to lead #{lead.id}",
            reference_type="lead",
            reference_id=lead.id,
            metadata={"channel_account_id": linkedin_account.id},
        )
        charged = True
        success = await client.send_linkedin_invitation(linkedin_account.unipile_account_id, provider_id, message)
    except InsufficientCreditsError as exc:
        raise _insufficient_credits_http(exc) from exc
    except Exception:
        if charged:
            refund_credits(
                db,
                user.id,
                "linkedin_invite",
                description=f"Refund failed LinkedIn invite to lead #{lead.id}",
                reference_type="lead",
                reference_id=lead.id,
            )
        raise
    
    if success:
        lead.linkedin_sent = True
        lead.linkedin_status = "requested"
        
        from models import MessageLog
        msg_log = MessageLog(
            lead_id=lead_id,
            channel="linkedin",
            direction="outbound",
            content=message,
            status="sent"
        )
        db.add(msg_log)
        db.commit()
        return {"success": True, "message": "LinkedIn invitation sent successfully"}
    else:
        if charged:
            refund_credits(
                db,
                user.id,
                "linkedin_invite",
                description=f"Refund failed LinkedIn invite to lead #{lead.id}",
                reference_type="lead",
                reference_id=lead.id,
            )
        raise HTTPException(status_code=500, detail="Failed to send LinkedIn invitation via Unipile")


@router.post("/send-whatsapp")
async def send_whatsapp_manually(request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Manually send a WhatsApp message to a specific lead."""
    data = await request.json()
    lead_id = data.get("lead_id")
    message = data.get("message", "")
    
    if not lead_id:
        raise HTTPException(status_code=400, detail="lead_id is required")
    
    lead = _verify_lead_ownership(lead_id, db, user)
    if not lead.whatsapp_number:
        raise HTTPException(status_code=404, detail="Lead not found or has no WhatsApp number")
    blocked_reason = suppression_reason(db, email=lead.email, domain=lead.domain, user_id=owner_id_for_lead(db, lead))
    if blocked_reason:
        raise HTTPException(status_code=400, detail=f"Recipient is suppressed: {blocked_reason}")
    
    # Find a connected WhatsApp account owned by this user
    wa_account = db.query(ChannelAccount).filter(
        ChannelAccount.account_type == "WHATSAPP",
        ChannelAccount.status == "OK",
        ChannelAccount.user_id == user.id,
    ).first()
    
    if not wa_account:
        raise HTTPException(status_code=400, detail="No connected WhatsApp account found. Please connect one in Omnichannel Settings.")
    
    client = UnipileClient()
    
    # If no message provided, generate one with AI
    if not message:
        from services.ai_writer import generate_whatsapp_message
        brief_summary = ""
        from models import LeadBrief
        brief = db.query(LeadBrief).filter(LeadBrief.lead_id == lead_id).first()
        if brief:
            brief_summary = f"{brief.company_overview or ''}"
        message = generate_whatsapp_message(
            first_name=lead.first_name or "",
            company_name=lead.company_name or lead.domain,
            brief_summary=brief_summary,
            template="Introduce ourselves briefly and ask if they'd be open to a quick chat."
        )
    
    if not message:
        raise HTTPException(status_code=500, detail="Failed to generate WhatsApp message")
    
    charged = False
    try:
        consume_credits(
            db,
            user.id,
            "whatsapp_message",
            description=f"WhatsApp message to lead #{lead.id}",
            reference_type="lead",
            reference_id=lead.id,
            metadata={"channel_account_id": wa_account.id},
        )
        charged = True
        success = await client.send_whatsapp_message(wa_account.unipile_account_id, lead.whatsapp_number, message)
    except InsufficientCreditsError as exc:
        raise _insufficient_credits_http(exc) from exc
    except Exception:
        if charged:
            refund_credits(
                db,
                user.id,
                "whatsapp_message",
                description=f"Refund failed WhatsApp message to lead #{lead.id}",
                reference_type="lead",
                reference_id=lead.id,
            )
        raise
    
    if success:
        lead.whatsapp_sent = True
        
        from models import MessageLog
        msg_log = MessageLog(
            lead_id=lead_id,
            channel="whatsapp",
            direction="outbound",
            content=message,
            status="sent"
        )
        db.add(msg_log)
        db.commit()
        return {"success": True, "message": "WhatsApp message sent successfully"}
    else:
        if charged:
            refund_credits(
                db,
                user.id,
                "whatsapp_message",
                description=f"Refund failed WhatsApp message to lead #{lead.id}",
                reference_type="lead",
                reference_id=lead.id,
            )
        raise HTTPException(status_code=500, detail="Failed to send WhatsApp message via Unipile")
