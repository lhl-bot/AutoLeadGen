import os

os.environ["AUTOLEADGEN_ENV"] = "test"
os.environ["DATABASE_URL"] = "sqlite+pysqlite://"
os.environ["JWT_SECRET_KEY"] = "test-secret"
os.environ["AUTH_USER_CACHE_TTL_SECONDS"] = "0"

import pytest

from database import Base, SessionLocal, engine
import models  # noqa: F401


@pytest.fixture()
def db_session():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
