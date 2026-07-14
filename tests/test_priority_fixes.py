import models
from routers.auth import delete_user, toggle_user_active
from routers.channels import (
    _find_owned_lead_for_message,
    _owned_unipile_account_name,
    _sync_unipile_accounts,
)
from routers.workflows import read_workflow
from services import auth as auth_service


def _user(db, username: str, *, is_admin: bool = False):
    user = models.User(
        username=username,
        hashed_password="x",
        is_active=True,
        is_admin=is_admin,
    )
    db.add(user)
    db.flush()
    return user


def _workflow(db, user, name: str):
    workflow = models.Workflow(
        user_id=user.id,
        name=name,
        search_keywords="distributor",
        target_positions="buyer",
    )
    db.add(workflow)
    db.flush()
    return workflow


def test_read_workflow_loads_bound_email_accounts(db_session):
    owner = _user(db_session, "owner")
    workflow = _workflow(db_session, owner, "Outreach")
    email_account = models.EmailAccount(
        user_id=owner.id,
        email="owner@example.com",
        smtp_host="smtp.example.com",
        smtp_user="owner@example.com",
        smtp_pass="secret",
    )
    db_session.add(email_account)
    db_session.flush()
    db_session.add(models.WorkflowEmail(
        workflow_id=workflow.id,
        email_account_id=email_account.id,
    ))
    db_session.commit()

    result = read_workflow(workflow.id, db_session, owner)

    assert result.id == workflow.id
    assert result.emails[0].email == "owner@example.com"
    assert not hasattr(result.emails[0], "smtp_pass")


def test_channel_sync_does_not_modify_or_claim_another_users_accounts(db_session):
    owner = _user(db_session, "owner")
    other = _user(db_session, "other")
    db_session.add_all([
        models.ChannelAccount(
            user_id=owner.id,
            account_type="LINKEDIN",
            unipile_account_id="owner-existing",
            name="Owner LinkedIn",
            status="OK",
        ),
        models.ChannelAccount(
            user_id=other.id,
            account_type="LINKEDIN",
            unipile_account_id="other-existing",
            name="Other LinkedIn",
            status="OK",
        ),
    ])
    db_session.commit()

    _sync_unipile_accounts(db_session, owner, [
        {
            "id": "owner-new",
            "provider": "LINKEDIN",
            "status": "OK",
            "name": _owned_unipile_account_name("New Owner LinkedIn", owner.id),
        },
        {
            "id": "other-existing",
            "provider": "LINKEDIN",
            "status": "CREDENTIALS",
            "name": "Other Changed",
        },
        {
            "id": "unowned-remote",
            "provider": "WHATSAPP",
            "status": "OK",
            "name": "Unowned WhatsApp",
        },
    ])

    owner_existing = db_session.query(models.ChannelAccount).filter_by(
        unipile_account_id="owner-existing"
    ).one()
    other_existing = db_session.query(models.ChannelAccount).filter_by(
        unipile_account_id="other-existing"
    ).one()
    owner_new = db_session.query(models.ChannelAccount).filter_by(
        unipile_account_id="owner-new"
    ).one()

    assert owner_existing.status == "DISCONNECTED"
    assert other_existing.status == "OK"
    assert other_existing.name == "Other LinkedIn"
    assert owner_new.user_id == owner.id
    assert owner_new.name == "New Owner LinkedIn"
    assert db_session.query(models.ChannelAccount).filter_by(
        unipile_account_id="unowned-remote"
    ).first() is None


def test_incoming_message_matches_only_lead_owned_by_channel_account(db_session):
    owner = _user(db_session, "owner")
    other = _user(db_session, "other")
    owner_workflow = _workflow(db_session, owner, "Owner workflow")
    other_workflow = _workflow(db_session, other, "Other workflow")
    db_session.add(models.ChannelAccount(
        user_id=owner.id,
        account_type="LINKEDIN",
        unipile_account_id="owner-linkedin",
        name="Owner LinkedIn",
        status="OK",
    ))
    other_lead = models.Lead(
        workflow_id=other_workflow.id,
        domain="other.example",
        linkedin_url="https://linkedin.com/in/shared-sender",
    )
    owner_lead = models.Lead(
        workflow_id=owner_workflow.id,
        domain="owner.example",
        linkedin_url="https://linkedin.com/in/shared-sender",
    )
    db_session.add_all([other_lead, owner_lead])
    db_session.commit()

    result = _find_owned_lead_for_message(
        db_session,
        channel_account_id="owner-linkedin",
        sender_id="shared-sender",
    )

    assert result.id == owner_lead.id
    assert _find_owned_lead_for_message(
        db_session,
        channel_account_id=None,
        sender_id="shared-sender",
    ) is None


def test_disabling_and_deleting_users_invalidates_auth_cache(db_session):
    admin = _user(db_session, "admin", is_admin=True)
    disabled_user = _user(db_session, "disabled")
    deleted_user = _user(db_session, "deleted")
    db_session.commit()
    auth_service._auth_user_cache[disabled_user.id] = (float("inf"), {"cached": True})
    auth_service._auth_user_cache[deleted_user.id] = (float("inf"), {"cached": True})

    toggle_user_active(disabled_user.id, db_session, admin)
    delete_user(deleted_user.id, db_session, admin)

    assert disabled_user.id not in auth_service._auth_user_cache
    assert deleted_user.id not in auth_service._auth_user_cache
