"""Versioned, preview-confirmed Product V2 settings endpoints.

The latest settings snapshot is derived from immutable AuditEvent rows. A
completed AutomationJob acts as the durable idempotency receipt, so replaying
the same request is safe while reusing a key for a different payload fails
closed.
"""
from __future__ import annotations

import os
import re
import threading
from contextlib import contextmanager
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import models as legacy
from database import get_db
from product_v2 import models
from product_v2.enums import JobStatus
from product_v2.services.domain import add_audit, utcnow
from product_v2.settings_schemas import (
    ProductSettingRead,
    ProductSettingSection,
    ProductSettingsRead,
    ProductSettingUpdate,
    SettingsErrorResponse,
    default_setting_values,
    validate_setting_values,
)
from runtime_config import RuntimeConfigurationError, environment, read_flag
from services.auth import get_current_user


router = APIRouter(prefix="/api/v2/settings", tags=["product-v2-settings"])
SETTINGS_ACTION = "product_settings.updated"
SETTINGS_ENTITY = "product_setting"
SETTINGS_JOB_TYPE = "product_settings.update"
MAX_SETTINGS_BYTES = 32_000
SENSITIVE_KEY_PARTS = {
    "access_key",
    "api_key",
    "apikey",
    "credential",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "token",
}
SENSITIVE_TEXT_PATTERNS = (
    # Assignment-shaped secrets, including pasted JSON/YAML snippets. Merely
    # discussing "token rotation" remains valid, while a value-bearing token
    # declaration fails closed.
    re.compile(
        r"(?ix)\b(?:api[ _-]?key|access[ _-]?key|access[ _-]?token|refresh[ _-]?token|"
        r"private[ _-]?key|client[ _-]?secret|password|passwd|credential|secret|token)\b"
        r"[\"']?\s*(?:=|:)\s*[\"']?\s*[^\s,;\"']{3,}"
    ),
    re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{4,}"),
    # Common standalone secret formats can be leaked without a field label.
    re.compile(r"(?i)\bsk-[a-z0-9][a-z0-9_-]{6,}\b"),
    re.compile(r"(?i)\b(?:gh[pousr]_[a-z0-9]{10,}|xox[baprs]-[a-z0-9-]{10,})\b"),
    re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
)

# SQLite ignores SELECT ... FOR UPDATE. This process-local lock preserves the
# same compare-and-write semantics in local/test mode. MySQL additionally locks
# the durable owner row below, so separate API processes are serialized by the
# database rather than relying on this registry.
_SETTINGS_LOCKS_GUARD = threading.Lock()
_SETTINGS_LOCKS: dict[tuple[int, str], threading.RLock] = {}
ERROR_RESPONSES = {
    401: {"model": SettingsErrorResponse, "description": "Authentication required"},
    409: {"model": SettingsErrorResponse, "description": "Version, idempotency, or safety-lock conflict"},
    422: {"model": SettingsErrorResponse, "description": "Invalid settings policy document"},
}


def _effective_locks() -> dict[str, Any]:
    environment = os.environ.get("AUTOLEADGEN_ENV", "local").strip().lower()
    connector_mode = os.environ.get("AUTOLEADGEN_CONNECTOR_MODE", "fake").strip().lower()
    try:
        hard_pause = read_flag(
            "OUTBOUND_HARD_PAUSE",
            default=environment in {"local", "test", "staging", "production"},
        )
        allow_real = read_flag("ALLOW_REAL_EXTERNAL_CALLS", default=False)
    except RuntimeConfigurationError:
        # A dashboard status endpoint must never make an invalid runtime control
        # look permissive. The worker/provider boundary independently fails too.
        hard_pause = True
        allow_real = False
    real_external_calls_allowed = (
        environment not in {"local", "test"}
        and connector_mode == "real"
        and allow_real
        and not hard_pause
    )
    return {
        "environment": environment,
        "connector_mode": connector_mode,
        "outbound_hard_pause": hard_pause,
        "real_external_calls_allowed": real_external_calls_allowed,
        "credentials_accepted_here": False,
    }


