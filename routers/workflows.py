from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload, subqueryload
from sqlalchemy import func
from typing import List
import os

import models, schemas
from database import get_db
from services.auth import get_current_user

router = APIRouter(prefix="/api/workflows", tags=["workflows"])

@router.get("/playbook-presets")
def get_playbook_presets():
    """Return all available playbook presets for the workflow creation UI."""
    from services.playbook_presets import get_all_presets
    return get_all_presets()


@router.get("/", response_model=List[schemas.WorkflowWithDetails])
def read_workflows(skip: int = 0, limit: int = 100, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    from sqlalchemy import text
    
    # === Query 1: Workflows + lead stats in one shot (filtered by user_id unless admin) ===
    where_clause = "WHERE w.user_id = :user_id" if not user.is_admin else ""
    sql = text(f"""
        SELECT w.*,
               cp.name AS pool_name,
               COALESCE(ls.total, 0)       AS leads_count,
               COALESCE(ls.contactable, 0) AS contactable_count,
               COALESCE(ls.needs_email, 0) AS needs_email_count,
               COALESCE(ls.replied, 0)     AS replied_count
               ,COALESCE(ls.bounced, 0)    AS bounced_count
               ,COALESCE(es.outbound_count, 0) AS outbound_count
        FROM workflows w
        LEFT JOIN client_pools cp ON w.client_pool_id = cp.id
        LEFT JOIN (
            SELECT workflow_id,
                   SUM(CASE WHEN status NOT IN ('rejected', 'invalid_email', 'bounced', 'unsubscribed', 'needs_email') THEN 1 ELSE 0 END) AS total,
                   SUM(CASE WHEN email IS NOT NULL AND email <> '' AND status NOT IN ('bounced', 'rejected', 'invalid_email', 'unsubscribed') THEN 1 ELSE 0 END) AS contactable,
                   SUM(CASE WHEN status = 'needs_email' THEN 1 ELSE 0 END) AS needs_email,
                   SUM(status = 'replied') AS replied,
                   SUM(status = 'bounced') AS bounced,
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
            "outbound_count": outbound_count,
            "bounce_rate": bounce_rate,
            "email_paused": email_paused,
            "avg_fit_score": float(r.get("avg_fit_score") or 0),
            "handoff_count": int(r.get("handoff_count") or 0),
            "client_pool_name": r["pool_name"],
        })
    return results

@router.post("/", response_model=schemas.Workflow)
def create_workflow(workflow: schemas.WorkflowCreate, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
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

    leads = db.query(models.Lead).filter(models.Lead.workflow_id == workflow_id).all()
    total = len(leads)
    if total == 0:
        return schemas.WorkflowPilotReport(workflow_id=workflow_id)

    matched = [
        lead for lead in leads
        if (lead.fit_score is not None and lead.fit_score >= 65) or lead.user_rating == "positive"
    ]
    email_leads = [lead for lead in leads if lead.email]
    valid_emails = [
        lead for lead in email_leads
        if lead.email_verified or lead.email_validation_status == "valid"
    ]
    contacted = [lead for lead in leads if lead.status in {"sent", "replied"}]
    replied = [lead for lead in leads if lead.status == "replied"]
    handoff = [lead for lead in leads if lead.handoff_recommended]
    scores = [lead.fit_score for lead in leads if lead.fit_score is not None]

    channel_counts = {}
    for lead in leads:
        channel = lead.source_channel or "unknown"
        channel_counts[channel] = channel_counts.get(channel, 0) + 1
    top_channels = [
        f"{name}: {count}" for name, count in sorted(
            channel_counts.items(), key=lambda item: item[1], reverse=True
        )[:5]
    ]

    return schemas.WorkflowPilotReport(
        workflow_id=workflow_id,
        leads_total=total,
        matched_leads=len(matched),
        match_rate=len(matched) / total if total else 0,
        email_valid_rate=len(valid_emails) / len(email_leads) if email_leads else 0,
        reply_rate=len(replied) / len(contacted) if contacted else 0,
        handoff_count=len(handoff),
        high_intent_count=len(replied) + len([lead for lead in handoff if lead.status != "replied"]),
        avg_fit_score=(sum(scores) / len(scores)) if scores else 0,
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
