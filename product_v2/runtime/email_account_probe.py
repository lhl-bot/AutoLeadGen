"""No-send SMTP/IMAP connectivity probe for production Email accounts."""
from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from email.utils import parseaddr
import imaplib
import smtplib
import ssl

from sqlalchemy.orm import Session

import models as legacy
from product_v2 import models
from product_v2.enums import Channel
from runtime_config import read_int
from services.auth import decrypt_smtp_pass


class EmailAccountProbeError(RuntimeError):
    """A stable, non-secret failure code suitable for audit storage."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class EmailProbeCredentials:
    email: str
    smtp_host: str
    smtp_port: int
    username: str
    password: str
    use_ssl: bool
    use_tls: bool
    imap_host: str
    imap_port: int


def load_email_probe_credentials(
    db: Session,
    *,
    owner_id: int,
    channel_account_id: int,
) -> tuple[models.ChannelAccount, EmailProbeCredentials]:
    account = db.query(models.ChannelAccount).filter_by(
        id=channel_account_id,
        owner_id=owner_id,
        channel=Channel.EMAIL,
        provider="smtp",
        enabled=True,
        archived_at=None,
    ).first()
    if account is None or account.legacy_email_account_id is None:
        raise EmailAccountProbeError("email_account_binding_invalid")
    source = db.query(legacy.EmailAccount).filter_by(
        id=account.legacy_email_account_id,
        user_id=owner_id,
    ).first()
    if source is None or source.email != account.provider_account_id:
        raise EmailAccountProbeError("email_account_identity_mismatch")
    try:
        password = decrypt_smtp_pass(source.smtp_pass)
    except Exception as exc:
        raise EmailAccountProbeError("email_credential_decrypt_failed") from exc
    credentials = EmailProbeCredentials(
        email=(source.email or "").strip(),
        smtp_host=(source.smtp_host or "").strip(),
        smtp_port=int(source.smtp_port or 0),
        username=(source.smtp_user or "").strip(),
        password=password,
        use_ssl=bool(source.use_ssl),
        use_tls=bool(source.use_tls),
        imap_host=(source.imap_host or "").strip(),
        imap_port=int(source.imap_port or 0),
    )
    _validate_credentials(credentials)
    return account, credentials


def _validate_credentials(credentials: EmailProbeCredentials) -> None:
    parsed = parseaddr(credentials.email)[1]
    if (
        not credentials.email
        or parsed != credentials.email
        or "@" not in credentials.email
        or "\r" in credentials.email
        or "\n" in credentials.email
    ):
        raise EmailAccountProbeError("email_sender_invalid")
    if not all(
        (
            credentials.smtp_host,
            credentials.username,
            credentials.password,
            credentials.imap_host,
        )
    ):
        raise EmailAccountProbeError("email_credentials_incomplete")
    if credentials.use_ssl == credentials.use_tls:
        raise EmailAccountProbeError("smtp_encryption_mode_invalid")
    if not 1 <= credentials.smtp_port <= 65535:
        raise EmailAccountProbeError("smtp_port_invalid")
    if not 1 <= credentials.imap_port <= 65535:
        raise EmailAccountProbeError("imap_port_invalid")


def _expect_smtp_success(response, code: str) -> None:
    status = response[0] if isinstance(response, tuple) and response else None
    if not isinstance(status, int) or not 200 <= status < 300:
        raise EmailAccountProbeError(code)


def probe_email_credentials(
    credentials: EmailProbeCredentials,
    *,
    timeout: int | None = None,
) -> None:
    """Authenticate to both services without issuing any mail-send command."""

    _validate_credentials(credentials)
    timeout = timeout or read_int(
        "EMAIL_ACCOUNT_PROBE_TIMEOUT_SECONDS",
        default=20,
        minimum=5,
        maximum=60,
    )
    context = ssl.create_default_context()
    smtp = None
    try:
        try:
            if credentials.use_ssl:
                smtp = smtplib.SMTP_SSL(
                    credentials.smtp_host,
                    credentials.smtp_port,
                    timeout=timeout,
                    context=context,
                )
            else:
                smtp = smtplib.SMTP(
                    credentials.smtp_host,
                    credentials.smtp_port,
                    timeout=timeout,
                )
                _expect_smtp_success(smtp.ehlo(), "smtp_ehlo_failed")
                _expect_smtp_success(
                    smtp.starttls(context=context),
                    "smtp_starttls_failed",
                )
                _expect_smtp_success(smtp.ehlo(), "smtp_ehlo_failed")
        except EmailAccountProbeError:
            raise
        except ssl.SSLError as exc:
            raise EmailAccountProbeError("smtp_tls_failed") from exc
        except (OSError, smtplib.SMTPException) as exc:
            raise EmailAccountProbeError("smtp_connect_failed") from exc

        try:
            smtp.login(credentials.username, credentials.password)
            _expect_smtp_success(smtp.noop(), "smtp_noop_failed")
        except smtplib.SMTPAuthenticationError as exc:
            raise EmailAccountProbeError("smtp_auth_failed") from exc
        except (OSError, smtplib.SMTPException) as exc:
            raise EmailAccountProbeError("smtp_session_failed") from exc
    finally:
        if smtp is not None:
            with suppress(Exception):
                smtp.quit()

    mailbox = None
    try:
        try:
            mailbox = imaplib.IMAP4_SSL(
                credentials.imap_host,
                credentials.imap_port,
                timeout=timeout,
                ssl_context=context,
            )
        except ssl.SSLError as exc:
            raise EmailAccountProbeError("imap_tls_failed") from exc
        except (OSError, imaplib.IMAP4.error) as exc:
            raise EmailAccountProbeError("imap_connect_failed") from exc
        try:
            mailbox.login(credentials.username, credentials.password)
        except imaplib.IMAP4.error as exc:
            raise EmailAccountProbeError("imap_auth_failed") from exc
        try:
            status, _ = mailbox.select("INBOX", readonly=True)
        except imaplib.IMAP4.error as exc:
            raise EmailAccountProbeError("imap_inbox_failed") from exc
        if status != "OK":
            raise EmailAccountProbeError("imap_inbox_failed")
    finally:
        if mailbox is not None:
            with suppress(Exception):
                mailbox.logout()
