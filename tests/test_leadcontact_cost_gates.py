import itertools

import models
from services import outbound_engine as oe

_uname = itertools.count()


def _lead(db, *, linkedin=None, email=None):
    user = models.User(username=f"o{next(_uname)}", hashed_password="x", is_active=True)
    db.add(user); db.flush()
    wf = models.Workflow(user_id=user.id, name="WF", status="active",
                         search_keywords="padel", target_positions="Owner")
    db.add(wf); db.flush()
    lead = models.Lead(workflow_id=wf.id, domain="x.com", company_name="X",
                       email=email, linkedin_url=linkedin, status="found")
    db.add(lead); db.commit(); db.refresh(lead)
    return lead


# ---- #2 email reuse ----
def test_find_existing_email_reuses_same_person(db_session):
    _lead(db_session, linkedin="https://lnkd.in/x", email="a@x.com")
    target = _lead(db_session, linkedin="https://lnkd.in/x", email=None)
    found = oe._find_existing_email("https://lnkd.in/x", exclude_lead_id=target.id)
    assert found == "a@x.com"


def test_find_existing_email_none_when_no_match(db_session):
    assert oe._find_existing_email("https://lnkd.in/none") is None
    assert oe._find_existing_email(None) is None


# ---- #5 daily budget ----
def test_email_budget_blocks_after_cap(db_session, monkeypatch):
    monkeypatch.setenv("LEADCONTACT_MAX_EMAIL_LOOKUPS_PER_DAY", "2")
    wf_id = 99999
    oe._lc_daily_lookups.clear()
    try:
        assert oe._lc_budget_check(wf_id, "email") is True
        oe._lc_budget_record(wf_id, "email")
        oe._lc_budget_record(wf_id, "email")
        assert oe._lc_budget_check(wf_id, "email") is False  # cap reached
    finally:
        oe._lc_daily_lookups.clear()


def test_zero_cap_means_unlimited(db_session, monkeypatch):
    monkeypatch.setenv("LEADCONTACT_MAX_PHONE_LOOKUPS_PER_DAY", "0")
    oe._lc_daily_lookups.clear()
    try:
        for _ in range(50):
            oe._lc_budget_record(88888, "phone")
        assert oe._lc_budget_check(88888, "phone") is True
    finally:
        oe._lc_daily_lookups.clear()
