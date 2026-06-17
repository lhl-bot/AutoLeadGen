"""Migration v9: Add customer_personas.company_size (target company-size buckets).

Idempotent — safe to run repeatedly.

Run:
    python migrate_v9.py
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
    if "customer_personas" not in set(inspector.get_table_names()):
        print("SKIP customer_personas table does not exist yet (create_all will build it).")
        return

    columns = {c["name"] for c in inspector.get_columns("customer_personas")}
    with engine.begin() as conn:
        if "company_size" not in columns:
            print("Adding customer_personas.company_size ...")
            conn.execute(text("ALTER TABLE customer_personas ADD COLUMN company_size TEXT NULL"))
        else:
            print("OK   customer_personas.company_size already exists")

    print("Migration completed successfully.")


if __name__ == "__main__":
    run()
