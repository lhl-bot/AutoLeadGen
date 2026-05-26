"""
Migration v4: Add multi-source lead discovery controls to workflows.

Run: python migrate_v4.py
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

    workflow_columns = {
        "search_sources": "TEXT",
        "competitor_names": "TEXT",
        "trade_show_names": "TEXT",
    }
    for col, typedef in workflow_columns.items():
        if not _column_exists(inspector, "workflows", col):
            migrations.append(f"ALTER TABLE workflows ADD COLUMN {col} {typedef}")

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
