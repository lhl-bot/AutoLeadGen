import asyncio
from pathlib import Path

import bcrypt
import httpx
import pytest

from main import app
from product_v2.production import deployment_configuration_checks, report
from product_v2.webhook_security import webhook_ingress_rejected
from runtime_config import RuntimeConfigurationError, read_secret
from services.auth import hash_password, verify_password
from scripts.bootstrap_production_admin import main as bootstrap_production_admin


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_production_gateway_sets_browser_security_boundaries():
    caddyfile = (PROJECT_ROOT / "deploy" / "Caddyfile").read_text(encoding="utf-8")

    for directive in (
        'Strict-Transport-Security "max-age=31536000; includeSubDomains"',
        'Content-Security-Policy "default-src \'self\'',
        "object-src 'none'",
        "frame-ancestors 'none'",
        "form-action 'self'",
        "connect-src 'self'",
        'Cross-Origin-Opener-Policy "same-origin"',
        'X-Permitted-Cross-Domain-Policies "none"',
    ):
        assert directive in caddyfile
    assert "@backend path /api/* /health /health/*" in caddyfile
    assert "/metrics" not in caddyfile.split("@backend path", 1)[1].splitlines()[0]


def test_secret_file_is_supported_and_ambiguous_injection_fails(tmp_path, monkeypatch):
    secret_file = tmp_path / "jwt"
    secret_file.write_text("file-secret-value\n", encoding="utf-8")
    monkeypatch.delenv("EXAMPLE_SECRET", raising=False)
    monkeypatch.setenv("EXAMPLE_SECRET_FILE", str(secret_file))
    assert read_secret("EXAMPLE_SECRET", required=True) == "file-secret-value"
    monkeypatch.setenv("EXAMPLE_SECRET", "direct-secret")
    try:
        read_secret("EXAMPLE_SECRET", required=True)
    except RuntimeConfigurationError as exc:
        assert "file-secret-value" not in str(exc)
    else:
        raise AssertionError("ambiguous secret injection must fail")


def test_password_hashing_uses_argon2_and_keeps_legacy_bcrypt_login_compatible():
    current = hash_password("correct horse battery staple")
    assert current.startswith("$argon2")
    assert verify_password("correct horse battery staple", current)
    assert not verify_password("wrong", current)

    legacy_bcrypt = bcrypt.hashpw(
        b"legacy-password",
        bcrypt.gensalt(rounds=4),
    ).decode("utf-8")
    assert verify_password("legacy-password", legacy_bcrypt)
    assert not verify_password("wrong", legacy_bcrypt)


