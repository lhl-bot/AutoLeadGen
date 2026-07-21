import json
import os
from pathlib import Path
import subprocess
import sys

from product_v2.shadow_replay import run_shadow_replay


def test_thirty_company_shadow_replay_is_fake_safe_and_traceable(db_session):
    # GIVEN: An empty isolated Product V2 database and the fake connector kill switch.
    # WHEN: Replaying 30 synthetic companies across email, LinkedIn, and WhatsApp.
    report = run_shadow_replay(db_session, company_count=30, run_id="pytest-shadow-30")

    # THEN: No external or duplicate call escapes, hard gates hold, and every outcome is traceable.
    assert report.passed is True
    assert report.companies == 30
    assert report.attempts == 30
    assert report.succeeded == 28
    assert report.blocked == 2
    assert report.external_calls == 0
    assert report.duplicate_attempts == 0
    assert report.duplicate_provider_messages == 0
    assert report.hard_gate_bypasses == 0
    assert report.traceable_attempts == 30
    assert report.account_traceable_attempts == 30
    assert report.message_events == 28
    assert report.cost_events == 28
    assert report.tasks == 2
    assert report.attempt_audits == 30
    assert report.heartbeat_stage_mismatches == 0
    assert report.real_provider_events == 0
    assert report.billable_events == 0


def test_shadow_replay_cli_ignores_application_database_url(tmp_path):
    # GIVEN: The application environment points at a database outside the replay workspace.
    project_root = Path(__file__).resolve().parents[1]
    application_database = tmp_path / "application-do-not-touch.db"
    output = tmp_path / "acceptance.json"
    environment = os.environ.copy()
    environment.update(
        {
            "AUTOLEADGEN_ENV": "test",
            "AUTOLEADGEN_CONNECTOR_MODE": "fake",
            "ALLOW_REAL_EXTERNAL_CALLS": "false",
            "DATABASE_URL": f"sqlite+pysqlite:///{application_database}",
        }
    )

    # WHEN: The CLI executes a small acceptance replay.
    completed = subprocess.run(
        [
            sys.executable,
            str(project_root / "scripts" / "shadow_replay_product_v2.py"),
            "--company-count",
            "6",
            "--output",
            str(output),
        ],
        cwd=project_root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    # THEN: Only the output-adjacent isolated DB exists, and the app DB is untouched.
    assert completed.returncode == 0
    assert output.with_suffix(".db").exists()
    assert application_database.exists() is False
    assert json.loads(output.read_text(encoding="utf-8"))["passed"] is True
