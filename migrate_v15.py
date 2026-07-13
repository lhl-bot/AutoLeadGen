"""Migration v15: Durable reply milestones.

Adds reply-history fields that remain true after the mutable automation status
moves from replied to drafted/sent. Existing human inbound messages are
backfilled; bounce/spam failure rows are intentionally excluded.

Idempotent — safe to run repeatedly.

Run:
    python migrate_v15.py
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
    tables = set(inspector.get_table_names())
    if "leads" not in tables:
        print("SKIP leads table does not exist yet (create_all will build it).")
        return

    columns = {column["name"] for column in inspector.get_columns("leads")}
    with engine.begin() as conn:
        if "has_replied" not in columns:
            print("Adding leads.has_replied column...")
            conn.execute(text(
                "ALTER TABLE leads ADD COLUMN has_replied TINYINT(1) NOT NULL DEFAULT 0, "
                "ADD INDEX ix_leads_has_replied (has_replied)"
            ))
        else:
            print("OK   leads.has_replied already exists")

        if "reply_intent" not in columns:
            print("Adding leads.reply_intent column...")
            conn.execute(text("ALTER TABLE leads ADD COLUMN reply_intent VARCHAR(50) NULL"))
        else:
            print("OK   leads.reply_intent already exists")

        if "email_logs" in tables:
            print("Backfilling reply milestones from inbound email history...")
            conn.execute(text("""
                UPDATE leads l
                SET l.has_replied = 1
                WHERE l.status NOT IN ('bounced', 'send_failed')
                  AND EXISTS (
                      SELECT 1
                      FROM email_logs el
                      WHERE el.lead_id = l.id
                        AND el.direction = 'inbound'
                        AND LOWER(el.from_email) = LOWER(l.email)
                  )
            """))
            conn.execute(text("""
                UPDATE leads
                SET reply_intent = CASE
                    WHEN status = 'unsubscribed' THEN 'unsubscribe'
                    WHEN status = 'rejected' THEN 'not_interested'
                    ELSE 'other'
                END
                WHERE has_replied = 1 AND reply_intent IS NULL
            """))

    print("Migration completed successfully.")


if __name__ == "__main__":
    run()
