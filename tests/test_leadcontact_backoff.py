from datetime import datetime, timedelta, timezone
import time

import models
from services import outbound_engine as oe


class _FakeLC:
    """Minimal LeadContact stand-in; records whether the (billable) search ran."""
    def __init__(self, employees=None, result=None):
        self._employees = employees or []
        self._result = result
        self.search_calls = 0

    def search_employees(self, **kwargs):
        self.search_calls += 1
        if self._result is not None:
            return self._result
        return {"employees": self._employees, "totalEmployeeCount": len(self._employees)}


def _make_workflow(db):
    user = models.User(username="owner", hashed_password="x", is_active=True)
    db.add(user)
    db.flush()
    wf = models.Workflow(
        user_id=user.id, name="WF", status="active",
        search_keywords="padel club", target_positions="Owner",
        target_region="Spain", product_focus="padel",
    )
    db.add(wf)
    db.commit()
    db.refresh(wf)
    return wf


def test_backoff_skips_the_billable_search(db_session):
    wf = _make_workflow(db_session)
    oe._leadcontact_backoff_until[wf.id] = time.time() + 3600
    lc = _FakeLC(employees=[{"fullName": "Would Cost Money"}])
    try:
        stats = oe._leadcontact_search_and_extract(wf.id, lc)
        assert stats["status"] == "backoff"
        assert stats["new_leads"] == 0
        assert lc.search_calls == 0  # never hit the paid API
    finally:
        oe._leadcontact_backoff_until.pop(wf.id, None)


def test_zero_new_leads_sets_backoff(db_session):
    wf = _make_workflow(db_session)
    oe._leadcontact_backoff_until.pop(wf.id, None)
    # Search returns nothing -> empty-result backoff should be armed.
    lc = _FakeLC(employees=[])
    try:
        stats = oe._leadcontact_search_and_extract(wf.id, lc)
        assert stats["new_leads"] == 0
        assert lc.search_calls >= 1
        assert oe._leadcontact_backoff_until.get(wf.id, 0) > time.time()
    finally:
        oe._leadcontact_backoff_until.pop(wf.id, None)


def test_leadcontact_daily_search_call_budget_stops_relaxation_ladder(db_session, monkeypatch):
    wf = _make_workflow(db_session)
    monkeypatch.setenv("LEADCONTACT_MAX_SEARCH_CALLS_PER_DAY", "1")
    lc = _FakeLC(employees=[])

    stats = oe._leadcontact_search_and_extract(wf.id, lc)

    assert stats["status"] == "budget_reached"
    assert lc.search_calls == 1


def test_leadcontact_search_dedupes_results_and_stores_next_page_cursor(db_session):
    wf = _make_workflow(db_session)
    employee = {
        "fullName": "A Buyer",
        "title": "Purchasing Manager",
        "companyName": "Acme Textiles",
        "email": "buyer@acme.example",
        "linkedinUrl": "https://linkedin.example/in/a-buyer",
    }
    lc = _FakeLC(result={
        "employees": [employee, dict(employee)],
        "totalEmployeeCount": 2,
        "nextPageToken": "page-2",
    })

    stats = oe._leadcontact_search_and_extract(wf.id, lc, batch_lead_limit=5)

    assert stats["new_leads"] == 1
    assert stats["next_page"] is True
    assert lc.search_calls == 1
    assert db_session.query(models.Lead).filter(models.Lead.workflow_id == wf.id).count() == 1
    assert oe._leadcontact_cursor[wf.id]["token"] == "page-2"


def test_search_entry_stops_when_total_lead_cap_is_reached(db_session, monkeypatch):
    wf = _make_workflow(db_session)
    monkeypatch.setenv("SEARCH_WORKFLOW_TOTAL_TARGET", "3")
    monkeypatch.setattr(
        oe,
        "search_domain_results",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("external search should not run")),
    )
    for i in range(3):
        db_session.add(models.Lead(
            workflow_id=wf.id,
            company_name=f"Company {i}",
            email=f"buyer{i}@example.com",
            status="sent",
            source_channel="leadcontact",
        ))
    db_session.commit()

    stats = oe.search_and_extract_leads(wf.id)

    assert stats["status"] == "search_capacity_reached"
    assert "total lead cap" in stats["reason"]


