from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func, case
from typing import List

import models, schemas
from database import get_db
from services.auth import get_current_user
from services.email_templates import render, lead_variables, find_missing_variables, TEMPLATE_VARIABLES

router = APIRouter(prefix="/api/email_templates", tags=["email_templates"])

def _owned_template(template_id: int, db: Session, user: models.User) -> models.EmailTemplate:
    query = db.query(models.EmailTemplate).filter(models.EmailTemplate.id == template_id)
    if not user.is_admin:
        query = query.filter(models.EmailTemplate.user_id == user.id)
    template = query.first()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return template


@router.get("/variables")
def list_variables(user: models.User = Depends(get_current_user)):
    """Placeholders available to templates, e.g. {{first_name}}."""
    return {"variables": TEMPLATE_VARIABLES}


@router.get("/", response_model=List[schemas.EmailTemplateStats])
def list_templates(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    query = db.query(models.EmailTemplate)
    if not user.is_admin:
        query = query.filter(models.EmailTemplate.user_id == user.id)
    templates = query.order_by(
        models.EmailTemplate.ab_group.asc(), models.EmailTemplate.variant_label.asc(), models.EmailTemplate.id.asc()
    ).all()

    # One grouped pass over leads for per-template sent/replied attribution.
    rows = (
        db.query(
            models.Lead.template_id.label("template_id"),
            func.sum(case((models.Lead.email_logs.any(models.EmailLog.direction == "outbound"), 1), else_=0)).label("sent"),
            func.sum(case(((models.Lead.has_replied.is_(True)) | (models.Lead.status == "replied"), 1), else_=0)).label("replied"),
        )
        .filter(models.Lead.template_id.isnot(None))
        .group_by(models.Lead.template_id)
        .all()
    )
    stats_by_id = {r.template_id: (int(r.sent or 0), int(r.replied or 0)) for r in rows}

    result = []
    for t in templates:
        sent, replied = stats_by_id.get(t.id, (0, 0))
        data = schemas.EmailTemplateStats.model_validate(t)
        data.sent_count = sent
        data.replied_count = replied
        data.reply_rate = round(replied / sent, 4) if sent else 0.0
        result.append(data)
    return result


@router.post("/", response_model=schemas.EmailTemplate)
def create_template(payload: schemas.EmailTemplateCreate, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    template = models.EmailTemplate(**payload.model_dump(), user_id=user.id)
    db.add(template)
    db.commit()
    db.refresh(template)
    return template


@router.put("/{template_id}", response_model=schemas.EmailTemplate)
def update_template(template_id: int, payload: schemas.EmailTemplateCreate, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    template = _owned_template(template_id, db, user)
    for key, value in payload.model_dump().items():
        setattr(template, key, value)
    db.commit()
    db.refresh(template)
    return template


@router.delete("/{template_id}")
def delete_template(template_id: int, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    template = _owned_template(template_id, db, user)

    # Detach any workflows pointing at this template so drafting falls back to AI.
    referencing = db.query(models.Workflow).filter(models.Workflow.template_id == template_id)
    for wf in referencing.all():
        wf.template_id = None
    db.delete(template)
    db.commit()
    return {"ok": True}


@router.post("/preview")
def preview_template(payload: schemas.TemplatePreviewRequest, user: models.User = Depends(get_current_user)):
    """Render a subject/body against a sample lead so users can see the result."""
    sample = {
        "first_name": "Alex",
        "last_name": "Chen",
        "company_name": "Acme Components",
        "job_title": "Purchasing Manager",
        "domain": "acme-components.com",
    }
    variables = lead_variables(sample)
    return {
        "subject": render(payload.subject, variables),
        "body": render(payload.body, variables),
        "unknown_variables": sorted(set(find_missing_variables(payload.subject) + find_missing_variables(payload.body))),
    }
