from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_ENTRYPOINTS = (
    "bootstrap_local_acceptance.py",
    "bootstrap_production_admin.py",
    "database_fingerprint.py",
    "probe_production_email_account.py",
    "production_backfill.py",
    "production_migrate.py",
    "production_preflight.py",
    "rotate_smtp_encryption_key.py",
    "worker_healthcheck.py",
)


def test_production_script_entrypoints_add_project_root_before_local_imports():
    for name in PRODUCTION_ENTRYPOINTS:
        source = (ROOT / "scripts" / name).read_text(encoding="utf-8")
        assert source.index("sys.path.insert(0, str(ROOT))") < source.index(
            "from database"
        ), name


def test_preflight_script_is_directly_executable_from_repository_root():
    completed = subprocess.run(
        [sys.executable, "scripts/production_preflight.py", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "--phase" in completed.stdout
