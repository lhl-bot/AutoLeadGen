from datetime import datetime, timezone

import models
from services.sender_accounts import select_sender_account


def _seed_workflow_with_accounts(db):
    user = models.User(username="owner", hashed_password="x", is_active=True)
    db.add(user)
    db.flush()
    workflow = models.Workflow(
        user_id=user.id,
        name="Outreach",
        status="active",
        search_keywords="padel distributor",
        target_positions="buyer",
    )
    db.add(workflow)
    db.flush()
    accounts = [
        models.EmailAccount(
            user_id=user.id,
            email="one@example.com",
            smtp_host="smtp.example.com",
            smtp_user="one@example.com",
            smtp_pass="secret",
        ),
        models.EmailAccount(
            user_id=user.id,
            email="two@example.com",
            smtp_host="smtp.example.com",
            smtp_user="two@example.com",
            smtp_pass="secret",
        ),
    ]
    db.add_all(accounts)
    db.flush()
    db.add_all([
        models.WorkflowEmail(workflow_id=workflow.id, email_account_id=accounts[0].id),
        models.WorkflowEmail(workflow_id=workflow.id, email_account_id=accounts[1].id),
    ])
    lead = models.Lead(
        workflow_id=workflow.id,
        domain="example.com",
        company_name="Example",
        email="buyer@example.com",
    )
    db.add(lead)
    db.commit()
    return workflow, accounts, lead


def _email_log(db, lead, from_email, sent_at):
    db.add(models.EmailLog(
        lead_id=lead.id,
        direction="outbound",
        from_email=from_email,
        to_email=lead.email,
        sent_at=sent_at,
    ))
    db.commit()


def test_select_sender_account_uses_first_bound_account_without_history(db_session):
    workflow, accounts, _ = _seed_workflow_with_accounts(db_session)

    selection = select_sender_account(
        db_session,
        workflow,
        per_account_daily_cap=10,
        now=datetime(2026, 6, 5, 12, tzinfo=timezone.utc),
    )

    assert selection.account.email == accounts[0].email
    assert selection.capped_accounts == []


def test_select_sender_account_round_robins_after_last_sender(db_session):
    workflow, accounts, lead = _seed_workflow_with_accounts(db_session)
    _email_log(
        db_session,
        lead,
        accounts[0].email,
        datetime(2026, 6, 5, 9, tzinfo=timezone.utc),
    )

    selection = select_sender_account(
        db_session,
        workflow,
        per_account_daily_cap=10,
        now=datetime(2026, 6, 5, 12, tzinfo=timezone.utc),
    )

    assert selection.account.email == accounts[1].email


def test_select_sender_account_skips_capped_accounts_in_round_robin_order(db_session):
    workflow, accounts, lead = _seed_workflow_with_accounts(db_session)
    now = datetime(2026, 6, 5, 12, tzinfo=timezone.utc)
    _email_log(db_session, lead, accounts[0].email, datetime(2026, 6, 5, 9, tzinfo=timezone.utc))
    _email_log(db_session, lead, accounts[1].email, datetime(2026, 6, 5, 10, tzinfo=timezone.utc))

    selection = select_sender_account(
        db_session,
        workflow,
        per_account_daily_cap=1,
        now=now,
    )

    assert selection.account is None
    assert selection.capped_accounts == [
        (accounts[0].email, 1),
        (accounts[1].email, 1),
    ]


def test_select_sender_account_returns_next_available_after_capped_account(db_session):
    workflow, accounts, lead = _seed_workflow_with_accounts(db_session)
    now = datetime(2026, 6, 5, 12, tzinfo=timezone.utc)
    _email_log(db_session, lead, accounts[0].email, datetime(2026, 6, 5, 8, tzinfo=timezone.utc))
    _email_log(db_session, lead, "external@example.com", datetime(2026, 6, 5, 10, tzinfo=timezone.utc))

    selection = select_sender_account(
        db_session,
        workflow,
        per_account_daily_cap=1,
        now=now,
    )

    assert selection.account.email == accounts[1].email
    assert selection.capped_accounts == [(accounts[0].email, 1)]
