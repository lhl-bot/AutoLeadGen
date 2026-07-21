"""Contracts for versioned Product V2 operating settings.

Settings are intentionally small policy documents. Credentials and provider
tokens are never accepted by this API; account secrets stay in dedicated
connector stores and are represented here only by readiness metadata.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal, Optional, Union
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ProductSettingSection(str, Enum):
    ICP_PLAYBOOK = "icp_playbook"
    CHANNELS_INTEGRATIONS = "channels_integrations"
    PROVIDERS = "providers"
    PERMISSIONS = "permissions"


class StrictSettingsModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IcpPlaybookSettings(StrictSettingsModel):
    summary: str = Field(default="", max_length=4000)
    target_industries: list[str] = Field(default_factory=list, max_length=100)
    target_roles: list[str] = Field(default_factory=list, max_length=100)
    evidence_requirements: list[str] = Field(default_factory=list, max_length=100)
    playbook_notes: str = Field(default="", max_length=12000)
    proposal_status: Literal["draft", "published"] = "draft"

    @field_validator("target_industries", "target_roles", "evidence_requirements")
    @classmethod
    def clean_string_list(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values if value.strip()]
        if any(len(value) > 255 for value in cleaned):
            raise ValueError("List values must not exceed 255 characters")
        return list(dict.fromkeys(cleaned))

    @model_validator(mode="after")
    def published_requires_evidence(self):
        if self.proposal_status == "published" and (
            not self.summary.strip() or not self.evidence_requirements
        ):
            raise ValueError("A published ICP requires a summary and evidence requirements")
        return self


class ChannelsIntegrationsSettings(StrictSettingsModel):
    email_enabled: bool = False
    linkedin_enabled: bool = False
    whatsapp_enabled: bool = False
    public_unsubscribe_url: str = Field(default="", max_length=2000)
    review_before_send: bool = True
    integration_notes: str = Field(default="", max_length=8000)

    @field_validator("public_unsubscribe_url")
    @classmethod
    def validate_public_unsubscribe_url(cls, value: str) -> str:
        value = value.strip()
        if not value:
            return value
        parsed = urlparse(value)
        local_hosts = {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme == "https" and parsed.netloc:
            return value
        if parsed.scheme == "http" and parsed.hostname in local_hosts:
            return value
        raise ValueError("Public unsubscribe URL must use HTTPS; local HTTP is allowed only for localhost")


class ProviderSettings(StrictSettingsModel):
    global_budget_limit: float = Field(default=0, ge=0, le=1_000_000_000)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    price_version: str = Field(default="local-unpriced", min_length=1, max_length=100)
    paid_miss_requires_review: bool = True
    provider_policy_notes: str = Field(default="", max_length=8000)


class PermissionSettings(StrictSettingsModel):
    paid_actions_require_confirmation: Literal[True] = True
    bulk_mutations_require_confirmation: Literal[True] = True
    opportunity_requires_human_confirmation: Literal[True] = True
    review_mode_send_requires_confirmation: Literal[True] = True
    role_policy_notes: str = Field(default="", max_length=8000)


class ProductSettingUpdate(BaseModel):
    values: dict[str, Any]
    expected_version: int = Field(ge=0)
    impact_preview_confirmed: Literal[True]


class ProductSettingRead(BaseModel):
    section: ProductSettingSection
    version: int
    values: dict[str, Any]
    updated_at: Optional[datetime] = None
    updated_by_user_id: Optional[int] = None
    effective_locks: dict[str, Any] = Field(default_factory=dict)


class ProductSettingsRead(BaseModel):
    settings: list[ProductSettingRead]


class SettingsErrorDetail(BaseModel):
    code: str
    message: str


class SettingsErrorResponse(BaseModel):
    # FastAPI emits a list for path/header/body validation before the endpoint
    # runs, while policy and conflict errors use the structured code/message
    # object.  Document both real wire shapes instead of promising one schema
    # for every 422 response.
    detail: Union[SettingsErrorDetail, list[dict[str, Any]]]


SETTINGS_MODELS = {
    ProductSettingSection.ICP_PLAYBOOK: IcpPlaybookSettings,
    ProductSettingSection.CHANNELS_INTEGRATIONS: ChannelsIntegrationsSettings,
    ProductSettingSection.PROVIDERS: ProviderSettings,
    ProductSettingSection.PERMISSIONS: PermissionSettings,
}


def default_setting_values(section: ProductSettingSection) -> dict[str, Any]:
    return SETTINGS_MODELS[section]().model_dump(mode="json")


def validate_setting_values(section: ProductSettingSection, values: dict[str, Any]) -> dict[str, Any]:
    return SETTINGS_MODELS[section].model_validate(values).model_dump(mode="json")
