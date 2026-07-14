"""Migration v13: In-app notifications.

Adds the notifications table for high-intent reply / send-failure / low-balance alerts.

Idempotent — safe to run repeatedly.

Run:
    python migrate_v13.py
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
        if "notifications" not in tables:
            print("Creating notifications table...")
            conn.execute(text("""
                CREATE TABLE notifications (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id INT NOT NULL,
                    type VARCHAR(50) NOT NULL,
                    title VARCHAR(255) NOT NULL,
                    body TEXT NULL,
                    link VARCHAR(255) NULL,
                    reference_type VARCHAR(50) NULL,
                    reference_id INT NULL,
                    is_read TINYINT(1) NOT NULL DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    INDEX ix_notifications_user_id (user_id),
                    INDEX ix_notifications_user_read_created (user_id, is_read, created_at),
                    CONSTRAINT fk_notifications_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """))
        else:
            print("OK   notifications already exists")

    print("Migration completed successfully.")


if __name__ == "__main__":
    run()
