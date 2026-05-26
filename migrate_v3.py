"""
Migration v3: Add AI acquisition pilot fields, persona qualification dimensions,
lead fit scoring, and human handoff signals.

Run: python migrate_v3.py
"""
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set. Configure .env before running migrations.")

engine = create_engine(DATABASE_URL)


def _column_exists(inspector, table: str, column: str) -> bool:
    columns = [c["name"] for c in inspector.get_columns(table)]
    return column in columns


def run():
    inspector = inspect(engine)
    migrations = []

    persona_columns = {
        "customer_types": "TEXT",
        "product_categories": "TEXT",
        "evidence_sources": "TEXT",
        "qualification_rules": "TEXT",
        "disqualification_rules": "TEXT",
        "cultural_notes": "TEXT",
        "positive_examples": "TEXT",
        "negative_examples": "TEXT",
    }
    for col, typedef in persona_columns.items():
        if not _column_exists(inspector, "customer_personas", col):
            migrations.append(f"ALTER TABLE customer_personas ADD COLUMN {col} {typedef}")

    workflow_columns = {
        "pilot_goal": "TEXT",
        "target_customer_type": "VARCHAR(255) DEFAULT NULL",
        "target_region": "VARCHAR(255) DEFAULT NULL",
        "product_focus": "VARCHAR(255) DEFAULT NULL",
        "manual_handoff_triggers": "TEXT",
    }
    for col, typedef in workflow_columns.items():
        if not _column_exists(inspector, "workflows", col):
            migrations.append(f"ALTER TABLE workflows ADD COLUMN {col} {typedef}")

    lead_columns = {
        "fit_score": "INT DEFAULT NULL",
        "fit_grade": "VARCHAR(5) DEFAULT NULL",
        "qualification_notes": "TEXT",
        "handoff_recommended": "BOOLEAN DEFAULT FALSE",
        "source_channel": "VARCHAR(50) DEFAULT NULL",
        "data_sources": "TEXT",
    }
    for col, typedef in lead_columns.items():
        if not _column_exists(inspector, "leads", col):
            migrations.append(f"ALTER TABLE leads ADD COLUMN {col} {typedef}")

    if not migrations:
        print("Database is already up to date. No migrations needed.")
        return

    print(f"Running {len(migrations)} migration(s)...")
    with engine.begin() as conn:
        for i, sql in enumerate(migrations, 1):
            print(f"  [{i}/{len(migrations)}] {sql}")
            conn.execute(text(sql))

    print("All migrations applied successfully.")


if __name__ == "__main__":
    run()
