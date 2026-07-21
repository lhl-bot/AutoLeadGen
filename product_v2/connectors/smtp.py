"""Production SMTP connector for Product V2 email attempts."""
from __future__ import annotations

from contextlib import suppress
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid, parseaddr
from datetime import datetime, timedelta, timezone
import smtplib
import ssl
from typing import Callable

from sqlalchemy import or_

import models as legacy
from database import SessionLocal
from product_v2 import models
from product_v2.connectors.base import ConnectorRequest, ConnectorResult
from product_v2.enums import (
    CampaignLifecycle,
    CampaignRunMode,
    Channel,
    ChannelAccountHealth,
    OwnerWritePath,
    SafetyLockScope,
)
from product_v2.production_controls import (
    auto_send_approval,
    production_auto_send_daily_cap,
)
from product_v2.settings_policy import review_policy_required
from runtime_config import environment, read_int
from services.auth import decrypt_smtp_pass
from services.suppression import find_exact_email_suppression


class SMTPConfigurationError(RuntimeError):
    """Raised before a Provider call when an account binding is invalid."""


class SMTPDeliveryUncertain(RuntimeError):
    """The SMTP server may have accepted DATA; reconciliation is required."""


def _valid_address(value: str) -> bool:
    display, parsed = parseaddr(value or "")
    return (
        not display
        and parsed == value
        and "@" in parsed
        and "\r" not in parsed
        and "\n" not in parsed
    )


