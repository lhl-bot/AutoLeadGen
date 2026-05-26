import imaplib
import email
import asyncio
import re
import logging
from email.header import decode_header
from email.utils import parseaddr
import json
from typing import Dict, Any, List
import os
from sqlalchemy import func
from services.auth import decrypt_smtp_pass
from database import SessionLocal, db_retry
from models import EmailAccount, EmailLog, Lead
from services.followup_engine import analyze_reply_intent, draft_followup_email
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

def test_imap_connection(imap_host: str, imap_port: int, email_user: str, email_pass: str) -> Dict[str, Any]:
    """Test IMAP connection."""
    try:
        mail = imaplib.IMAP4_SSL(imap_host, imap_port)
        mail.login(email_user, email_pass)
        mail.logout()
        return {"success": True, "message": "IMAP connection successful!"}
    except Exception as e:
        return {"success": False, "message": str(e)}

def _get_text_from_email(msg) -> str:
    text_content = ""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition"))
            if content_type in {"text/plain", "text/html"} and "attachment" not in content_disposition:
                try:
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        text_content += payload.decode(charset, errors="replace")
                except Exception:
                    pass
    else:
        try:
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                text_content = payload.decode(charset, errors="replace")
        except Exception:
            pass
    return text_content.strip()


def _decode_header_value(value: str) -> str:
    if not value:
        return ""
    parts = []
    for text, encoding in decode_header(value):
        if isinstance(text, bytes):
            parts.append(text.decode(encoding or "utf-8", errors="replace"))
        else:
            parts.append(text)
    return "".join(parts)


