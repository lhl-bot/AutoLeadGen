#!/usr/bin/env python3
"""Validate a legacy v16 schema before explicitly stamping its baseline.

This command intentionally does not import ``database`` or application models. Its
target comes only from ``LEGACY_V16_DATABASE_URL`` so a repository ``.env`` cannot
silently redirect validation to the application's configured database.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine, URL, make_url
from sqlalchemy.pool import NullPool


REVISION = "0001_legacy_v16_baseline"
TARGET_ENV = "LEGACY_V16_DATABASE_URL"
SAFE_ENVIRONMENTS = {"local", "test"}
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

# Frozen validator contract for the legacy v16 baseline. This is deliberately kept
# independent from Base.metadata so later model edits cannot silently change what an
# existing database must satisfy before it is stamped.
EXPECTED_SCHEMA: dict[str, tuple[str, ...]] = {
    "channel_accounts": (
        "id", "user_id", "account_type", "unipile_account_id", "name", "status",
        "created_at", "updated_at",
    ),
    "chat_messages": ("id", "session_id", "role", "content", "created_at"),
    "chat_sessions": ("id", "user_id", "title", "created_at", "updated_at"),
    "client_pools": (
        "id", "user_id", "name", "description", "excluded_domains", "created_at",
    ),
    "credit_transactions": (
        "id", "wallet_id", "user_id", "amount", "balance_after", "transaction_type",
        "action", "description", "reference_type", "reference_id", "metadata_json",
        "created_by_user_id", "created_at",
    ),
    "credit_wallets": (
        "id", "user_id", "balance", "lifetime_granted", "lifetime_used", "created_at",
        "updated_at",
    ),
    "crm_webhooks": (
        "id", "user_id", "name", "url", "secret", "events", "is_active",
        "last_status", "last_error", "last_delivered_at", "created_at",
    ),
    "customer_personas": (
        "id", "user_id", "name", "target_industry", "target_countries",
        "target_keywords", "negative_keywords", "target_roles", "ai_prompt_template",
        "customer_types", "product_categories", "company_size", "evidence_sources",
        "qualification_rules", "disqualification_rules", "cultural_notes",
        "positive_examples", "negative_examples", "created_at",
    ),
    "email_accounts": (
        "id", "user_id", "email", "display_name", "smtp_host", "smtp_port",
        "smtp_user", "smtp_pass", "use_tls", "use_ssl", "imap_host", "imap_port",
        "created_at",
    ),
    "email_logs": (
        "id", "lead_id", "direction", "from_email", "to_email", "subject", "body",
        "sent_at", "message_id",
    ),
    "email_suppressions": (
        "id", "user_id", "lead_id", "email", "domain", "reason", "source", "created_at",
    ),
    "email_templates": (
        "id", "user_id", "name", "category", "ab_group", "variant_label", "subject",
        "body", "weight", "is_active", "created_at", "updated_at",
    ),
    "lead_briefs": (
        "id", "lead_id", "company_overview", "recent_news", "pain_points",
        "value_proposition_alignment", "specific_products", "recent_activity",
        "personalization_hook", "research_status", "quality_flags", "evidence_sources",
        "researched_at", "created_at", "updated_at",
    ),
    "lead_feedbacks": (
        "id", "user_id", "lead_id", "workflow_id", "rating", "reason", "lead_snapshot",
        "created_at",
    ),
    "leads": (
        "id", "workflow_id", "client_pool_id", "domain", "company_name", "email",
        "first_name", "last_name", "job_title", "linkedin_url", "status", "ai_draft",
        "send_fail_count", "followup_count", "last_reply_at", "reply_snippet",
        "automation_block_reason", "automation_blocked_at", "has_replied", "reply_intent",
        "user_rating", "email_verified", "email_validation_status", "timezone", "fit_score",
        "fit_grade", "qualification_notes", "handoff_recommended", "source_channel",
        "data_sources", "whatsapp_number", "linkedin_status", "linkedin_sent",
        "whatsapp_sent", "template_id", "template_variant", "sales_stage", "created_at",
        "updated_at",
    ),
    "message_logs": (
        "id", "lead_id", "channel", "direction", "content", "status", "sent_at",
    ),
    "notifications": (
        "id", "user_id", "type", "title", "body", "link", "reference_type",
        "reference_id", "is_read", "created_at",
    ),
    "processed_domains": ("id", "workflow_id", "domain", "created_at"),
    "provider_usage_events": (
        "id", "provider", "operation", "workflow_id", "lead_id", "status", "units",
        "estimated_credits", "result_count", "metadata_json", "created_at",
    ),
    "snovio_usage_events": (
        "id", "endpoint", "domain", "email", "status", "result_count",
        "estimated_credits", "metadata_json", "created_at",
    ),
    "users": (
        "id", "username", "hashed_password", "display_name", "is_admin", "is_active",
        "created_at",
    ),
    "workflow_emails": ("id", "workflow_id", "email_account_id"),
    "workflows": (
        "id", "user_id", "name", "status", "search_keywords", "target_positions",
        "ai_prompt", "email_signature", "client_pool_id", "persona_id", "daily_limit",
        "send_interval_min", "send_interval_max", "auto_followup", "max_followups",
        "followup_steps", "email_sending_paused", "email_pause_reason", "template_id",
        "search_offset", "playbook_type", "domain_warmup_enabled", "pilot_goal",
        "target_customer_type", "target_region", "product_focus", "manual_handoff_triggers",
        "search_sources", "competitor_names", "trade_show_names", "enable_linkedin",
        "enable_whatsapp", "linkedin_invite_message", "whatsapp_message_template",
        "linkedin_daily_limit", "created_at",
    ),
}


class ValidationError(RuntimeError):
    """A safe, user-facing configuration or schema-validation failure."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _redacted_target(url: URL) -> str:
    return url.render_as_string(hide_password=True)


