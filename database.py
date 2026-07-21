from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import sessionmaker

import os
from dotenv import load_dotenv
from runtime_config import read_secret
# Explicit process/container configuration must always win over repository .env.
# Overriding it can redirect local migrations or backfills to an unrelated DB.
load_dotenv(override=False)

# Direct environment variables remain convenient locally; production can mount
# a secret and set DATABASE_URL_FILE without exposing credentials in process
# listings, Compose files, or checked-in env files.
SQLALCHEMY_DATABASE_URL = read_secret("DATABASE_URL", required=True)

import time
import functools
import asyncio
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import StaticPool


def _engine_options(database_url: str) -> dict:
    if database_url.startswith("sqlite"):
        options = {
            "connect_args": {"check_same_thread": False},
        }
        if database_url in {"sqlite://", "sqlite:///:memory:", "sqlite+pysqlite://", "sqlite+pysqlite:///:memory:"}:
            options["poolclass"] = StaticPool
        return options

    return {
        "pool_size": 10,
        "max_overflow": 20,
        "pool_recycle": int(os.environ.get("DB_POOL_RECYCLE_SECONDS", "1800")),
        "pool_use_lifo": True,
        "pool_pre_ping": True,    # Ping before using to avoid "MySQL server has gone away"
        "connect_args": {
            "connect_timeout": 10,
            "read_timeout": 60,     # Allow longer queries for large tables
            "write_timeout": 30,
        },
    }


# Remove NullPool to enable connection pooling (QueuePool is default)
engine = create_engine(SQLALCHEMY_DATABASE_URL, **_engine_options(SQLALCHEMY_DATABASE_URL))
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def db_retry(max_attempts=3, delay=2, backoff=2):
    """
    Decorator to retry database functions on SQLAlchemy OperationalError.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            attempts = 0
            current_delay = delay
            while attempts < max_attempts:
                try:
                    return func(*args, **kwargs)
                except OperationalError as e:
                    attempts += 1
                    if attempts >= max_attempts:
                        raise e
                    print(f"⚠️ Database OperationalError caught in {func.__name__}: {e}. Retrying ({attempts}/{max_attempts}) in {current_delay}s...")
                    time.sleep(current_delay)
                    current_delay *= backoff
        return wrapper
    return decorator

def db_retry_async(max_attempts=3, delay=2, backoff=2):
    """
    Async decorator to retry database functions on SQLAlchemy OperationalError.
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            attempts = 0
            current_delay = delay
            while attempts < max_attempts:
                try:
                    return await func(*args, **kwargs)
                except OperationalError as e:
                    attempts += 1
                    if attempts >= max_attempts:
                        raise e
                    print(f"⚠️ Async Database OperationalError caught in {func.__name__}: {e}. Retrying ({attempts}/{max_attempts}) in {current_delay}s...")
                    await asyncio.sleep(current_delay)
                    current_delay *= backoff
        return wrapper
    return decorator

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