def _contains_sensitive_material(value: Any) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if any(part in normalized for part in SENSITIVE_KEY_PARTS):
                return True
            if _contains_sensitive_material(nested):
                return True
    elif isinstance(value, list):
        return any(_contains_sensitive_material(item) for item in value)
    elif isinstance(value, str):
        return any(pattern.search(value) is not None for pattern in SENSITIVE_TEXT_PATTERNS)
    return False


@contextmanager
def _settings_write_lock(owner_id: int, section: ProductSettingSection):
    key = (owner_id, section.value)
    with _SETTINGS_LOCKS_GUARD:
        lock = _SETTINGS_LOCKS.setdefault(key, threading.RLock())
    with lock:
        yield


def _lock_owner_row(db: Session, owner_id: int) -> None:
    """Acquire the durable mutex used for atomic settings version allocation."""

    owner = (
        db.query(legacy.User.id)
        .filter(legacy.User.id == owner_id)
        .with_for_update()
        .one_or_none()
    )
    if owner is None:
        raise HTTPException(status_code=401, detail={"code": "USER_NOT_FOUND", "message": "User not found"})


def _latest_event(
    db: Session,
    owner_id: int,
    section: ProductSettingSection,
    *,
    for_update: bool = False,
):
    query = (
        db.query(models.AuditEvent)
        .filter_by(
            owner_id=owner_id,
            action=SETTINGS_ACTION,
            entity_type=SETTINGS_ENTITY,
            entity_id=section.value,
        )
        .order_by(models.AuditEvent.id.desc())
    )
    if for_update:
        query = query.with_for_update()
    return query.first()


def _snapshot_from_event(
    section: ProductSettingSection,
    event: models.AuditEvent | None,
) -> ProductSettingRead:
    if event is None:
        return ProductSettingRead(
            section=section,
            version=0,
            values=default_setting_values(section),
            effective_locks=_effective_locks(),
        )
    stored = event.after_data or {}
    return ProductSettingRead(
        section=section,
        version=int(stored.get("version", 0)),
        values=validate_setting_values(section, stored.get("values") or {}),
        updated_at=event.created_at,
        updated_by_user_id=event.actor_user_id,
        effective_locks=_effective_locks(),
    )


def _idempotent_replay(
    db: Session,
    *,
    user: legacy.User,
    idempotency_key: str,
    command_payload: dict[str, Any],
) -> ProductSettingRead | None:
    receipt = db.query(models.AutomationJob).filter_by(idempotency_key=idempotency_key).first()
    if receipt is None:
        return None
    if (
        receipt.owner_id != user.id
        or receipt.job_type != SETTINGS_JOB_TYPE
        or (receipt.payload or {}) != command_payload
        or not receipt.result
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "IDEMPOTENCY_KEY_REUSED",
                "message": "Idempotency-Key is already associated with a different command",
            },
        )
    return ProductSettingRead.model_validate(receipt.result)


@router.get(
    "",
    response_model=ProductSettingsRead,
    responses={401: ERROR_RESPONSES[401]},
)
def list_product_settings(
    db: Session = Depends(get_db),
    user: legacy.User = Depends(get_current_user),
):
    return ProductSettingsRead(
        settings=[
            _snapshot_from_event(section, _latest_event(db, user.id, section))
            for section in ProductSettingSection
        ]
    )


@router.get(
    "/{section}",
    response_model=ProductSettingRead,
    responses={401: ERROR_RESPONSES[401], 422: ERROR_RESPONSES[422]},
)
def get_product_setting(
    section: ProductSettingSection,
    db: Session = Depends(get_db),
    user: legacy.User = Depends(get_current_user),
):
    return _snapshot_from_event(section, _latest_event(db, user.id, section))


