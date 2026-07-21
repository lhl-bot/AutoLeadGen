from __future__ import annotations

import asyncio

import httpx

import models as legacy
from product_v2 import models
from product_v2.connectors.registry import build_local_registry
from product_v2.enums import (
    AttemptStatus,
    Channel,
    ChannelAccountHealth,
    JobStatus,
    MessageEventType,
    SafetyLockScope,
    StageStatus,
    TaskStatus,
    TaskType,
    WorkerType,
)
from product_v2.runtime.outbound import execute_attempt
from product_v2.runtime.queue import heartbeat
from product_v2.runtime.worker import execute_job
from product_v2.services.domain import utcnow
from services.auth import get_current_user


def _save_setting(db_session, *, owner_id: int, section: str, values: dict) -> None:
    db_session.add(
        models.AuditEvent(
            owner_id=owner_id,
            actor_user_id=owner_id,
            action="product_settings.updated",
            entity_type="product_setting",
            entity_id=section,
            after_data={"version": 1, "values": values},
        )
    )


def _activation_prerequisites(db_session, user: legacy.User) -> models.ChannelAccount:
    _save_setting(
        db_session,
        owner_id=user.id,
        section="icp_playbook",
        values={
            "summary": "帮助家纺零售商缩短新品打样周期",
            "target_industries": ["家纺零售"],
            "target_roles": ["采购负责人"],
            "evidence_requirements": ["公司官网与产品页面"],
            "playbook_notes": "",
            "proposal_status": "published",
        },
    )
    _save_setting(
        db_session,
        owner_id=user.id,
        section="channels_integrations",
        values={
            "email_enabled": True,
            "linkedin_enabled": False,
            "whatsapp_enabled": False,
            "public_unsubscribe_url": "https://pilot.example/unsubscribe",
            "review_before_send": True,
            "integration_notes": "",
        },
    )
    account = models.ChannelAccount(
        owner_id=user.id,
        channel=Channel.EMAIL,
        provider="fake-email",
        provider_account_id="pilot-sender@example.test",
        enabled=True,
        health_status=ChannelAccountHealth.HEALTHY,
        health_checked_at=utcnow(),
        daily_limit=10,
        timezone="Asia/Shanghai",
    )
    db_session.add(account)
    heartbeat(
        db_session,
        worker_name="activation-outbound",
        worker_type=WorkerType.OUTBOUND,
        status=StageStatus.IDLE,
    )
    heartbeat(
        db_session,
        worker_name="activation-inbox",
        worker_type=WorkerType.INBOX,
        status=StageStatus.IDLE,
    )
    db_session.commit()
    return account


def _execute_job(db_session, job_id: int) -> dict:
    job = db_session.get(models.AutomationJob, job_id)
    assert job is not None
    result = execute_job(db_session, job)
    db_session.commit()
    return result


