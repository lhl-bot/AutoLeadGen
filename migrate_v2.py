"""
Migration v2: Add feedback loop, timezone, playbook support, and email verification fields.

Run: python migrate_v2.py
"""
import os
import sys
from sqlalchemy import create_engine, text, inspect

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("Error: DATABASE_URL not set. Please set it in your .env file or as an environment variable.")
    sys.exit(1)

engine = create_engine(DATABASE_URL)


def _column_exists(inspector, table: str, column: str) -> bool:
    columns = [c["name"] for c in inspector.get_columns(table)]
    return column in columns


def _table_exists(inspector, table: str) -> bool:
    return table in inspector.get_table_names()


def run():
    inspector = inspect(engine)

    migrations = []

    # ── Lead table additions ──
    lead_columns = {
        "user_rating": "VARCHAR(20) DEFAULT NULL",
        "email_verified": "BOOLEAN DEFAULT FALSE",
        "email_validation_status": "VARCHAR(50) DEFAULT NULL",
        "timezone": "VARCHAR(50) DEFAULT NULL",
    }
    for col, typedef in lead_columns.items():
        if not _column_exists(inspector, "leads", col):
            migrations.append(f"ALTER TABLE leads ADD COLUMN {col} {typedef}")

    # ── Workflow table additions ──
    wf_columns = {
        "playbook_type": "VARCHAR(50) DEFAULT 'standard'",
        "domain_warmup_enabled": "BOOLEAN DEFAULT FALSE",
    }
    for col, typedef in wf_columns.items():
        if not _column_exists(inspector, "workflows", col):
            migrations.append(f"ALTER TABLE workflows ADD COLUMN {col} {typedef}")

    # ── LeadFeedback table ──
    if not _table_exists(inspector, "lead_feedbacks"):
        migrations.append("""
            CREATE TABLE lead_feedbacks (
                id INTEGER PRIMARY KEY AUTO_INCREMENT,
                user_id INTEGER NOT NULL,
                lead_id INTEGER NOT NULL,
                workflow_id INTEGER,
                rating VARCHAR(20) NOT NULL,
                reason TEXT,
                lead_snapshot JSON,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (lead_id) REFERENCES leads(id)
            )
        """)

    if not migrations:
        print("✅ Database is already up to date. No migrations needed.")
        return

    print(f"🔄 Running {len(migrations)} migration(s)...")
    with engine.begin() as conn:
        for i, sql in enumerate(migrations, 1):
            sql_preview = sql.strip().split("\n")[0][:80]
            print(f"  [{i}/{len(migrations)}] {sql_preview}...")
            conn.execute(text(sql))

    print("✅ All migrations applied successfully.")


if __name__ == "__main__":
    run()