class SMTPConnector:
    channel = Channel.EMAIL
    provider = "smtp"
    is_fake = False

    def __init__(self, session_factory: Callable = SessionLocal):
        self._session_factory = session_factory

    @staticmethod
    def _assert_runtime_account_ready(db, account: models.ChannelAccount) -> None:
        """Recheck mutable sender controls immediately around SMTP I/O."""

        if environment() not in {"staging", "production"}:
            return
        if not account.enabled or account.archived_at is not None:
            raise SMTPConfigurationError("smtp_account_disabled")
        if account.health_status != ChannelAccountHealth.HEALTHY:
            raise SMTPConfigurationError("smtp_account_unhealthy")
        checked_at = account.health_checked_at
        if checked_at is None:
            raise SMTPConfigurationError("smtp_account_health_stale")
        checked_utc = (
            checked_at
            if checked_at.tzinfo is not None
            else checked_at.replace(tzinfo=timezone.utc)
        )
        maximum_age = read_int(
            "PRODUCT_V2_ACCOUNT_HEALTH_TTL_SECONDS",
            default=300,
            minimum=1,
            maximum=86_400,
        )
        if datetime.now(timezone.utc) - checked_utc > timedelta(seconds=maximum_age):
            raise SMTPConfigurationError("smtp_account_health_stale")
        active_lock = db.query(models.SafetyLock.id).filter(
            models.SafetyLock.owner_id == account.owner_id,
            models.SafetyLock.active.is_(True),
            or_(
                models.SafetyLock.scope == SafetyLockScope.GLOBAL,
                models.SafetyLock.channel_account_id == account.id,
            ),
        ).first()
        if active_lock:
            raise SMTPConfigurationError("smtp_account_safety_lock")

    def _assert_account_runtime_ready(self, request: ConnectorRequest) -> None:
        try:
            account_id = int(request.metadata["channel_account_id"])
            owner_id = int(request.metadata["owner_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SMTPConfigurationError("smtp_account_binding_missing") from exc
        db = self._session_factory()
        try:
            account = db.query(models.ChannelAccount).filter_by(
                id=account_id,
                owner_id=owner_id,
                channel=Channel.EMAIL,
                provider=self.provider,
            ).first()
            if account is None:
                raise SMTPConfigurationError("smtp_account_binding_invalid")
            self._assert_runtime_account_ready(db, account)
        finally:
            db.close()

    def _assert_recipient_not_legacy_suppressed(self, request: ConnectorRequest) -> None:
        """Fail closed on exact legacy opt-outs at the SMTP boundary."""

        try:
            owner_id = int(request.metadata["owner_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SMTPConfigurationError("smtp_delivery_owner_missing") from exc
        db = self._session_factory()
        try:
            if find_exact_email_suppression(
                db,
                email=request.recipient,
                user_id=owner_id,
            ) is not None:
                raise SMTPConfigurationError("smtp_recipient_suppressed")
        finally:
            db.close()

    def _credentials(self, request: ConnectorRequest):
        try:
            account_id = int(request.metadata["channel_account_id"])
            owner_id = int(request.metadata["owner_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SMTPConfigurationError("smtp_account_binding_missing") from exc
        db = self._session_factory()
        try:
            account = db.query(models.ChannelAccount).filter_by(
                id=account_id,
                owner_id=owner_id,
                channel=Channel.EMAIL,
                provider=self.provider,
                enabled=True,
                archived_at=None,
            ).first()
            if account is None or account.legacy_email_account_id is None:
                raise SMTPConfigurationError("smtp_account_binding_invalid")
            self._assert_runtime_account_ready(db, account)
            source = db.query(legacy.EmailAccount).filter_by(
                id=account.legacy_email_account_id,
                user_id=owner_id,
            ).first()
            if source is None or source.email != account.provider_account_id:
                raise SMTPConfigurationError("smtp_account_identity_mismatch")
            # Copy scalars while the short-lived credential session is open;
            # ORM objects and plaintext secrets never enter attempt metadata.
            values = {
                "email": source.email,
                "display_name": source.display_name,
                "host": source.smtp_host,
                "port": source.smtp_port,
                "user": source.smtp_user,
                "password": decrypt_smtp_pass(source.smtp_pass),
                "use_ssl": bool(source.use_ssl),
                "use_tls": bool(source.use_tls),
                "daily_limit": account.daily_limit,
            }
        finally:
            db.close()
        if not values["host"] or not values["user"] or not values["password"]:
            raise SMTPConfigurationError("smtp_credentials_incomplete")
        if values["use_ssl"] and values["use_tls"]:
            raise SMTPConfigurationError("smtp_transport_mode_ambiguous")
        if not values["use_ssl"] and not values["use_tls"]:
            raise SMTPConfigurationError("smtp_encryption_required")
        if not isinstance(values["port"], int) or not 1 <= values["port"] <= 65535:
            raise SMTPConfigurationError("smtp_port_invalid")
        if not _valid_address(values["email"]):
            raise SMTPConfigurationError("smtp_sender_invalid")
        return values

    def _assert_exact_automatic_cohort(self, owner_id: int) -> None:
        db = self._session_factory()
        try:
            rows = db.query(models.Campaign.owner_id).join(
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
                models.Campaign.run_mode == CampaignRunMode.AUTO,
                models.Campaign.archived_at.is_(None),
            ).distinct().all()
            active = {
                campaign_owner_id
                for (campaign_owner_id,) in rows
                if not review_policy_required(
                    db,
                    owner_id=campaign_owner_id,
                    lock=False,
                )
            }
            approval = auto_send_approval(active)
            if not approval.passed or owner_id not in active:
                raise SMTPConfigurationError("smtp_production_auto_send_not_approved")
        finally:
            db.close()

    def _assert_delivery_approval(
        self,
        request: ConnectorRequest,
        credentials: dict,
    ) -> None:
        """Make review/auto approval a final-boundary invariant in production."""

        if environment() not in {"staging", "production"}:
            return
        try:
            owner_id = int(request.metadata["owner_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SMTPConfigurationError("smtp_delivery_owner_missing") from exc
        run_mode = request.metadata.get("run_mode")
        review_approved = request.metadata.get("review_approved") is True
        daily_limit = credentials.get("daily_limit")
        if (
            not isinstance(daily_limit, int)
            or isinstance(daily_limit, bool)
            or not 1 <= daily_limit <= 100
        ):
            raise SMTPConfigurationError("smtp_production_daily_limit_invalid")
        if run_mode == "review":
            if not review_approved:
                raise SMTPConfigurationError("smtp_review_approval_missing")
            return
        if run_mode != "auto":
            raise SMTPConfigurationError("smtp_production_run_mode_invalid")
        if review_approved:
            return
        self._assert_exact_automatic_cohort(owner_id)
        daily_cap = production_auto_send_daily_cap()
        if (
            not isinstance(daily_limit, int)
            or isinstance(daily_limit, bool)
            or daily_limit < 1
            or daily_limit > daily_cap
        ):
            raise SMTPConfigurationError("smtp_production_auto_daily_limit_invalid")

    def send(self, request: ConnectorRequest) -> ConnectorResult:
        try:
            if request.channel != Channel.EMAIL or not _valid_address(request.recipient):
                raise SMTPConfigurationError("smtp_recipient_invalid")
            subject = (request.subject or "").strip()
            if not subject or "\r" in subject or "\n" in subject:
                raise SMTPConfigurationError("smtp_subject_invalid")
            if not request.body.strip():
                raise SMTPConfigurationError("smtp_body_missing")
            self._assert_recipient_not_legacy_suppressed(request)
            credentials = self._credentials(request)
            self._assert_delivery_approval(request, credentials)
            timeout = read_int("SMTP_TIMEOUT_SECONDS", default=20, minimum=5, maximum=60)
        except Exception as exc:
            reason = str(exc) if isinstance(exc, SMTPConfigurationError) else "smtp_preflight_failed"
            return ConnectorResult(
                accepted=False,
                provider=self.provider,
                provider_message_id=None,
                raw={"reason": reason},
            )

        message = EmailMessage()
        sender = credentials["email"]
        message["From"] = (
            formataddr((credentials["display_name"], sender))
            if credentials["display_name"]
            else sender
        )
        message["To"] = request.recipient
        message["Subject"] = subject
        message["Date"] = formatdate(localtime=False)
        message_id = make_msgid(domain=sender.rsplit("@", 1)[-1])
        message["Message-ID"] = message_id
        message["Reply-To"] = sender
        unsubscribe_url = request.metadata.get("unsubscribe_url")
        if isinstance(unsubscribe_url, str) and unsubscribe_url.startswith("https://"):
            message["List-Unsubscribe"] = (
                f"<mailto:{sender}?subject=unsubscribe>, <{unsubscribe_url}>"
            )
            message["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
        message.set_content(request.body, subtype="plain", charset="utf-8")

        context = ssl.create_default_context()
        server = None
        send_started = False
        try:
            if credentials["use_ssl"]:
                server = smtplib.SMTP_SSL(
                    credentials["host"],
                    credentials["port"],
                    timeout=timeout,
                    context=context,
                )
            else:
                server = smtplib.SMTP(
                    credentials["host"],
                    credentials["port"],
                    timeout=timeout,
                )
                server.ehlo()
                server.starttls(context=context)
                server.ehlo()
            server.login(credentials["user"], credentials["password"])
            self._assert_account_runtime_ready(request)
            # Recheck after authentication at the last safe instruction before
            # SMTP DATA, covering an opt-out recorded during session setup.
            self._assert_recipient_not_legacy_suppressed(request)
            send_started = True
            refused = server.send_message(
                message,
                from_addr=sender,
                to_addrs=[request.recipient],
            )
            if refused:
                response = refused.get(request.recipient)
                code = response[0] if isinstance(response, tuple) and response else None
                return ConnectorResult(
                    accepted=False,
                    provider=self.provider,
                    provider_message_id=None,
                    raw={"reason": "recipient_refused", "smtp_code": code},
                )
            return ConnectorResult(
                accepted=True,
                provider=self.provider,
                provider_message_id=message_id,
                raw={"accepted": True, "transport": "smtp"},
            )
        except smtplib.SMTPRecipientsRefused as exc:
            response = exc.recipients.get(request.recipient)
            code = response[0] if isinstance(response, tuple) and response else None
            return ConnectorResult(
                accepted=False,
                provider=self.provider,
                provider_message_id=None,
                raw={"reason": "recipient_refused", "smtp_code": code},
            )
        except smtplib.SMTPDataError as exc:
            return ConnectorResult(
                accepted=False,
                provider=self.provider,
                provider_message_id=None,
                raw={"reason": "data_rejected", "smtp_code": exc.smtp_code},
            )
        except (smtplib.SMTPAuthenticationError, smtplib.SMTPConnectError) as exc:
            return ConnectorResult(
                accepted=False,
                provider=self.provider,
                provider_message_id=None,
                raw={"reason": "smtp_session_rejected", "smtp_code": exc.smtp_code},
            )
        except SMTPConfigurationError as exc:
            return ConnectorResult(
                accepted=False,
                provider=self.provider,
                provider_message_id=None,
                raw={"reason": str(exc)},
            )
        except Exception as exc:
            if send_started:
                raise SMTPDeliveryUncertain("smtp_delivery_state_unknown") from exc
            return ConnectorResult(
                accepted=False,
                provider=self.provider,
                provider_message_id=None,
                raw={"reason": "smtp_session_failed_before_send"},
            )
        finally:
            if server is not None:
                with suppress(Exception):
                    server.quit()
