"""Migration v7: Add commercial credit wallets and transaction ledger.

Run:
    python migrate_v7.py
"""
import os

from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect, text

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set. Configure .env before running migrations.")

engine = create_engine(DATABASE_URL)


def _initial_balance() -> int:
    try:
        return max(0, int(os.environ.get("CREDITS_DEFAULT_BALANCE", "100")))
    except (TypeError, ValueError):
        return 100


def run():
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    with engine.begin() as conn:
        if "credit_wallets" not in tables:
            print("Creating credit_wallets table...")
            conn.execute(text("""
                CREATE TABLE credit_wallets (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id INT NOT NULL,
                    balance INT NOT NULL DEFAULT 0,
                    lifetime_granted INT NOT NULL DEFAULT 0,
                    lifetime_used INT NOT NULL DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    UNIQUE KEY uq_credit_wallets_user_id (user_id),
                    INDEX ix_credit_wallets_user_id (user_id),
                    CONSTRAINT fk_credit_wallets_user_id
                      FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """))
        else:
            print("OK   credit_wallets already exists")

        if "credit_transactions" not in tables:
            print("Creating credit_transactions table...")
            conn.execute(text("""
                CREATE TABLE credit_transactions (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    wallet_id INT NOT NULL,
                    user_id INT NOT NULL,
                    amount INT NOT NULL,
                    balance_after INT NOT NULL,
                    transaction_type VARCHAR(50) NOT NULL,
                    action VARCHAR(100) NOT NULL,
                    description VARCHAR(500) NULL,
                    reference_type VARCHAR(100) NULL,
                    reference_id VARCHAR(100) NULL,
                    metadata_json JSON NULL,
                    created_by_user_id INT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    INDEX ix_credit_transactions_wallet_id (wallet_id),
                    INDEX ix_credit_transactions_user_id (user_id),
                    INDEX ix_credit_transactions_user_created (user_id, created_at),
                    INDEX ix_credit_transactions_action_created (action, created_at),
                    CONSTRAINT fk_credit_transactions_wallet_id
                      FOREIGN KEY (wallet_id) REFERENCES credit_wallets(id) ON DELETE CASCADE,
                    CONSTRAINT fk_credit_transactions_user_id
                      FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    CONSTRAINT fk_credit_transactions_created_by
                      FOREIGN KEY (created_by_user_id) REFERENCES users(id) ON DELETE SET NULL
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """))
        else:
            print("OK   credit_transactions already exists")

        initial_balance = _initial_balance()
        users = conn.execute(text("""
            SELECT u.id
            FROM users u
            LEFT JOIN credit_wallets cw ON cw.user_id = u.id
            WHERE cw.id IS NULL
        """)).mappings().all()

        for row in users:
            user_id = row["id"]
            result = conn.execute(text("""
                INSERT INTO credit_wallets (user_id, balance, lifetime_granted, lifetime_used)
                VALUES (:user_id, :balance, :balance, 0)
            """), {"user_id": user_id, "balance": initial_balance})
            wallet_id = result.lastrowid
            if initial_balance > 0:
                conn.execute(text("""
                    INSERT INTO credit_transactions (
                        wallet_id, user_id, amount, balance_after, transaction_type,
                        action, description, reference_type
                    )
                    VALUES (
                        :wallet_id, :user_id, :amount, :amount, 'grant',
                        'initial_grant', 'Initial credit balance', 'migration'
                    )
                """), {"wallet_id": wallet_id, "user_id": user_id, "amount": initial_balance})
            print(f"Seeded credit wallet for user #{user_id} with {initial_balance} credits")

    print("Migration completed successfully.")


if __name__ == "__main__":
    run()