def _target_url() -> URL:
    raw_url = os.environ.get(TARGET_ENV, "").strip()
    if not raw_url:
        raise ValidationError(
            "TARGET_REQUIRED",
            f"Set {TARGET_ENV} explicitly; the application DATABASE_URL is never used.",
        )
    try:
        return make_url(raw_url)
    except Exception as exc:  # pragma: no cover - SQLAlchemy owns URL parsing details
        raise ValidationError("INVALID_TARGET", f"{TARGET_ENV} is not a valid SQLAlchemy URL.") from exc


def _sqlite_path(url: URL) -> Path | None:
    if url.get_backend_name() != "sqlite" or url.database in {None, "", ":memory:"}:
        return None
    return Path(url.database).expanduser().resolve()


def _assert_existing_sqlite(url: URL) -> None:
    path = _sqlite_path(url)
    if path is None:
        if url.get_backend_name() == "sqlite":
            raise ValidationError(
                "PERSISTENT_SQLITE_REQUIRED",
                "Use an existing file-backed SQLite database for legacy validation.",
            )
        return
    if not path.is_file():
        raise ValidationError(
            "SQLITE_NOT_FOUND",
            "The SQLite target must already exist; validation will not create it.",
        )


def _assert_stamp_allowed(url: URL) -> None:
    environment = os.environ.get("AUTOLEADGEN_ENV", "").strip().lower()
    isolated = os.environ.get("PRODUCT_V2_ISOLATED_DATABASE", "").strip().lower() == "true"
    if environment not in SAFE_ENVIRONMENTS:
        raise ValidationError(
            "STAMP_ENVIRONMENT_BLOCKED",
            "--stamp is allowed only when AUTOLEADGEN_ENV is local or test.",
        )
    if not isolated:
        raise ValidationError(
            "STAMP_ISOLATION_REQUIRED",
            "--stamp requires PRODUCT_V2_ISOLATED_DATABASE=true.",
        )

    backend = url.get_backend_name()
    if backend == "sqlite":
        return
    if backend != "mysql":
        raise ValidationError(
            "STAMP_TARGET_BLOCKED",
            "--stamp supports only SQLite or loopback MySQL autoleadgen_v2* databases.",
        )
    database = (url.database or "").lower()
    host = (url.host or "").lower()
    if host not in LOOPBACK_HOSTS or not database.startswith("autoleadgen_v2"):
        raise ValidationError(
            "STAMP_TARGET_BLOCKED",
            "MySQL stamping requires a loopback host and an autoleadgen_v2* database name.",
        )


