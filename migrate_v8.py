"""Migration v8: Add Snov.io usage audit events.

Run:
    python migrate_v8.py
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
        if "snovio_usage_events" not in tables:
            print("Creating snovio_usage_events table...")
            conn.execute(text("""
                CREATE TABLE snovio_usage_events (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    endpoint VARCHAR(120) NOT NULL,
                    domain VARCHAR(255) NULL,
                    email VARCHAR(255) NULL,
                    status VARCHAR(50) NULL,
                    result_count INT NOT NULL DEFAULT 0,
                    estimated_credits INT NULL,
                    metadata_json JSON NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    INDEX ix_snovio_usage_events_endpoint (endpoint),
                    INDEX ix_snovio_usage_events_domain (domain),
                    INDEX ix_snovio_usage_events_email (email),
                    INDEX ix_snovio_usage_events_created_endpoint (created_at, endpoint),
                    INDEX ix_snovio_usage_events_domain_created (domain, created_at)
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """))
        else:
            print("OK   snovio_usage_events already exists")

    print("Migration completed successfully.")


if __name__ == "__main__":
    run()
