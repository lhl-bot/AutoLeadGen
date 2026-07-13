import os

os.environ["AUTOLEADGEN_ENV"] = "test"
os.environ["DATABASE_URL"] = "sqlite+pysqlite://"
os.environ["JWT_SECRET_KEY"] = "test-secret"
os.environ["AUTH_USER_CACHE_TTL_SECONDS"] = "0"

import pytest

from database import Base, SessionLocal, engine
import models  # noqa: F401


@pytest.fixture(autouse=True)
def _reset_engine_state():
    """Clear in-memory LeadContact engine state so it can't leak between tests.

    Workflow ids reset to 1 each test (tables are recreated), but these module-level
    dicts persist, so without this a prior test's cursor/backoff/rotation pollutes
    the next one.
    """
    try:
        from services import outbound_engine as oe
        for d in (
            oe._leadcontact_cursor,
            oe._leadcontact_backoff_until,
            oe._leadcontact_kw_rotation,
            oe._leadcontact_search_calls,
        ):
            d.clear()
    except Exception:
        pass
    yield


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
