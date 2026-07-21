from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import and_, case, func, or_
from typing import List, Literal, Optional
from pydantic import BaseModel, Field

import models, schemas
from database import get_db
from services.auth import get_current_user
from services.credits import InsufficientCreditsError

router = APIRouter(prefix="/api/leads", tags=["leads"])


class SendDraftRequest(BaseModel):
    draft: Optional[str] = None


class BulkLeadRequest(BaseModel):
    lead_ids: List[int] = Field(min_length=1, max_length=50)


class BulkLeadActionRequest(BulkLeadRequest):
    action: Literal["reject", "retry", "score", "delete", "blacklist", "move_pool", "set_stage"]
    target_pool_id: Optional[int] = None
    target_stage: Optional[str] = None


class LeadStageRequest(BaseModel):
    stage: str


def _owned_leads_query(db: Session, user: models.User):
    query = (
        db.query(models.Lead)
        .outerjoin(models.Workflow, models.Workflow.id == models.Lead.workflow_id)
        .outerjoin(models.ClientPool, models.ClientPool.id == models.Lead.client_pool_id)
    )
    if not user.is_admin:
        query = query.filter(or_(
            models.Workflow.user_id == user.id,
            models.ClientPool.user_id == user.id,
        ))
    return query


def _send_result_message(lead: models.Lead, send_result) -> str:
    if lead.status == "sent":
        return "Sent"
    if isinstance(send_result, dict) and send_result.get("message"):
        return str(send_result["message"])
    return lead.reply_snippet or "Send was not completed"


