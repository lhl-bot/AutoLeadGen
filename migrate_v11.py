"""Migration v11: Email templates with A/B testing.

Adds:
  - email_templates table
  - leads.template_id, leads.template_variant (A/B attribution)
  - workflows.template_id (use a template/A-B group instead of pure AI)

Idempotent — safe to run repeatedly.

Run:
    python migrate_v11.py
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
        if "email_templates" not in tables:
            print("Creating email_templates table...")
            conn.execute(text("""
                CREATE TABLE email_templates (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id INT NOT NULL,
                    name VARCHAR(255) NOT NULL,
                    category VARCHAR(50) DEFAULT 'cold',
                    ab_group VARCHAR(100) NULL,
                    variant_label VARCHAR(20) DEFAULT 'A',
                    subject VARCHAR(500) NULL,
                    body TEXT NOT NULL,
                    weight INT DEFAULT 1,
                    is_active TINYINT(1) DEFAULT 1,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    INDEX ix_email_templates_user_id (user_id),
                    INDEX ix_email_templates_ab_group (ab_group),
                    INDEX ix_email_templates_user_group (user_id, ab_group),
                    CONSTRAINT fk_email_templates_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """))
        else:
            print("OK   email_templates already exists")

        lead_cols = {c["name"] for c in inspector.get_columns("leads")} if "leads" in tables else set()
        if "leads" in tables and "template_id" not in lead_cols:
            print("Adding leads.template_id ...")
            conn.execute(text("ALTER TABLE leads ADD COLUMN template_id INT NULL, ADD INDEX ix_leads_template_id (template_id)"))
        if "leads" in tables and "template_variant" not in lead_cols:
            print("Adding leads.template_variant ...")
            conn.execute(text("ALTER TABLE leads ADD COLUMN template_variant VARCHAR(20) NULL"))

        wf_cols = {c["name"] for c in inspector.get_columns("workflows")} if "workflows" in tables else set()
        if "workflows" in tables and "template_id" not in wf_cols:
            print("Adding workflows.template_id ...")
            conn.execute(text("ALTER TABLE workflows ADD COLUMN template_id INT NULL"))

    print("Migration completed successfully.")


if __name__ == "__main__":
    run()