def test_production_admin_bootstrap_refuses_direct_password_environment(monkeypatch):
    values = {
        "AUTOLEADGEN_ENV": "production",
        "BOOTSTRAP_ADMIN_APPROVED": "true",
        "OUTBOUND_HARD_PAUSE": "true",
        "ALLOW_REAL_EXTERNAL_CALLS": "false",
        "PRODUCT_V2_LEGACY_WRITERS_FROZEN": "true",
        "PRODUCTION_CHANGE_ID": "CHG-1234",
        "RELEASE_SHA": "a" * 40,
        "BOOTSTRAP_ADMIN_USERNAME": "approved-admin",
        "BOOTSTRAP_ADMIN_PASSWORD": "must-not-be-accepted",
    }
    monkeypatch.delenv("OUTBOUND_HARD_PAUSE_FILE", raising=False)
    monkeypatch.delenv("BOOTSTRAP_ADMIN_PASSWORD_FILE", raising=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(SystemExit, match="secret file"):
        bootstrap_production_admin()


def test_production_webhook_ingress_defaults_to_rejected(monkeypatch):
    monkeypatch.setenv("AUTOLEADGEN_ENV", "production")
    monkeypatch.delenv("PRODUCT_V2_WEBHOOK_REJECT_ALL", raising=False)
    assert webhook_ingress_rejected() is True
    monkeypatch.setenv("PRODUCT_V2_WEBHOOK_REJECT_ALL", "false")
    assert webhook_ingress_rejected() is False


def test_deployment_preflight_passes_only_with_explicit_artifact_and_host_gates(monkeypatch):
    values = {
        "AUTOLEADGEN_ENV": "production",
        "DATABASE_URL": "mysql+pymysql://user:pass@db.example.com/autoleadgen",
        "JWT_SECRET_KEY": "j" * 40,
        "SMTP_ENCRYPTION_KEY": "s" * 40,
        "UNSUBSCRIBE_TOKEN_SECRET": "u" * 40,
        "PRODUCT_V2_WEBHOOK_SECRET": "w" * 40,
        "CORS_ORIGINS": "https://app.example.com",
        "ALLOWED_HOSTS": "app.example.com",
        "RELEASE_SHA": "a" * 40,
        "IMAGE_DIGEST": "sha256:" + "b" * 64,
        "PRODUCTION_CHANGE_ID": "CHG-1234",
        "PRODUCT_V2_BACKUP_RESTORE_EVIDENCE_ID": "restore-test-1234",
        "PRODUCT_V2_MONITORING_EVIDENCE_ID": "alert-route-test-1234",
        "PRODUCT_V2_STAGING_ACCEPTANCE_EVIDENCE_ID": "staging-run-1234",
        "PRODUCT_V2_OWNER_PATH_ENFORCEMENT": "true",
        "ENABLE_BACKGROUND_WORKERS": "false",
        "ALLOW_REAL_ACQUISITION_CALLS": "false",
    }
    for name in ("DATABASE_URL_FILE", "JWT_SECRET_KEY_FILE", "SMTP_ENCRYPTION_KEY_FILE", "UNSUBSCRIBE_TOKEN_SECRET_FILE", "PRODUCT_V2_WEBHOOK_SECRET_FILE"):
        monkeypatch.delenv(name, raising=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    payload = report(deployment_configuration_checks(phase="deploy"), phase="deploy")
    assert payload["status"] == "pass"
    serialized = str(payload)
    assert "user:pass" not in serialized
    assert values["JWT_SECRET_KEY"] not in serialized


def test_enable_real_requires_pause_and_verify_live_requires_release(monkeypatch):
    values = {
        "AUTOLEADGEN_ENV": "production",
        "DATABASE_URL": "mysql+pymysql://user:pass@db.example.com/autoleadgen",
        "JWT_SECRET_KEY": "j" * 40,
        "SMTP_ENCRYPTION_KEY": "s" * 40,
        "UNSUBSCRIBE_TOKEN_SECRET": "u" * 40,
        "PRODUCT_V2_WEBHOOK_SECRET": "w" * 40,
        "CORS_ORIGINS": "https://app.example.com",
        "ALLOWED_HOSTS": "app.example.com",
        "RELEASE_SHA": "a" * 40,
        "IMAGE_DIGEST": "sha256:" + "b" * 64,
        "PRODUCTION_CHANGE_ID": "CHG-1234",
        "PRODUCT_V2_BACKUP_RESTORE_EVIDENCE_ID": "restore-test-1234",
        "PRODUCT_V2_MONITORING_EVIDENCE_ID": "alert-route-test-1234",
        "PRODUCT_V2_STAGING_ACCEPTANCE_EVIDENCE_ID": "staging-run-1234",
        "PRODUCT_V2_EMAIL_DNS_EVIDENCE_ID": "email-dns-test-1234",
        "PRODUCT_V2_COMPLIANCE_APPROVAL_ID": "compliance-approval-1234",
        "PRODUCT_V2_SHADOW_EVIDENCE_ID": "shadow-run-1234",
        "PRODUCT_V2_OWNER_PATH_ENFORCEMENT": "true",
        "ENABLE_BACKGROUND_WORKERS": "false",
        "AUTOLEADGEN_CONNECTOR_MODE": "real",
        "ALLOW_REAL_EXTERNAL_CALLS": "true",
        "ALLOW_REAL_ACQUISITION_CALLS": "false",
        "PRODUCT_V2_LEGACY_READ_ONLY": "true",
        "PRODUCT_V2_LEGACY_WRITERS_FROZEN": "true",
    }
    for name in (
        "DATABASE_URL_FILE",
        "JWT_SECRET_KEY_FILE",
        "SMTP_ENCRYPTION_KEY_FILE",
        "UNSUBSCRIBE_TOKEN_SECRET_FILE",
        "PRODUCT_V2_WEBHOOK_SECRET_FILE",
        "OUTBOUND_HARD_PAUSE_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    monkeypatch.setenv("OUTBOUND_HARD_PAUSE", "true")
    enable = deployment_configuration_checks(phase="enable-real")
    verify_before_release = deployment_configuration_checks(phase="verify-live")
    assert next(item for item in enable if item.name == "outbound_hard_pause").passed
    assert not next(
        item for item in verify_before_release if item.name == "outbound_hard_pause"
    ).passed

    monkeypatch.setenv("OUTBOUND_HARD_PAUSE", "false")
    enable_after_release = deployment_configuration_checks(phase="enable-real")
    verify = deployment_configuration_checks(phase="verify-live")
    assert not next(
        item for item in enable_after_release if item.name == "outbound_hard_pause"
    ).passed
    assert next(item for item in verify if item.name == "outbound_hard_pause").passed

    monkeypatch.setenv("ALLOW_REAL_ACQUISITION_CALLS", "true")
    monkeypatch.setenv("ACQUISITION_OWNER_ALLOWLIST", "1")
    monkeypatch.setenv("ACQUISITION_APPROVAL_ID", "ACQ-APPROVAL-TEST-1234")
    monkeypatch.setenv("ACQUISITION_PRICE_VERSION", "pilot-v1")
    narrowed_scope = deployment_configuration_checks(phase="verify-live")
    assert next(
        item for item in narrowed_scope if item.name == "real_acquisition_governance"
    ).passed


def test_readiness_and_metrics_endpoints_are_operational_in_test_runtime(db_session):
    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get("/health/ready"), await client.get("/metrics")

    readiness, metrics = asyncio.run(run())
    assert readiness.status_code == 200
    assert readiness.json()["status"] == "ready"
    assert metrics.status_code == 200
    assert "autoleadgen_http_requests_total" in metrics.text
    assert 'autoleadgen_message_events_total{event_type="sent"} 0' in metrics.text
    assert 'autoleadgen_message_events_total{event_type="bounced"} 0' in metrics.text
    assert 'autoleadgen_message_events_total{event_type="unsubscribed"} 0' in metrics.text
    assert 'autoleadgen_message_events_total{event_type="complained"} 0' in metrics.text
