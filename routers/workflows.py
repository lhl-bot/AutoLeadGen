from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload, subqueryload
from sqlalchemy import and_, case, func, or_
from typing import List
import os

import models, schemas
from database import get_db
from services.auth import get_current_user

router = APIRouter(prefix="/api/workflows", tags=["workflows"])

@router.get("/playbook-presets")
def get_playbook_presets(user: models.User = Depends(get_current_user)):
    """Return all available playbook presets for the workflow creation UI."""
    from services.playbook_presets import get_all_presets
    return get_all_presets()


def _assert_workflow_dependencies_owned(workflow: schemas.WorkflowCreate, db: Session, user: models.User) -> None:
    if user.is_admin:
        return
    if workflow.client_pool_id:
        pool = db.query(models.ClientPool).filter(
            models.ClientPool.id == workflow.client_pool_id,
            models.ClientPool.user_id == user.id,
        ).first()
        if not pool:
            raise HTTPException(status_code=404, detail="Client pool not found")
    if workflow.persona_id:
        persona = db.query(models.CustomerPersona).filter(
            models.CustomerPersona.id == workflow.persona_id,
            models.CustomerPersona.user_id == user.id,
        ).first()
        if not persona:
            raise HTTPException(status_code=404, detail="Persona not found")
    if workflow.email_account_ids:
        owned_count = db.query(models.EmailAccount).filter(
            models.EmailAccount.id.in_(workflow.email_account_ids),
            models.EmailAccount.user_id == user.id,
        ).count()
        if owned_count != len(set(workflow.email_account_ids)):
            raise HTTPException(status_code=404, detail="Email account not found")


