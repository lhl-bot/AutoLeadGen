import pytest
from fastapi import HTTPException

import models
from routers.workflow_health import workflow_health


def _seed(db, username="rep"):
    user = models.User(username=username, hashed_password="x", is_active=True)
    db.add(user)
    db.flush()
    pool = models.ClientPool(user_id=user.id, name="P")
    db.add(pool)
    db.flush()
    wf = models.Workflow(user_id=user.id, client_pool=pool, name="W",
                         search_keywords="bedding", target_positions="Buyer")
    db.add(wf)
    db.flush()
    return user, wf


def test_health_funnel_and_stuck(db_session):
    user, wf = _seed(db_session)
    statuses = ["found", "found", "drafted", "sent", "needs_email", "needs_email", "low_score"]
    for i, st in enumerate(statuses):
        db_session.add(models.Lead(
            workflow_id=wf.id, domain=f"d{i}.example",
            email=(f"a{i}@d{i}.example" if st in ("drafted", "sent") else None),
            status=st, source_channel="leadcontact",
        ))
    db_session.commit()

    h = workflow_health(wf.id, db=db_session, user=user)
    assert h["workflow"]["id"] == wf.id
    assert h["totals"]["total_leads"] == 7
    assert h["totals"]["with_email"] == 2
    assert h["funnel"]["found"] == 2
    assert h["funnel"]["drafted"] == 1
    assert h["funnel"]["sent"] == 1
    # stuck buckets, sorted by count desc
    stuck = {s["status"]: s["count"] for s in h["stuck"]}
    assert stuck["needs_email"] == 2
    assert stuck["low_score"] == 1
    assert h["stuck"][0]["count"] >= h["stuck"][-1]["count"]
    assert h["by_source"]["leadcontact"] == 7
    assert h["automation"]["search_state"] in {"running", "paused", "inactive", "backoff"}
    assert h["quality"]["research_valid"] == 0
    assert h["delivery"]["initial_sent"] == 0
    assert h["providers"]["snovio_required"] is False


def test_health_providers_and_warnings(db_session, monkeypatch):
    monkeypatch.setenv("LEADCONTACT_API_KEY", "k")
    monkeypatch.delenv("SNOVIO_CLIENT_ID", raising=False)
    monkeypatch.setenv("OUTBOUND_AUTO_SEND_DRAFTS", "false")
    user, wf = _seed(db_session)
    db_session.add(models.Lead(workflow_id=wf.id, domain="x.example", status="drafted", email="a@x.example"))
    db_session.commit()

    h = workflow_health(wf.id, db=db_session, user=user)
    assert h["providers"]["leadcontact"] == "configured"
    assert h["providers"]["snovio"] == "disabled"
    assert h["providers"]["sender_accounts"] == 0
    # warnings should flag no sender + review-mode draft
    joined = " ".join(h["warnings"])
    assert "发信邮箱" in joined
    assert "auto_send" in joined or "审核" in joined


def test_health_rejects_unowned(db_session):
    user, wf = _seed(db_session)
    other = models.User(username="stranger", hashed_password="x", is_active=True)
    db_session.add(other)
    db_session.commit()
    with pytest.raises(HTTPException) as exc:
        workflow_health(wf.id, db=db_session, user=other)
    assert exc.value.status_code == 404
