import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base
import models as legacy
from product_v2 import models
from product_v2.enums import (
    JobStatus,
    OwnerWritePath,
    ProviderCostStatus,
    RestrictionScope,
    SafetyLockScope,
    TaskPriority,
    TaskStatus,
    TaskType,
)
from product_v2.migration_api import owner_path_write_exception
from product_v2.migration_state import (
    MIGRATION_AUDIT_ACTION,
    MIGRATION_JOB_TYPE,
    OwnerMigrationConflict,
    preview_owner_path,
    read_owner_migration_state,
    switch_owner_path,
)
from product_v2.runtime.worker import execute_job
from services.auth import create_access_token, get_current_user


def test_owner_switch_is_preview_confirmed_idempotent_and_blocks_active_rollback(
    db_session,
):
    # GIVEN: An owner with no explicit state; absence means legacy/version zero.
    user = legacy.User(
        username="owner-migration-unit",
        hashed_password="x",
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    initial = read_owner_migration_state(db_session, user.id)
    assert initial.current_path == OwnerWritePath.LEGACY
    assert initial.version == 0
    assert initial.explicit is False

    # WHEN: The reviewed preview is switched to V2 and replayed with the same
    # idempotency key.
    preview = preview_owner_path(
        db_session,
        owner_id=user.id,
        target_path=OwnerWritePath.V2,
    )
    switched = switch_owner_path(
        db_session,
        owner_id=user.id,
        actor_user_id=user.id,
        target_path=OwnerWritePath.V2,
        expected_version=preview.expected_version,
        preview_checksum=preview.preview_checksum,
        idempotency_key="owner-switch-unit-0001",
    )
    replay = switch_owner_path(
        db_session,
        owner_id=user.id,
        actor_user_id=user.id,
        target_path=OwnerWritePath.V2,
        expected_version=preview.expected_version,
        preview_checksum=preview.preview_checksum,
        idempotency_key="owner-switch-unit-0001",
    )
    assert switched == replay
    assert switched.current_path == OwnerWritePath.V2
    assert switched.version == 1
    assert db_session.query(models.OwnerMigrationState).filter_by(owner_id=user.id).count() == 1
    assert db_session.query(models.AutomationJob).filter_by(
        owner_id=user.id,
        job_type=MIGRATION_JOB_TYPE,
    ).count() == 1
    assert db_session.query(models.AuditEvent).filter_by(
        owner_id=user.id,
        action=MIGRATION_AUDIT_ACTION,
    ).count() == 1

    # THEN: A key cannot be repurposed and active V2 work prevents rollback to
    # the legacy writer.
    with pytest.raises(OwnerMigrationConflict) as conflict:
        switch_owner_path(
            db_session,
            owner_id=user.id,
            actor_user_id=user.id,
            target_path=OwnerWritePath.LEGACY,
            expected_version=1,
            preview_checksum="0" * 64,
            idempotency_key="owner-switch-unit-0001",
        )
    assert conflict.value.code == "IDEMPOTENCY_KEY_REUSED"
    db_session.add(
        models.AutomationJob(
            owner_id=user.id,
            status=JobStatus.PENDING,
            job_type="campaign.start",
            queue="campaign",
            payload={},
            idempotency_key="owner-active-job-unit-0001",
        )
    )
    db_session.flush()
    rollback_preview = preview_owner_path(
        db_session,
        owner_id=user.id,
        target_path=OwnerWritePath.LEGACY,
    )
    assert {
        blocker["code"] for blocker in rollback_preview.blockers
    }.issuperset(
        {"LEGACY_WRITE_RECOVERY_NOT_APPROVED", "V2_EXECUTION_STILL_ACTIVE"}
    )


def test_concurrent_owner_switch_has_one_winner_and_one_stale_loser(
    tmp_path,
):
    # GIVEN: Two independent sessions submit the exact same reviewed version
    # with different idempotency keys.
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'owner-switch-race.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    seed = Session()
    user = legacy.User(
        username="owner-switch-race",
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
            result = switch_owner_path(
                db,
                owner_id=owner_id,
                actor_user_id=owner_id,
                target_path=OwnerWritePath.V2,
                expected_version=preview.expected_version,
                preview_checksum=preview.preview_checksum,
                idempotency_key=f"owner-switch-race-{index:04d}",
            )
            db.commit()
            return ("success", result.version)
        except OwnerMigrationConflict as exc:
            db.rollback()
            return ("conflict", exc.code)
        finally:
            db.close()

    # WHEN: Both contenders cross the process barrier together.
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(contender, (1, 2)))

    # THEN: The owner mutex and version CAS permit exactly one state/audit/job.
    assert sorted(item[0] for item in outcomes) == ["conflict", "success"]
    assert next(item[1] for item in outcomes if item[0] == "conflict") == (
        "OWNER_PATH_VERSION_CONFLICT"
    )
    verify = Session()
    try:
        assert verify.query(models.OwnerMigrationState).filter_by(owner_id=owner_id).count() == 1
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


def test_owner_migration_api_fences_each_writer_and_preserves_compliance(
    db_session,
    monkeypatch,
):
    # GIVEN: Production-like owner enforcement, without the deployment-wide
    # emergency freeze, and an authenticated owner still on legacy.
    monkeypatch.setenv("PRODUCT_V2_OWNER_PATH_TEST_BYPASS", "false")
    monkeypatch.setenv("PRODUCT_V2_OWNER_PATH_ENFORCEMENT", "true")
    monkeypatch.setenv("PRODUCT_V2_LEGACY_READ_ONLY", "false")
    monkeypatch.setenv("PRODUCT_V2_LEGACY_WRITERS_FROZEN", "true")
    user = legacy.User(
        username="owner-migration-api",
        hashed_password="x",
        is_active=True,
        is_admin=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    token = create_access_token(user.id, is_admin=True)
    headers = {"Authorization": f"Bearer {token}"}
    import main

    main.app.dependency_overrides[get_current_user] = lambda: user

    async def flow():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            v2_blocked = await client.post(
                "/api/v2/companies",
                headers=headers,
                json={"name": "Blocked before switch", "domain": "blocked.example"},
            )
            assert v2_blocked.status_code == 409
            assert v2_blocked.json()["detail"]["code"] == "OWNER_V2_WRITE_PATH_INACTIVE"

            # Consent is a precise safety exception and remains durable even
            # though ordinary V2 writes are fenced.
            consent = await client.post(
                "/api/v2/consent-restrictions",
                headers={**headers, "Idempotency-Key": "owner-consent-api-0001"},
                json={"scope": "global", "reason": "legal request", "source": "manual"},
            )
            assert consent.status_code == 201, consent.text

            legacy_allowed = await client.post(
                "/api/personas/",
                headers=headers,
                json={},
            )
            assert legacy_allowed.status_code != 409

            preview_response = await client.post(
                "/api/v2/migration-state/preview",
                headers=headers,
                json={"target_path": "v2"},
            )
            assert preview_response.status_code == 200, preview_response.text
            preview = preview_response.json()
            switched = await client.put(
                "/api/v2/migration-state",
                headers={**headers, "Idempotency-Key": "owner-switch-api-0001"},
                json={
                    "target_path": "v2",
                    "expected_version": preview["expected_version"],
                    "preview_checksum": preview["preview_checksum"],
                    "impact_preview_confirmed": True,
                },
            )
            assert switched.status_code == 200, switched.text
            assert switched.json()["current_path"] == "v2"

            v2_allowed = await client.post(
                "/api/v2/companies",
                headers=headers,
                json={"name": "Allowed after switch", "domain": "allowed.example"},
            )
            assert v2_allowed.status_code == 201, v2_allowed.text
            legacy_blocked = await client.post(
                "/api/personas",
                headers=headers,
                json={},
            )
            assert legacy_blocked.status_code == 409
            assert legacy_blocked.json()["detail"]["code"] == (
                "OWNER_LEGACY_WRITE_PATH_INACTIVE"
            )

    try:
        asyncio.run(flow())
    finally:
        main.app.dependency_overrides.clear()


def test_owner_path_safety_exceptions_are_exact_and_method_bounded():
    assert owner_path_write_exception("/api/v2/migration-state", "PUT")
    assert owner_path_write_exception("/api/v2/migration-state/preview", "POST")
    assert not owner_path_write_exception("/api/v2/migration-state", "POST")
    assert not owner_path_write_exception("/api/v2/migration-state/preview", "PUT")
    assert not owner_path_write_exception("/api/v2/migration-state-forged", "PUT")
    assert not owner_path_write_exception(
        "/api/v2/migration-state/preview/forged",
        "POST",
    )
    assert owner_path_write_exception(
        "/api/v2/webhooks/42/unipile-v2/events",
        "POST",
    )
    assert not owner_path_write_exception(
        "/api/v2/webhooks/42/unipile-v2/events/forged",
        "POST",
    )
    assert not owner_path_write_exception(
        "/api/v2/webhooks/0/unipile/events",
        "POST",
    )
    assert not owner_path_write_exception(
        "/api/v2/webhooks/42/unipile/events",
        "PUT",
    )
    assert owner_path_write_exception("/api/v2/consent-restrictions", "POST")
    assert not owner_path_write_exception("/api/v2/consent-restrictions/1", "POST")


def test_production_cutover_requires_writer_freeze_and_approved_safe_recovery(
    db_session,
    monkeypatch,
):
    # GIVEN: A production-like owner cutover without evidence that out-of-band
    # legacy workers (PM2/background processes) have actually stopped.
    monkeypatch.setenv("PRODUCT_V2_OWNER_PATH_TEST_BYPASS", "false")
    monkeypatch.setenv("PRODUCT_V2_OWNER_PATH_ENFORCEMENT", "true")
    monkeypatch.delenv("PRODUCT_V2_LEGACY_WRITERS_FROZEN", raising=False)
    monkeypatch.delenv("PRODUCT_V2_LEGACY_WRITE_RECOVERY_APPROVED", raising=False)
    user = legacy.User(
        username="owner-cutover-approvals",
        hashed_password="x",
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()

    frozen_preview = preview_owner_path(
        db_session,
        owner_id=user.id,
        target_path=OwnerWritePath.V2,
    )
    assert {item["code"] for item in frozen_preview.blockers} == {
        "LEGACY_WRITERS_NOT_FROZEN"
    }

    # WHEN: Explicit freeze evidence is supplied, the reviewed V2 switch may
    # commit. Read fallback remains independent from this write-path state.
    monkeypatch.setenv("PRODUCT_V2_LEGACY_WRITERS_FROZEN", "true")
    ready_preview = preview_owner_path(
        db_session,
        owner_id=user.id,
        target_path=OwnerWritePath.V2,
    )
    assert ready_preview.blockers == []
    assert ready_preview.effects["read_fallback_changed"] is False
    switch_owner_path(
        db_session,
        owner_id=user.id,
        actor_user_id=user.id,
        target_path=OwnerWritePath.V2,
        expected_version=ready_preview.expected_version,
        preview_checksum=ready_preview.preview_checksum,
        idempotency_key="owner-cutover-approval-0001",
    )

    # THEN: Reopening legacy writes is a separate approval and still fails
    # closed while V1 cannot prove V2 consent, locks, uncertainty, tasks, or
    # cooldown enforcement.
    company = models.Company(
        owner_id=user.id,
        name="Recovery risk",
        normalized_domain="recovery-risk.example",
        last_cold_outreach_at=datetime.now(timezone.utc),
    )
    db_session.add(company)
    db_session.add_all(
        [
            models.ConsentRestriction(
                owner_id=user.id,
                idempotency_key="owner-recovery-consent-0001",
                scope=RestrictionScope.GLOBAL,
                reason="do not contact",
                source="test",
            ),
            models.SafetyLock(
                owner_id=user.id,
                scope=SafetyLockScope.GLOBAL,
                code="owner-recovery-global-lock",
                reason="hard stop",
            ),
            models.ProviderCostEvent(
                owner_id=user.id,
                provider="recovery-test",
                operation="email_send",
                status=ProviderCostStatus.UNKNOWN,
                price_version="test-v1",
                idempotency_key="owner-recovery-cost-0001",
            ),
            models.Task(
                owner_id=user.id,
                task_type=TaskType.RECONCILIATION,
                status=TaskStatus.OPEN,
                priority=TaskPriority.URGENT,
                title="Reconcile uncertain send",
            ),
        ]
    )
    db_session.flush()
    unsafe_preview = preview_owner_path(
        db_session,
        owner_id=user.id,
        target_path=OwnerWritePath.LEGACY,
    )
    unsafe_codes = {item["code"] for item in unsafe_preview.blockers}
    assert {
        "LEGACY_WRITE_RECOVERY_NOT_APPROVED",
        "V2_CONSENT_NOT_PROJECTED",
        "V2_SAFETY_LOCKS_ACTIVE",
        "V2_UNCERTAINTY_UNRESOLVED",
        "V2_RECONCILIATION_OPEN",
        "V2_COOLDOWNS_NOT_PROJECTED",
    }.issubset(unsafe_codes)

    monkeypatch.setenv("PRODUCT_V2_LEGACY_WRITE_RECOVERY_APPROVED", "true")
    approved_but_unsafe = preview_owner_path(
        db_session,
        owner_id=user.id,
        target_path=OwnerWritePath.LEGACY,
    )
    approved_codes = {item["code"] for item in approved_but_unsafe.blockers}
    assert "LEGACY_WRITE_RECOVERY_NOT_APPROVED" not in approved_codes
    assert unsafe_codes - {"LEGACY_WRITE_RECOVERY_NOT_APPROVED"} == approved_codes


def test_disabled_bearer_identity_keeps_route_auth_401_semantics(
    db_session,
    monkeypatch,
):
    # GIVEN: A structurally valid token for a now-disabled owner whose state is
    # V2. The migration middleware must not leak that state through a 409.
    monkeypatch.setenv("PRODUCT_V2_OWNER_PATH_TEST_BYPASS", "false")
    monkeypatch.setenv("PRODUCT_V2_OWNER_PATH_ENFORCEMENT", "true")
    monkeypatch.setenv("PRODUCT_V2_LEGACY_READ_ONLY", "false")
    user = legacy.User(
        username="disabled-owner-path-auth",
        hashed_password="x",
        is_active=False,
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(
        models.OwnerMigrationState(
            owner_id=user.id,
            current_path=OwnerWritePath.V2,
            version=1,
            switched_at=datetime.now(timezone.utc),
            switched_by_user_id=user.id,
        )
    )
    db_session.commit()
    token = create_access_token(user.id)
    import main

    async def flow():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/api/personas/",
                headers={"Authorization": f"Bearer {token}"},
                json={},
            )

    response = asyncio.run(flow())
    assert response.status_code == 401


def test_claimed_job_is_terminally_cancelled_when_owner_is_not_on_v2(
    db_session,
    monkeypatch,
):
    # GIVEN: A production-like worker has already claimed work for an owner
    # whose durable state is still legacy.
    monkeypatch.setenv("PRODUCT_V2_OWNER_PATH_TEST_BYPASS", "false")
    monkeypatch.setenv("PRODUCT_V2_OWNER_PATH_ENFORCEMENT", "true")
    user = legacy.User(
        username="owner-job-runtime-fence",
        hashed_password="x",
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    job = models.AutomationJob(
        owner_id=user.id,
        status=JobStatus.CLAIMED,
        job_type="campaign.start",
        queue="campaign",
        payload={},
        idempotency_key="owner-job-runtime-fence-0001",
        lease_owner="worker-a",
    )
    db_session.add(job)
    db_session.flush()

    # WHEN: Execution re-checks the owner after claim and before domain writes.
    result = execute_job(db_session, job)
    db_session.flush()

    # THEN: The job is terminally cancelled and only its safety audit remains.
    assert result["cancelled"] is True
    assert job.status == JobStatus.CANCELLED
    assert job.last_error == "owner_v2_write_path_inactive"
    assert job.lease_owner is None
    assert db_session.query(models.AuditEvent).filter_by(
        owner_id=user.id,
        action="automation_job.owner_path_cancelled",
        entity_id=str(job.id),
    ).count() == 1
