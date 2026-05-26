from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

import models, schemas
from database import get_db
from services.email_sender import test_smtp_connection
from services.auth import get_current_user, encrypt_smtp_pass, decrypt_smtp_pass

router = APIRouter(prefix="/api/email_accounts", tags=["email_accounts"])

@router.post("/", response_model=schemas.EmailAccount)
def create_email_account(account: schemas.EmailAccountCreate, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    data = account.model_dump()
    if data.get("smtp_pass"):
        data["smtp_pass"] = encrypt_smtp_pass(data["smtp_pass"])
    db_account = models.EmailAccount(**data, user_id=user.id)
    db.add(db_account)
    db.commit()
    db.refresh(db_account)
    return db_account

@router.get("/", response_model=List[schemas.EmailAccount])
def read_email_accounts(skip: int = 0, limit: int = 100, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    query = db.query(models.EmailAccount)
    if not user.is_admin:
        query = query.filter(models.EmailAccount.user_id == user.id)
    return query.offset(skip).limit(limit).all()

@router.put("/{account_id}", response_model=schemas.EmailAccount)
def update_email_account(account_id: int, account: schemas.EmailAccountCreate, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    query = db.query(models.EmailAccount).filter(models.EmailAccount.id == account_id)
    if not user.is_admin:
        query = query.filter(models.EmailAccount.user_id == user.id)
    db_account = query.first()
    if not db_account:
        raise HTTPException(status_code=404, detail="Email Account not found")
    
    update_data = account.model_dump()
    if update_data.get("smtp_pass"):
        update_data["smtp_pass"] = encrypt_smtp_pass(update_data["smtp_pass"])
    for key, value in update_data.items():
        setattr(db_account, key, value)
        
    db.commit()
    db.refresh(db_account)
    return db_account

@router.delete("/{account_id}")
def delete_email_account(account_id: int, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    query = db.query(models.EmailAccount).filter(models.EmailAccount.id == account_id)
    if not user.is_admin:
        query = query.filter(models.EmailAccount.user_id == user.id)
    db_account = query.first()
    if db_account is None:
        raise HTTPException(status_code=404, detail="Email Account not found")
    db.delete(db_account)
    db.commit()
    return {"ok": True}

@router.post("/{account_id}/test_smtp")
def test_smtp(account_id: int, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    query = db.query(models.EmailAccount).filter(models.EmailAccount.id == account_id)
    if not user.is_admin:
        query = query.filter(models.EmailAccount.user_id == user.id)
    db_account = query.first()
    if not db_account:
        raise HTTPException(status_code=404, detail="Email Account not found")
    
    result = test_smtp_connection(
        smtp_host=db_account.smtp_host,
        smtp_port=db_account.smtp_port,
        smtp_user=db_account.smtp_user,
        smtp_pass=decrypt_smtp_pass(db_account.smtp_pass),
        use_ssl=db_account.use_ssl,
        use_tls=db_account.use_tls
    )
    return result

@router.post("/{account_id}/test_imap")
def test_imap(account_id: int, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    query = db.query(models.EmailAccount).filter(models.EmailAccount.id == account_id)
    if not user.is_admin:
        query = query.filter(models.EmailAccount.user_id == user.id)
    db_account = query.first()
    if not db_account:
        raise HTTPException(status_code=404, detail="Email Account not found")
    
    from services.inbox_monitor import test_imap_connection
    result = test_imap_connection(
        imap_host=db_account.imap_host,
        imap_port=db_account.imap_port,
        email_user=db_account.email,
        email_pass=db_account.smtp_pass
    )
    return result
