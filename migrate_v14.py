"""Migration v14: Outbound CRM webhooks.

Adds the crm_webhooks table for pushing lead events to external CRMs/endpoints.

Idempotent — safe to run repeatedly.

Run:
    python migrate_v14.py
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

    with engine.begin() as conn:
        if "crm_webhooks" not in tables:
            print("Creating crm_webhooks table...")
            conn.execute(text("""
                CREATE TABLE crm_webhooks (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id INT NOT NULL,
                    name VARCHAR(255) NOT NULL,
                    url VARCHAR(1000) NOT NULL,
                    secret VARCHAR(255) NULL,
                    events VARCHAR(500) DEFAULT 'lead.won',
                    is_active TINYINT(1) DEFAULT 1,
                    last_status INT NULL,
                    last_error TEXT NULL,
                    last_delivered_at DATETIME NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    INDEX ix_crm_webhooks_user_id (user_id),
                    INDEX ix_crm_webhooks_user_active (user_id, is_active),
                    CONSTRAINT fk_crm_webhooks_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """))
        else:
            print("OK   crm_webhooks already exists")

    print("Migration completed successfully.")


if __name__ == "__main__":
    run()
