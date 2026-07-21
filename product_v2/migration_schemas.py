"""Public contracts for the owner-scoped V1/V2 cutover control plane."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from product_v2.enums import OwnerWritePath


class OwnerMigrationStateRead(BaseModel):
    owner_id: int
    current_path: OwnerWritePath
    version: int = Field(ge=0)
    explicit: bool
    switched_at: Optional[datetime] = None
    switched_by_user_id: Optional[int] = None


class OwnerMigrationPreviewRequest(BaseModel):
    target_path: OwnerWritePath


class OwnerMigrationPreview(BaseModel):
    owner_id: int
    current_path: OwnerWritePath
    target_path: OwnerWritePath
    expected_version: int = Field(ge=0)
    preview_checksum: str = Field(min_length=64, max_length=64)
    effects: dict[str, Any]
    blockers: list[dict[str, Any]] = Field(default_factory=list)


class OwnerMigrationSwitch(BaseModel):
    target_path: OwnerWritePath
    expected_version: int = Field(ge=0)
    preview_checksum: str = Field(min_length=64, max_length=64)
    impact_preview_confirmed: Literal[True]
