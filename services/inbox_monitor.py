import imaplib
import email
import asyncio
import re
import time
import logging
from email.header import decode_header
from email.utils import parseaddr
from typing import Dict, Any, List
import os
from services.auth import decrypt_smtp_pass
from database import SessionLocal, db_retry
from models import EmailAccount, EmailLog, Lead
from services.followup_engine import analyze_reply_intent, draft_followup_email
from services.email_threads import (
    canonical_inbound_message_id,
    find_lead_for_bounce,
    find_lead_for_inbound,
    inbound_message_exists,
)
from services.suppression import suppress_lead
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
    try:
        for text, encoding in decode_header(str(value)):
            if isinstance(text, bytes):
                enc = encoding or "utf-8"
                try:
                    parts.append(text.decode(enc, errors="replace"))
                except (LookupError, ValueError):
                    parts.append(text.decode("utf-8", errors="replace"))
            else:
                parts.append(str(text))
    except Exception:
        return str(value)
    
    decoded = "".join(parts)
    # Fallback regex for any leftover encoded words
    if "=?" in decoded:
        import re
        def replace_word(match):
            word = match.group(0)
            try:
                p = decode_header(word)
                if p and isinstance(p[0][0], bytes):
                    enc = p[0][1] or "utf-8"
                    try:
                        return p[0][0].decode(enc, errors="replace")
                    except (LookupError, ValueError):
                        return p[0][0].decode("utf-8", errors="replace")
            except Exception:
                pass
            return word
        decoded = re.sub(r'=\?[^?]+\?[QB]\?[^?]+\?=', replace_word, decoded, flags=re.IGNORECASE)
    return decoded



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
                password = decrypt_smtp_pass(account.smtp_pass)

                # Retry IMAP connection up to 3 times with backoff
                mail = None
                for attempt in range(3):
                    try:
                        mail = imaplib.IMAP4_SSL(account.imap_host, account.imap_port, timeout=15)
                        mail.login(account.email, password)
                        break
                    except (imaplib.IMAP4.abort, OSError, EOFError) as conn_err:
                        if attempt < 2:
                            wait = (attempt + 1) * 3
                            logger.warning(f"IMAP connect attempt {attempt+1}/3 failed for {account.email}: {conn_err}. Retrying in {wait}s...")
                            time.sleep(wait)
                            mail = None
                        else:
                            raise
                if mail is None:
                    continue

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
                                
                                # Extract sender email
                                sender = _decode_header_value(msg.get("From") or "")
                                sender_email = parseaddr(sender)[1] or sender.strip()
                                    
                                reply_text = _get_text_from_email(msg)
                                subject = _decode_header_value(msg.get("Subject", ""))
                                msg_id = canonical_inbound_message_id(
                                    msg.get("Message-ID"),
                                    account_email=account.email,
                                    sender_email=sender_email,
                                    subject=subject,
                                    date_header=msg.get("Date", ""),
                                    body=reply_text,
                                )
                                if inbound_message_exists(
                                    db,
                                    account_email=account.email,
                                    message_id=msg_id,
                                ):
                                    continue

                                if _is_bounce(sender_email, subject, reply_text):
                                    bounced_recipients = _extract_bounced_recipients(msg, reply_text)
                                    lead = None
                                    for recipient in bounced_recipients:
                                        lead = find_lead_for_bounce(
                                            db,
                                            account=account,
                                            recipient_email=recipient,
                                        )
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

                                # Resolve by thread headers first, then mailbox-scoped
                                # outbound history, never by a global lead-email match.
                                lead = find_lead_for_inbound(
                                    db,
                                    account=account,
                                    sender_email=sender_email,
                                    in_reply_to=msg.get("In-Reply-To"),
                                    references=msg.get("References"),
                                )
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
                                        lead.has_replied = True
                                        lead.reply_intent = "unsubscribe"
                                        suppress_lead(db, lead, reason="unsubscribe", source="inbound_reply")
                                        lead.last_reply_at = datetime.now(timezone.utc)
                                        lead.reply_snippet = reply_text[:200]
                                        logger.info(f"[UNSUB] Lead {sender_email} requested unsubscribe. Marked as unsubscribed. Msg ID: {msg_id}")
                                        db.commit()
                                        continue
                                    
                                    # Persist the real customer reply before any AI call.
                                    # Intent analysis/drafting is enrichment and must never
                                    # make an already-received message disappear on failure.
                                    lead.status = "replied"
                                    lead.last_reply_at = datetime.now(timezone.utc)
                                    lead.reply_snippet = reply_text[:200]
                                    lead.has_replied = True
                                    lead.reply_intent = lead.reply_intent or "other"
                                    db.commit()

                                    # AI intent enrichment is failure-isolated.
                                    try:
                                        analysis = analyze_reply_intent(reply_text)
                                        intent = analysis.get("intent", "other")
                                    except Exception as intent_error:
                                        intent = "other"
                                        logger.warning(
                                            "Reply intent analysis failed for lead %s; preserving reply as other: %s",
                                            lead.id,
                                            intent_error,
                                        )
                                    lead.reply_intent = intent

                                    if intent == "not_interested":
                                        lead.status = "rejected"
                                    db.commit()

                                    if intent in ["interested", "more_info"]:
                                        try:
                                            draft = draft_followup_email({
                                                "first_name": lead.first_name,
                                                "company_name": lead.company_name
                                            }, reply_text, intent)
                                            lead.ai_draft = draft
                                        except Exception as draft_error:
                                            db.rollback()
                                            logger.warning(
                                                "Reply draft generation failed for lead %s; reply remains available: %s",
                                                lead.id,
                                                draft_error,
                                            )
                                            continue

                                        # Alert the owner about a high-intent reply.
                                        try:
                                            from services.notifications import notify, owner_id_for_lead
                                            who = lead.company_name or lead.first_name or sender_email
                                            notify(
                                                db,
                                                owner_id_for_lead(db, lead),
                                                "high_intent_reply",
                                                f"High-intent reply from {who}",
                                                body=reply_text[:200],
                                                link="/dashboard/replies",
                                                reference_type="lead",
                                                reference_id=lead.id,
                                                commit=False,
                                            )
                                        except Exception:
                                            pass
                                        db.commit()
                                    logger.info(f"[REPLY] Logged reply from {sender_email}. Status: {lead.status}. Msg ID: {msg_id}")
                                else:
                                    logger.info(
                                        "No mailbox-scoped lead matched inbound message from %s to %s (Message-ID: %s)",
                                        sender_email,
                                        account.email,
                                        msg_id,
                                    )
                mail.logout()
            except Exception as e:
                logger.error(f"Error checking IMAP for {account.email}: {e}")
    finally:
        db.close()
