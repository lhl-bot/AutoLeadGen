import random

import pytest

import models, schemas
from services import email_templates as et
from routers.email_templates import list_templates, create_template, delete_template, preview_template


# ---- rendering ----

def test_render_substitutes_known_and_blanks_unknown():
    variables = et.lead_variables({"first_name": "Alex", "company_name": "Acme"})
    out = et.render("Hi {{first_name}} at {{company_name}} — {{mystery}}!", variables)
    assert out == "Hi Alex at Acme — !"


def test_full_name_and_domain_fallback():
    v = et.lead_variables({"first_name": "Alex", "last_name": "Chen", "domain": "acme.com"})
    assert v["full_name"] == "Alex Chen"
    assert v["domain"] == "acme.com"


def test_find_missing_variables():
    assert et.find_missing_variables("Hi {{first_name}} {{foo}} {{bar}}") == ["bar", "foo"]


# ---- A/B variant selection ----

def _tpl(label, weight=1, active=True):
    t = models.EmailTemplate(name=f"v{label}", body="b", variant_label=label, weight=weight, is_active=active)
    return t


def test_pick_variant_respects_weight_distribution():
    a, b = _tpl("A", weight=1), _tpl("B", weight=9)
    rng = random.Random(42)
    counts = {"A": 0, "B": 0}
    for _ in range(1000):
        counts[et.pick_variant([a, b], rng=rng).variant_label] += 1
    # B should dominate roughly 9:1; assert it's clearly the majority.
    assert counts["B"] > counts["A"] * 3


def test_pick_variant_skips_inactive():
    a = _tpl("A", active=False)
    b = _tpl("B", active=True)
    for _ in range(20):
        assert et.pick_variant([a, b]).variant_label == "B"


def test_pick_variant_empty():
    assert et.pick_variant([]) is None


# ---- CRUD + stats via router ----

def _user(db, name="marketer"):
    u = models.User(username=name, hashed_password="x", is_active=True)
    db.add(u)
    db.flush()
    return u


def test_create_and_reply_rate_stats(db_session):
    user = _user(db_session)
    tpl = create_template(
        schemas.EmailTemplateCreate(name="Opener", body="Hi {{first_name}}", ab_group="g1", variant_label="A"),
        db=db_session, user=user,
    )
    pool = models.ClientPool(user_id=user.id, name="P")
    db_session.add(pool)
    db_session.flush()
    # 3 sent on this template, 1 of which replied.
    for status in ("sent", "sent", "replied"):
        db_session.add(models.Lead(
            client_pool_id=pool.id, domain="x.example", email="a@x.example",
            status=status, template_id=tpl.id, template_variant="A",
        ))
    db_session.flush()

    stats = list_templates(db=db_session, user=user)
    row = next(s for s in stats if s.id == tpl.id)
    assert row.sent_count == 3
    assert row.replied_count == 1
    assert row.reply_rate == round(1 / 3, 4)


def test_delete_template_detaches_workflow(db_session):
    user = _user(db_session)
    tpl = create_template(
        schemas.EmailTemplateCreate(name="T", body="hello"), db=db_session, user=user,
    )
    pool = models.ClientPool(user_id=user.id, name="P")
    wf = models.Workflow(user_id=user.id, client_pool=pool, name="W",
                         search_keywords="k", target_positions="b", template_id=tpl.id)
    db_session.add_all([pool, wf])
    db_session.flush()

    delete_template(tpl.id, db=db_session, user=user)
    db_session.refresh(wf)
    assert wf.template_id is None


def test_blank_body_rejected():
    with pytest.raises(Exception):
        schemas.EmailTemplateCreate(name="T", body="   ")


def test_preview_flags_unknown_variables():
    user = models.User(username="u", hashed_password="x")
    result = preview_template(
        schemas.TemplatePreviewRequest(subject="Hi {{first_name}}", body="From {{company_name}} {{oops}}"),
        user=user,
    )
    assert result["subject"] == "Hi Alex"
    assert "oops" in result["unknown_variables"]