def test_search_entry_stops_when_bad_lead_ratio_is_too_high(db_session, monkeypatch):
    wf = _make_workflow(db_session)
    monkeypatch.setenv("SEARCH_STOP_BAD_RATIO_MIN_LEADS", "10")
    monkeypatch.setenv("SEARCH_STOP_BAD_RATIO", "0.80")
    monkeypatch.setenv("SEARCH_WORKFLOW_TOTAL_TARGET", "1000")
    monkeypatch.setattr(
        oe,
        "search_domain_results",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("external search should not run")),
    )
    for i in range(9):
        db_session.add(models.Lead(
            workflow_id=wf.id,
            company_name=f"No Email {i}",
            status="needs_email",
            source_channel="leadcontact",
        ))
    db_session.add(models.Lead(
        workflow_id=wf.id,
        company_name="Good Company",
        email="buyer@example.com",
        status="sent",
        source_channel="leadcontact",
    ))
    db_session.commit()

    stats = oe.search_and_extract_leads(wf.id)

    assert stats["status"] == "search_capacity_reached"
    assert "bad lead ratio" in stats["reason"]


def test_leadcontact_daily_search_contact_budget_blocks_paid_search(db_session, monkeypatch):
    wf = _make_workflow(db_session)
    monkeypatch.setenv("LEADCONTACT_MAX_SEARCH_CONTACTS_PER_DAY", "1")
    db_session.add(models.Lead(
        workflow_id=wf.id,
        company_name="Already Used",
        status="needs_email",
        source_channel="leadcontact",
    ))
    db_session.commit()
    lc = _FakeLC(employees=[{"fullName": "Would Cost Money"}])

    stats = oe._leadcontact_search_and_extract(wf.id, lc)

    assert stats["status"] == "budget_reached"
    assert lc.search_calls == 0


def test_web_search_stops_when_snovio_is_unavailable_and_company_only_disabled(db_session, monkeypatch):
    wf = _make_workflow(db_session)
    monkeypatch.delenv("APOLLO_API_KEY", raising=False)
    monkeypatch.delenv("LEADCONTACT_API_KEY", raising=False)
    monkeypatch.setenv("SEARCH_SAVE_COMPANIES_WITHOUT_EMAIL", "false")

    class _NoSnov:
        def _authenticate(self):
            return False

    monkeypatch.setattr(oe, "get_snovio_client", lambda: _NoSnov())
    monkeypatch.setattr(
        oe,
        "search_domain_results",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("domain search should not run")),
    )

    stats = oe.search_and_extract_leads(wf.id)

    assert stats["status"] == "snovio_unavailable"
    assert "Snov.io" in stats["error"]


def test_cold_followup_candidates_prioritize_verified_high_fit_leads(db_session):
    wf = _make_workflow(db_session)
    now = datetime.now(timezone.utc)

    def add_lead(email, *, score, validation_status="valid", sent_hours_ago=72):
        lead = models.Lead(
            workflow_id=wf.id,
            company_name=email.split("@")[1],
            email=email,
            status="sent",
            followup_count=0,
            fit_score=score,
            email_validation_status=validation_status,
        )
        db_session.add(lead)
        db_session.flush()
        db_session.add(models.EmailLog(
            lead_id=lead.id,
            direction="outbound",
            from_email="sender@example.com",
            to_email=email,
            subject="Intro",
            body="Hello",
            sent_at=now - timedelta(hours=sent_hours_ago),
        ))
        return lead

    unknown = add_lead("unknown@example.com", score=100, validation_status="unknown")
    replied = add_lead("replied@example.com", score=100)
    replied.has_replied = True
    lower_fit = add_lead("lower@example.com", score=70)
    high_fit_newer = add_lead("high@example.com", score=96, sent_hours_ago=24)
    best = add_lead("best@example.com", score=98)
    db_session.commit()

    candidates = oe._cold_followup_candidates(db_session, wf.id, max_followups=3, limit=3)

    assert unknown not in candidates
    assert replied not in candidates
    assert [lead.id for lead in candidates] == [best.id, high_fit_newer.id, lower_fit.id]
