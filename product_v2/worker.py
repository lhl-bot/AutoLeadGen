"""Dedicated Product V2 worker entrypoint.

Example: ``python -m product_v2.worker outbound``.  The API never invokes this
loop and all local channels resolve to deterministic fake connectors.
"""
from __future__ import annotations

import argparse
import os
import socket
import time

from database import SessionLocal
from product_v2.connectors import build_runtime_registry
from product_v2.enums import StageStatus, WorkerType
from product_v2.runtime.outbound import execute_attempt
from product_v2.runtime.queue import LeaseFenceLost, claim_attempt, claim_job, heartbeat, lease_fence
from product_v2.runtime.worker import execute_job
from product_v2.runtime.imap_inbox import poll_imap_inbox
from runtime_config import RuntimeConfigurationError, read_flag


QUEUE_BY_WORKER = {
    WorkerType.PROSPECTING: ("prospecting",),
    WorkerType.RESEARCH: ("research",),
    WorkerType.OUTBOUND: ("campaign", "outbound"),
    WorkerType.INBOX: ("inbox",),
    WorkerType.OMNICHANNEL: ("omnichannel",),
}


def _worker_runtime_enabled(worker_type: WorkerType) -> bool:
    """Return whether the complete local worker topology may run safely.

    Product V2 ships deterministic fake handlers for every local queue plus
    real Email outbound and inbox handlers in staging/production.  The global
    outbound hard pause must not stop IMAP ingestion: replies, bounces, and
    unsubscribe intent remain safety inputs while sending is contained.
    """

    environment = os.environ.get("AUTOLEADGEN_ENV", "local").strip().lower()
    connector_mode = os.environ.get("AUTOLEADGEN_CONNECTOR_MODE", "fake").strip().lower()
    if environment in {"local", "test"} and connector_mode == "fake":
        return True
    try:
        external_allowed = read_flag("ALLOW_REAL_EXTERNAL_CALLS", default=False)
        hard_paused = read_flag("OUTBOUND_HARD_PAUSE", default=True)
    except RuntimeConfigurationError:
        return False
    real_runtime = (
        environment in {"staging", "production"}
        and connector_mode == "real"
        and external_allowed
    )
    if worker_type == WorkerType.INBOX:
        return real_runtime
    if worker_type == WorkerType.OUTBOUND:
        return real_runtime and not hard_paused
    if worker_type == WorkerType.PROSPECTING:
        try:
            acquisition_allowed = read_flag("ALLOW_REAL_ACQUISITION_CALLS", default=False)
        except RuntimeConfigurationError:
            acquisition_allowed = False
        return real_runtime and acquisition_allowed
    return False


def run_once(worker_name: str, worker_type: WorkerType) -> bool:
    db = SessionLocal()
    registry = None
    did_work = False
    try:
        implemented = _worker_runtime_enabled(worker_type)
        release_identity = {
            "release_sha": os.environ.get("RELEASE_SHA", "unknown"),
            "image_digest": os.environ.get("IMAGE_DIGEST", "unknown"),
        }
        if implemented and worker_type == WorkerType.OUTBOUND:
            registry = build_runtime_registry()
        heartbeat(
            db,
            worker_name=worker_name,
            worker_type=worker_type,
            status=StageStatus.RUNNING if implemented else StageStatus.DISABLED,
            details={
                **release_identity,
                "connector_mode": os.environ.get("AUTOLEADGEN_CONNECTOR_MODE", "fake"),
                "external_calls_allowed": bool(
                    implemented
                    and os.environ.get("AUTOLEADGEN_CONNECTOR_MODE", "fake") == "real"
                ),
                "implemented": implemented,
                "capability": (
                    "real_email_outbound"
                    if implemented
                    and worker_type == WorkerType.OUTBOUND
                    and os.environ.get("AUTOLEADGEN_CONNECTOR_MODE", "fake") == "real"
                    else "real_email_inbox"
                    if implemented
                    and worker_type == WorkerType.INBOX
                    and os.environ.get("AUTOLEADGEN_CONNECTOR_MODE", "fake") == "real"
                    else "real_acquisition"
                    if implemented
                    and worker_type == WorkerType.PROSPECTING
                    and os.environ.get("AUTOLEADGEN_CONNECTOR_MODE", "fake") == "real"
                    else "fake_queue_consumer" if implemented else None
                ),
                "reason": None if implemented else "runtime_controls_not_enabled",
            },
        )
        db.commit()
        if not implemented:
            return False
        if (
            worker_type == WorkerType.INBOX
            and os.environ.get("AUTOLEADGEN_CONNECTOR_MODE", "fake") == "real"
        ):
            poll_result = poll_imap_inbox(db)
            heartbeat(
                db,
                worker_name=worker_name,
                worker_type=worker_type,
                status=StageStatus.RUNNING,
                details={
                    **release_identity,
                    "connector_mode": "real",
                    "capability": "real_email_inbox",
                    **poll_result.safe_details(),
                },
            )
            db.commit()
            return poll_result.did_work
        job = claim_job(db, worker_name=worker_name, queues=QUEUE_BY_WORKER[worker_type])
        job_fence = lease_fence(job) if job else None
        # claim_job also performs durable expired-lease recovery. Commit even
        # when no new job was claimable so recovered FAILED/RETRY state is not
        # rolled back when this short-lived session closes.
        db.commit()
        if job:
            # Persist the claim before execution. A process crash can then be
            # recovered by the lease policy instead of silently replaying work.
            try:
                execute_job(db, job, lease_fence=job_fence)
            except LeaseFenceLost:
                # A replacement worker owns a later generation.  Rolling back
                # is mandatory: committing here could persist stale Campaign
                # mutations even though the job completion itself was fenced.
                db.rollback()
            except Exception:
                db.commit()
            else:
                db.commit()
            did_work = True
        if worker_type == WorkerType.OUTBOUND:
            attempt = claim_attempt(db, worker_name=worker_name)
            attempt_fence = lease_fence(attempt) if attempt else None
            # As above, an empty claim may still have converted expired
            # attempts to UNKNOWN and created reconciliation tasks.
            db.commit()
            if attempt:
                # The claim and lease must survive a crash after Provider
                # acknowledgement; expired claims become UNKNOWN and require
                # reconciliation, never an automatic resend.
                try:
                    execute_attempt(
                        db,
                        attempt=attempt,
                        registry=registry,
                        lease_fence=attempt_fence,
                    )
                except Exception:
                    db.rollback()
                else:
                    db.commit()
                did_work = True
        return did_work
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("worker_type", choices=[item.value for item in WorkerType])
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--name")
    args = parser.parse_args()
    worker_type = WorkerType(args.worker_type)
    worker_name = args.name or f"{socket.gethostname()}:{worker_type.value}:{os.getpid()}"
    if args.once:
        run_once(worker_name, worker_type)
        return
    while True:
        did_work = run_once(worker_name, worker_type)
        if not did_work:
            time.sleep(max(0.2, min(args.interval, 30.0)))


if __name__ == "__main__":
    main()
