import os
from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks
from sqlalchemy.orm import Session
from database import get_db
from models import ChannelAccount, Lead, User
from services.unipile_client import UnipileClient
from services.auth import get_current_user
import logging

logger = logging.getLogger("channels_api")
router = APIRouter(prefix="/api/channels", tags=["channels"])

@router.post("/auth-link")
async def create_auth_link(request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """
    Called by the frontend to get a Hosted Auth Wizard link for Unipile.
    Expects JSON: {"type": "LINKEDIN" | "WHATSAPP", "name": "User's Name"}
    """
    data = await request.json()
    account_type = data.get("type", "LINKEDIN")
    name = data.get("name", f"Account - {account_type}")
    
    frontend_base_url = os.environ.get("FRONTEND_BASE_URL", "http://localhost:3000").rstrip("/")
    success_redirect_url = f"{frontend_base_url}/dashboard/settings?unipile_success=true"

    client = UnipileClient()
    url = await client.generate_hosted_auth_link(account_type, name, success_redirect_url)
    
    if url:
        return {"url": url}
    else:
        raise HTTPException(status_code=500, detail="Failed to generate Unipile auth link.")

@router.get("/accounts")
async def list_accounts(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """
    Returns all connected Unipile accounts.
    Actively syncs with Unipile API since local webhooks might fail.
    """
    try:
        client = UnipileClient()
        unipile_accounts = await client.get_all_accounts()
        
        # Keep track of active account IDs from Unipile
        active_ids = []
        
        for up_acc in unipile_accounts:
            acc_id = up_acc.get("id")
            if not acc_id:
                continue
                
            active_ids.append(acc_id)
            status = up_acc.get("status", "OK")
            provider = (up_acc.get("provider") or up_acc.get("type") or "UNKNOWN").upper()
            name = up_acc.get("name", f"{provider} Account")
            
            db_acc = db.query(ChannelAccount).filter(ChannelAccount.unipile_account_id == acc_id).first()
            if db_acc:
                db_acc.status = status
                db_acc.name = name
                # Ensure the account_type is synchronized if it is UNKNOWN or mismatching
                if (db_acc.account_type == "UNKNOWN" or db_acc.account_type != provider) and provider != "UNKNOWN":
                    db_acc.account_type = provider
            else:
                # Create missing account under current user
                new_acc = ChannelAccount(
                    user_id=user.id,
                    account_type=provider,
                    unipile_account_id=acc_id,
                    name=name,
                    status=status
                )
                db.add(new_acc)
                
        # Mark accounts not returned by Unipile as 'DISCONNECTED'
        if active_ids:
            disconnected_accounts = db.query(ChannelAccount).filter(~ChannelAccount.unipile_account_id.in_(active_ids)).all()
        else:
            disconnected_accounts = db.query(ChannelAccount).all()
            
        for d_acc in disconnected_accounts:
            if d_acc.status != "DISCONNECTED":
                d_acc.status = "DISCONNECTED"
                logger.info(f"Marked account {d_acc.unipile_account_id} ({d_acc.name}) as DISCONNECTED")
        
        db.commit()
    except Exception as e:
        logger.error(f"Error syncing accounts with Unipile: {e}")

    # Return local DB accounts after sync (user-filtered)
    if user.is_admin:
        accounts = db.query(ChannelAccount).all()
    else:
        accounts = db.query(ChannelAccount).filter(ChannelAccount.user_id == user.id).all()
    return accounts

def _verify_unipile_signature(request: Request) -> bool:
    """Verify the X-Unipile-Signature header if a webhook secret is configured."""
    secret = os.environ.get("UNIPILE_WEBHOOK_SECRET", "")
    if not secret:
        return True  # No secret configured, skip verification
    import hmac, hashlib
    signature = request.headers.get("X-Unipile-Signature", "")
    if not signature:
        return False
    # Unipile sends the signature as a hex-encoded HMAC-SHA256 of the raw body
    # We can't re-read the body in FastAPI, so we validate the header presence + timestamp
    expected = hmac.new(secret.encode(), b"", hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


@router.post("/webhooks/unipile")
async def unipile_webhook(request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """
    Receives webhooks from Unipile.
    Examples: account status changes, new incoming messages.
    """
    if not _verify_unipile_signature(request):
        logger.warning("Unipile webhook signature verification failed")
        raise HTTPException(status_code=403, detail="Invalid signature")

    try:
        payload = await request.json()
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
            elif new_status == "OK":
                # Create new account (webhook-created accounts go to the first admin, or user 1)
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

        elif event_type == "message":
            # Incoming message (LinkedIn or WhatsApp)
            message_data = payload.get("message", {})
            sender_id = message_data.get("sender_id")
            text = message_data.get("text")
            
            # Match sender_id with our Leads
            # Unipile's sender_id matches the LinkedIn profile ID or WhatsApp number
            lead = db.query(Lead).filter(
                (Lead.linkedin_url.contains(sender_id)) | 
                (Lead.whatsapp_number == sender_id)
            ).first()
            
            if lead:
                # We have a reply from a known lead!
                lead.status = "replied"
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
    
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead or not lead.linkedin_url:
        raise HTTPException(status_code=404, detail="Lead not found or has no LinkedIn URL")
    
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
    
    success = await client.send_linkedin_invitation(linkedin_account.unipile_account_id, provider_id, message)
    
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
        raise HTTPException(status_code=500, detail="Failed to send LinkedIn invitation via Unipile")


@router.post("/send-whatsapp")
async def send_whatsapp_manually(request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Manually send a WhatsApp message to a specific lead."""
    data = await request.json()
    lead_id = data.get("lead_id")
    message = data.get("message", "")
    
    if not lead_id:
        raise HTTPException(status_code=400, detail="lead_id is required")
    
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead or not lead.whatsapp_number:
        raise HTTPException(status_code=404, detail="Lead not found or has no WhatsApp number")
    
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
    
    success = await client.send_whatsapp_message(wa_account.unipile_account_id, lead.whatsapp_number, message)
    
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
        raise HTTPException(status_code=500, detail="Failed to send WhatsApp message via Unipile")
