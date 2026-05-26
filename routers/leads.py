from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import or_
from typing import List, Optional

import models, schemas
from database import get_db
from services.auth import get_current_user

router = APIRouter(prefix="/api/leads", tags=["leads"])

def run_preference_learning_bg(persona_id: int):
    from database import SessionLocal
    from services.preference_learner import learn_preferences_for_persona
    db = SessionLocal()
    try:
        learn_preferences_for_persona(db, persona_id)
    finally:
        db.close()

@router.put("/{lead_id}", response_model=schemas.Lead)
def update_lead(lead_id: int, lead_update: schemas.LeadCreate, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    db_lead = db.query(models.Lead).filter(models.Lead.id == lead_id).first()
    if not db_lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    update_data = lead_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_lead, key, value)

    db.commit()
    db.refresh(db_lead)
    return db_lead

@router.delete("/{lead_id}")
def delete_lead(lead_id: int, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    db_lead = db.query(models.Lead).filter(models.Lead.id == lead_id).first()
    if not db_lead:
        raise HTTPException(status_code=404, detail="Lead not found")
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

    db_lead = db.query(models.Lead).filter(models.Lead.id == lead_id).first()
    if not db_lead:
        raise HTTPException(status_code=404, detail="Lead not found")

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