@router.get("/", response_model=List[schemas.WorkflowWithDetails])
def read_workflows(skip: int = 0, limit: int = 100, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    from sqlalchemy import text
    
    # === Query 1: Workflows + lead stats in one shot (filtered by user_id unless admin) ===
    where_clause = "WHERE w.user_id = :user_id" if not user.is_admin else ""
    sql = text(f"""
        SELECT w.*,
               cp.name AS pool_name,
               p.name AS persona_name,
               COALESCE(ls.total, 0)       AS leads_count,
               COALESCE(ls.contactable, 0) AS contactable_count,
               COALESCE(ls.needs_email, 0) AS needs_email_count,
               COALESCE(ls.replied, 0)     AS replied_count
               ,COALESCE(ls.bounced, 0)    AS bounced_count
               ,COALESCE(ls.low_score, 0)  AS low_score_count
               ,COALESCE(es.outbound_count, 0) AS outbound_count
        FROM workflows w
        LEFT JOIN client_pools cp ON w.client_pool_id = cp.id
        LEFT JOIN customer_personas p ON w.persona_id = p.id
        LEFT JOIN (
            SELECT workflow_id,
                   COUNT(id) AS total,
                   SUM(CASE WHEN email IS NOT NULL AND email <> '' AND status NOT IN ('bounced', 'rejected', 'invalid_email', 'unsubscribed', 'low_score', 'needs_email') THEN 1 ELSE 0 END) AS contactable,
                   SUM(CASE WHEN status = 'needs_email' THEN 1 ELSE 0 END) AS needs_email,
                   SUM(CASE WHEN has_replied = 1 OR status = 'replied' THEN 1 ELSE 0 END) AS replied,
                   SUM(status = 'bounced') AS bounced,
                   SUM(status = 'low_score') AS low_score,
                   AVG(fit_score) AS avg_fit_score,
                   SUM(CASE WHEN handoff_recommended = 1 THEN 1 ELSE 0 END) AS handoff_count
            FROM leads
            GROUP BY workflow_id
        ) ls ON ls.workflow_id = w.id
        LEFT JOIN (
            SELECT l.workflow_id,
                   COUNT(el.id) AS outbound_count
            FROM email_logs el
            JOIN leads l ON l.id = el.lead_id
            WHERE el.direction = 'outbound'
            GROUP BY l.workflow_id
        ) es ON es.workflow_id = w.id
        {where_clause}
        ORDER BY w.id DESC
        LIMIT :limit OFFSET :skip
    """)
    params = {"limit": limit, "skip": skip}
    if not user.is_admin:
        params["user_id"] = user.id
    rows = db.execute(sql, params).mappings().all()
    if not rows:
        return []
    
    # === Query 2: All email accounts for these workflows ===
    wf_ids = [r["id"] for r in rows]
    placeholders = ",".join([str(wid) for wid in wf_ids])
    we_sql = text(f"""
        SELECT we.workflow_id, ea.id, ea.email, ea.display_name,
               ea.smtp_host, ea.smtp_port, ea.smtp_user,
               ea.use_tls, ea.use_ssl, ea.imap_host, ea.imap_port, ea.created_at
        FROM workflow_emails we
        JOIN email_accounts ea ON we.email_account_id = ea.id
        WHERE we.workflow_id IN ({placeholders})
    """)
    we_rows = db.execute(we_sql).mappings().all()
    
    email_map = {}
    for we in we_rows:
        wid = we["workflow_id"]
        if wid not in email_map:
            email_map[wid] = []
        email_map[wid].append({
            "id": we["id"], "email": we["email"], "display_name": we["display_name"],
            "smtp_host": we["smtp_host"], "smtp_port": we["smtp_port"], "smtp_user": we["smtp_user"],
            "use_tls": we["use_tls"], "use_ssl": we["use_ssl"],
            "imap_host": we["imap_host"], "imap_port": we["imap_port"],
            "created_at": we["created_at"],
        })
    
    # === Build response purely from memory, zero extra DB calls ===
    results = []
    for r in rows:
        outbound_count = int(r["outbound_count"] or 0)
        bounced_count = int(r["bounced_count"] or 0)
        bounce_rate = (bounced_count / outbound_count) if outbound_count else 0
        try:
            pause_threshold = float(os.environ.get("EMAIL_BOUNCE_RATE_PAUSE_THRESHOLD", "0.08"))
        except (TypeError, ValueError):
            pause_threshold = 0.08
        try:
            min_sent_for_pause = int(os.environ.get("EMAIL_MIN_SENT_FOR_BOUNCE_PAUSE", "20"))
        except (TypeError, ValueError):
            min_sent_for_pause = 20
        email_paused = outbound_count >= min_sent_for_pause and bounce_rate >= pause_threshold
        results.append({
            "id": r["id"], "name": r["name"], "status": r["status"],
            "search_keywords": r["search_keywords"], "target_positions": r["target_positions"],
            "ai_prompt": r.get("ai_prompt"), "email_signature": r.get("email_signature"),
            "client_pool_id": r["client_pool_id"],
            "persona_id": r.get("persona_id"),
            "daily_limit": r["daily_limit"],
            "send_interval_min": r["send_interval_min"], "send_interval_max": r["send_interval_max"],
            "auto_followup": bool(r["auto_followup"]), "max_followups": r["max_followups"],
            "search_offset": r["search_offset"], "created_at": r["created_at"],
            "playbook_type": r.get("playbook_type", "standard"),
            "domain_warmup_enabled": bool(r.get("domain_warmup_enabled", False)),
            "pilot_goal": r.get("pilot_goal"),
            "target_customer_type": r.get("target_customer_type"),
            "target_region": r.get("target_region"),
            "product_focus": r.get("product_focus"),
            "manual_handoff_triggers": r.get("manual_handoff_triggers"),
            "search_sources": r.get("search_sources"),
            "competitor_names": r.get("competitor_names"),
            "trade_show_names": r.get("trade_show_names"),
            "enable_linkedin": bool(r.get("enable_linkedin", False)),
            "enable_whatsapp": bool(r.get("enable_whatsapp", False)),
            "linkedin_invite_message": r.get("linkedin_invite_message"),
            "whatsapp_message_template": r.get("whatsapp_message_template"),
            "linkedin_daily_limit": r.get("linkedin_daily_limit", 20),
            "emails": email_map.get(r["id"], []),
            "leads_count": r["leads_count"],
            "contactable_count": r["contactable_count"],
            "needs_email_count": r["needs_email_count"],
            "replied_count": r["replied_count"],
            "bounced_count": bounced_count,
            "low_score_count": int(r.get("low_score_count") or 0),
            "outbound_count": outbound_count,
            "bounce_rate": bounce_rate,
            "email_paused": email_paused,
            "avg_fit_score": float(r.get("avg_fit_score") or 0),
            "handoff_count": int(r.get("handoff_count") or 0),
            "client_pool_name": r["pool_name"],
            "persona_name": r.get("persona_name"),
        })
    return results

@router.post("/", response_model=schemas.Workflow)
def create_workflow(workflow: schemas.WorkflowCreate, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    _assert_workflow_dependencies_owned(workflow, db, user)
    wf_data = workflow.model_dump(exclude={"email_account_ids"})
    db_wf = models.Workflow(**wf_data, user_id=user.id)
    db.add(db_wf)
    db.commit()
    db.refresh(db_wf)
    
    for email_id in workflow.email_account_ids:
        db_we = models.WorkflowEmail(workflow_id=db_wf.id, email_account_id=email_id)
        db.add(db_we)
    db.commit()
    db.refresh(db_wf)
    return db_wf

@router.put("/{workflow_id}", response_model=schemas.Workflow)
def update_workflow(workflow_id: int, workflow: schemas.WorkflowCreate, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    _assert_workflow_dependencies_owned(workflow, db, user)
    query = db.query(models.Workflow).filter(models.Workflow.id == workflow_id)
    if not user.is_admin:
        query = query.filter(models.Workflow.user_id == user.id)
    db_wf = query.first()
    if not db_wf:
        raise HTTPException(status_code=404, detail="Workflow not found")

    search_scope_changed = (
        db_wf.search_keywords != workflow.search_keywords
        or db_wf.target_positions != workflow.target_positions
    )
    update_data = workflow.model_dump(exclude={"email_account_ids"})
    for key, value in update_data.items():
        setattr(db_wf, key, value)

    if search_scope_changed:
        db_wf.search_offset = 0
        db.query(models.ProcessedDomain).filter(models.ProcessedDomain.workflow_id == workflow_id).delete(synchronize_session=False)
        
    db.query(models.WorkflowEmail).filter(models.WorkflowEmail.workflow_id == workflow_id).delete()
    for email_id in workflow.email_account_ids:
        db_we = models.WorkflowEmail(workflow_id=workflow_id, email_account_id=email_id)
        db.add(db_we)
        
    db.commit()
    db.refresh(db_wf)
    return db_wf


from typing import Optional
from pydantic import BaseModel
class KeywordGenerateRequest(BaseModel):
    persona_id: Optional[int] = None
    description: Optional[str] = None


@router.post("/generate-keywords")
def generate_keywords(
    req: KeywordGenerateRequest,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    persona_text = ""
    if req.persona_id:
        persona_query = db.query(models.CustomerPersona).filter(models.CustomerPersona.id == req.persona_id)
        if not user.is_admin:
            persona_query = persona_query.filter(models.CustomerPersona.user_id == user.id)
        persona = persona_query.first()
        if persona:
            persona_text = (
                f"Name: {persona.name}\n"
                f"Target Industry: {persona.target_industry or ''}\n"
                f"Keywords: {persona.target_keywords or ''}\n"
                f"Roles: {persona.target_roles or ''}\n"
                f"Customer Types: {persona.customer_types or ''}\n"
                f"Product Categories: {persona.product_categories or ''}\n"
            )
    if req.description:
        persona_text += f"\nAdditional Context / Product Focus:\n{req.description}"
        
    if not persona_text.strip():
        raise HTTPException(status_code=400, detail="Must provide persona_id or description")
        
    from services.ai_writer import generate_search_keywords
    keywords = generate_search_keywords(persona_text)
    return {"keywords": keywords}


@router.post("/{workflow_id}/search")
def run_workflow_search(
    workflow_id: int,
    batch_lead_limit: int = Query(25, ge=1, le=200),
    max_domains: int = Query(80, ge=10, le=250),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    query = db.query(models.Workflow).filter(models.Workflow.id == workflow_id)
    if not user.is_admin:
        query = query.filter(models.Workflow.user_id == user.id)
    db_wf = query.first()
    if not db_wf:
        raise HTTPException(status_code=404, detail="Workflow not found")

    from services.outbound_engine import is_workflow_search_running, launch_workflow_search
    if is_workflow_search_running(workflow_id):
        return {"started": False, "message": "Search is already running for this workflow."}

    launch_workflow_search(workflow_id, batch_lead_limit, max_domains, ignore_cooldown=True)
    return {
        "started": True,
        "workflow_id": workflow_id,
        "message": "Lead search started in the background."
    }


@router.get("/{workflow_id}", response_model=schemas.WorkflowWithDetails)
def read_workflow(workflow_id: int, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    query = (
        db.query(models.Workflow)
        .options(
            joinedload(models.Workflow.workflow_emails)
            .joinedload(models.WorkflowEmail.email_account)
        )
        .filter(models.Workflow.id == workflow_id)
    )
    if not user.is_admin:
        query = query.filter(models.Workflow.user_id == user.id)
    wf = query.first()
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found")
    workflow_data = schemas.Workflow.model_validate(wf).model_dump()
    emails = [
        schemas.EmailAccount.model_validate(binding.email_account)
        for binding in wf.workflow_emails
        if binding.email_account is not None
    ]
    return schemas.WorkflowWithDetails(**workflow_data, emails=emails)


@router.get("/{workflow_id}/pilot-report", response_model=schemas.WorkflowPilotReport)
def get_workflow_pilot_report(
    workflow_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    query = db.query(models.Workflow).filter(models.Workflow.id == workflow_id)
    if not user.is_admin:
        query = query.filter(models.Workflow.user_id == user.id)
    workflow = query.first()
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    aggregate = (
        db.query(
            func.count(models.Lead.id).label("total"),
            func.sum(case((
                or_(models.Lead.fit_score >= 65, models.Lead.user_rating == "positive"),
                1,
            ), else_=0)).label("matched"),
            func.sum(case((
                models.Lead.email.isnot(None) & (models.Lead.email != ""),
                1,
            ), else_=0)).label("email_leads"),
            func.sum(case((
                (models.Lead.email.isnot(None) & (models.Lead.email != ""))
                & or_(
                    models.Lead.email_verified.is_(True),
                    models.Lead.email_validation_status == "valid",
                ),
                1,
            ), else_=0)).label("valid_emails"),
            func.sum(case((
                models.Lead.email_logs.any(models.EmailLog.direction == "outbound"),
                1,
            ), else_=0)).label("contacted"),
            func.sum(case((
                or_(models.Lead.has_replied.is_(True), models.Lead.status == "replied"),
                1,
            ), else_=0)).label("replied"),
            func.sum(case((
                models.Lead.handoff_recommended.is_(True),
                1,
            ), else_=0)).label("handoff"),
            func.sum(case((
                or_(
                    models.Lead.reply_intent.in_(("interested", "more_info")),
                    and_(models.Lead.status == "replied", models.Lead.reply_intent.is_(None)),
                    models.Lead.handoff_recommended.is_(True),
                ),
                1,
            ), else_=0)).label("high_intent"),
            func.avg(models.Lead.fit_score).label("avg_fit_score"),
        )
        .filter(models.Lead.workflow_id == workflow_id)
        .one()
    )
    total = int(aggregate.total or 0)
    if total == 0:
        return schemas.WorkflowPilotReport(workflow_id=workflow_id)

    channel_rows = (
        db.query(
            func.coalesce(models.Lead.source_channel, "unknown").label("channel"),
            func.count(models.Lead.id).label("lead_count"),
        )
        .filter(models.Lead.workflow_id == workflow_id)
        .group_by(func.coalesce(models.Lead.source_channel, "unknown"))
        .order_by(func.count(models.Lead.id).desc())
        .limit(5)
        .all()
    )
    top_channels = [
        f"{row.channel}: {row.lead_count}"
        for row in channel_rows
    ]

    matched = int(aggregate.matched or 0)
    email_leads = int(aggregate.email_leads or 0)
    valid_emails = int(aggregate.valid_emails or 0)
    contacted = int(aggregate.contacted or 0)
    replied = int(aggregate.replied or 0)
    return schemas.WorkflowPilotReport(
        workflow_id=workflow_id,
        leads_total=total,
        matched_leads=matched,
        match_rate=matched / total,
        email_valid_rate=valid_emails / email_leads if email_leads else 0,
        reply_rate=replied / contacted if contacted else 0,
        handoff_count=int(aggregate.handoff or 0),
        high_intent_count=int(aggregate.high_intent or 0),
        avg_fit_score=float(aggregate.avg_fit_score or 0),
        top_channels=top_channels,
    )

@router.delete("/{workflow_id}")
def delete_workflow(workflow_id: int, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    query = db.query(models.Workflow).filter(models.Workflow.id == workflow_id)
    if not user.is_admin:
        query = query.filter(models.Workflow.user_id == user.id)
    db_wf = query.first()
    if not db_wf:
        raise HTTPException(status_code=404, detail="Workflow not found")
        
    # Bulk delete associated ProcessedDomains
    db.query(models.ProcessedDomain).filter(models.ProcessedDomain.workflow_id == workflow_id).delete(synchronize_session=False)
    
    # Bulk delete associated EmailLogs
    lead_ids_query = db.query(models.Lead.id).filter(models.Lead.workflow_id == workflow_id)
    db.query(models.EmailLog).filter(models.EmailLog.lead_id.in_(lead_ids_query)).delete(synchronize_session=False)
    
    # Bulk delete associated Leads
    db.query(models.Lead).filter(models.Lead.workflow_id == workflow_id).delete(synchronize_session=False)
    
    # Bulk delete associated WorkflowEmails
    db.query(models.WorkflowEmail).filter(models.WorkflowEmail.workflow_id == workflow_id).delete(synchronize_session=False)
    
    db.delete(db_wf)
    db.commit()
    return {"ok": True}

@router.post("/{workflow_id}/toggle")
def toggle_workflow(workflow_id: int, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    query = db.query(models.Workflow).filter(models.Workflow.id == workflow_id)
    if not user.is_admin:
        query = query.filter(models.Workflow.user_id == user.id)
    db_wf = query.first()
    if not db_wf:
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    db_wf.status = "active" if db_wf.status == "paused" else "paused"
    db.commit()
    return {"status": db_wf.status}
