import models
from routers.analytics import get_onboarding


def _make_user(db, username="founder"):
    user = models.User(username=username, hashed_password="x", is_active=True)
    db.add(user)
    db.flush()
    return user


def _steps(result):
    return {s["key"]: s for s in result["steps"]}


def test_onboarding_empty_account_has_no_steps_done(db_session):
    user = _make_user(db_session)
    result = get_onboarding(db=db_session, user=user)
    assert result["completed"] == 0
    assert result["total"] == 6
    assert result["all_done"] is False
    assert all(not s["done"] for s in result["steps"])


def test_onboarding_tracks_each_step_independently(db_session):
    user = _make_user(db_session)

    db_session.add(models.CustomerPersona(user_id=user.id, name="Importers"))
    db_session.add(models.EmailAccount(
        user_id=user.id, email="sales@acme.com",
        smtp_host="smtp.acme.com", smtp_port=587,
        smtp_user="sales@acme.com", smtp_pass="x",
    ))
    pool = models.ClientPool(user_id=user.id, name="EU Buyers")
    workflow = models.Workflow(
        user_id=user.id, client_pool=pool, name="Q3 Outreach",
        search_keywords="distributor", target_positions="buyer",
    )
    db_session.add_all([pool, workflow])
    db_session.flush()

    # A sourced lead that has reached the review/drafted stage.
    db_session.add(models.Lead(
        workflow_id=workflow.id, client_pool_id=pool.id,
        domain="buyer.example", email="p@buyer.example", status="drafted",
    ))
    db_session.flush()

    result = get_onboarding(db=db_session, user=user)
    steps = _steps(result)
    assert steps["persona"]["done"] is True
    assert steps["email"]["done"] is True
    assert steps["pool"]["done"] is True
    assert steps["workflow"]["done"] is True
    assert steps["leads"]["done"] is True
    assert steps["review"]["done"] is True
    assert result["all_done"] is True
    assert result["completed"] == 6


def test_onboarding_is_scoped_per_user(db_session):
    owner = _make_user(db_session, "owner")
    other = _make_user(db_session, "stranger")
    db_session.add(models.CustomerPersona(user_id=owner.id, name="Owner persona"))
    db_session.flush()

    # The other user must not inherit the owner's progress.
    result = get_onboarding(db=db_session, user=other)
    assert _steps(result)["persona"]["done"] is False
