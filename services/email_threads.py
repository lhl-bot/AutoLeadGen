"""Email conversation attribution and reply-thread helpers."""
import hashlib
import re
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from models import EmailAccount, EmailLog, Lead, Workflow, WorkflowEmail


_MESSAGE_ID_RE = re.compile(r"<[^<>]+>")


def extract_message_ids(*values: Optional[str]) -> list[str]:
    """Return normalized RFC message ids while preserving header order."""
    result: list[str] = []
    for value in values:
        if not value:
            continue
        matches = _MESSAGE_ID_RE.findall(value)
        candidates = matches or value.split()
        for candidate in candidates:
            normalized = candidate.strip()
            if normalized and normalized not in result:
                result.append(normalized)
    return result


def canonical_inbound_message_id(
    message_id: Optional[str],
    *,
    account_email: str,
    sender_email: str,
    subject: str,
    date_header: str,
    body: str,
) -> str:
    """Keep provider ids or create a stable id for messages that omit one."""
    if message_id and message_id.strip():
        return message_id.strip()
    raw = "\x1f".join([
        account_email.strip().lower(),
        sender_email.strip().lower(),
        subject.strip(),
        date_header.strip(),
        body.strip(),
    ])
    digest = hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()
    return f"<autoleadgen-{digest}@local>"


def inbound_message_exists(
    db: Session,
    *,
    account_email: str,
    message_id: Optional[str],
) -> bool:
    """Deduplicate per mailbox so one RFC id sent to two inboxes is still handled."""
    if not message_id:
        return False
    return db.query(EmailLog.id).filter(
        EmailLog.direction == "inbound",
        EmailLog.message_id == message_id.strip(),
        func.lower(EmailLog.to_email) == account_email.strip().lower(),
    ).first() is not None


def _latest_outbound_match(
    db: Session,
    *,
    account: EmailAccount,
    recipient_email: str,
    message_ids: Optional[list[str]] = None,
) -> Optional[Lead]:
    query = (
        db.query(Lead)
        .join(EmailLog, EmailLog.lead_id == Lead.id)
        .join(Workflow, Workflow.id == Lead.workflow_id)
        .filter(
            Workflow.user_id == account.user_id,
            EmailLog.direction == "outbound",
            func.lower(EmailLog.from_email) == account.email.strip().lower(),
            func.lower(EmailLog.to_email) == recipient_email.strip().lower(),
        )
    )
    if message_ids:
        query = query.filter(EmailLog.message_id.in_(message_ids))
    return query.order_by(EmailLog.sent_at.desc(), EmailLog.id.desc()).first()


def find_lead_for_inbound(
    db: Session,
    *,
    account: EmailAccount,
    sender_email: str,
    in_reply_to: Optional[str] = None,
    references: Optional[str] = None,
) -> Optional[Lead]:
    """Resolve an inbound message without crossing mailbox, workflow, or user scope."""
    sender = (sender_email or "").strip().lower()
    if not sender:
        return None

    thread_ids = extract_message_ids(in_reply_to, references)
    if thread_ids:
        lead = _latest_outbound_match(
            db,
            account=account,
            recipient_email=sender,
            message_ids=thread_ids,
        )
        if lead:
            return lead

    # Some providers omit reply headers. A real outbound log from this exact
    # mailbox to this sender is the next strongest attribution signal.
    lead = _latest_outbound_match(
        db,
        account=account,
        recipient_email=sender,
    )
    if lead:
        return lead

    # Final fallback: only leads in workflows currently bound to this mailbox.
    return (
        db.query(Lead)
        .join(Workflow, Workflow.id == Lead.workflow_id)
        .join(WorkflowEmail, WorkflowEmail.workflow_id == Workflow.id)
        .filter(
            Workflow.user_id == account.user_id,
            WorkflowEmail.email_account_id == account.id,
            func.lower(Lead.email) == sender,
        )
        .order_by(Lead.updated_at.desc(), Lead.id.desc())
        .first()
    )


def find_lead_for_bounce(
    db: Session,
    *,
    account: EmailAccount,
    recipient_email: str,
) -> Optional[Lead]:
    return _latest_outbound_match(
        db,
        account=account,
        recipient_email=recipient_email,
    )


@dataclass
class ReplyThreadContext:
    account: Optional[EmailAccount]
    in_reply_to: Optional[str]
    references: Optional[str]
    original_sender_missing: bool = False


def reply_thread_context(db: Session, lead: Lead) -> ReplyThreadContext:
    """Select the original sender and latest customer message for a reply."""
    if not lead.workflow_id:
        return ReplyThreadContext(None, None, None)

    last_outbound = (
        db.query(EmailLog)
        .filter(EmailLog.lead_id == lead.id, EmailLog.direction == "outbound")
        .order_by(EmailLog.sent_at.desc(), EmailLog.id.desc())
        .first()
    )
    last_inbound = (
        db.query(EmailLog)
        .filter(EmailLog.lead_id == lead.id, EmailLog.direction == "inbound")
        .filter(func.lower(EmailLog.from_email) == (lead.email or "").strip().lower())
        .order_by(EmailLog.sent_at.desc(), EmailLog.id.desc())
        .first()
    )

    workflow = db.query(Workflow).filter(Workflow.id == lead.workflow_id).first()
    if not workflow:
        return ReplyThreadContext(None, None, None)

    account_query = (
        db.query(EmailAccount)
        .join(WorkflowEmail, WorkflowEmail.email_account_id == EmailAccount.id)
        .filter(
            WorkflowEmail.workflow_id == lead.workflow_id,
            EmailAccount.user_id == workflow.user_id,
        )
    )
    original_sender_missing = False
    account = None
    if last_outbound and last_outbound.from_email:
        account = account_query.filter(
            func.lower(EmailAccount.email) == last_outbound.from_email.strip().lower()
        ).first()
        original_sender_missing = account is None
    else:
        account = account_query.order_by(EmailAccount.id.asc()).first()

    reply_id = last_inbound.message_id if last_inbound and last_inbound.message_id else None
    outbound_id = last_outbound.message_id if last_outbound and last_outbound.message_id else None
    in_reply_to = reply_id or outbound_id
    references_list = extract_message_ids(outbound_id, reply_id)
    references = " ".join(references_list) if references_list else None
    return ReplyThreadContext(
        account=account,
        in_reply_to=in_reply_to,
        references=references,
        original_sender_missing=original_sender_missing,
    )
