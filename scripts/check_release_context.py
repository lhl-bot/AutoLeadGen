#!/usr/bin/env python3
"""Fail CI if release-tracked files contain forbidden evidence or strong secrets."""
from __future__ import annotations

import argparse
from pathlib import Path, PurePosixPath
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_RELEASE_PATTERNS = (
    "backup_workflow18_scope.py",
    "migrate_v16.py",
    "prepare_workflow18_*.py",
    "verify_workflow18_*.py",
    "workflow18_*.md",
    "workflow18_*.json",
    "jysk_supplier_call_*.md",
    "product_logic_audit_*.ipynb",
    "product_logic_audit_*.json",
)
FORBIDDEN_FILE_PATTERNS = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*credentials*.json",
)
SECRET_PATTERNS = (
    ("private_key", re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("aws_access_key", re.compile(rb"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", re.compile(rb"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b")),
    ("slack_token", re.compile(rb"\bxox(?:a|b|p|r|s)-[A-Za-z0-9-]{20,}\b")),
    ("openai_key", re.compile(rb"\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}\b")),
)
MAX_SCAN_BYTES = 2 * 1024 * 1024


def tracked_files(root: Path = ROOT) -> list[Path]:
    completed = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return [root / item.decode("utf-8") for item in completed.stdout.split(b"\0") if item]


def _matches(path: Path, patterns: tuple[str, ...], *, root: Path) -> bool:
    relative = PurePosixPath(path.relative_to(root).as_posix())
    return any(relative.match(pattern) for pattern in patterns)


def release_context_findings(files: list[Path], *, root: Path = ROOT) -> list[str]:
    findings: list[str] = []
    for path in files:
        relative = path.relative_to(root).as_posix()
        if _matches(path, FORBIDDEN_RELEASE_PATTERNS, root=root):
            findings.append(f"forbidden_release_evidence:{relative}")
            continue
        if relative != ".env.example" and _matches(
            path, FORBIDDEN_FILE_PATTERNS, root=root
        ):
            findings.append(f"forbidden_sensitive_filename:{relative}")
            continue
        try:
            if not path.is_file() or path.stat().st_size > MAX_SCAN_BYTES:
                continue
            payload = path.read_bytes()
        except OSError:
            findings.append(f"unreadable_tracked_file:{relative}")
            continue
        for name, pattern in SECRET_PATTERNS:
            if pattern.search(payload):
                findings.append(f"strong_secret_pattern:{name}:{relative}")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.parse_args()
    findings = release_context_findings(tracked_files(), root=ROOT)
    if findings:
        for finding in findings:
            print(finding)
        return 1
    print("Release context contains no forbidden evidence or strong secret pattern")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
