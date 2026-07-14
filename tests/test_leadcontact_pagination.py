import models
from services import outbound_engine as oe


class _PagingLC:
    """Records the kwargs of each call and returns a scripted page + nextPageToken."""
    def __init__(self, pages):
        self.pages = pages  # list of (employees, next_token)
        self.calls = []

    def search_employees(self, **kwargs):
        idx = len(self.calls)
        self.calls.append(kwargs)
        emps, tok = self.pages[min(idx, len(self.pages) - 1)]
        return {"employees": emps, "nextPageToken": tok, "totalEmployeeCount": 999}


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


def _emp(tag):
    return {"fullName": f"{tag} Person", "title": "CEO", "companyName": f"Co{tag}",
            "email": f"{tag}@co.com", "linkedinUrl": f"https://lnkd.in/{tag}"}


def test_first_run_stores_returned_cursor(db_session):
    wf = _make_workflow(db_session)
    oe._leadcontact_cursor.pop(wf.id, None)
    oe._leadcontact_backoff_until.pop(wf.id, None)
    lc = _PagingLC(pages=[([_emp("a")], "TOK123")])
    try:
        stats = oe._leadcontact_search_and_extract(wf.id, lc, batch_lead_limit=3)
        assert stats["new_leads"] == 1
        assert lc.calls[0].get("next_page_token") is None  # first page: no token
        cur = oe._leadcontact_cursor.get(wf.id)
        assert cur is not None and cur["token"] == "TOK123"
    finally:
        oe._leadcontact_cursor.pop(wf.id, None)
        oe._leadcontact_backoff_until.pop(wf.id, None)


def test_next_run_sends_stored_token_and_clears_when_exhausted(db_session):
    wf = _make_workflow(db_session)
    oe._leadcontact_backoff_until.pop(wf.id, None)
    # Seed cursor as if page 1 was already fetched for the winning (specific) tier.
    plan = oe._build_leadcontact_query_plan("padel club", "Owner", "Spain", "padel")
    oe._leadcontact_cursor[wf.id] = {"query": plan[0], "token": "PREV_TOK"}
    lc = _PagingLC(pages=[([_emp("b")], "")])  # empty token => last page
    try:
        oe._leadcontact_search_and_extract(wf.id, lc, batch_lead_limit=3)
        # The stored token was passed to continue pagination (not re-fetch page 1).
        assert lc.calls[0].get("next_page_token") == "PREV_TOK"
        # Exhausted (empty nextPageToken) => cursor cleared.
        assert wf.id not in oe._leadcontact_cursor
    finally:
        oe._leadcontact_cursor.pop(wf.id, None)
        oe._leadcontact_backoff_until.pop(wf.id, None)
