"""Add reporting indexes for API usage and dashboard pages.

Run with:
    python3 migrate_v6.py
"""
from sqlalchemy import text

from database import engine


INDEXES = [
    (
        "leads",
        "ix_leads_created_source_channel",
        "CREATE INDEX ix_leads_created_source_channel ON leads (created_at, source_channel)",
    ),
    (
        "leads",
        "ix_leads_updated_at",
        "CREATE INDEX ix_leads_updated_at ON leads (updated_at)",
    ),
    (
        "message_logs",
        "ix_message_logs_direction_sent_channel",
        "CREATE INDEX ix_message_logs_direction_sent_channel ON message_logs (direction, sent_at, channel)",
    ),
    (
        "email_logs",
        "ix_email_logs_direction_sent_at",
        "CREATE INDEX ix_email_logs_direction_sent_at ON email_logs (direction, sent_at)",
    ),
    (
        "processed_domains",
        "ix_processed_domains_created_at",
        "CREATE INDEX ix_processed_domains_created_at ON processed_domains (created_at)",
    ),
    (
        "lead_briefs",
        "ix_lead_briefs_created_at",
        "CREATE INDEX ix_lead_briefs_created_at ON lead_briefs (created_at)",
    ),
    (
        "chat_messages",
        "ix_chat_messages_role_created_at",
        "CREATE INDEX ix_chat_messages_role_created_at ON chat_messages (role, created_at)",
    ),
    (
        "channel_accounts",
        "ix_channel_accounts_status",
        "CREATE INDEX ix_channel_accounts_status ON channel_accounts (status)",
    ),
]


def _index_exists(conn, table: str, index_name: str) -> bool:
    return conn.execute(
        text(f"SHOW INDEX FROM `{table}` WHERE Key_name = :index_name"),
        {"index_name": index_name},
    ).first() is not None


def main():
    with engine.begin() as conn:
        for table, index_name, ddl in INDEXES:
            if _index_exists(conn, table, index_name):
                print(f"OK   {index_name} already exists")
                continue
            print(f"ADD  {index_name}")
            conn.execute(text(ddl))
    print("Done.")


if __name__ == "__main__":
    main()
