"""MySQL-only concurrency regressions for Campaign Revision publication."""

import os
from pathlib import Path
from queue import Queue
from threading import Barrier, Thread
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

import models as legacy
from product_v2 import models
from product_v2.enums import CampaignLifecycle, CampaignRevisionStatus, JobStatus
from product_v2.services.domain import (
    campaign_revision_diff,
    campaign_revision_diff_checksum,
    publish_campaign_revision,
)


MYSQL_URL = os.environ.get("PRODUCT_V2_MYSQL_TEST_URL")
pytestmark = pytest.mark.mysql


def _session_factory(monkeypatch):
    if not MYSQL_URL:
        pytest.skip("PRODUCT_V2_MYSQL_TEST_URL is not configured")
    url = make_url(MYSQL_URL)
    if not url.drivername.startswith("mysql"):
        pytest.fail("PRODUCT_V2_MYSQL_TEST_URL must use MySQL")
    if not (url.database or "").endswith("_test"):
        pytest.fail("Refusing to test against a database whose name does not end in _test")
    monkeypatch.setenv("DATABASE_URL", MYSQL_URL)
    monkeypatch.setenv("AUTOLEADGEN_ENV", "test")
    command.upgrade(Config(str(Path(__file__).parents[2] / "alembic.ini")), "head")
    engine = create_engine(MYSQL_URL, pool_pre_ping=True)
    return engine, sessionmaker(bind=engine, autoflush=False)


def _seed_revisions(Session, *, draft_count):
    suffix = uuid4().hex[:12]
    db = Session()
    user = legacy.User(
        username=f"revision-publish-{suffix}", hashed_password="x", is_active=True
    )
    db.add(user)
    db.flush()
    campaign = models.Campaign(
        owner_id=user.id,
        name=f"Revision publish {suffix}",
        lifecycle=CampaignLifecycle.DRAFT,
        published_revision_number=1,
    )
    db.add(campaign)
    db.flush()
    base = models.CampaignRevision(
        owner_id=user.id,
        campaign_id=campaign.id,
        revision_number=1,
        status=CampaignRevisionStatus.PUBLISHED,
        icp_definition={"version": "base"},
    )
    db.add(base)
    db.flush()
    drafts = []
    for offset in range(draft_count):
        draft = models.CampaignRevision(
            owner_id=user.id,
            campaign_id=campaign.id,
            revision_number=offset + 2,
            status=CampaignRevisionStatus.DRAFT,
            icp_definition={"proposal": offset + 1},
        )
        db.add(draft)
        drafts.append(draft)
    db.commit()
    owner_id = user.id
    campaign_id = campaign.id
    base_id = base.id
    draft_ids = [draft.id for draft in drafts]

    checksums = {}
    for draft in drafts:
        reviewed_base, diff = campaign_revision_diff(
            db, campaign=campaign, proposed=draft
        )
        checksums[draft.id] = campaign_revision_diff_checksum(
            campaign_id=campaign.id,
            base_revision_id=reviewed_base.id,
            proposed_revision_id=draft.id,
            diff=diff,
        )
    db.close()
    return owner_id, campaign_id, base_id, draft_ids, checksums, suffix


