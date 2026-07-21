import os
from pathlib import Path
import subprocess
import sys


def test_explicit_database_url_cannot_be_overridden_by_dotenv():
    # GIVEN: A local process with an explicit isolated database URL.
    project_root = Path(__file__).resolve().parents[1]
    explicit_url = "sqlite+pysqlite:///:memory:"
    environment = os.environ.copy()
    environment.update({"AUTOLEADGEN_ENV": "local", "DATABASE_URL": explicit_url})

    # WHEN: The database module loads alongside the repository .env file.
    completed = subprocess.run(
        [sys.executable, "-c", "import database; print(database.SQLALCHEMY_DATABASE_URL)"],
        cwd=project_root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    # THEN: The explicit process URL remains authoritative.
    assert completed.stdout.strip() == explicit_url
