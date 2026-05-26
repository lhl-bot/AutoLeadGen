import logging
import asyncio
from datetime import datetime, timezone
from database import SessionLocal
from models import Lead, Workflow, ChannelAccount
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
            if lead.status == "sent":
                # Email was sent. Check how long ago it was sent.
                # If > 48 hours and no reply, escalate to LinkedIn
                hours_since_update = (datetime.now(timezone.utc) - lead.updated_at.replace(tzinfo=timezone.utc)).total_seconds() / 3600
                if hours_since_update > 48 and lead.linkedin_status == "unconnected":
                    await self._execute_linkedin_connect(lead)
            
            elif lead.status == "replied":
                # They replied to an email. Agent should handle via email followup engine.
                pass
                
            elif lead.linkedin_status == "connected" and lead.status != "replied":
                # They accepted LinkedIn request but haven't replied.
                # Send WhatsApp if available, or LinkedIn message.
                hours_since_update = (datetime.now(timezone.utc) - lead.updated_at.replace(tzinfo=timezone.utc)).total_seconds() / 3600
                if hours_since_update > 72 and lead.whatsapp_number:
                    await self._execute_whatsapp_message(lead)

        except Exception as e:
            logger.error(f"Error evaluating sequence for lead {lead_id}: {e}")
        finally:
            db.close()

    async def _execute_linkedin_connect(self, lead: Lead):
        logger.info(f"[OMNICHANNEL] Sending LinkedIn connect request to {lead.linkedin_url}")
        
        db = SessionLocal()
        try:
            # Find an active LinkedIn account for this user/workflow pool
            # For simplicity, just pick the first connected LinkedIn account
            acc = db.query(ChannelAccount).filter(ChannelAccount.account_type == "LINKEDIN", ChannelAccount.status == "OK").first()
            if not acc:
                logger.warning("No active LinkedIn accounts found to send connection request.")
                return

            # Note: lead.linkedin_url needs to be converted or extracted to provider_id.
            # Assuming lead.linkedin_url contains the vanity name or we have it.
            # For Unipile, provider_id is the LinkedIn public identifier
            provider_id = lead.linkedin_url.strip("/").split("/")[-1] if lead.linkedin_url else None
            
            if provider_id:
                success = await self.unipile.send_linkedin_invitation(
                    account_id=acc.unipile_account_id,
                    provider_id=provider_id,
                    message="Hi, I'd love to connect and discuss potential synergies."
                )
                
                if success:
                    l = db.query(Lead).filter(Lead.id == lead.id).first()
                    if l:
                        l.linkedin_status = "requested"
                        db.commit()
        finally:
            db.close()

    async def _execute_whatsapp_message(self, lead: Lead):
        logger.info(f"[OMNICHANNEL] Sending WhatsApp message to {lead.whatsapp_number}")
        
        db = SessionLocal()
        try:
            acc = db.query(ChannelAccount).filter(ChannelAccount.account_type == "WHATSAPP", ChannelAccount.status == "OK").first()
            if not acc:
                logger.warning("No active WhatsApp accounts found.")
                return
                
            success = await self.unipile.send_whatsapp_message(
                account_id=acc.unipile_account_id,
                phone_number=lead.whatsapp_number,
                text=f"Hi {lead.first_name}, just following up on our email!"
            )
        finally:
            db.close()

async def omni_channel_daemon():
    """Background task to constantly evaluate lead sequences."""
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
