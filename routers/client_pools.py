from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, case
from typing import List

import models, schemas
from database import get_db
from services.auth import get_current_user

router = APIRouter(prefix="/api/client_pools", tags=["client_pools"])

@router.get("/", response_model=List[schemas.ClientPoolWithStats])
def read_client_pools(skip: int = 0, limit: int = 100, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    query = db.query(models.ClientPool)
    if not user.is_admin:
        query = query.filter(models.ClientPool.user_id == user.id)
    pools = query.offset(skip).limit(limit).all()
    
    # Single query: all lead stats grouped by client_pool_id
    lead_stats = (
        db.query(
            models.Lead.client_pool_id,
            func.count(models.Lead.id).label("total"),
            func.sum(case((models.Lead.status.in_(["sent", "replied"]), 1), else_=0)).label("contacted"),
            func.sum(case((models.Lead.status == "replied", 1), else_=0)).label("replied"),
        )
        .filter(models.Lead.client_pool_id.isnot(None))
        .group_by(models.Lead.client_pool_id)
        .all()
    )
    stats_map = {
        row.client_pool_id: {
            "total_leads": row.total,
            "contacted_leads": int(row.contacted or 0),
            "replied_leads": int(row.replied or 0),
        }
        for row in lead_stats
    }
    
    # Single query: workflow counts grouped by client_pool_id
    wf_stats = (
        db.query(
            models.Workflow.client_pool_id,
            func.count(models.Workflow.id).label("wf_count"),
        )
        .filter(models.Workflow.client_pool_id.isnot(None))
        .group_by(models.Workflow.client_pool_id)
        .all()
    )
    wf_map = {row.client_pool_id: row.wf_count for row in wf_stats}
    
    results = []
    for pool in pools:
        s = stats_map.get(pool.id, {"total_leads": 0, "contacted_leads": 0, "replied_leads": 0})
        pool_dict = pool.__dict__.copy()
        pool_dict["total_leads"] = s["total_leads"]
        pool_dict["contacted_leads"] = s["contacted_leads"]
        pool_dict["replied_leads"] = s["replied_leads"]
        pool_dict["workflow_count"] = wf_map.get(pool.id, 0)
        results.append(pool_dict)
    return results

@router.post("/", response_model=schemas.ClientPool)
def create_client_pool(pool: schemas.ClientPoolCreate, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    db_pool = models.ClientPool(**pool.model_dump(), user_id=user.id)
    db.add(db_pool)
    db.commit()
    db.refresh(db_pool)
    return db_pool

@router.get("/{pool_id}", response_model=schemas.ClientPoolWithStats)
def read_client_pool(pool_id: int, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    pool = db.query(models.ClientPool).filter(models.ClientPool.id == pool_id, models.ClientPool.user_id == user.id).first()
    if not pool:
        raise HTTPException(status_code=404, detail="Client Pool not found")

    # Single query for all lead stats
    stats = (
        db.query(
            func.count(models.Lead.id).label("total"),
            func.sum(case((models.Lead.status.in_(["sent", "replied"]), 1), else_=0)).label("contacted"),
            func.sum(case((models.Lead.status == "replied", 1), else_=0)).label("replied"),
        )
        .filter(models.Lead.client_pool_id == pool.id)
        .first()
    )
    wf_count = db.query(func.count(models.Workflow.id)).filter(models.Workflow.client_pool_id == pool.id).scalar()

    pool_dict = pool.__dict__.copy()
    pool_dict["total_leads"] = stats.total or 0
    pool_dict["contacted_leads"] = int(stats.contacted or 0)
    pool_dict["replied_leads"] = int(stats.replied or 0)
    pool_dict["workflow_count"] = wf_count or 0
    return pool_dict

@router.put("/{pool_id}", response_model=schemas.ClientPool)
def update_client_pool(pool_id: int, pool: schemas.ClientPoolCreate, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    query = db.query(models.ClientPool).filter(models.ClientPool.id == pool_id)
    if not user.is_admin:
        query = query.filter(models.ClientPool.user_id == user.id)
    db_pool = query.first()
    if not db_pool:
        raise HTTPException(status_code=404, detail="Client Pool not found")

    for key, value in pool.model_dump().items():
        setattr(db_pool, key, value)

    db.commit()
    db.refresh(db_pool)
    return db_pool

@router.delete("/{pool_id}")
def delete_client_pool(pool_id: int, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    query = db.query(models.ClientPool).filter(models.ClientPool.id == pool_id)
    if not user.is_admin:
        query = query.filter(models.ClientPool.user_id == user.id)
    db_pool = query.first()
    if not db_pool:
        raise HTTPException(status_code=404, detail="Client Pool not found")
    
    # Unlink workflows before deleting
    db.query(models.Workflow).filter(models.Workflow.client_pool_id == pool_id).update(
        {models.Workflow.client_pool_id: None}
    )
    
    # Bulk delete associated EmailLogs
    lead_ids_query = db.query(models.Lead.id).filter(models.Lead.client_pool_id == pool_id)
    db.query(models.EmailLog).filter(models.EmailLog.lead_id.in_(lead_ids_query)).delete(synchronize_session=False)
    
    # Bulk delete associated Leads
    db.query(models.Lead).filter(models.Lead.client_pool_id == pool_id).delete(synchronize_session=False)
    
    # Delete the pool
    db.delete(db_pool)
    db.commit()
    return {"ok": True}

@router.get("/{pool_id}/leads", response_model=List[schemas.Lead])
def read_pool_leads(
    pool_id: int,
    status: str = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(1000, ge=1, le=5000),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    pool_query = db.query(models.ClientPool).filter(models.ClientPool.id == pool_id)
    if not user.is_admin:
        pool_query = pool_query.filter(models.ClientPool.user_id == user.id)
    pool = pool_query.first()
    if not pool:
        raise HTTPException(status_code=404, detail="Client Pool not found")

    query = db.query(models.Lead).filter(models.Lead.client_pool_id == pool_id)
    if status:
        query = query.filter(models.Lead.status == status)
    return query.order_by(models.Lead.created_at.desc()).offset(skip).limit(limit).all()

@router.post("/{pool_id}/search")
def run_pool_search(
    pool_id: int,
    batch_lead_limit: int = Query(25, ge=1, le=200),
    max_domains: int = Query(80, ge=10, le=250),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    pool_query = db.query(models.ClientPool).filter(models.ClientPool.id == pool_id)
    if not user.is_admin:
        pool_query = pool_query.filter(models.ClientPool.user_id == user.id)
    pool = pool_query.first()
    if not pool:
        raise HTTPException(status_code=404, detail="Client Pool not found")

    wf_query = db.query(models.Workflow).filter(models.Workflow.client_pool_id == pool_id)
    if not user.is_admin:
        wf_query = wf_query.filter(models.Workflow.user_id == user.id)
    workflows = wf_query.order_by(models.Workflow.status.asc(), models.Workflow.id.desc()).all()
    if not workflows:
        return {
            "started": False,
            "pool_id": pool_id,
            "workflow_count": 0,
            "message": "No workflows are bound to this client pool yet."
        }

    from services.outbound_engine import is_workflow_search_running, launch_workflow_search

    started_ids = []
    busy_ids = []
    for workflow in workflows:
        if is_workflow_search_running(workflow.id):
            busy_ids.append(workflow.id)
            continue
        if launch_workflow_search(workflow.id, batch_lead_limit, max_domains):
            started_ids.append(workflow.id)
        else:
            busy_ids.append(workflow.id)

    return {
        "started": bool(started_ids),
        "pool_id": pool_id,
        "workflow_count": len(workflows),
        "started_workflow_ids": started_ids,
        "busy_workflow_ids": busy_ids,
        "message": f"Started lead search for {len(started_ids)} workflow(s)."
    }
