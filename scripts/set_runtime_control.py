#!/usr/bin/env python3
"""Atomically update one reviewed production runtime control file."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import tempfile


CONTROL_NAMES = frozenset(
    {
        "outbound_hard_pause",
        "acquisition_hard_pause",
        "linkedin_hard_pause",
        "whatsapp_hard_pause",
        "webhook_reject_all",
    }
)
_CHANGE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{2,99}$")
_RELEASE_SHA = re.compile(r"^[0-9a-f]{7,64}$")


def update_control(
    *,
    directory: Path,
    control: str,
    value: bool,
    change_id: str,
    approved_release_sha: str | None,
) -> dict[str, object]:
    if control not in CONTROL_NAMES:
        raise ValueError("Unknown runtime control")
    if not _CHANGE_ID.fullmatch(change_id):
        raise ValueError("change_id must be a non-secret approved change identifier")
    if not value and (
        approved_release_sha is None
        or _RELEASE_SHA.fullmatch(approved_release_sha) is None
    ):
        raise ValueError("Releasing a control requires an approved release SHA")

    root = directory.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Runtime control directory must already exist")
    target = root / control
    previous: str | None = None
    try:
        metadata = target.lstat()
    except FileNotFoundError:
        metadata = None
    if metadata is not None:
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("Runtime control target must be a regular file")
        previous = target.read_text(encoding="utf-8").strip().lower()
        if previous not in {"true", "false"}:
            raise ValueError("Existing runtime control has an invalid value")

    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=f".{control}.",
            dir=root,
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            os.chmod(temporary.name, 0o400)
            temporary.write("true\n" if value else "false\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, target)
        temporary_name = None
        directory_fd = os.open(root, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass

    return {
        "status": "updated",
        "control": control,
        "previous": previous,
        "current": "true" if value else "false",
        "change_id": change_id,
        "approved_release_sha": approved_release_sha,
        "changed_at": datetime.now(timezone.utc).isoformat(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--control", choices=sorted(CONTROL_NAMES), required=True)
    parser.add_argument("--value", choices=("true", "false"), required=True)
    parser.add_argument("--change-id", required=True)
    parser.add_argument("--approved-release-sha")
    args = parser.parse_args()
    try:
        result = update_control(
            directory=args.directory,
            control=args.control,
            value=args.value == "true",
            change_id=args.change_id,
            approved_release_sha=args.approved_release_sha,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