def _validation_engine(url: URL) -> Engine:
    path = _sqlite_path(url)
    if path is not None:
        # SQLite is opened in OS-enforced read-only mode. Alembic receives a separate,
        # normal connection only after an explicitly authorized --stamp validation.
        return create_engine(
            "sqlite+pysqlite://",
            creator=lambda: sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True),
            poolclass=NullPool,
        )
    return create_engine(url, poolclass=NullPool)


def validate_schema(engine: Engine) -> dict[str, Any]:
    with engine.connect() as connection:
        inspector = inspect(connection)
        present_tables = set(inspector.get_table_names())
        missing_tables = sorted(set(EXPECTED_SCHEMA) - present_tables)
        unexpected_tables = sorted(
            present_tables - set(EXPECTED_SCHEMA) - {"alembic_version"}
        )
        missing_columns: dict[str, list[str]] = {}
        for table_name, expected_columns in EXPECTED_SCHEMA.items():
            if table_name not in present_tables:
                continue
            actual_columns = {column["name"] for column in inspector.get_columns(table_name)}
            missing = sorted(set(expected_columns) - actual_columns)
            if missing:
                missing_columns[table_name] = missing

        current_revision = None
        if "alembic_version" in present_tables:
            current_revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one_or_none()

    return {
        "ok": not missing_tables and not unexpected_tables and not missing_columns,
        "expected_revision": REVISION,
        "current_revision": current_revision,
        "expected_table_count": len(EXPECTED_SCHEMA),
        "validated_table_count": len(set(EXPECTED_SCHEMA) & present_tables),
        "missing_tables": missing_tables,
        "unexpected_tables": unexpected_tables,
        "missing_columns": missing_columns,
    }


def _stamp(url: URL, current_revision: str | None) -> bool:
    if current_revision not in {None, REVISION}:
        raise ValidationError(
            "REVISION_CONFLICT",
            f"Refusing to replace existing Alembic revision {current_revision!r}.",
        )
    if current_revision == REVISION:
        return False

    # Importing Alembic only after every safety and schema check keeps the default path
    # read-only. env.py intentionally requires DATABASE_URL, so bind it to the already
    # validated dedicated target for the duration of this process.
    from alembic import command
    from alembic.config import Config

    project_root = Path(__file__).resolve().parents[1]
    os.environ["DATABASE_URL"] = url.render_as_string(hide_password=False)
    config = Config(str(project_root / "alembic.ini"))
    command.stamp(config, REVISION)
    return True


def _emit(payload: dict[str, Any], *, pretty: bool) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only legacy v16 schema validation with an isolated opt-in stamp.",
    )
    parser.add_argument("--stamp", action="store_true", help=f"stamp {REVISION} after validation")
    parser.add_argument("--pretty", action="store_true", help="pretty-print the JSON result")
    args = parser.parse_args(argv)

    url: URL | None = None
    try:
        url = _target_url()
        if args.stamp:
            # Fail target protection before making any database connection.
            _assert_stamp_allowed(url)
        _assert_existing_sqlite(url)
        engine = _validation_engine(url)
        try:
            result = validate_schema(engine)
        finally:
            engine.dispose()

        payload: dict[str, Any] = {
            "mode": "validate_and_stamp" if args.stamp else "validate_only",
            "target": _redacted_target(url),
            "stamped": False,
            **result,
        }
        if not result["ok"]:
            payload["error_code"] = "SCHEMA_MISMATCH"
            _emit(payload, pretty=args.pretty)
            return 1
        if args.stamp:
            payload["stamped"] = _stamp(url, result["current_revision"])
            payload["current_revision"] = REVISION
        _emit(payload, pretty=args.pretty)
        return 0
    except ValidationError as exc:
        _emit(
            {
                "ok": False,
                "mode": "validate_and_stamp" if args.stamp else "validate_only",
                "target": _redacted_target(url) if url is not None else None,
                "stamped": False,
                "error_code": exc.code,
                "error": str(exc),
            },
            pretty=args.pretty,
        )
        return 2
    except Exception as exc:  # Keep CLI failures structured without leaking credentials.
        _emit(
            {
                "ok": False,
                "mode": "validate_and_stamp" if args.stamp else "validate_only",
                "target": _redacted_target(url) if url is not None else None,
                "stamped": False,
                "error_code": "VALIDATION_FAILED",
                "error": type(exc).__name__,
            },
            pretty=args.pretty,
        )
        return 3


if __name__ == "__main__":
    sys.exit(main())
