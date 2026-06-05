"""
Migration v5: Add suppression list for unsubscribe and do-not-contact controls.

Run: python migrate_v5.py
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
    if "email_suppressions" in inspector.get_table_names():
        print("Database is already up to date. No migrations needed.")
        return

    create_table = """
    CREATE TABLE email_suppressions (
        id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT NULL,
        lead_id INT NULL,
        email VARCHAR(255) NULL,
        domain VARCHAR(255) NULL,
        reason VARCHAR(100) NOT NULL DEFAULT 'manual',
        source VARCHAR(100) NOT NULL DEFAULT 'system',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        INDEX ix_email_suppressions_user_id (user_id),
        INDEX ix_email_suppressions_lead_id (lead_id),
        INDEX ix_email_suppressions_email (email),
        INDEX ix_email_suppressions_domain (domain),
        UNIQUE KEY uq_email_suppression_user_email (user_id, email),
        UNIQUE KEY uq_email_suppression_user_domain (user_id, domain),
        CONSTRAINT fk_email_suppressions_user_id FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL,
        CONSTRAINT fk_email_suppressions_lead_id FOREIGN KEY (lead_id) REFERENCES leads(id) ON DELETE SET NULL
    ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """

    print("Creating email_suppressions table...")
    with engine.begin() as conn:
        conn.execute(text(create_table))
    print("Migration completed successfully.")


if __name__ == "__main__":
    run()