@router.put(
    "/{section}",
    response_model=ProductSettingRead,
    responses=ERROR_RESPONSES,
)
def update_product_setting(
    section: ProductSettingSection,
    payload: ProductSettingUpdate,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=255),
    db: Session = Depends(get_db),
    user: legacy.User = Depends(get_current_user),
):
    if _contains_sensitive_material(payload.values):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "CREDENTIALS_NOT_ACCEPTED",
                "message": "Credentials, API keys, and tokens must not be stored in Product V2 settings",
            },
        )

    try:
        values = validate_setting_values(section, payload.values)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "INVALID_SETTINGS_POLICY", "message": str(exc)},
        ) from exc
    if section == ProductSettingSection.CHANNELS_INTEGRATIONS and environment() in {
        "staging",
        "production",
    }:
        unavailable = [
            channel
            for channel in ("linkedin", "whatsapp")
            if values.get(f"{channel}_enabled") is True
        ]
        if unavailable:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "CHANNEL_UNAVAILABLE",
                    "message": (
                        "This release supports production Email only; unavailable channels: "
                        + ", ".join(unavailable)
                    ),
                },
            )
    if len(str(values).encode("utf-8")) > MAX_SETTINGS_BYTES:
        raise HTTPException(
            status_code=422,
            detail={"code": "SETTINGS_TOO_LARGE", "message": "Settings payload exceeds the 32 KB limit"},
        )

    command_payload = {
        "section": section.value,
        "values": values,
        "expected_version": payload.expected_version,
        "impact_preview_confirmed": True,
    }
    with _settings_write_lock(user.id, section):
        try:
            # Lock a stable row before reading the latest immutable event. This
            # makes expected_version comparison and version allocation one
            # database transaction, even when different idempotency keys race.
            _lock_owner_row(db, user.id)
            replay = _idempotent_replay(
                db,
                user=user,
                idempotency_key=idempotency_key,
                command_payload=command_payload,
            )
            if replay is not None:
                db.rollback()  # release the durable owner-row lock before returning
                replay.effective_locks = _effective_locks()
                return replay

            previous = _snapshot_from_event(
                section,
                _latest_event(db, user.id, section, for_update=True),
            )
            if previous.version != payload.expected_version:
                db.rollback()
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "SETTINGS_VERSION_CONFLICT",
                        "message": f"Settings changed since preview; expected version {payload.expected_version}, current version is {previous.version}",
                    },
                )

            observed_at = utcnow()
            updated = ProductSettingRead(
                section=section,
                version=previous.version + 1,
                values=values,
                updated_at=observed_at,
                updated_by_user_id=user.id,
                effective_locks=_effective_locks(),
            )
            persisted_snapshot = updated.model_dump(mode="json")
            receipt = models.AutomationJob(
                owner_id=user.id,
                status=JobStatus.SUCCEEDED,
                job_type=SETTINGS_JOB_TYPE,
                queue="settings",
                payload=command_payload,
                idempotency_key=idempotency_key,
                priority=100,
                scheduled_at=observed_at,
                attempts=1,
                max_attempts=1,
                result=persisted_snapshot,
                completed_at=observed_at,
            )
            db.add(receipt)
            add_audit(
                db,
                owner_id=user.id,
                actor_user_id=user.id,
                action=SETTINGS_ACTION,
                entity_type=SETTINGS_ENTITY,
                entity_id=section.value,
                before={"version": previous.version, "values": previous.values},
                after={"version": updated.version, "values": updated.values},
                metadata={"impact_preview_confirmed": True},
                correlation_id=idempotency_key,
            )
            db.commit()
            return updated
        except IntegrityError as exc:
            db.rollback()
            replay = _idempotent_replay(
                db,
                user=user,
                idempotency_key=idempotency_key,
                command_payload=command_payload,
            )
            if replay is not None:
                db.rollback()
                replay.effective_locks = _effective_locks()
                return replay
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "SETTINGS_WRITE_CONFLICT", "message": str(exc.orig)},
            ) from exc
        except HTTPException:
            db.rollback()
            raise
        except Exception:
            db.rollback()
            raise
