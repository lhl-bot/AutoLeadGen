from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from models import EmailLog, Lead


@dataclass
class SendFailureUpdate:
    fail_count: int
    permanently_failed: bool


def record_send_success(
    db: Session,
    lead: Lead,
    *,
    from_email: str,
    subject: str,
    body: str,
    message_id: Optional[str] = None,
) -> EmailLog:
    lead.status = "sent"
    lead.send_fail_count = 0
    email_log = EmailLog(
        lead_id=lead.id,
        direction="outbound",
        from_email=from_email,
        to_email=lead.email,
        subject=subject,
        body=body,
        message_id=message_id,
    )
    db.add(email_log)
    db.commit()
    return email_log


def record_send_failure(
    db: Session,
    lead: Lead,
    *,
    message: Optional[str] = None,
    max_failures: int = 3,
) -> SendFailureUpdate:
    lead.send_fail_count = (lead.send_fail_count or 0) + 1
    permanently_failed = lead.send_fail_count >= max_failures
    if message:
        lead.reply_snippet = f"Send failed (attempt {lead.send_fail_count}/{max_failures}): {message}"
    if permanently_failed:
        lead.status = "send_failed"
    db.commit()
    return SendFailureUpdate(
        fail_count=lead.send_fail_count,
        permanently_failed=permanently_failed,
    )
