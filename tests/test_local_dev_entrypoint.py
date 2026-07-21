from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def test_local_dev_shell_entrypoints_are_valid_and_documented():
    for path in (ROOT / "run.sh", ROOT / "scripts" / "dev.sh"):
        completed = subprocess.run(
            ["bash", "-n", str(path)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr

    completed = subprocess.run(
        ["bash", "scripts/dev.sh", "help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "setup" in completed.stdout
    assert "start" in completed.stdout
    assert "check" in completed.stdout
    assert "AUTOLEADGEN_FRONTEND_MODE" in completed.stdout


def test_legacy_run_script_delegates_to_the_safe_local_entrypoint():
    source = (ROOT / "run.sh").read_text(encoding="utf-8")

    assert "scripts/dev.sh" in source
    assert "venv/bin/activate" not in source
    assert "--env-file .env" not in source


def test_local_entrypoint_enforces_fake_isolated_runtime_defaults():
    source = (ROOT / "scripts" / "dev.sh").read_text(encoding="utf-8")

    expected_defaults = (
        "AUTOLEADGEN_ENV=local",
        "AUTOLEADGEN_CONNECTOR_MODE=fake",
        "ALLOW_REAL_EXTERNAL_CALLS=false",
        "ALLOW_REAL_ACQUISITION_CALLS=false",
        "OUTBOUND_HARD_PAUSE=true",
        "PRODUCT_V2_ISOLATED_DATABASE=true",
    )
    for default in expected_defaults:
        assert default in source

    assert ".local/dev" in source
    assert "sqlite+pysqlite" in source
