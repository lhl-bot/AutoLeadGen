"""HTTP control plane for owner-scoped Product V2 cutover."""

from __future__ import annotations

import re
from typing import Optional

import jwt
from jwt.exceptions import InvalidTokenError
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import models as legacy
from database import get_db
from product_v2.enums import OwnerWritePath
from product_v2.migration_schemas import (
    OwnerMigrationPreview,
    OwnerMigrationPreviewRequest,
    OwnerMigrationStateRead,
    OwnerMigrationSwitch,
)
from product_v2.migration_state import (
    OwnerMigrationConflict,
    owner_path_enforcement_enabled,
    owner_v2_write_enabled,
    preview_owner_path,
    read_owner_migration_state,
    switch_owner_path,
)
from product_v2.schemas import ErrorResponse
from services.auth import ALGORITHM, SECRET_KEY, get_current_user


ERROR_RESPONSES = {
    401: {"model": ErrorResponse, "description": "Authentication required"},
    409: {"model": ErrorResponse, "description": "Owner path, preview, or idempotency conflict"},
}

router = APIRouter(prefix="/api/v2/migration-state", tags=["product-v2-migration"])


_SIGNED_PROVIDER_INGRESS = re.compile(
    r"/api/v2/webhooks/[1-9][0-9]*/[a-z0-9][a-z0-9._-]{0,99}/events"
)


def owner_path_write_exception(path: str, method: str) -> bool:
    """Return whether a V2 write must survive either application write path.

    The exception surface is intentionally narrow: cutover control, explicit
    consent restrictions, and authenticated-by-signature provider ingress.
    None of these routes can start a Campaign or send an outbound message.
    """

    normalized = path.rstrip("/") or "/"
    normalized_method = method.upper()
    return (
        (
            normalized_method == "PUT"
            and normalized == "/api/v2/migration-state"
        )
        or (
            normalized_method == "POST"
            and normalized == "/api/v2/migration-state/preview"
        )
        or (
            normalized_method == "POST"
            and (
                normalized == "/api/v2/consent-restrictions"
                or _SIGNED_PROVIDER_INGRESS.fullmatch(normalized) is not None
            )
        )
    )


def authenticated_owner_id(request: Request) -> Optional[int]:
    """Decode an existing Bearer identity without replacing route auth.

    Missing and invalid credentials are left to the endpoint's normal auth
    dependency so the cutover fence never changes a 401 into a misleading
    owner-path conflict.
    """

    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return int(payload.get("sub"))
    except (InvalidTokenError, TypeError, ValueError):
        return None


def _conflict(exc: OwnerMigrationConflict) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": exc.code, "message": exc.message, **exc.context},
    )


def require_v2_write_path(
    request: Request,
    db: Session = Depends(get_db),
) -> None:
    """Fence every mutating V2 route except this cutover control plane."""

    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    if owner_path_write_exception(request.url.path, request.method):
        return
    owner_id = authenticated_owner_id(request)
    if owner_id is None:
        return
    owner_exists = db.query(legacy.User.id).filter(
        legacy.User.id == owner_id,
        legacy.User.is_active.is_(True),
    ).first()
    if owner_exists is None:
        return
    try:
        enabled = owner_v2_write_enabled(db, owner_id, lock=True)
    except OwnerMigrationConflict as exc:
        raise _conflict(exc) from exc
    if not enabled:
        current = read_owner_migration_state(db, owner_id)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "OWNER_V2_WRITE_PATH_INACTIVE",
                "message": "This owner has not explicitly activated the Product V2 write path",
                "current_path": current.current_path.value,
                "version": current.version,
            },
        )


@router.get("", response_model=OwnerMigrationStateRead, responses={401: ERROR_RESPONSES[401]})
def get_owner_migration_state(
    db: Session = Depends(get_db),
    user: legacy.User = Depends(get_current_user),
):
    return read_owner_migration_state(db, user.id)


@router.post(
    "/preview",
    response_model=OwnerMigrationPreview,
    responses={401: ERROR_RESPONSES[401]},
)
def preview_owner_migration_state(
    payload: OwnerMigrationPreviewRequest,
    db: Session = Depends(get_db),
    user: legacy.User = Depends(get_current_user),
):
    return preview_owner_path(db, owner_id=user.id, target_path=payload.target_path)


@router.put(
    "",
    response_model=OwnerMigrationStateRead,
    responses=ERROR_RESPONSES,
)
def update_owner_migration_state(
    payload: OwnerMigrationSwitch,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=8,
        max_length=255,
    ),
    db: Session = Depends(get_db),
    user: legacy.User = Depends(get_current_user),
):
    try:
        result = switch_owner_path(
            db,
            owner_id=user.id,
            actor_user_id=user.id,
            target_path=payload.target_path,
            expected_version=payload.expected_version,
            preview_checksum=payload.preview_checksum,
            idempotency_key=idempotency_key,
        )
        return result
    except OwnerMigrationConflict as exc:
        db.rollback()
        raise _conflict(exc) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "OWNER_PATH_WRITE_CONFLICT",
                "message": "Concurrent owner path switch conflicted",
            },
        ) from exc


def legacy_write_path_allowed(db: Session, owner_id: int, *, lock: bool = False) -> bool:
    """Used by the legacy middleware while it holds the owner-row fence."""

    if not owner_path_enforcement_enabled():
        return True
    if lock:
        from product_v2.migration_state import lock_owner_row

        lock_owner_row(db, owner_id)
    return read_owner_migration_state(db, owner_id).current_path == OwnerWritePath.LEGACY