@router.get("", response_model=List[schemas.Lead])
@router.get("/", response_model=List[schemas.Lead], include_in_schema=False)
def list_leads(
    workflow_id: Optional[int] = Query(None),
    pool_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    research_status: Optional[Literal["valid", "insufficient", "missing"]] = Query(None),
    email_status: Optional[Literal["valid", "unknown", "invalid", "no_email"]] = Query(None),
    contact_history: Optional[Literal["never_contacted", "contacted"]] = Query(None),
    search: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Paginated lead listing with optional filters. Access controlled by workflow/pool ownership."""
    query = db.query(models.Lead)

    # Filter by workflow ownership
    if workflow_id:
        wf = db.query(models.Workflow).filter(models.Workflow.id == workflow_id).first()
        if not wf:
            raise HTTPException(status_code=404, detail="Workflow not found")
        if not user.is_admin and wf.user_id != user.id:
            raise HTTPException(status_code=404, detail="Workflow not found")
        query = query.filter(models.Lead.workflow_id == workflow_id)
    elif pool_id:
        pool = db.query(models.ClientPool).filter(models.ClientPool.id == pool_id).first()
        if not pool:
            raise HTTPException(status_code=404, detail="Pool not found")
        if not user.is_admin and pool.user_id != user.id:
            raise HTTPException(status_code=404, detail="Pool not found")
        query = query.filter(models.Lead.client_pool_id == pool_id)
    elif not user.is_admin:
        # Non-admin without specific filter: show only their leads via workflow or pool
        wf_ids = [w.id for w in db.query(models.Workflow.id).filter(models.Workflow.user_id == user.id).all()]
        pool_ids = [p.id for p in db.query(models.ClientPool.id).filter(models.ClientPool.user_id == user.id).all()]
        conditions = []
        if wf_ids:
            conditions.append(models.Lead.workflow_id.in_(wf_ids))
        if pool_ids:
            conditions.append(models.Lead.client_pool_id.in_(pool_ids))
        if conditions:
            query = query.filter(or_(*conditions))
        else:
            return []

    if status:
        query = query.filter(models.Lead.status == status)
    if research_status == "missing":
        query = query.filter(~models.Lead.brief.has())
    elif research_status:
        query = query.filter(models.Lead.brief.has(
            models.LeadBrief.research_status == research_status
        ))
    if email_status == "no_email":
        query = query.filter(or_(
            models.Lead.email.is_(None),
            models.Lead.email == "",
            models.Lead.email_validation_status == "no_email",
        ))
    elif email_status:
        query = query.filter(
            models.Lead.email.isnot(None),
            models.Lead.email != "",
            models.Lead.email_validation_status == email_status,
        )
    if contact_history == "never_contacted":
        query = query.filter(~models.Lead.email_logs.any(
            models.EmailLog.direction == "outbound"
        ))
    elif contact_history == "contacted":
        query = query.filter(models.Lead.email_logs.any(
            models.EmailLog.direction == "outbound"
        ))
    if search:
        search_term = f"%{search}%"
        query = query.filter(
            or_(models.Lead.company_name.ilike(search_term),
                models.Lead.domain.ilike(search_term),
                models.Lead.email.ilike(search_term),
                models.Lead.first_name.ilike(search_term),
                models.Lead.last_name.ilike(search_term))
        )

    leads = query.order_by(models.Lead.id.desc()).offset(skip).limit(limit).all()
    return leads


@router.get("/review-center")
def review_center(
    limit: int = Query(100, ge=1, le=200),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Return the user's operational review queues and their total counts."""
    base = _owned_leads_query(db, user)
    counts = (
        base.with_entities(
            func.sum(case((models.Lead.status == "drafted", 1), else_=0)).label("drafted"),
            func.sum(case((models.Lead.status == "needs_email", 1), else_=0)).label("needs_email"),
            func.sum(case((or_(
                models.Lead.status == "needs_research",
                models.Lead.automation_block_reason.like("research%"),
                models.Lead.automation_block_reason == "company_relevance_not_verified",
                models.Lead.automation_block_reason == "role_not_verified",
            ), 1), else_=0)).label("needs_research"),
            func.sum(case((models.Lead.status == "send_failed", 1), else_=0)).label("send_failed"),
            func.sum(case((
                or_(
                    models.Lead.reply_intent.in_(("interested", "more_info")),
                    and_(models.Lead.status == "replied", models.Lead.reply_intent.is_(None)),
                    models.Lead.handoff_recommended.is_(True),
                ),
                1,
            ), else_=0)).label("high_intent"),
        )
        .one()
    )

    def queue(condition):
        return (
            _owned_leads_query(db, user)
            .filter(condition)
            .order_by(
                models.Lead.handoff_recommended.desc(),
                models.Lead.fit_score.desc(),
                models.Lead.updated_at.desc(),
            )
            .limit(limit)
            .all()
        )

    return {
        "counts": {
            "drafted": int(counts.drafted or 0),
            "needs_email": int(counts.needs_email or 0),
            "needs_research": int(counts.needs_research or 0),
            "send_failed": int(counts.send_failed or 0),
            "high_intent": int(counts.high_intent or 0),
        },
        "queues": {
            "drafted": queue(models.Lead.status == "drafted"),
            "needs_email": queue(models.Lead.status == "needs_email"),
            "needs_research": queue(or_(
                models.Lead.status == "needs_research",
                models.Lead.automation_block_reason.like("research%"),
                models.Lead.automation_block_reason == "company_relevance_not_verified",
                models.Lead.automation_block_reason == "role_not_verified",
            )),
            "send_failed": queue(models.Lead.status == "send_failed"),
            "high_intent": queue(or_(
                models.Lead.reply_intent.in_(("interested", "more_info")),
                and_(models.Lead.status == "replied", models.Lead.reply_intent.is_(None)),
                models.Lead.handoff_recommended.is_(True),
            )),
        },
    }


@router.post("/bulk/action")
def bulk_lead_action(
    payload: BulkLeadActionRequest,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    lead_ids = list(dict.fromkeys(payload.lead_ids))
    leads = _owned_leads_query(db, user).filter(models.Lead.id.in_(lead_ids)).all()
    leads_by_id = {lead.id: lead for lead in leads}
    results = []

    # move_pool needs a validated, owned destination pool resolved once up front.
    target_pool = None
    if payload.action == "move_pool":
        if not payload.target_pool_id:
            raise HTTPException(status_code=400, detail="target_pool_id is required to move leads")
        pool_q = db.query(models.ClientPool).filter(models.ClientPool.id == payload.target_pool_id)
        if not user.is_admin:
            pool_q = pool_q.filter(models.ClientPool.user_id == user.id)
        target_pool = pool_q.first()
        if not target_pool:
            raise HTTPException(status_code=404, detail="Target pool not found")

    if payload.action == "set_stage":
        from services.sales_stages import is_valid_stage
        if not is_valid_stage(payload.target_stage):
            raise HTTPException(status_code=400, detail="Invalid sales stage")

    # Lazily imported helpers used only by some actions.
    persona_cache: dict[int, Optional[models.CustomerPersona]] = {}

    def resolve_persona(workflow: Optional[models.Workflow]) -> Optional[models.CustomerPersona]:
        if not workflow or not workflow.persona_id:
            return None
        if workflow.persona_id not in persona_cache:
            persona_cache[workflow.persona_id] = (
                db.query(models.CustomerPersona)
                .filter(models.CustomerPersona.id == workflow.persona_id)
                .first()
            )
        return persona_cache[workflow.persona_id]

    for lead_id in lead_ids:
        lead = leads_by_id.get(lead_id)
        if not lead:
            results.append({"lead_id": lead_id, "ok": False, "message": "Lead not found"})
            continue

        if payload.action == "reject":
            if lead.status != "drafted":
                results.append({
                    "lead_id": lead_id,
                    "ok": False,
                    "message": f"Only drafted leads can be rejected (current: {lead.status})",
                })
                continue
            lead.status = "rejected"
            results.append({"lead_id": lead_id, "ok": True, "status": "rejected"})
            continue

        if payload.action == "score":
            from services.lead_scoring import apply_lead_score
            workflow = db.query(models.Workflow).filter(models.Workflow.id == lead.workflow_id).first()
            score = apply_lead_score(db, lead, workflow=workflow, persona=resolve_persona(workflow))
            results.append({
                "lead_id": lead_id,
                "ok": True,
                "fit_score": score.score,
                "fit_grade": score.grade,
            })
            continue

        if payload.action == "delete":
            db.delete(lead)
            results.append({"lead_id": lead_id, "ok": True, "status": "deleted"})
            continue

        if payload.action == "blacklist":
            from services.suppression import suppress_lead
            suppress_lead(db, lead, reason="manual", source="bulk", status="rejected")
            results.append({"lead_id": lead_id, "ok": True, "status": "rejected"})
            continue

        if payload.action == "move_pool":
            lead.client_pool_id = target_pool.id
            results.append({"lead_id": lead_id, "ok": True, "pool_id": target_pool.id})
            continue

        if payload.action == "set_stage":
            lead.sales_stage = payload.target_stage
            results.append({"lead_id": lead_id, "ok": True, "sales_stage": payload.target_stage})
            continue

        # action == "retry"
        if lead.status != "send_failed":
            results.append({
                "lead_id": lead_id,
                "ok": False,
                "message": f"Only failed sends can be retried (current: {lead.status})",
            })
            continue
        if not (lead.ai_draft or "").strip():
            results.append({"lead_id": lead_id, "ok": False, "message": "Lead has no draft"})
            continue
        if not lead.email:
            results.append({"lead_id": lead_id, "ok": False, "message": "Lead has no email"})
            continue
        lead.status = "drafted"
        lead.send_fail_count = 0
        results.append({"lead_id": lead_id, "ok": True, "status": "drafted"})

    db.commit()

    # Fan out CRM webhooks for stage moves after the commit lands.
    if payload.action == "set_stage":
        for item in results:
            if item["ok"]:
                lead = leads_by_id.get(item["lead_id"])
                if lead:
                    _dispatch_stage_webhook(db, lead, payload.target_stage)

    return {
        "action": payload.action,
        "requested": len(lead_ids),
        "succeeded": sum(1 for item in results if item["ok"]),
        "failed": sum(1 for item in results if not item["ok"]),
        "results": results,
    }


@router.post("/bulk/send-drafts")
async def bulk_send_reviewed_drafts(
    payload: BulkLeadRequest,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    lead_ids = list(dict.fromkeys(payload.lead_ids))
    leads = _owned_leads_query(db, user).filter(models.Lead.id.in_(lead_ids)).all()
    leads_by_id = {lead.id: lead for lead in leads}
    workflow_ids = {lead.workflow_id for lead in leads if lead.workflow_id}
    workflows = db.query(models.Workflow).filter(models.Workflow.id.in_(workflow_ids)).all() if workflow_ids else []
    workflows_by_id = {workflow.id: workflow for workflow in workflows}

    from services.outbound_engine import send_lead_email

    results = []
    for lead_id in lead_ids:
        lead = leads_by_id.get(lead_id)
        if not lead:
            results.append({"lead_id": lead_id, "ok": False, "message": "Lead not found"})
            continue
        if lead.status != "drafted":
            results.append({
                "lead_id": lead_id,
                "ok": False,
                "message": f"Lead is not awaiting review (current: {lead.status})",
            })
            continue
        if not lead.email:
            results.append({"lead_id": lead_id, "ok": False, "message": "Lead has no recipient email"})
            continue
        if not (lead.ai_draft or "").strip():
            results.append({"lead_id": lead_id, "ok": False, "message": "Lead has no reviewed draft"})
            continue
        workflow = workflows_by_id.get(lead.workflow_id)
        if not workflow:
            results.append({"lead_id": lead_id, "ok": False, "message": "Workflow not found"})
            continue

        try:
            send_result = await send_lead_email(
                lead,
                workflow,
                db,
                raise_on_credit_error=True,
                manual_reviewed=True,
            )
            db.refresh(lead)
            results.append({
                "lead_id": lead_id,
                "ok": lead.status == "sent",
                "status": lead.status,
                "message": _send_result_message(lead, send_result),
            })
        except InsufficientCreditsError as exc:
            db.rollback()
            results.append({
                "lead_id": lead_id,
                "ok": False,
                "message": "Insufficient credits",
                "required": exc.required,
                "balance": exc.balance,
            })
        except Exception as exc:
            db.rollback()
            results.append({"lead_id": lead_id, "ok": False, "message": str(exc)})

    return {
        "requested": len(lead_ids),
        "succeeded": sum(1 for item in results if item["ok"]),
        "failed": sum(1 for item in results if not item["ok"]),
        "results": results,
    }


@router.get("/board")
def sales_board(
    pool_id: Optional[int] = Query(None),
    workflow_id: Optional[int] = Query(None),
    limit_per_stage: int = Query(100, ge=1, le=300),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Leads grouped into sales-pipeline columns for the kanban board."""
    from services.sales_stages import SALES_STAGES, effective_stage

    query = _owned_leads_query(db, user)
    if pool_id is not None:
        query = query.filter(models.Lead.client_pool_id == pool_id)
    if workflow_id is not None:
        query = query.filter(models.Lead.workflow_id == workflow_id)

    leads = query.order_by(models.Lead.updated_at.desc()).all()

    columns = {stage: [] for stage in SALES_STAGES}
    for lead in leads:
        stage = effective_stage(lead.sales_stage, lead.status)
        bucket = columns.setdefault(stage, [])
        if len(bucket) >= limit_per_stage:
            continue
        bucket.append({
            "id": lead.id,
            "company_name": lead.company_name,
            "domain": lead.domain,
            "first_name": lead.first_name,
            "last_name": lead.last_name,
            "email": lead.email,
            "job_title": lead.job_title,
            "status": lead.status,
            "fit_score": lead.fit_score,
            "fit_grade": lead.fit_grade,
            "sales_stage": stage,
        })

    totals = {stage: len([l for l in leads if effective_stage(l.sales_stage, l.status) == stage]) for stage in SALES_STAGES}
    return {
        "stages": SALES_STAGES,
        "columns": columns,
        "totals": totals,
        "total_leads": len(leads),
    }


@router.post("/{lead_id}/stage")
def set_lead_stage(
    lead_id: int,
    payload: LeadStageRequest,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Move a single lead to a sales stage (used by kanban drag-and-drop)."""
    from services.sales_stages import is_valid_stage
    if not is_valid_stage(payload.stage):
        raise HTTPException(status_code=400, detail="Invalid sales stage")
    lead = _verify_lead_ownership(lead_id, db, user)
    lead.sales_stage = payload.stage
    db.commit()
    _dispatch_stage_webhook(db, lead, payload.stage)
    return {"lead_id": lead_id, "sales_stage": payload.stage}


def _dispatch_stage_webhook(db: Session, lead: models.Lead, stage: str) -> None:
    """Push a lead.<stage> event to the owner's CRM webhooks (best-effort)."""
    try:
        from services.crm_webhooks import dispatch_event
        from services.notifications import owner_id_for_lead
        dispatch_event(db, owner_id_for_lead(db, lead), f"lead.{stage}", lead)
    except Exception:
        pass


def run_preference_learning_bg(persona_id: int):
    from database import SessionLocal
    from services.preference_learner import learn_preferences_for_persona
    db = SessionLocal()
    try:
        learn_preferences_for_persona(db, persona_id)
    finally:
        db.close()

def _verify_lead_ownership(lead_id: int, db: Session, user: models.User) -> models.Lead:
    """Verify the current user owns the lead (via workflow or client pool). Returns the lead or raises 404."""
    query = db.query(models.Lead).outerjoin(
        models.Workflow, models.Workflow.id == models.Lead.workflow_id
    ).outerjoin(
        models.ClientPool, models.ClientPool.id == models.Lead.client_pool_id
    ).filter(models.Lead.id == lead_id)
    if not user.is_admin:
        query = query.filter(or_(models.Workflow.user_id == user.id, models.ClientPool.user_id == user.id))
    lead = query.first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


@router.post("/{lead_id}/research/retry")
def retry_lead_research(
    lead_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    lead = _verify_lead_ownership(lead_id, db, user)
    from services.research_quality import is_usable_company_domain, utcnow

    if not is_usable_company_domain(lead.domain):
        raise HTTPException(status_code=400, detail="Set a valid company domain before retrying research")
    has_outbound = db.query(models.EmailLog.id).filter(
        models.EmailLog.lead_id == lead.id,
        models.EmailLog.direction == "outbound",
    ).first() is not None
    lead.status = "needs_research" if not has_outbound else lead.status
    lead.automation_block_reason = "research_refresh_queued"
    lead.automation_blocked_at = utcnow()
    db.commit()

    from services.research_agent import refresh_lead_research

    background_tasks.add_task(refresh_lead_research, lead.id)
    return {"ok": True, "lead_id": lead.id, "status": "queued"}


@router.put("/{lead_id}", response_model=schemas.Lead)
def update_lead(lead_id: int, lead_update: schemas.LeadCreate, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    db_lead = _verify_lead_ownership(lead_id, db, user)

    update_data = lead_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_lead, key, value)

    db.commit()
    db.refresh(db_lead)
    return db_lead

@router.delete("/{lead_id}")
def delete_lead(lead_id: int, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    db_lead = _verify_lead_ownership(lead_id, db, user)
    # Cascade-delete child records
    db.query(models.EmailLog).filter(models.EmailLog.lead_id == lead_id).delete(synchronize_session=False)
    db.query(models.LeadFeedback).filter(models.LeadFeedback.lead_id == lead_id).delete(synchronize_session=False)
    from models import MessageLog, LeadBrief
    db.query(MessageLog).filter(MessageLog.lead_id == lead_id).delete(synchronize_session=False)
    db.query(LeadBrief).filter(LeadBrief.lead_id == lead_id).delete(synchronize_session=False)
    db.delete(db_lead)
    db.commit()
    return {"ok": True}


@router.post("/{lead_id}/rate")
def rate_lead(
    lead_id: int,
    payload: schemas.LeadRateRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """
    Rate a lead as positive or negative. This data feeds the RLHF feedback loop
    to improve AI search accuracy over time.
    """
    if payload.rating not in ("positive", "negative"):
        raise HTTPException(status_code=400, detail="Rating must be 'positive' or 'negative'")

    db_lead = _verify_lead_ownership(lead_id, db, user)

    # Update the lead's rating
    db_lead.user_rating = payload.rating
    
    # Create a feedback record with a snapshot for future analysis
    snapshot = {
        "domain": db_lead.domain,
        "company_name": db_lead.company_name,
        "job_title": db_lead.job_title,
        "email": db_lead.email,
        "status": db_lead.status,
        "fit_score": db_lead.fit_score,
        "fit_grade": db_lead.fit_grade,
        "source_channel": db_lead.source_channel,
    }
    feedback = models.LeadFeedback(
        user_id=user.id,
        lead_id=lead_id,
        workflow_id=db_lead.workflow_id,
        rating=payload.rating,
        reason=payload.reason,
        lead_snapshot=snapshot,
    )
    db.add(feedback)

    workflow = db.query(models.Workflow).filter(models.Workflow.id == db_lead.workflow_id).first()
    persona = None
    if workflow and workflow.persona_id:
        persona = db.query(models.CustomerPersona).filter(models.CustomerPersona.id == workflow.persona_id).first()
    from services.lead_scoring import apply_lead_score
    apply_lead_score(db, db_lead, workflow=workflow, persona=persona)

    db.commit()

    # Trigger preference learning in the background if the workflow is associated with a persona
    if workflow and workflow.persona_id:
        background_tasks.add_task(run_preference_learning_bg, workflow.persona_id)
    
    return {
        "ok": True,
        "lead_id": lead_id,
        "rating": payload.rating,
    }


@router.post("/{lead_id}/send-draft")
async def send_reviewed_draft(
    lead_id: int,
    payload: SendDraftRequest,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """
    Send one reviewed AI draft. This is the commercial-safe path when
    OUTBOUND_AUTO_SEND_DRAFTS is disabled.
    """
    db_lead = _verify_lead_ownership(lead_id, db, user)
    if db_lead.status in {"unsubscribed", "rejected"}:
        raise HTTPException(status_code=400, detail=f"Lead is {db_lead.status}; sending is blocked")
    if not db_lead.email:
        raise HTTPException(status_code=400, detail="Lead has no recipient email")
    if not db_lead.workflow_id:
        raise HTTPException(status_code=400, detail="Lead has no workflow")

    draft = (payload.draft or "").strip()
    if draft:
        db_lead.ai_draft = draft
        db_lead.status = "drafted"
        db.commit()
    if not (db_lead.ai_draft or "").strip():
        raise HTTPException(status_code=400, detail="Lead has no reviewed draft to send")

    workflow = db.query(models.Workflow).filter(models.Workflow.id == db_lead.workflow_id).first()
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    from services.outbound_engine import send_lead_email
    try:
        send_result = await send_lead_email(
            db_lead,
            workflow,
            db,
            raise_on_credit_error=True,
            manual_reviewed=True,
        )
    except InsufficientCreditsError as exc:
        raise HTTPException(
            status_code=402,
            detail={
                "message": "Insufficient credits",
                "action": exc.action,
                "required": exc.required,
                "balance": exc.balance,
            },
        ) from exc
    db.refresh(db_lead)
    return {
        "ok": db_lead.status == "sent",
        "lead_id": db_lead.id,
        "status": db_lead.status,
        "message": _send_result_message(db_lead, send_result),
    }


@router.post("/{lead_id}/score", response_model=schemas.LeadScoreResponse)
def score_lead(
    lead_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """
    Recalculate the AI fit score for a lead using its workflow persona,
    website brief, validation state, channels, and user feedback.
    """
    query = db.query(models.Lead).outerjoin(
        models.Workflow, models.Workflow.id == models.Lead.workflow_id
    ).outerjoin(
        models.ClientPool, models.ClientPool.id == models.Lead.client_pool_id
    ).filter(models.Lead.id == lead_id)
    if not user.is_admin:
        query = query.filter(or_(models.Workflow.user_id == user.id, models.ClientPool.user_id == user.id))
    db_lead = query.first()
    if not db_lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    workflow = db.query(models.Workflow).filter(models.Workflow.id == db_lead.workflow_id).first()
    persona = None
    if workflow and workflow.persona_id:
        persona = db.query(models.CustomerPersona).filter(models.CustomerPersona.id == workflow.persona_id).first()

    from services.lead_scoring import apply_lead_score
    score = apply_lead_score(db, db_lead, workflow=workflow, persona=persona)
    db.commit()
    return schemas.LeadScoreResponse(
        lead_id=db_lead.id,
        fit_score=score.score,
        fit_grade=score.grade,
        handoff_recommended=score.handoff_recommended,
        qualification_notes=score.notes,
    )


@router.get("/feedback-summary", response_model=schemas.FeedbackSummary)
def get_feedback_summary(
    workflow_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """
    Returns a summary of positive/negative feedback for a workflow.
    Used to display feedback stats and to inject RLHF context into AI prompts.
    """
    query = db.query(models.LeadFeedback).filter(models.LeadFeedback.user_id == user.id)
    if workflow_id:
        query = query.filter(models.LeadFeedback.workflow_id == workflow_id)

    all_feedback = query.order_by(models.LeadFeedback.created_at.desc()).all()

    total_positive = sum(1 for f in all_feedback if f.rating == "positive")
    total_negative = sum(1 for f in all_feedback if f.rating == "negative")

    # Extract recent domains for positive/negative to show patterns
    recent_positive_domains = []
    recent_negative_domains = []
    for f in all_feedback[:50]:
        domain = (f.lead_snapshot or {}).get("domain", "") if f.lead_snapshot else ""
        if not domain:
            continue
        if f.rating == "positive" and domain not in recent_positive_domains:
            recent_positive_domains.append(domain)
        elif f.rating == "negative" and domain not in recent_negative_domains:
            recent_negative_domains.append(domain)

    return schemas.FeedbackSummary(
        total_positive=total_positive,
        total_negative=total_negative,
        recent_positive_domains=recent_positive_domains[:10],
        recent_negative_domains=recent_negative_domains[:10],
    )


@router.get("/{lead_id}", response_model=schemas.Lead)
def read_lead(
    lead_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Return a single lead owned by the current user."""
    return _verify_lead_ownership(lead_id, db, user)


@router.get("/{lead_id}/brief", response_model=schemas.LeadBriefResponse)
def get_lead_brief(
    lead_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """
    Get the AI research brief for a specific lead.
    """
    query = db.query(models.Lead).outerjoin(
        models.Workflow, models.Workflow.id == models.Lead.workflow_id
    ).outerjoin(
        models.ClientPool, models.ClientPool.id == models.Lead.client_pool_id
    ).filter(models.Lead.id == lead_id)
    if not user.is_admin:
        query = query.filter(or_(models.Workflow.user_id == user.id, models.ClientPool.user_id == user.id))
    db_lead = query.first()
    if not db_lead:
        raise HTTPException(status_code=404, detail="Lead not found")
        
    if not db_lead.brief:
        raise HTTPException(status_code=404, detail="Brief not found for this lead")

    return db_lead.brief


@router.post("/{lead_id}/brief", response_model=schemas.LeadBriefResponse)
async def generate_lead_brief(
    lead_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Run AI deep-research on demand and (re)generate this lead's brief.

    The brief is otherwise only produced by the outbound engine, so pool leads
    that were never processed had no brief to show. This generates it on request.
    """
    db_lead = _verify_lead_ownership(lead_id, db, user)
    domain = (db_lead.domain or "").strip()
    if not domain:
        raise HTTPException(status_code=400, detail="Lead has no domain/website to research")

    from services.research_agent import build_and_save_lead_brief
    try:
        ok = await build_and_save_lead_brief(lead_id, domain)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AI research failed: {exc}") from exc
    if not ok:
        raise HTTPException(status_code=502, detail="AI research did not return a usable brief; please try again")

    # The brief was written in a separate session — read it back fresh.
    db.expire_all()
    brief = db.query(models.LeadBrief).filter(models.LeadBrief.lead_id == lead_id).first()
    if not brief:
        raise HTTPException(status_code=502, detail="Brief generation produced no result")
    return brief
