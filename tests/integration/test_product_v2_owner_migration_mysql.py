"""MySQL-only owner write-path serialization checks."""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session as SQLAlchemySession, sessionmaker

import models as legacy
from product_v2 import models
from product_v2.enums import OwnerWritePath
from product_v2.migration_state import (
    MIGRATION_AUDIT_ACTION,
    MIGRATION_JOB_TYPE,
    OwnerMigrationConflict,
    preview_owner_path,
    read_owner_migration_state,
    serialize_owner_write_path,
    switch_owner_path,
)


MYSQL_URL = os.environ.get("PRODUCT_V2_MYSQL_TEST_URL")
pytestmark = pytest.mark.mysql


def _mysql_url() -> str:
    if not MYSQL_URL:
        pytest.skip("PRODUCT_V2_MYSQL_TEST_URL is not configured")
    url = make_url(MYSQL_URL)
    if not url.drivername.startswith("mysql"):
        pytest.fail("PRODUCT_V2_MYSQL_TEST_URL must use MySQL")
    if not (url.database or "").endswith("_test"):
        pytest.fail("Refusing to test against a database whose name does not end in _test")
    return MYSQL_URL


def _session_factory(monkeypatch):
    url = _mysql_url()
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("AUTOLEADGEN_ENV", "test")
    monkeypatch.setenv("PRODUCT_V2_OWNER_FENCE_TIMEOUT_SECONDS", "5")
    command.upgrade(
        Config(str(Path(__file__).parents[2] / "alembic.ini")),
        "head",
    )
    engine = create_engine(url, pool_pre_ping=True)
    return engine, sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _cleanup_owner(Session, owner_id: int) -> None:
    db = Session()
    try:
        db.query(models.SafetyLock).filter_by(owner_id=owner_id).delete(
            synchronize_session=False
        )
        db.query(models.ProviderCostEvent).filter_by(owner_id=owner_id).delete(
            synchronize_session=False
        )
        db.query(models.AuditEvent).filter_by(owner_id=owner_id).delete(
            synchronize_session=False
        )
        db.query(models.AutomationJob).filter_by(owner_id=owner_id).delete(
            synchronize_session=False
        )
        db.query(models.OwnerMigrationState).filter_by(owner_id=owner_id).delete(
            synchronize_session=False
        )
        db.query(models.ChannelAccount).filter_by(owner_id=owner_id).delete(
            synchronize_session=False
        )
        db.query(legacy.CustomerPersona).filter_by(user_id=owner_id).delete(
            synchronize_session=False
        )
        db.query(legacy.User).filter_by(id=owner_id).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def test_mysql_concurrent_owner_switch_has_one_cas_winner(monkeypatch):
    engine, Session = _session_factory(monkeypatch)
    suffix = uuid4().hex[:12]
    seed = Session()
    user = legacy.User(
        username=f"mysql-owner-switch-{suffix}",
        hashed_password="x",
        is_active=True,
    )
    seed.add(user)
    seed.commit()
    owner_id = user.id
    preview = preview_owner_path(
        seed,
        owner_id=owner_id,
        target_path=OwnerWritePath.V2,
    )
    seed.close()
    barrier = Barrier(2)

    def contender(index: int):
        db = Session()
        try:
            barrier.wait(timeout=5)
            state = switch_owner_path(
                db,
                owner_id=owner_id,
                actor_user_id=owner_id,
                target_path=OwnerWritePath.V2,
                expected_version=preview.expected_version,
                preview_checksum=preview.preview_checksum,
                idempotency_key=f"mysql-owner-switch-{suffix}-{index}",
            )
            db.commit()
            return ("success", state.version)
        except OwnerMigrationConflict as exc:
            db.rollback()
            return ("conflict", exc.code)
        finally:
            db.close()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(contender, (1, 2)))
        assert sorted(item[0] for item in outcomes) == ["conflict", "success"]
        assert next(item[1] for item in outcomes if item[0] == "conflict") == (
            "OWNER_PATH_VERSION_CONFLICT"
        )
        verify = Session()
        try:
            assert verify.query(models.OwnerMigrationState).filter_by(
                owner_id=owner_id
            ).count() == 1
            assert verify.query(models.AutomationJob).filter_by(
                owner_id=owner_id,
                job_type=MIGRATION_JOB_TYPE,
            ).count() == 1
            assert verify.query(models.AuditEvent).filter_by(
                owner_id=owner_id,
                action=MIGRATION_AUDIT_ACTION,
            ).count() == 1
        finally:
            verify.close()
    finally:
        _cleanup_owner(Session, owner_id)
        engine.dispose()


