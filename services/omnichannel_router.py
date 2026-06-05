import logging
import asyncio
import os
import re
import httpx
from datetime import datetime, timezone
from database import SessionLocal, db_retry_async
from models import Lead, Workflow, ChannelAccount, MessageLog
from services.credits import InsufficientCreditsError, consume_credits, refund_credits
from services.unipile_client import UnipileClient

logger = logging.getLogger("omnichannel_router")

class OmnichannelRouter:
    """
    Handles multi-touch sequences across different platforms.
    Example Sequence:
    Day 1: Send Email (via outbound_engine.py)
    Day 2: If no reply, send LinkedIn Connect Request
    Day 5: If connected on LinkedIn but no reply, send WhatsApp Message
    """
    
    def __init__(self):
        self.unipile = UnipileClient()

    @db_retry_async()
    async def evaluate_lead_sequence(self, lead_id: int):
        db = SessionLocal()
        try:
            lead = db.query(Lead).filter(Lead.id == lead_id).first()
            if not lead:
                return
                
            workflow = db.query(Workflow).filter(Workflow.id == lead.workflow_id).first()
            if not workflow:
                return

            # Example State Machine Logic
            last_update = lead.updated_at or lead.created_at or datetime.now(timezone.utc)
            hours_since_update = (datetime.now(timezone.utc) - last_update.replace(tzinfo=timezone.utc)).total_seconds() / 3600

            if lead.status == "sent":
                # Email was sent. Check how long ago it was sent.
                # If > 48 hours and no reply, escalate to LinkedIn
                if hours_since_update > 48 and lead.linkedin_status == "unconnected" and lead.linkedin_url:
                    await self._execute_linkedin_connect(lead)
            
            elif lead.linkedin_status == "requested":
                # LinkedIn connect request was sent. Check if it was accepted.
                await self._check_linkedin_connection_status(lead)
            
            elif lead.status == "replied":
                # They replied to an email. Agent should handle via email followup engine.
                pass
                
            elif lead.linkedin_status == "connected" and lead.status != "replied":
                # They accepted LinkedIn request but haven't replied.
                # Send WhatsApp if available, or LinkedIn message.
                if hours_since_update > 72:
                    if not lead.whatsapp_number and workflow.enable_whatsapp:
                        # Proactively enrich WhatsApp phone number for connected LinkedIn users
                        await self._enrich_whatsapp_number(lead)
                        # Refresh lead info from DB
                        lead = db.query(Lead).filter(Lead.id == lead_id).first()
                    
                    if lead.whatsapp_number:
                        await self._execute_whatsapp_message(lead)

        except Exception as e:
            logger.error(f"Error evaluating sequence for lead {lead_id}: {e}")
        finally:
            db.close()

    @db_retry_async()
    async def _execute_linkedin_connect(self, lead: Lead):
        logger.info(f"[OMNICHANNEL] Sending LinkedIn connect request to {lead.linkedin_url}")
        
        db = SessionLocal()
        credit_charged = False
        try:
            workflow = db.query(Workflow).filter(Workflow.id == lead.workflow_id).first()
            owner_id = workflow.user_id if workflow else None
            if not owner_id:
                return

            # Find an active LinkedIn account for this user/workflow pool
            acc = db.query(ChannelAccount).filter(
                ChannelAccount.account_type == "LINKEDIN",
                ChannelAccount.status == "OK",
                ChannelAccount.user_id == owner_id,
            ).first()
            if not acc:
                logger.warning("No active LinkedIn accounts found to send connection request.")
                return

            # Note: lead.linkedin_url needs to be converted or extracted to provider_id.
            # For Unipile, provider_id starts with 'ACo...' and must be resolved from the vanity name
            provider_id = await self.unipile.get_linkedin_provider_id(acc.unipile_account_id, lead.linkedin_url)
            
            if provider_id:
                message = "Hi, I'd love to connect and discuss potential synergies."
                try:
                    consume_credits(
                        db,
                        owner_id,
                        "linkedin_invite",
                        description=f"Omnichannel LinkedIn invite for lead #{lead.id}",
                        reference_type="lead",
                        reference_id=lead.id,
                    )
                    credit_charged = True
                except InsufficientCreditsError as credit_err:
                    logger.warning(
                        f"LinkedIn connect skipped for lead {lead.id}: "
                        f"insufficient credits (required={credit_err.required}, balance={credit_err.balance})"
                    )
                    return

                success = await self.unipile.send_linkedin_invitation(
                    account_id=acc.unipile_account_id,
                    provider_id=provider_id,
                    message=message
                )
                
                # Record in DB
                l = db.query(Lead).filter(Lead.id == lead.id).first()
                if l:
                    if success:
                        l.linkedin_sent = True
                        l.linkedin_status = "requested"
                    else:
                        l.linkedin_status = "failed"
                    
                    msg_log = MessageLog(
                        lead_id=lead.id,
                        channel="linkedin",
                        direction="outbound",
                        content=message,
                        status="sent" if success else "failed"
                    )
                    db.add(msg_log)
                    db.commit()
                    if not success and credit_charged:
                        refund_credits(
                            db,
                            owner_id,
                            "linkedin_invite",
                            description=f"Refund failed omnichannel LinkedIn invite for lead #{lead.id}",
                            reference_type="lead",
                            reference_id=lead.id,
                        )
                        credit_charged = False
            else:
                logger.warning(f"Could not resolve LinkedIn provider_id for vanity name from {lead.linkedin_url}")
        except Exception:
            if credit_charged:
                refund_credits(
                    db,
                    owner_id,
                    "linkedin_invite",
                    description=f"Refund omnichannel LinkedIn error for lead #{lead.id}",
                    reference_type="lead",
                    reference_id=lead.id,
                )
            raise
        finally:
            db.close()

    @db_retry_async()
    async def _check_linkedin_connection_status(self, lead: Lead):
        logger.info(f"[OMNICHANNEL] Checking LinkedIn connection status for lead {lead.id} ({lead.linkedin_url})")
        
        db = SessionLocal()
        try:
            acc = db.query(ChannelAccount).filter(ChannelAccount.account_type == "LINKEDIN", ChannelAccount.status == "OK").first()
            if not acc:
                return

            match = re.search(r'linkedin\.com/in/([^/?#]+)', lead.linkedin_url)
            if not match:
                return
            vanity_name = match.group(1).strip('/')
            
            url = f"{self.unipile.dsn}/api/v1/users/{vanity_name}"
            params = {"account_id": acc.unipile_account_id}
            
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, headers=self.unipile.headers, params=params, timeout=10.0)
                if resp.status_code == 200:
                    data = resp.json()
                    relation = data.get("relation") or ""
                    # "1ST" in relation indicates first-degree LinkedIn connection
                    if "1ST" in relation.upper():
                        l = db.query(Lead).filter(Lead.id == lead.id).first()
                        if l:
                            l.linkedin_status = "connected"
                            db.commit()
                            logger.info(f"[OMNICHANNEL] Lead {lead.id} LinkedIn connection request ACCEPTED!")
                else:
                    logger.warning(f"[OMNICHANNEL] Unipile user query returned status {resp.status_code} for {vanity_name}")
        except Exception as e:
            logger.error(f"[OMNICHANNEL] Exception in check_linkedin_connection_status: {e}")
        finally:
            db.close()

    @db_retry_async()
    async def _enrich_whatsapp_number(self, lead: Lead):
        _lc_key = os.environ.get("LEADCONTACT_API_KEY", "").strip()
        if not _lc_key:
            return
            
        from services.leadcontact_client import LeadContactClient
        lc = LeadContactClient(_lc_key)
        
        logger.info(f"[OMNICHANNEL] Attempting to enrich WhatsApp number for lead {lead.id} ({lead.linkedin_url})")
        
        loop = asyncio.get_event_loop()
        try:
            phone_result = await loop.run_in_executor(None, lc.query_phone, lead.linkedin_url)
            phones = phone_result.get("phones", [])
            if phones:
                best = next((p for p in phones if p["valid"]), phones[0])
                whatsapp_number = best["phone"]
                
                db = SessionLocal()
                try:
                    l = db.query(Lead).filter(Lead.id == lead.id).first()
                    if l:
                        l.whatsapp_number = whatsapp_number
                        db.commit()
                        logger.info(f"[OMNICHANNEL] Successfully enriched WhatsApp number for lead {lead.id}: {whatsapp_number}")
                finally:
                    db.close()
        except Exception as e:
            logger.error(f"[OMNICHANNEL] Failed to enrich WhatsApp number: {e}")

    @db_retry_async()
    async def _execute_whatsapp_message(self, lead: Lead):
        logger.info(f"[OMNICHANNEL] Sending WhatsApp message to {lead.whatsapp_number}")
        
        db = SessionLocal()
        credit_charged = False
        try:
            workflow = db.query(Workflow).filter(Workflow.id == lead.workflow_id).first()
            owner_id = workflow.user_id if workflow else None
            if not owner_id:
                return

            acc = db.query(ChannelAccount).filter(
                ChannelAccount.account_type == "WHATSAPP",
                ChannelAccount.status == "OK",
                ChannelAccount.user_id == owner_id,
            ).first()
            if not acc:
                logger.warning("No active WhatsApp accounts found.")
                return
                
            message_text = f"Hi {lead.first_name}, just following up on our email!"
            try:
                consume_credits(
                    db,
                    owner_id,
                    "whatsapp_message",
                    description=f"Omnichannel WhatsApp message for lead #{lead.id}",
                    reference_type="lead",
                    reference_id=lead.id,
                )
                credit_charged = True
            except InsufficientCreditsError as credit_err:
                logger.warning(
                    f"WhatsApp skipped for lead {lead.id}: "
                    f"insufficient credits (required={credit_err.required}, balance={credit_err.balance})"
                )
                return

            success = await self.unipile.send_whatsapp_message(
                account_id=acc.unipile_account_id,
                phone_number=lead.whatsapp_number,
                text=message_text
            )
            
            # Record in DB
            l = db.query(Lead).filter(Lead.id == lead.id).first()
            if l:
                l.whatsapp_sent = True
                msg_log = MessageLog(
                    lead_id=lead.id,
                    channel="whatsapp",
                    direction="outbound",
                    content=message_text,
                    status="sent" if success else "failed"
                )
                db.add(msg_log)
                db.commit()
                if not success and credit_charged:
                    refund_credits(
                        db,
                        owner_id,
                        "whatsapp_message",
                        description=f"Refund failed omnichannel WhatsApp message for lead #{lead.id}",
                        reference_type="lead",
                        reference_id=lead.id,
                    )
                    credit_charged = False
        except Exception:
            if credit_charged:
                refund_credits(
                    db,
                    owner_id,
                    "whatsapp_message",
                    description=f"Refund omnichannel WhatsApp error for lead #{lead.id}",
                    reference_type="lead",
                    reference_id=lead.id,
                )
            raise
        finally:
            db.close()

async def omni_channel_daemon():
    """Background task to constantly evaluate lead sequences."""
    logger.info("Omnichannel engine started")
    router = OmnichannelRouter()
    while True:
        try:
            db = SessionLocal()
            try:
                # Find leads that are in active workflows and haven't bounced/rejected
                active_leads = db.query(Lead).join(Workflow).filter(
                    Workflow.status == "active",
                    Lead.status.notin_(["bounced", "rejected", "unsubscribed"])
                ).limit(100).all()
                
                lead_ids = [l.id for l in active_leads]
            finally:
                db.close()
                
            for lid in lead_ids:
                await router.evaluate_lead_sequence(lid)
                
        except Exception as e:
            logger.error(f"Omnichannel Daemon Error: {e}")
            
        await asyncio.sleep(300) # Run every 5 minutes
