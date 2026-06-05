from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import sessionmaker

import os
from dotenv import load_dotenv
load_dotenv(override=True)

# DATABASE_URL must be set in .env
SQLALCHEMY_DATABASE_URL = os.environ.get("DATABASE_URL")
if not SQLALCHEMY_DATABASE_URL:
    raise RuntimeError("❌ DATABASE_URL is not set! Please configure it in your .env file. Example: mysql+pymysql://user:pass@host:3306/dbname?charset=utf8mb4")

import time
import functools
import asyncio
from sqlalchemy.exc import OperationalError

# Remove NullPool to enable connection pooling (QueuePool is default)
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_recycle=int(os.environ.get("DB_POOL_RECYCLE_SECONDS", "1800")),
    pool_use_lifo=True,
    pool_pre_ping=True,    # Ping before using to avoid "MySQL server has gone away"
    connect_args={
        'connect_timeout': 10,
        'read_timeout': 60,     # Allow longer queries for large tables
        'write_timeout': 30,
    }
)
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
