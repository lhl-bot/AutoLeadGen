"""Migration v12: Add manual sales-pipeline stage to leads.

Adds the nullable column `leads.sales_stage` (default 'new') used by the kanban
sales board, independent of the automation `status`.

Idempotent — safe to run repeatedly.

Run:
    python migrate_v12.py
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
    if "leads" not in set(inspector.get_table_names()):
        print("SKIP leads table does not exist yet (create_all will build it).")
        return

    columns = {c["name"] for c in inspector.get_columns("leads")}
    with engine.begin() as conn:
        if "sales_stage" not in columns:
            print("Adding leads.sales_stage column...")
            conn.execute(text("ALTER TABLE leads ADD COLUMN sales_stage VARCHAR(30) DEFAULT 'new', ADD INDEX ix_leads_sales_stage (sales_stage)"))
        else:
            print("OK   leads.sales_stage already exists")

    print("Migration completed successfully.")


if __name__ == "__main__":
    run()
