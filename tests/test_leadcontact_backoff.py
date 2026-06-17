import time

import models
from services import outbound_engine as oe


class _FakeLC:
    """Minimal LeadContact stand-in; records whether the (billable) search ran."""
    def __init__(self, employees=None):
        self._employees = employees or []
        self.search_calls = 0

    def search_employees(self, **kwargs):
        self.search_calls += 1
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
