import models
from services.snovio_usage import record_snovio_usage


def test_records_event_with_fields(db_session, monkeypatch):
    monkeypatch.setenv("SNOVIO_USAGE_AUDIT_ENABLED", "true")
    record_snovio_usage(
        endpoint="domain-search", domain="acme.com", email="a@acme.com",
        status="ok", result_count=5, estimated_credits=2, metadata={"batch": 1},
    )
    row = db_session.query(models.SnovioUsageEvent).one()
    assert row.endpoint == "domain-search"
    assert row.domain == "acme.com"
    assert row.email == "a@acme.com"
    assert row.status == "ok"
    assert row.result_count == 5
    assert row.estimated_credits == 2
    assert row.metadata_json == {"batch": 1}


def test_audit_disabled_records_nothing(db_session, monkeypatch):
    monkeypatch.setenv("SNOVIO_USAGE_AUDIT_ENABLED", "false")
    record_snovio_usage(endpoint="domain-search", domain="acme.com")
    assert db_session.query(models.SnovioUsageEvent).count() == 0


def test_truncates_endpoint_and_clamps_count(db_session, monkeypatch):
    monkeypatch.setenv("SNOVIO_USAGE_AUDIT_ENABLED", "true")
    record_snovio_usage(endpoint="e" * 200, result_count=-9)
    row = db_session.query(models.SnovioUsageEvent).one()
    assert len(row.endpoint) == 120
    assert row.result_count == 0


def test_blank_optionals_stored_as_null(db_session, monkeypatch):
    monkeypatch.setenv("SNOVIO_USAGE_AUDIT_ENABLED", "true")
    record_snovio_usage(endpoint="e", domain="", email="")
    row = db_session.query(models.SnovioUsageEvent).one()
    assert row.domain is None
    assert row.email is None
