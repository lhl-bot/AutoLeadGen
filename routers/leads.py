from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import or_
from typing import List, Optional
from pydantic import BaseModel

import models, schemas
from database import get_db
from services.auth import get_current_user
from services.credits import InsufficientCreditsError

router = APIRouter(prefix="/api/leads", tags=["leads"])


class SendDraftRequest(BaseModel):
    draft: Optional[str] = None


@router.get("", response_model=List[schemas.Lead])
@router.get("/", response_model=List[schemas.Lead], include_in_schema=False)
def list_leads(
    workflow_id: Optional[int] = Query(None),
    pool_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
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
        await send_lead_email(db_lead, workflow, db, raise_on_credit_error=True)
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
        "message": "Sent" if db_lead.status == "sent" else (db_lead.reply_snippet or "Send was not completed"),
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
