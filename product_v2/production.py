"""Production readiness and fail-closed release preflight checks."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Iterable
from urllib.parse import urlsplit

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

import models as legacy
from product_v2 import models
from product_v2.enums import (
    AttemptStatus,
    CampaignLifecycle,
    CampaignRunMode,
    Channel,
    ChannelAccountHealth,
    OwnerWritePath,
    ProviderCostStatus,
    StageStatus,
    WorkerType,
)
from product_v2.migration_state import owner_path_enforcement_enabled
from product_v2.production_controls import auto_send_approval
from product_v2.services.domain import as_utc, utcnow
from product_v2.settings_policy import configured_public_unsubscribe_url, setting_document
from product_v2.settings_schemas import ProductSettingSection
from runtime_config import (
    RuntimeConfigurationError,
    environment,
    read_flag,
    read_int,
    read_secret,
)
from services.auth import decrypt_smtp_pass


PROJECT_ROOT = Path(__file__).resolve().parents[1]
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_EVIDENCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{2,199}$")


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    message: str
    blocking: bool = True


def _check(name: str, passed: bool, success: str, failure: str) -> Check:
    return Check(name=name, passed=passed, message=success if passed else failure)


def _evidence_check(name: str) -> Check:
    value = os.environ.get(name, "").strip()
    return _check(
        f"evidence_{name.lower()}",
        _EVIDENCE_ID.fullmatch(value) is not None,
        f"{name} is linked to the change record",
        f"{name} must be a non-secret approved evidence identifier",
    )


def expected_migration_head() -> str:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    heads = ScriptDirectory.from_config(config).get_heads()
    if len(heads) != 1:
        raise RuntimeError("Alembic must have exactly one migration head")
    return heads[0]


def current_migration_revision(db: Session) -> str | None:
    return MigrationContext.configure(db.connection()).get_current_revision()


def database_identity_fingerprint(db: Session) -> str:
    """Return a secret-free exact MySQL target identity digest."""

    if db.get_bind().dialect.name != "mysql":
        raise RuntimeError("Production database identity requires MySQL")
    row = db.execute(
        text(
            "SELECT DATABASE() AS database_name, @@hostname AS hostname, @@port AS port"
        )
    ).mappings().one()
    canonical = json.dumps(
        {
            "database_name": row["database_name"],
            "hostname": row["hostname"],
            "port": int(row["port"]),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def database_readiness_checks(db: Session, *, require_head: bool = True) -> list[Check]:
    checks: list[Check] = []
    try:
        db.execute(text("SELECT 1")).scalar_one()
        checks.append(Check("database_connection", True, "Database connection succeeded"))
    except Exception:
        db.rollback()
        return [Check("database_connection", False, "Database connection failed")]
    if require_head:
        try:
            expected = expected_migration_head()
            current = current_migration_revision(db)
            checks.append(
                _check(
                    "database_migration_head",
                    current == expected,
                    f"Database is at migration head {expected}",
                    "Database migration is missing or behind the release head",
                )
            )
        except Exception:
            checks.append(
                Check(
                    "database_migration_head",
                    False,
                    "Database migration state could not be verified",
                )
            )
    return checks


def _secret_checks() -> list[Check]:
    checks: list[Check] = []
    resolved: dict[str, str] = {}
    for name in (
        "DATABASE_URL",
        "JWT_SECRET_KEY",
        "SMTP_ENCRYPTION_KEY",
        "UNSUBSCRIBE_TOKEN_SECRET",
        "PRODUCT_V2_WEBHOOK_SECRET",
    ):
        try:
            value = read_secret(name, required=True) or ""
            resolved[name] = value
            minimum = 32 if name != "DATABASE_URL" else 1
            checks.append(
                _check(
                    f"secret_{name.lower()}",
                    len(value.encode("utf-8")) >= minimum,
                    f"{name} is injected",
                    f"{name} is missing or too short",
                )
            )
        except RuntimeConfigurationError:
            checks.append(Check(f"secret_{name.lower()}", False, f"{name} injection is invalid"))
    if all(
        name in resolved
        for name in (
            "JWT_SECRET_KEY",
            "SMTP_ENCRYPTION_KEY",
            "UNSUBSCRIBE_TOKEN_SECRET",
            "PRODUCT_V2_WEBHOOK_SECRET",
        )
    ):
        values = {
            resolved["JWT_SECRET_KEY"],
            resolved["SMTP_ENCRYPTION_KEY"],
            resolved["UNSUBSCRIBE_TOKEN_SECRET"],
            resolved["PRODUCT_V2_WEBHOOK_SECRET"],
        }
        checks.append(
            _check(
                "secret_separation",
                len(values) == 4,
                "JWT, SMTP encryption, unsubscribe, and webhook keys are independent",
                "JWT, SMTP encryption, unsubscribe, and webhook keys must be independent",
            )
        )
    return checks


def deployment_configuration_checks(*, phase: str) -> list[Check]:
    checks: list[Check] = []
    env = environment()
    checks.append(
        _check(
            "environment",
            env in {"staging", "production"},
            f"Runtime environment is {env}",
            "Production preflight requires staging or production",
        )
    )
    checks.extend(_secret_checks())
    try:
        database_url = read_secret("DATABASE_URL", required=True) or ""
        driver = make_url(database_url).drivername
        checks.append(
            _check(
                "database_engine",
                driver.startswith("mysql"),
                "Database engine is MySQL-compatible",
                "Production requires a MySQL-compatible DATABASE_URL",
            )
        )
    except Exception:
        checks.append(Check("database_engine", False, "Database URL cannot be parsed"))

    cors = [item.strip() for item in os.environ.get("CORS_ORIGINS", "").split(",") if item.strip()]
    cors_safe = bool(cors) and all(
        item.startswith("https://") and "*" not in item and "localhost" not in item
        for item in cors
    )
    allowed_hosts = [
        item.strip()
        for item in os.environ.get("ALLOWED_HOSTS", "").split(",")
        if item.strip()
    ]
    checks.append(
        _check(
            "allowed_hosts",
            bool(allowed_hosts) and "*" not in allowed_hosts,
            "Host allow-list is explicit",
            "ALLOWED_HOSTS must contain explicit production hostnames",
        )
    )
    checks.append(
        _check(
            "cors_origins",
            cors_safe,
            "CORS origins are explicit HTTPS origins",
            "CORS_ORIGINS must contain only explicit production HTTPS origins",
        )
    )
    checks.append(
        _check(
            "owner_path_enforcement",
            owner_path_enforcement_enabled(),
            "Owner-scoped single-writer enforcement is enabled",
            "Product V2 owner-path enforcement is disabled",
        )
    )
    checks.append(
        _check(
            "legacy_background_workers",
            not read_flag("ENABLE_BACKGROUND_WORKERS", default=False),
            "Legacy in-process background workers are disabled",
            "Legacy in-process background workers must be disabled",
        )
    )
    real_acquisition_enabled = read_flag("ALLOW_REAL_ACQUISITION_CALLS", default=False)
    acquisition_controls_ready = (
        not real_acquisition_enabled
        or (
            bool(os.environ.get("ACQUISITION_OWNER_ALLOWLIST", "").strip())
            and bool(os.environ.get("ACQUISITION_APPROVAL_ID", "").strip())
            and bool(os.environ.get("ACQUISITION_PRICE_VERSION", "").strip())
        )
    )
    checks.append(
        _check(
            "real_acquisition_governance",
            acquisition_controls_ready,
            "Real acquisition is disabled or approval-bound with pricing and an Owner allow-list",
            "Real acquisition requires ACQUISITION_OWNER_ALLOWLIST, ACQUISITION_APPROVAL_ID and ACQUISITION_PRICE_VERSION",
        )
    )
    checks.append(
        _check(
            "release_sha",
            bool(os.environ.get("RELEASE_SHA", "").strip()),
            "Release source identifier is present",
            "RELEASE_SHA is required",
        )
    )
    checks.append(
        _check(
            "image_digest",
            bool(_DIGEST.fullmatch(os.environ.get("IMAGE_DIGEST", "").strip())),
            "Immutable image digest is present",
            "IMAGE_DIGEST must be a sha256 digest",
        )
    )
    checks.append(
        _check(
            "change_id",
            bool(os.environ.get("PRODUCTION_CHANGE_ID", "").strip()),
            "Approved change identifier is present",
            "PRODUCTION_CHANGE_ID is required",
        )
    )
    for evidence_name in (
        "PRODUCT_V2_BACKUP_RESTORE_EVIDENCE_ID",
        "PRODUCT_V2_MONITORING_EVIDENCE_ID",
        "PRODUCT_V2_STAGING_ACCEPTANCE_EVIDENCE_ID",
    ):
        checks.append(_evidence_check(evidence_name))

    if phase in {"enable-real", "verify-live"}:
        for evidence_name in (
            "PRODUCT_V2_EMAIL_DNS_EVIDENCE_ID",
            "PRODUCT_V2_COMPLIANCE_APPROVAL_ID",
            "PRODUCT_V2_SHADOW_EVIDENCE_ID",
        ):
            checks.append(_evidence_check(evidence_name))
        mode = os.environ.get("AUTOLEADGEN_CONNECTOR_MODE", "").strip().lower()
        checks.append(
            _check(
                "connector_mode",
                mode == "real",
                "Real connector mode is selected",
                "AUTOLEADGEN_CONNECTOR_MODE must be real",
            )
        )
        checks.append(
            _check(
                "external_calls_approval",
                read_flag("ALLOW_REAL_EXTERNAL_CALLS", default=False),
                "Real external calls are explicitly approved",
                "ALLOW_REAL_EXTERNAL_CALLS must be true",
            )
        )
        hard_pause = read_flag("OUTBOUND_HARD_PAUSE", default=True)
        if phase == "enable-real":
            checks.append(
                _check(
                    "outbound_hard_pause",
                    hard_pause,
                    "Outbound hard pause remains engaged for safe promotion",
                    "OUTBOUND_HARD_PAUSE must remain engaged during enable-real preflight",
                )
            )
        else:
            checks.append(
                _check(
                    "outbound_hard_pause",
                    not hard_pause,
                    "Outbound hard pause is released",
                    "OUTBOUND_HARD_PAUSE must be explicitly released",
                )
            )
        checks.append(
            _check(
                "legacy_api_read_only",
                read_flag("PRODUCT_V2_LEGACY_READ_ONLY", default=False),
                "Legacy API is read-only",
                "PRODUCT_V2_LEGACY_READ_ONLY must be true",
            )
        )
        checks.append(
            _check(
                "legacy_writers_frozen",
                read_flag("PRODUCT_V2_LEGACY_WRITERS_FROZEN", default=False),
                "Legacy writers are explicitly frozen",
                "PRODUCT_V2_LEGACY_WRITERS_FROZEN must be true",
            )
        )
    return checks


def _count(db: Session, query) -> int:
    return int(query.count())


def domain_preflight_checks(db: Session, *, phase: str) -> list[Check]:
    checks = database_readiness_checks(db, require_head=True)
    if not all(check.passed for check in checks):
        return checks

    active_admins = _count(
        db,
        db.query(legacy.User).filter(
            legacy.User.is_admin.is_(True),
            legacy.User.is_active.is_(True),
        ),
    )
    checks.append(
        _check(
            "active_administrator",
            active_admins > 0,
            f"{active_admins} active administrator account(s) exist",
            "At least one active administrator account is required",
        )
    )

    v2_owner_rows = db.query(models.OwnerMigrationState.owner_id).filter(
            models.OwnerMigrationState.current_path == OwnerWritePath.V2
        ).all()
    v2_owner_ids = {owner_id for (owner_id,) in v2_owner_rows}
    v2_owners = len(v2_owner_ids)
    checks.append(
        Check(
            name="v2_owner_cohort",
            passed=v2_owners > 0,
            message=(
                f"V2 cohort contains {v2_owners} owner(s)"
                if v2_owners > 0
                else "No owner has activated the Product V2 write path"
            ),
            blocking=phase != "deploy",
        )
    )
    if read_flag("ALLOW_REAL_ACQUISITION_CALLS", default=False):
        raw_acquisition_owners = os.environ.get("ACQUISITION_OWNER_ALLOWLIST", "")
        acquisition_owner_ids = {
            int(value.strip())
            for value in raw_acquisition_owners.split(",")
            if value.strip().isdigit()
        }
        checks.append(
            _check(
                "acquisition_owner_cohort",
                bool(acquisition_owner_ids) and acquisition_owner_ids.issubset(v2_owner_ids),
                "Real acquisition is limited to the active V2 Owner cohort",
                "ACQUISITION_OWNER_ALLOWLIST must be a non-empty subset of active V2 Owners",
            )
        )

    uncertain = _count(
        db,
        db.query(models.OutreachAttempt).filter(
            models.OutreachAttempt.owner_id.in_(v2_owner_ids or {-1}),
            models.OutreachAttempt.status.in_((AttemptStatus.SENDING, AttemptStatus.UNKNOWN))
        ),
    )
    unresolved_costs = _count(
        db,
        db.query(models.ProviderCostEvent).filter(
            models.ProviderCostEvent.owner_id.in_(v2_owner_ids or {-1}),
            models.ProviderCostEvent.status.in_(
                (ProviderCostStatus.RESERVED, ProviderCostStatus.UNKNOWN)
            )
        ),
    )
    checks.append(
        _check(
            "provider_uncertainty",
            uncertain == 0 and unresolved_costs == 0,
            "No unresolved Provider attempts or costs exist",
            f"Unresolved Provider state exists (attempts={uncertain}, costs={unresolved_costs})",
        )
    )

    if phase in {"enable-real", "verify-live"}:
        release_channel_phase = os.environ.get(
            "PRODUCT_V2_RELEASE_CHANNEL_PHASE",
            "email",
        ).strip().lower()
        allowed_channels = {Channel.EMAIL}
        if release_channel_phase in {"linkedin", "whatsapp"}:
            allowed_channels.add(Channel.LINKEDIN)
        if release_channel_phase == "whatsapp":
            allowed_channels.add(Channel.WHATSAPP)
        active_campaigns = select(models.Campaign.id).join(
            models.OwnerMigrationState,
            models.OwnerMigrationState.owner_id == models.Campaign.owner_id,
        ).where(
            models.OwnerMigrationState.current_path == OwnerWritePath.V2,
            models.Campaign.lifecycle.in_(
                (CampaignLifecycle.READY, CampaignLifecycle.RUNNING, CampaignLifecycle.PAUSED)
            ),
            models.Campaign.archived_at.is_(None),
        )
        active_campaign_rows = db.query(
            models.Campaign.owner_id,
            models.Campaign.run_mode,
        ).join(
                models.OwnerMigrationState,
                models.OwnerMigrationState.owner_id == models.Campaign.owner_id,
            ).filter(
                models.OwnerMigrationState.current_path == OwnerWritePath.V2,
                models.Campaign.lifecycle.in_(
                    (
                        CampaignLifecycle.READY,
                        CampaignLifecycle.RUNNING,
                        CampaignLifecycle.PAUSED,
                    )
                ),
                models.Campaign.archived_at.is_(None),
            ).all()
        active_owner_ids = {owner_id for owner_id, _run_mode in active_campaign_rows}
        shadow_campaigns = sum(
            1
            for _owner_id, run_mode in active_campaign_rows
            if run_mode == CampaignRunMode.SHADOW
        )
        checks.append(
            _check(
                "production_campaign_modes",
                shadow_campaigns == 0,
                "Every active production Campaign uses review or auto mode",
                f"{shadow_campaigns} active shadow Campaign(s) cannot run with a real registry",
            )
        )
        unsupported = _count(
            db,
            db.query(models.SequenceStep).join(
                models.CampaignRevision,
                models.SequenceStep.campaign_revision_id == models.CampaignRevision.id,
            ).filter(
                models.CampaignRevision.campaign_id.in_(active_campaigns),
                models.SequenceStep.channel.notin_(allowed_channels),
                models.SequenceStep.archived_at.is_(None),
            ),
        )
        incomplete_templates = _count(
            db,
            db.query(models.SequenceStep).join(
                models.CampaignRevision,
                models.SequenceStep.campaign_revision_id == models.CampaignRevision.id,
            ).filter(
                models.CampaignRevision.campaign_id.in_(active_campaigns),
                models.SequenceStep.channel == Channel.EMAIL,
                models.SequenceStep.archived_at.is_(None),
                (
                    models.SequenceStep.subject_template.is_(None)
                    | (models.SequenceStep.subject_template == "")
                    | models.SequenceStep.body_template.is_(None)
                    | (models.SequenceStep.body_template == "")
                    | models.SequenceStep.channel_account_id.is_(None)
                ),
            ),
        )
        checks.append(
            _check(
                "approved_channel_phase",
                unsupported == 0,
                f"Active revisions match the {release_channel_phase} channel phase",
                f"{unsupported} Sequence Step(s) exceed the {release_channel_phase} phase",
            )
        )
        checks.append(
            _check(
                "immutable_message_templates",
                incomplete_templates == 0,
                "Active email steps have immutable templates and account bindings",
                f"{incomplete_templates} active email step(s) lack templates or account bindings",
            )
        )

        channel_policy_ready = bool(active_owner_ids)
        unsubscribe_ready = bool(active_owner_ids)
        owner_review_policy: dict[int, bool] = {}
        for owner_id in active_owner_ids:
            channel_policy = setting_document(
                db,
                owner_id=owner_id,
                section=ProductSettingSection.CHANNELS_INTEGRATIONS,
            )
            policy_is_valid = (
                channel_policy.configured
                and bool(channel_policy.values.get("email_enabled"))
                and bool(channel_policy.values.get("linkedin_enabled"))
                == (Channel.LINKEDIN in allowed_channels)
                and bool(channel_policy.values.get("whatsapp_enabled"))
                == (Channel.WHATSAPP in allowed_channels)
            )
            channel_policy_ready = channel_policy_ready and policy_is_valid
            owner_review_policy[owner_id] = bool(
                channel_policy.values.get("review_before_send", True)
            )
            unsubscribe_url = configured_public_unsubscribe_url(
                db, owner_id=owner_id
            )
            parsed = urlsplit(unsubscribe_url)
            unsubscribe_ready = unsubscribe_ready and (
                parsed.scheme == "https"
                and bool(parsed.hostname)
                and parsed.username is None
                and parsed.password is None
                and parsed.query == ""
                and parsed.fragment == ""
                and parsed.path.rstrip("/") == "/api/unsubscribe/v2"
            )
        checks.append(
            _check(
                "email_review_policy",
                channel_policy_ready,
                f"Active V2 owners match the {release_channel_phase} channel phase",
                "Every active V2 owner must enable only channels approved for this release phase",
            )
        )
        unreviewed_auto_owner_ids = {
            owner_id
            for owner_id, run_mode in active_campaign_rows
            if run_mode == CampaignRunMode.AUTO
            and not owner_review_policy.get(owner_id, True)
        }
        auto_approval = auto_send_approval(unreviewed_auto_owner_ids)
        checks.append(
            Check(
                name="production_auto_send",
                passed=auto_approval.passed,
                message=auto_approval.message,
            )
        )
        checks.append(
            _check(
                "public_unsubscribe_url",
                unsubscribe_ready,
                "Active V2 owners use the public HTTPS V2 unsubscribe endpoint",
                "Every active V2 owner must configure https://<host>/api/unsubscribe/v2",
            )
        )

        stale_after = utcnow() - timedelta(
            seconds=read_int(
                "PRODUCT_V2_ACCOUNT_HEALTH_MAX_AGE_SECONDS",
                default=3600,
                minimum=60,
                maximum=86400,
            )
        )
        email_accounts = db.query(models.ChannelAccount, legacy.EmailAccount).join(
            legacy.EmailAccount,
            models.ChannelAccount.legacy_email_account_id == legacy.EmailAccount.id,
        ).join(
            models.OwnerMigrationState,
            models.OwnerMigrationState.owner_id == models.ChannelAccount.owner_id,
        ).filter(
            models.OwnerMigrationState.current_path == OwnerWritePath.V2,
            models.ChannelAccount.channel == Channel.EMAIL,
            models.ChannelAccount.provider == "smtp",
            models.ChannelAccount.enabled.is_(True),
            models.ChannelAccount.archived_at.is_(None),
        ).all()
        def credential_ready(source) -> bool:
            try:
                return bool(decrypt_smtp_pass(source.smtp_pass))
            except Exception:
                return False

        accounts_ready = bool(email_accounts) and all(
            account.health_status == ChannelAccountHealth.HEALTHY
            and account.health_checked_at is not None
            and as_utc(account.health_checked_at) >= stale_after
            and bool(source.imap_host)
            and bool(source.smtp_host)
            and bool(source.smtp_pass)
            and bool(source.use_ssl or source.use_tls)
            and not bool(source.use_ssl and source.use_tls)
            and isinstance(account.daily_limit, int)
            and not isinstance(account.daily_limit, bool)
            and 1 <= account.daily_limit <= 100
            and credential_ready(source)
            for account, source in email_accounts
        )
        checks.append(
            _check(
                "smtp_imap_accounts",
                accounts_ready,
                f"{len(email_accounts)} SMTP/IMAP account(s) are healthy and fresh",
                "No healthy, fresh, encrypted, daily-capped SMTP+IMAP account is ready",
            )
        )
        automatic_accounts = [
            account
            for account, _source in email_accounts
            if account.owner_id in unreviewed_auto_owner_ids
        ]
        automatic_capacity_ready = all(
            isinstance(account.daily_limit, int)
            and not isinstance(account.daily_limit, bool)
            and 1 <= account.daily_limit <= auto_approval.daily_cap
            for account in automatic_accounts
        )
        checks.append(
            _check(
                "production_auto_send_capacity",
                automatic_capacity_ready,
                (
                    "Automatic sender-account limits are within the approved daily ceiling"
                    if automatic_accounts
                    else "No unreviewed automatic sender account is active"
                ),
                "Every automatic sender account requires a positive daily limit within the deployment ceiling",
            )
        )

    if phase == "verify-live":
        stale_after = utcnow() - timedelta(seconds=90)
        expected_release_sha = os.environ.get("RELEASE_SHA", "").strip()
        expected_image_digest = os.environ.get("IMAGE_DIGEST", "").strip()
        for worker_type in (WorkerType.OUTBOUND, WorkerType.INBOX):
            candidates = db.query(models.WorkerHeartbeat).filter(
                models.WorkerHeartbeat.worker_type == worker_type,
                models.WorkerHeartbeat.status == StageStatus.RUNNING,
                models.WorkerHeartbeat.last_seen_at >= stale_after,
            ).order_by(models.WorkerHeartbeat.last_seen_at.desc()).all()
            heartbeat = next(
                (
                    row
                    for row in candidates
                    if (row.details or {}).get("release_sha") == expected_release_sha
                    and (row.details or {}).get("image_digest")
                    == expected_image_digest
                ),
                None,
            )
            checks.append(
                _check(
                    f"worker_{worker_type.value}",
                    heartbeat is not None,
                    f"{worker_type.value} worker heartbeat is fresh and release-bound",
                    f"{worker_type.value} worker heartbeat is missing, stale, or from another release",
                )
            )
    return checks


def report(checks: Iterable[Check], *, phase: str) -> dict[str, object]:
    items = list(checks)
    return {
        "status": "pass" if all(item.passed or not item.blocking for item in items) else "fail",
        "phase": phase,
        "environment": environment(),
        "release_sha": os.environ.get("RELEASE_SHA", "unknown"),
        "checks": [asdict(item) for item in items],
    }