def _publish_in_threads(
    Session,
    *,
    owner_id,
    campaign_id,
    base_id,
    publish_requests,
):
    loaded = Barrier(len(publish_requests))
    results = Queue()

    def publish_one(revision_id, checksum, key):
        db = Session()
        try:
            campaign = db.get(models.Campaign, campaign_id)
            revision = db.get(models.CampaignRevision, revision_id)
            # All contenders establish an old REPEATABLE READ snapshot before
            # either reaches the Product V2 serialization fence.
            loaded.wait(timeout=10)
            published = publish_campaign_revision(
                db,
                campaign=campaign,
                revision=revision,
                actor_user_id=owner_id,
                idempotency_key=key,
                base_revision_id=base_id,
                reviewed_diff_checksum=checksum,
                human_confirmed=True,
            )
            db.commit()
            results.put(("published", published.id))
        except ValueError as exc:
            db.rollback()
            results.put(("rejected", str(exc)))
        except Exception as exc:  # surfaced with type/name in the parent assertion
            db.rollback()
            results.put(("error", f"{type(exc).__name__}: {exc}"))
        finally:
            db.close()

    threads = [
        Thread(target=publish_one, args=request, daemon=True)
        for request in publish_requests
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
    assert all(not thread.is_alive() for thread in threads), "publish workers deadlocked"
    return [results.get_nowait() for _ in publish_requests]


def _cleanup(Session, owner_id):
    cleanup = Session()
    try:
        cleanup.query(models.AuditEvent).filter_by(owner_id=owner_id).delete(
            synchronize_session=False
        )
        cleanup.query(models.AutomationJob).filter_by(owner_id=owner_id).delete(
            synchronize_session=False
        )
        cleanup.query(models.SequenceStep).filter_by(owner_id=owner_id).delete(
            synchronize_session=False
        )
        cleanup.query(models.CampaignRevision).filter_by(owner_id=owner_id).delete(
            synchronize_session=False
        )
        cleanup.query(models.Campaign).filter_by(owner_id=owner_id).delete(
            synchronize_session=False
        )
        cleanup.query(models.ChannelAccount).filter_by(owner_id=owner_id).delete(
            synchronize_session=False
        )
        cleanup.query(legacy.User).filter_by(id=owner_id).delete(
            synchronize_session=False
        )
        cleanup.commit()
    finally:
        cleanup.close()


def test_mysql_two_drafts_reviewed_from_same_base_serialize_to_one_winner(monkeypatch):
    # GIVEN: Two immutable drafts were both reviewed against the same published
    # base revision.
    engine, Session = _session_factory(monkeypatch)
    owner_id, campaign_id, base_id, draft_ids, checksums, suffix = _seed_revisions(
        Session, draft_count=2
    )
    try:
        # WHEN: Independent MySQL transactions publish the drafts concurrently.
        results = _publish_in_threads(
            Session,
            owner_id=owner_id,
            campaign_id=campaign_id,
            base_id=base_id,
            publish_requests=[
                (draft_id, checksums[draft_id], f"concurrent-draft-{suffix}-{index}")
                for index, draft_id in enumerate(draft_ids)
            ],
        )

        # THEN: The Campaign lock admits one winner and the waiter re-reads the
        # changed baseline and rejects its review as stale.
        assert [kind for kind, _ in results].count("published") == 1, results
        assert [kind for kind, _ in results].count("rejected") == 1, results
        assert any("stale" in detail for kind, detail in results if kind == "rejected")

        verify = Session()
        try:
            revisions = {
                row.id: row
                for row in verify.query(models.CampaignRevision)
                .filter(models.CampaignRevision.id.in_([base_id, *draft_ids]))
                .all()
            }
            campaign = verify.get(models.Campaign, campaign_id)
            winner_ids = [
                revision_id
                for revision_id in draft_ids
                if revisions[revision_id].status == CampaignRevisionStatus.PUBLISHED
            ]
            assert len(winner_ids) == 1
            assert campaign.published_revision_number == revisions[winner_ids[0]].revision_number
            assert revisions[base_id].status == CampaignRevisionStatus.SUPERSEDED
            assert verify.query(models.AuditEvent).filter_by(
                owner_id=owner_id, action="campaign_revision.published"
            ).count() == 1
            assert verify.query(models.AutomationJob).filter_by(
                owner_id=owner_id,
                job_type="campaign_revision.publish",
                status=JobStatus.SUCCEEDED,
            ).count() == 1
        finally:
            verify.close()
    finally:
        _cleanup(Session, owner_id)
        engine.dispose()


def test_mysql_concurrent_same_publish_key_replays_one_receipt_and_audit(monkeypatch):
    # GIVEN: One reviewed draft and two delivery attempts carrying the same
    # Idempotency-Key.
    engine, Session = _session_factory(monkeypatch)
    owner_id, campaign_id, base_id, draft_ids, checksums, suffix = _seed_revisions(
        Session, draft_count=1
    )
    draft_id = draft_ids[0]
    key = f"concurrent-replay-{suffix}"
    try:
        # WHEN: Both transactions publish at the same instant.
        results = _publish_in_threads(
            Session,
            owner_id=owner_id,
            campaign_id=campaign_id,
            base_id=base_id,
            publish_requests=[
                (draft_id, checksums[draft_id], key),
                (draft_id, checksums[draft_id], key),
            ],
        )

        # THEN: Both callers observe success, but the unique database receipt
        # and immutable audit trail each contain exactly one command record.
        assert results == [("published", draft_id), ("published", draft_id)]
        verify = Session()
        try:
            assert verify.query(models.AuditEvent).filter_by(
                owner_id=owner_id,
                action="campaign_revision.published",
                correlation_id=key,
            ).count() == 1
            receipt = verify.query(models.AutomationJob).filter_by(
                idempotency_key=key
            ).one()
            assert receipt.status == JobStatus.SUCCEEDED
            assert receipt.result["campaign_id"] == campaign_id
            assert receipt.result["revision_id"] == draft_id
        finally:
            verify.close()
    finally:
        _cleanup(Session, owner_id)
        engine.dispose()