def test_mysql_legacy_write_completes_before_concurrent_path_switch(monkeypatch):
    # GIVEN: A legacy request owns a separate ORM session and holds only the
    # owner advisory fence, not a users-row lock that could deadlock its FK.
    engine, Session = _session_factory(monkeypatch)
    suffix = uuid4().hex[:12]
    seed = Session()
    user = legacy.User(
        username=f"mysql-owner-legacy-fence-{suffix}",
        hashed_password="x",
        is_active=True,
    )
    seed.add(user)
    seed.commit()
    owner_id = user.id
    preview = preview_owner_path(
        seed,
        owner_id=owner_id,
        target_path=OwnerWritePath.V2,
    )
    seed.close()
    legacy_has_fence = Event()
    allow_legacy_commit = Event()
    switch_finished = Event()

    def legacy_writer():
        db = Session()
        try:
            with serialize_owner_write_path(db, owner_id):
                assert read_owner_migration_state(db, owner_id).current_path == (
                    OwnerWritePath.LEGACY
                )
                legacy_has_fence.set()
                assert allow_legacy_commit.wait(timeout=5)
                db.add(
                    legacy.CustomerPersona(
                        user_id=owner_id,
                        name=f"Serialized legacy write {suffix}",
                    )
                )
                # This FK to users would self-deadlock if middleware held the
                # owner row from another connection while awaiting this route.
                db.commit()
            return "legacy_committed"
        finally:
            db.close()

    def switcher():
        db = Session()
        try:
            state = switch_owner_path(
                db,
                owner_id=owner_id,
                actor_user_id=owner_id,
                target_path=OwnerWritePath.V2,
                expected_version=preview.expected_version,
                preview_checksum=preview.preview_checksum,
                idempotency_key=f"mysql-owner-after-legacy-{suffix}",
            )
            db.commit()
            return state.current_path
        finally:
            switch_finished.set()
            db.close()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            legacy_future = pool.submit(legacy_writer)
            assert legacy_has_fence.wait(timeout=5)
            switch_future = pool.submit(switcher)
            # The switch must wait at the advisory fence, not finish early or
            # deadlock the legacy route's user FK operation.
            assert not switch_finished.wait(timeout=0.25)
            allow_legacy_commit.set()
            assert legacy_future.result(timeout=5) == "legacy_committed"
            assert switch_future.result(timeout=5) == OwnerWritePath.V2

        verify = Session()
        try:
            assert verify.query(legacy.CustomerPersona).filter_by(
                user_id=owner_id
            ).count() == 1
            assert read_owner_migration_state(verify, owner_id).current_path == (
                OwnerWritePath.V2
            )
        finally:
            verify.close()
    finally:
        allow_legacy_commit.set()
        _cleanup_owner(Session, owner_id)
        engine.dispose()


def test_mysql_switch_holds_advisory_fence_through_commit(monkeypatch):
    # GIVEN: A switch is deliberately paused after its state/audit/job flushes
    # but before the transaction commit becomes visible.
    engine, BaseSession = _session_factory(monkeypatch)
    suffix = uuid4().hex[:12]
    seed = BaseSession()
    user = legacy.User(
        username=f"mysql-owner-commit-window-{suffix}",
        hashed_password="x",
        is_active=True,
    )
    seed.add(user)
    seed.commit()
    owner_id = user.id
    preview = preview_owner_path(
        seed,
        owner_id=owner_id,
        target_path=OwnerWritePath.V2,
    )
    seed.close()
    switch_entered_commit = Event()
    allow_switch_commit = Event()
    legacy_acquired_fence = Event()

    class CommitWindowSession(SQLAlchemySession):
        pause_owner_switch_commit = False

        def commit(self):
            if self.pause_owner_switch_commit:
                switch_entered_commit.set()
                assert allow_switch_commit.wait(timeout=5)
                self.pause_owner_switch_commit = False
            return super().commit()

    SwitchSession = sessionmaker(
        bind=engine,
        class_=CommitWindowSession,
        autoflush=False,
        expire_on_commit=False,
    )

    def switcher():
        db = SwitchSession()
        db.pause_owner_switch_commit = True
        try:
            return switch_owner_path(
                db,
                owner_id=owner_id,
                actor_user_id=owner_id,
                target_path=OwnerWritePath.V2,
                expected_version=preview.expected_version,
                preview_checksum=preview.preview_checksum,
                idempotency_key=f"mysql-owner-commit-window-{suffix}",
            ).current_path
        finally:
            db.close()

    def would_be_legacy_writer():
        db = BaseSession()
        try:
            with serialize_owner_write_path(db, owner_id):
                legacy_acquired_fence.set()
                state = read_owner_migration_state(db, owner_id).current_path
                if state == OwnerWritePath.LEGACY:
                    db.add(
                        legacy.CustomerPersona(
                            user_id=owner_id,
                            name=f"Must not cross commit window {suffix}",
                        )
                    )
                    db.commit()
                return state
        finally:
            db.close()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            switch_future = pool.submit(switcher)
            assert switch_entered_commit.wait(timeout=5)
            legacy_future = pool.submit(would_be_legacy_writer)

            # WHEN: The new state is flushed but not committed, the advisory
            # fence must remain owned by the switch connection.
            assert not legacy_acquired_fence.wait(timeout=0.25)
            allow_switch_commit.set()

            # THEN: The switch commits first; the legacy request subsequently
            # observes V2 and cannot write through the old path.
            assert switch_future.result(timeout=5) == OwnerWritePath.V2
            assert legacy_future.result(timeout=5) == OwnerWritePath.V2

        verify = BaseSession()
        try:
            assert read_owner_migration_state(verify, owner_id).current_path == (
                OwnerWritePath.V2
            )
            assert verify.query(legacy.CustomerPersona).filter_by(
                user_id=owner_id
            ).count() == 0
        finally:
            verify.close()
    finally:
        allow_switch_commit.set()
        _cleanup_owner(BaseSession, owner_id)
        engine.dispose()
