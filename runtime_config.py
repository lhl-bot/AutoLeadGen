"""Small, dependency-free helpers for fail-closed runtime configuration.

Production secrets may be supplied either directly (``NAME``) or through a
mounted secret file (``NAME_FILE``).  The two forms are mutually exclusive so
an old environment value cannot silently override a rotated Docker/Kubernetes
secret.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


TRUTHY = frozenset({"1", "true", "yes", "on"})
FALSY = frozenset({"0", "false", "no", "off"})


class RuntimeConfigurationError(RuntimeError):
    """Raised when deployment configuration is ambiguous or unsafe."""


def environment() -> str:
    return os.environ.get("AUTOLEADGEN_ENV", "local").strip().lower()


def is_production_like() -> bool:
    return environment() in {"staging", "production"}


def read_secret(
    name: str,
    *,
    required: bool = False,
    default: Optional[str] = None,
) -> Optional[str]:
    """Read ``NAME`` or ``NAME_FILE`` without ever logging the value."""

    direct = os.environ.get(name)
    file_name = os.environ.get(f"{name}_FILE")
    if direct and file_name:
        raise RuntimeConfigurationError(
            f"Configure exactly one of {name} or {name}_FILE"
        )

    value: Optional[str]
    if file_name:
        path = Path(file_name)
        try:
            if not path.is_file():
                raise OSError("not a regular file")
            value = path.read_text(encoding="utf-8").rstrip("\r\n")
        except OSError as exc:
            raise RuntimeConfigurationError(
                f"Unable to read configured secret file for {name}"
            ) from exc
    else:
        value = direct

    if value is None:
        value = default
    if required and not value:
        raise RuntimeConfigurationError(
            f"Configure {name} or {name}_FILE"
        )
    return value


def read_flag(name: str, *, default: bool = False) -> bool:
    direct = os.environ.get(name)
    file_name = os.environ.get(f"{name}_FILE")
    if direct is not None and file_name:
        raise RuntimeConfigurationError(
            f"Configure exactly one of {name} or {name}_FILE"
        )
    if file_name:
        try:
            raw = Path(file_name).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise RuntimeConfigurationError(
                f"Unable to read configured control file for {name}"
            ) from exc
    else:
        raw = direct
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in TRUTHY:
        return True
    if normalized in FALSY:
        return False
    raise RuntimeConfigurationError(f"{name} must be a boolean value")


def read_int(
    name: str,
    *,
    default: int,
    minimum: Optional[int] = None,
    maximum: Optional[int] = None,
) -> int:
    raw = os.environ.get(name)
    try:
        value = default if raw is None else int(raw)
    except ValueError as exc:
        raise RuntimeConfigurationError(f"{name} must be an integer") from exc
    if minimum is not None and value < minimum:
        raise RuntimeConfigurationError(f"{name} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise RuntimeConfigurationError(f"{name} must be at most {maximum}")
    return value