def test_csv_activation_flow_requires_verification_review_and_exact_approved_copy(db_session):
    # GIVEN: An invited owner with a healthy administrator-provisioned fake mailbox.
    user = legacy.User(username="activation-csv-owner", hashed_password="x", is_active=True)
    db_session.add(user)
    db_session.commit()
    account = _activation_prerequisites(db_session, user)

    import main

    main.app.dependency_overrides[get_current_user] = lambda: user

    async def prepare_and_launch():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # WHEN: Chinese and English columns, a duplicate row, and an invalid email are previewed.
            preview = await client.post(
                "/api/v2/acquisition-runs/import/preview",
                headers={"Idempotency-Key": "activation-csv-import-001"},
                files={
                    "file": (
                        "首批客户.csv",
                        (
                            "公司名称,Domain,姓名,职位,邮箱\n"
                            "Nordic Home,nordic-home.example,Ada,Buyer,ada@nordic-home.example\n"
                            "Nordic Home,nordic-home.example,Ada Duplicate,Buyer,ada@nordic-home.example\n"
                            "Bad Mail,bad-mail.example,Bob,Buyer,not-an-email\n"
                            "Maison Lin,maison-lin.example,Lin,采购经理,lin@maison-lin.example\n"
                            "Ocean Textiles,ocean-textiles.example,Chen,Buyer,chen@ocean-textiles.example\n"
                        ).encode("utf-8"),
                        "text/csv",
                    )
                },
            )
            assert preview.status_code == 201, preview.text
            run = preview.json()
            replay = await client.post(
                "/api/v2/acquisition-runs/import/preview",
                headers={"Idempotency-Key": "activation-csv-import-001"},
                files={
                    "file": (
                        "首批客户.csv",
                        (
                            "公司名称,Domain,姓名,职位,邮箱\n"
                            "Nordic Home,nordic-home.example,Ada,Buyer,ada@nordic-home.example\n"
                            "Nordic Home,nordic-home.example,Ada Duplicate,Buyer,ada@nordic-home.example\n"
                            "Bad Mail,bad-mail.example,Bob,Buyer,not-an-email\n"
                            "Maison Lin,maison-lin.example,Lin,采购经理,lin@maison-lin.example\n"
                            "Ocean Textiles,ocean-textiles.example,Chen,Buyer,chen@ocean-textiles.example\n"
                        ).encode("utf-8"),
                        "text/csv",
                    )
                },
            )
            assert replay.status_code == 201, replay.text
            assert replay.json()["id"] == run["id"]
            statuses = [candidate["status"] for candidate in run["candidates"]]
            assert statuses.count("ready") == 3
            assert "duplicate" in statuses
            assert "invalid" in statuses
            candidate_ids = [
                candidate["id"] for candidate in run["candidates"] if candidate["status"] == "ready"
            ]

            verify = await client.post(
                f"/api/v2/acquisition-runs/{run['id']}/verify",
                headers={"Idempotency-Key": "activation-csv-verify-001"},
                json={"candidate_ids": candidate_ids, "paid_action_confirmed": True},
            )
            assert verify.status_code == 202, verify.text
            return run["id"], candidate_ids, verify.json()["job_id"]

    try:
        run_id, candidate_ids, verify_job_id = asyncio.run(prepare_and_launch())
        _execute_job(db_session, verify_job_id)

        async def commit_and_request_launch():
            transport = httpx.ASGITransport(app=main.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                committed = await client.post(
                    f"/api/v2/acquisition-runs/{run_id}/commit",
                    headers={"Idempotency-Key": "activation-csv-commit-001"},
                    json={"candidate_ids": candidate_ids, "human_confirmed": True},
                )
                assert committed.status_code == 200, committed.text
                replay = await client.post(
                    f"/api/v2/acquisition-runs/{run_id}/commit",
                    headers={"Idempotency-Key": "activation-csv-commit-001"},
                    json={"candidate_ids": candidate_ids, "human_confirmed": True},
                )
                assert replay.status_code == 200, replay.text

                draft = {
                    "run_id": run_id,
                    "candidate_ids": candidate_ids,
                    "channel_account_id": account.id,
                    "plan_name": "首批 3 人试跑",
                    "objective": "验证采购负责人是否愿意了解快速打样服务",
                    "tone": "专业、简洁、尊重",
                    "language": "中文",
                    "subject_template": "{{company_name}} 的新品打样建议",
                    "body_template": "你好 {{first_name}}，我们为 {{company_name}} 提供快速打样。\n\n{{unsubscribe_url}}",
                    "daily_limit": 3,
                }
                launch_preview = await client.post("/api/v2/activation/launch-preview", json=draft)
                assert launch_preview.status_code == 200, launch_preview.text
                assert launch_preview.json()["blockers"] == []
                launch = await client.post(
                    "/api/v2/activation/launch",
                    headers={"Idempotency-Key": "activation-csv-launch-001"},
                    json={
                        **draft,
                        "preview_checksum": launch_preview.json()["checksum"],
                        "human_confirmed": True,
                    },
                )
                assert launch.status_code == 202, launch.text
                return launch.json()["job_id"], draft, launch_preview.json()["checksum"]

        launch_job_id, launch_draft, launch_checksum = asyncio.run(commit_and_request_launch())
        launch_result = _execute_job(db_session, launch_job_id)

        # A transport retry must return its durable receipt even if current
        # safety state changed after the original command was accepted.
        retry_lock = models.SafetyLock(
            owner_id=user.id,
            scope=SafetyLockScope.GLOBAL,
            code="idempotency-retry-proof",
            reason="force a changed preview after the accepted launch",
            active=True,
        )
        db_session.add(retry_lock)
        db_session.commit()

        async def replay_launch_after_state_change():
            transport = httpx.ASGITransport(app=main.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.post(
                    "/api/v2/activation/launch",
                    headers={"Idempotency-Key": "activation-csv-launch-001"},
                    json={
                        **launch_draft,
                        "preview_checksum": launch_checksum,
                        "human_confirmed": True,
                    },
                )

        replayed_launch = asyncio.run(replay_launch_after_state_change())
        assert replayed_launch.status_code == 202, replayed_launch.text
        assert replayed_launch.json()["job_id"] == launch_job_id
        retry_lock.active = False
        retry_lock.unlocked_at = utcnow()
        retry_lock.unlocked_by_user_id = user.id
        db_session.commit()

        # WHEN: Existing workers turn the immutable plan into REVIEW attempts.
        _execute_job(db_session, launch_result["start_job_id"])
        enrollment_jobs = db_session.query(models.AutomationJob).filter(
            models.AutomationJob.job_type == "enrollment.created",
            models.AutomationJob.enrollment_id.in_(launch_result["enrollment_ids"]),
            models.AutomationJob.status == JobStatus.PENDING,
        ).all()
        for job in enrollment_jobs:
            _execute_job(db_session, job.id)
        attempts = db_session.query(models.OutreachAttempt).filter_by(
            campaign_id=launch_result["campaign_id"]
        ).order_by(models.OutreachAttempt.id.asc()).all()
        assert len(attempts) == 3
        registry = build_local_registry()
        for attempt in attempts:
            execute_attempt(db_session, attempt=attempt, registry=registry)
            db_session.commit()
            assert attempt.status == AttemptStatus.BLOCKED

        first_task = db_session.query(models.Task).filter_by(
            attempt_id=attempts[0].id,
            task_type=TaskType.DRAFT_REVIEW,
        ).one()

        async def edit_and_approve():
            transport = httpx.ASGITransport(app=main.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.patch(
                    f"/api/v2/tasks/{first_task.id}",
                    json={
                        "review_subject": "为 Nordic Home 准备的打样建议",
                        "review_body": "这是销售逐封编辑并批准的唯一正文。\n\nhttps://pilot.example/unsubscribe",
                        "status": "completed",
                    },
                )
                assert response.status_code == 200, response.text

        asyncio.run(edit_and_approve())
        db_session.expire_all()
        first_attempt = db_session.get(models.OutreachAttempt, attempts[0].id)
        execute_attempt(db_session, attempt=first_attempt, registry=registry)
        db_session.commit()

        # THEN: Exactly the edited snapshot is sent and activation reaches first-send success.
        assert first_attempt.status == AttemptStatus.SUCCEEDED
        sent = db_session.query(models.MessageEvent).filter_by(
            outreach_attempt_id=first_attempt.id,
            event_type=MessageEventType.SENT,
        ).one()
        assert sent.subject == "为 Nordic Home 准备的打样建议"
        assert sent.body.startswith("这是销售逐封编辑并批准的唯一正文。")
        assert db_session.query(models.AuditEvent).filter_by(
            owner_id=user.id,
            action="activation.draft_approved",
            entity_type="task",
            entity_id=str(first_task.id),
        ).count() == 1
        assert db_session.query(models.AuditEvent).filter_by(
            owner_id=user.id,
            action="activation.first_send_succeeded",
            entity_type="campaign",
            entity_id=str(launch_result["campaign_id"]),
        ).count() == 1

        async def read_activation():
            transport = httpx.ASGITransport(app=main.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.get("/api/v2/activation")

        activation = asyncio.run(read_activation())
        assert activation.status_code == 200, activation.text
        assert activation.json()["activated"] is True
        assert all(step["completed"] for step in activation.json()["steps"])
    finally:
        main.app.dependency_overrides.clear()


def test_csv_acquisition_fails_closed_on_untrusted_identity_and_bounded_upload(
    db_session,
):
    # GIVEN: An authenticated owner importing data that must remain staging-only.
    user = legacy.User(username="activation-csv-safety", hashed_password="x", is_active=True)
    db_session.add(user)
    db_session.commit()

    import main

    main.app.dependency_overrides[get_current_user] = lambda: user

    async def exercise_import_guards():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            unsafe_csv = (
                "Company,Domain,Email\n"
                "Public Mail,gmail.com,buyer@gmail.com\n"
                "Mismatch,example-corp.example,buyer@another-corp.example\n"
                "Malformed,bad host,buyer@bad-host.example\n"
            ).encode()
            preview = await client.post(
                "/api/v2/acquisition-runs/import/preview",
                headers={"Idempotency-Key": "activation-csv-safety-001"},
                files={"file": ("unsafe.csv", unsafe_csv, "text/csv")},
            )
            conflict = await client.post(
                "/api/v2/acquisition-runs/import/preview",
                headers={"Idempotency-Key": "activation-csv-safety-001"},
                files={
                    "file": (
                        "different.csv",
                        b"Company,Domain,Email\nSafe,safe.example,buyer@safe.example\n",
                        "text/csv",
                    )
                },
            )
            oversized = await client.post(
                "/api/v2/acquisition-runs/import/preview",
                headers={"Idempotency-Key": "activation-csv-safety-oversized"},
                files={
                    "file": (
                        "oversized.csv",
                        b"Company,Domain\n" + b"x" * (2 * 1024 * 1024),
                        "text/csv",
                    )
                },
            )
            return preview, conflict, oversized

    try:
        preview, conflict, oversized = asyncio.run(exercise_import_guards())
        assert preview.status_code == 201, preview.text
        candidates = preview.json()["candidates"]
        assert [item["status"] for item in candidates] == ["invalid", "invalid", "invalid"]
        assert [item["normalized_domain"] for item in candidates] == [
            None,
            "example-corp.example",
            None,
        ]
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"
        assert oversized.status_code == 413
        assert oversized.json()["detail"]["code"] == "CSV_IMPORT_TOO_LARGE"
        assert db_session.query(models.Company).count() == 0
        assert db_session.query(models.Contact).count() == 0
        assert db_session.query(models.ContactPoint).count() == 0
    finally:
        main.app.dependency_overrides.clear()


def test_real_acquisition_is_rejected_before_any_paid_job_is_enqueued(
    db_session,
    monkeypatch,
):
    # GIVEN: Production Email may be enabled while real prospecting remains unapproved.
    user = legacy.User(username="activation-real-gate", hashed_password="x", is_active=True)
    db_session.add(user)
    db_session.commit()
    monkeypatch.setenv("AUTOLEADGEN_ENV", "production")
    monkeypatch.setenv("AUTOLEADGEN_CONNECTOR_MODE", "real")
    monkeypatch.setenv("ALLOW_REAL_EXTERNAL_CALLS", "true")
    monkeypatch.setenv("ALLOW_REAL_ACQUISITION_CALLS", "false")

    import main

    main.app.dependency_overrides[get_current_user] = lambda: user

    async def request_paid_search():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/api/v2/acquisition-runs/search",
                headers={"Idempotency-Key": "activation-real-gate-001"},
                json={
                    "name": "blocked real search",
                    "product_summary": "production paid connector safety",
                    "target_industries": ["textile"],
                    "target_roles": ["buyer"],
                    "target_regions": ["UK"],
                    "limit": 5,
                    "paid_action_confirmed": True,
                },
            )

    try:
        response = asyncio.run(request_paid_search())
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "REAL_ACQUISITION_NOT_APPROVED"
        assert db_session.query(models.AcquisitionRun).count() == 0
        assert db_session.query(models.AutomationJob).count() == 0
    finally:
        main.app.dependency_overrides.clear()


def test_ai_acquisition_is_evidence_first_and_paid_actions_require_confirmation(db_session):
    # GIVEN: An invited owner in isolated fake mode.
    user = legacy.User(username="activation-ai-owner", hashed_password="x", is_active=True)
    db_session.add(user)
    db_session.commit()
    _activation_prerequisites(db_session, user)

    import main

    main.app.dependency_overrides[get_current_user] = lambda: user

    async def start_search():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            rejected = await client.post(
                "/api/v2/acquisition-runs/search",
                headers={"Idempotency-Key": "activation-ai-search-001"},
                json={
                    "name": "北欧家纺客户",
                    "product_summary": "快速家纺打样",
                    "target_industries": ["家纺零售"],
                    "target_roles": ["采购负责人"],
                    "target_regions": ["北欧"],
                    "limit": 5,
                    "paid_action_confirmed": False,
                },
            )
            assert rejected.status_code == 422
            accepted = await client.post(
                "/api/v2/acquisition-runs/search",
                headers={"Idempotency-Key": "activation-ai-search-001"},
                json={
                    "name": "北欧家纺客户",
                    "product_summary": "快速家纺打样",
                    "target_industries": ["家纺零售"],
                    "target_roles": ["采购负责人"],
                    "target_regions": ["北欧"],
                    "limit": 5,
                    "paid_action_confirmed": True,
                },
            )
            assert accepted.status_code == 202, accepted.text
            return accepted.json()["id"], accepted.json()["job_id"]

    try:
        run_id, search_job_id = asyncio.run(start_search())
        _execute_job(db_session, search_job_id)
        run = db_session.get(models.AcquisitionRun, run_id)
        candidates = db_session.query(models.AcquisitionCandidate).filter_by(run_id=run_id).all()
        assert run.status == "ready"
        assert len(candidates) == 5
        assert all(candidate.evidence.get("snippet") for candidate in candidates)
        assert all(candidate.normalized_email is None for candidate in candidates)
        assert db_session.query(models.ProviderCostEvent).filter_by(
            idempotency_key=f"cost:acquisition-search:{run_id}"
        ).one().billable is False

        async def verify_selected():
            transport = httpx.ASGITransport(app=main.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    f"/api/v2/acquisition-runs/{run_id}/verify",
                    headers={"Idempotency-Key": "activation-ai-verify-001"},
                    json={
                        "candidate_ids": [candidates[0].id, candidates[1].id],
                        "paid_action_confirmed": True,
                    },
                )
                assert response.status_code == 202, response.text
                return response.json()["job_id"]

        _execute_job(db_session, asyncio.run(verify_selected()))
        db_session.expire_all()
        refreshed = db_session.query(models.AcquisitionCandidate).filter_by(run_id=run_id).order_by(
            models.AcquisitionCandidate.id.asc()
        ).all()
        assert [candidate.selected for candidate in refreshed] == [True, True, False, False, False]
        assert all(candidate.normalized_email for candidate in refreshed[:2])
        assert all(candidate.normalized_email is None for candidate in refreshed[2:])
        assert db_session.query(models.EvidenceSnapshot).count() == 0
    finally:
        main.app.dependency_overrides.clear()
