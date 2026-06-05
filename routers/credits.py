from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

import models
import schemas
from database import get_db
from services.auth import get_current_user, require_admin
from services.credits import (
    InsufficientCreditsError,
    get_credit_summary,
    grant_credits,
    list_credit_transactions,
    pricing_table,
)

router = APIRouter(prefix="/api/credits", tags=["credits"])


@router.get("/me", response_model=schemas.CreditSummary)
def read_my_credits(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    summary = get_credit_summary(db, user.id)
    db.commit()
    return summary


@router.get("/pricing")
def read_credit_pricing(user: models.User = Depends(get_current_user)):
    return {"pricing": pricing_table()}


@router.get("/transactions", response_model=schemas.CreditLedgerResponse)
def read_my_credit_transactions(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    summary = get_credit_summary(db, user.id)
    transactions = list_credit_transactions(db, user.id, limit=limit, offset=offset)
    db.commit()
    return {"summary": summary, "transactions": transactions}


@router.get("/users/{user_id}", response_model=schemas.CreditLedgerResponse)
def read_user_credit_transactions(
    user_id: int,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    admin: models.User = Depends(require_admin),
):
    target = db.query(models.User).filter(models.User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    summary = get_credit_summary(db, user_id)
    transactions = list_credit_transactions(db, user_id, limit=limit, offset=offset)
    db.commit()
    return {"summary": summary, "transactions": transactions}


@router.post("/users/{user_id}/grant", response_model=schemas.CreditLedgerResponse)
def grant_user_credits(
    user_id: int,
    payload: schemas.CreditGrantRequest,
    db: Session = Depends(get_db),
    admin: models.User = Depends(require_admin),
):
    target = db.query(models.User).filter(models.User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if payload.amount == 0:
        raise HTTPException(status_code=400, detail="Amount cannot be zero")

    try:
        grant_credits(
            db,
            user_id,
            payload.amount,
            description=payload.description,
            created_by_user_id=admin.id,
            metadata={"admin_username": admin.username},
        )
    except InsufficientCreditsError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot deduct {abs(payload.amount)} credits; user balance is {exc.balance}",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    summary = get_credit_summary(db, user_id)
    transactions = list_credit_transactions(db, user_id, limit=50, offset=0)
    return {"summary": summary, "transactions": transactions}
