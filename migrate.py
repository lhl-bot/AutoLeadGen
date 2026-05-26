import sys
from dotenv import load_dotenv
load_dotenv()
from database import engine
from sqlalchemy import text

commands = [
    "ALTER TABLE workflows ADD COLUMN enable_linkedin BOOLEAN DEFAULT FALSE;",
    "ALTER TABLE workflows ADD COLUMN enable_whatsapp BOOLEAN DEFAULT FALSE;",
    "ALTER TABLE workflows ADD COLUMN linkedin_invite_message TEXT;",
    "ALTER TABLE workflows ADD COLUMN whatsapp_message_template TEXT;",
    "ALTER TABLE workflows ADD COLUMN linkedin_daily_limit INT DEFAULT 20;",
    "ALTER TABLE leads ADD COLUMN linkedin_sent BOOLEAN DEFAULT FALSE;",
    "ALTER TABLE leads ADD COLUMN whatsapp_sent BOOLEAN DEFAULT FALSE;"
]

with engine.connect() as conn:
    for cmd in commands:
        try:
            conn.execute(text(cmd))
            print(f"Executed: {cmd}")
        except Exception as e:
            print(f"Skipped/Error on {cmd}: {e}")
    conn.commit()
print("Migration completed.")
