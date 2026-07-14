"""Migration v10: Add configurable cold follow-up sequence to workflows.

Adds the nullable JSON column `workflows.followup_steps`. When set, it stores an
ordered list of ``{"day_offset": int, "instruction": str | null}`` describing the
cold follow-up cadence, overriding the legacy max_followups + global interval.

Idempotent — safe to run repeatedly.

Run:
    python migrate_v10.py
"""
import os

from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set. Configure .env before running migrations.")

engine = create_engine(DATABASE_URL)


def run():
    inspector = inspect(engine)
    if "workflows" not in set(inspector.get_table_names()):
        print("SKIP workflows table does not exist yet (create_all will build it).")
        return

    columns = {c["name"] for c in inspector.get_columns("workflows")}
    with engine.begin() as conn:
        if "followup_steps" not in columns:
            print("Adding workflows.followup_steps column...")
            conn.execute(text("ALTER TABLE workflows ADD COLUMN followup_steps JSON NULL"))
        else:
            print("OK   workflows.followup_steps already exists")

    print("Migration completed successfully.")


if __name__ == "__main__":
    run()