_BOUNCE_SENDERS = (
    "mailer-daemon",
    "postmaster",
    "mail delivery subsystem",
    "mailsupport.aliyun.com",
)
_BOUNCE_KEYWORDS = (
    "undelivered mail",
    "delivery status notification",
    "delivery failure",
    "mail delivery failed",
    "returned mail",
    "message not delivered",
    "failure notice",
    "undeliverable",
    "550 ",
    "5.1.1",
    "5.2.2",
    "5.7.1",
    "未送达",
    "退信",
    "无法发送到",
    "系统应答",
    "eso_local_spam",
)
_EMAIL_RE = re.compile(r"[A-Z0-9._%+\-']+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.IGNORECASE)


def _is_bounce(sender_email: str, subject: str, body: str) -> bool:
    haystack = f"{sender_email} {subject} {body[:2000]}".lower()
    return any(s in haystack for s in _BOUNCE_SENDERS) or any(k in haystack for k in _BOUNCE_KEYWORDS)


def _extract_bounced_recipients(msg, body: str) -> List[str]:
    recipients = set()
    found_structured_status = False

    failed_header = msg.get("X-Failed-Recipients", "")
    for addr in _EMAIL_RE.findall(failed_header):
        recipients.add(addr.lower())
        found_structured_status = True

    for part in msg.walk() if msg.is_multipart() else []:
        if part.get_content_type() != "message/delivery-status":
            continue
        payload = part.get_payload()
        if not isinstance(payload, list):
            continue
        found_structured_status = True
        for status_msg in payload:
            for header in ("Final-Recipient", "Original-Recipient"):
                value = status_msg.get(header, "")
                for addr in _EMAIL_RE.findall(value):
                    recipients.add(addr.lower())

    if found_structured_status:
        return list(recipients)

    # Some providers send plain-text bounces without a delivery-status part.
    # In that case, only trust addresses attached to explicit recipient labels;
    # do not scan every email in the body, because quoted original messages can
    # contain unrelated addresses and cause false bounce attribution.
    label_patterns = [
        r"(?:Final-Recipient|Original-Recipient)\s*:\s*(?:rfc822;)?\s*([A-Z0-9._%+\-']+@[A-Z0-9.\-]+\.[A-Z]{2,})",
        r"(?:X-Failed-Recipients|Failed Recipient|Recipient address|Undelivered To)\s*:\s*([A-Z0-9._%+\-']+@[A-Z0-9.\-]+\.[A-Z]{2,})",
        r"(?:The following address(?:es)? failed|Delivery to the following recipient failed)[^\n\r]*[\n\r]+[<\s]*([A-Z0-9._%+\-']+@[A-Z0-9.\-]+\.[A-Z]{2,})",
        r"无法发送到\s*([A-Z0-9._%+\-']+@[A-Z0-9.\-]+\.[A-Z]{2,})",
    ]
    for pattern in label_patterns:
        for addr in re.findall(pattern, body, flags=re.IGNORECASE):
            recipients.add(addr.lower())
    return list(recipients)


def _bounce_summary(subject: str, body: str) -> str:
    compact = re.sub(r"\s+", " ", body or "").strip()
    patterns = [
        r"(系统应答\s*:\s*[^退<。]+)",
        r"(ESO_LOCAL_SPAM[^<。]+)",
        r"(退信原因\s*:[^解决<]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, compact, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()[:200]
    return (subject or compact)[:200]

async def run_inbox_monitor_loop():
    interval = int(os.environ.get("INBOX_MONITOR_INTERVAL_SECONDS", "300"))
    logger.info(f"Inbox monitor started; interval={interval}s")
    while True:
        try:
            await asyncio.to_thread(check_inbox_for_replies)
        except Exception as e:
            logger.error(f"Error in inbox monitor loop: {e}")
        await asyncio.sleep(interval)

@db_retry()
def check_inbox_for_replies():
    """
    Check all configured email accounts for replies.
    """
    db = SessionLocal()
    try:
        accounts = db.query(EmailAccount).filter(EmailAccount.imap_host.isnot(None)).all()
        for account in accounts:
            try:
                mail = imaplib.IMAP4_SSL(account.imap_host, account.imap_port)
                mail.login(account.email, decrypt_smtp_pass(account.smtp_pass))
                mail.select("inbox")
                
                # Retrieve lookback window
                lookback_days = int(os.environ.get("IMAP_LOOKBACK_DAYS", "7"))
                date_cutoff = (datetime.now() - timedelta(days=lookback_days)).strftime("%d-%b-%Y")
                
                # Search for emails since date_cutoff
                status, messages = mail.search(None, f'(SINCE "{date_cutoff}")')
                if status == "OK" and messages[0]:
                    email_ids = messages[0].split()
                    for e_id in email_ids:
                        # Use BODY.PEEK[] so it doesn't automatically mark emails as read
                        _, msg_data = mail.fetch(e_id, '(BODY.PEEK[])')
                        for response_part in msg_data:
                            if isinstance(response_part, tuple):
                                msg = email.message_from_bytes(response_part[1])
                                
                                # 1. Extract Message-ID and deduplicate
                                msg_id = msg.get("Message-ID")
                                if msg_id:
                                    msg_id = msg_id.strip()
                                    existing_log = db.query(EmailLog).filter(EmailLog.message_id == msg_id).first()
                                    if existing_log:
                                        continue  # Already processed this email
                                
                                # Extract sender email
                                sender = _decode_header_value(msg.get("From") or "")
                                sender_email = parseaddr(sender)[1] or sender.strip()
                                    
                                reply_text = _get_text_from_email(msg)
                                subject = _decode_header_value(msg.get("Subject", ""))

                                if _is_bounce(sender_email, subject, reply_text):
                                    bounced_recipients = _extract_bounced_recipients(msg, reply_text)
                                    lead = None
                                    for recipient in bounced_recipients:
                                        lead = db.query(Lead).join(EmailLog).filter(
                                            func.lower(Lead.email) == recipient,
                                            EmailLog.direction == "outbound",
                                            func.lower(EmailLog.to_email) == recipient,
                                        ).order_by(EmailLog.sent_at.desc()).first()
                                        if lead:
                                            break
                                    if lead:
                                        db.add(EmailLog(
                                            lead_id=lead.id,
                                            direction="inbound",
                                            from_email=sender_email,
                                            to_email=account.email,
                                            subject=subject,
                                            body=reply_text,
                                            message_id=msg_id
                                        ))
                                        
                                        # Check if this is a local spam engine rejection
                                        reply_lower = (reply_text or "").lower()
                                        if "eso_local_spam" in reply_lower or "local spam engine" in reply_lower:
                                            lead.status = "send_failed"
                                            logger.info(f"[SPAM_BLOCK] Lead {lead.email} marked send_failed (spammed by local engine). Msg ID: {msg_id}")
                                        else:
                                            lead.status = "bounced"
                                            logger.info(f"[BOUNCE] Lead {lead.email} marked bounced. Msg ID: {msg_id}")
                                            
                                        lead.last_reply_at = datetime.now(timezone.utc)
                                        lead.reply_snippet = _bounce_summary(subject, reply_text)
                                        db.commit()
                                    continue

                                # Find corresponding Lead
                                lead = db.query(Lead).filter(Lead.email == sender_email).first()
                                if lead:
                                    # Log inbound email first (always)
                                    db_log = EmailLog(
                                        lead_id=lead.id,
                                        direction="inbound",
                                        from_email=sender_email,
                                        to_email=account.email,
                                        subject=subject,
                                        body=reply_text,
                                        message_id=msg_id
                                    )
                                    db.add(db_log)
                                    
                                    # === Unsubscribe Detection ===
                                    _unsub_keywords = ["unsubscribe", "opt out", "opt-out", "stop emailing",
                                                       "remove me", "take me off", "don't contact", "do not contact",
                                                       "no longer wish", "退订", "取消订阅"]
                                    reply_lower = (reply_text + " " + subject).lower()
                                    is_unsubscribe = any(kw in reply_lower for kw in _unsub_keywords)
                                    
                                    if is_unsubscribe:
                                        lead.status = "unsubscribed"
                                        lead.last_reply_at = datetime.now(timezone.utc)
                                        lead.reply_snippet = reply_text[:200]
                                        logger.info(f"[UNSUB] Lead {sender_email} requested unsubscribe. Marked as unsubscribed. Msg ID: {msg_id}")
                                        db.commit()
                                        continue
                                    
                                    # Update Lead as replied
                                    lead.status = "replied"
                                    lead.last_reply_at = datetime.now(timezone.utc)
                                    lead.reply_snippet = reply_text[:200]
                                    
                                    # AI Followup Analysis
                                    analysis = analyze_reply_intent(reply_text)
                                    intent = analysis.get("intent", "other")
                                    
                                    if intent == "not_interested":
                                        lead.status = "rejected"
                                    elif intent in ["interested", "more_info"]:
                                        draft = draft_followup_email({
                                            "first_name": lead.first_name,
                                            "company_name": lead.company_name
                                        }, reply_text, intent)
                                        lead.ai_draft = draft
                                        
                                    db.commit()
                                    logger.info(f"[REPLY] Logged reply from {sender_email}. Status: {lead.status}. Msg ID: {msg_id}")
                mail.logout()
            except Exception as e:
                logger.error(f"Error checking IMAP for {account.email}: {e}")
    finally:
        db.close()
