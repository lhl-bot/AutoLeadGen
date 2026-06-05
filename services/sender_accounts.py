from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from models import EmailAccount, EmailLog, Lead, Workflow, WorkflowEmail


@dataclass
class SenderAccountSelection:
    account: Optional[EmailAccount]
    capped_accounts: list[tuple[str, int]]
    daily_cap: int


def select_sender_account(
    db: Session,
    workflow: Workflow,
    *,
    per_account_daily_cap: int,
    now: Optional[datetime] = None,
) -> SenderAccountSelection:
    """Select a sender account using workflow ownership, round-robin, and daily caps."""
    workflow_emails = (
        db.query(WorkflowEmail)
        .join(WorkflowEmail.email_account)
        .filter(
            WorkflowEmail.workflow_id == workflow.id,
            EmailAccount.user_id == workflow.user_id,
        )
        .all()
    )
    accounts = [we.email_account for we in workflow_emails if we.email_account]
    if not accounts:
        return SenderAccountSelection(account=None, capped_accounts=[], daily_cap=per_account_daily_cap)

    last_log = (
        db.query(EmailLog)
        .join(Lead)
        .filter(
            Lead.workflow_id == workflow.id,
            EmailLog.direction == "outbound",
        )
        .order_by(EmailLog.sent_at.desc())
        .first()
    )

    start_index = 0
    if last_log:
        for i, account in enumerate(accounts):
            if account.email == last_log.from_email:
                start_index = (i + 1) % len(accounts)
                break

    current_time = now or datetime.now(timezone.utc)
    today = current_time.replace(hour=0, minute=0, second=0, microsecond=0)
    capped_accounts: list[tuple[str, int]] = []

    for offset in range(len(accounts)):
        idx = (start_index + offset) % len(accounts)
        candidate = accounts[idx]
        sent_by_account_today = (
            db.query(EmailLog)
            .filter(
                EmailLog.direction == "outbound",
                EmailLog.from_email == candidate.email,
                EmailLog.sent_at >= today,
            )
            .count()
        )
        if sent_by_account_today < per_account_daily_cap:
            return SenderAccountSelection(
                account=candidate,
                capped_accounts=capped_accounts,
                daily_cap=per_account_daily_cap,
            )
        capped_accounts.append((candidate.email, sent_by_account_today))

    return SenderAccountSelection(
        account=None,
        capped_accounts=capped_accounts,
        daily_cap=per_account_daily_cap,
    )
