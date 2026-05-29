from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

import models, schemas
from database import get_db
from services.auth import get_current_user

router = APIRouter(prefix="/api/personas", tags=["personas"])

@router.post("/", response_model=schemas.CustomerPersona)
def create_persona(persona: schemas.CustomerPersonaCreate, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    db_persona = models.CustomerPersona(**persona.model_dump(), user_id=user.id)
    db.add(db_persona)
    db.commit()
    db.refresh(db_persona)
    return db_persona

@router.get("/", response_model=List[schemas.CustomerPersona])
def read_personas(skip: int = 0, limit: int = 100, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    query = db.query(models.CustomerPersona)
    if not user.is_admin:
        query = query.filter(models.CustomerPersona.user_id == user.id)
    return query.offset(skip).limit(limit).all()

@router.get("/{persona_id}", response_model=schemas.CustomerPersona)
def read_persona(persona_id: int, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    query = db.query(models.CustomerPersona).filter(models.CustomerPersona.id == persona_id)
    if not user.is_admin:
        query = query.filter(models.CustomerPersona.user_id == user.id)
    db_persona = query.first()
    if db_persona is None:
        raise HTTPException(status_code=404, detail="Persona not found")
    return db_persona

@router.put("/{persona_id}", response_model=schemas.CustomerPersona)
def update_persona(persona_id: int, persona: schemas.CustomerPersonaCreate, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    query = db.query(models.CustomerPersona).filter(models.CustomerPersona.id == persona_id)
    if not user.is_admin:
        query = query.filter(models.CustomerPersona.user_id == user.id)
    db_persona = query.first()
    if db_persona is None:
        raise HTTPException(status_code=404, detail="Persona not found")
    
    for key, value in persona.model_dump().items():
        setattr(db_persona, key, value)
        
    db.commit()
    db.refresh(db_persona)
    return db_persona

@router.delete("/{persona_id}")
def delete_persona(persona_id: int, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    query = db.query(models.CustomerPersona).filter(models.CustomerPersona.id == persona_id)
    if not user.is_admin:
        query = query.filter(models.CustomerPersona.user_id == user.id)
    db_persona = query.first()
    if db_persona is None:
        raise HTTPException(status_code=404, detail="Persona not found")
    db.delete(db_persona)
    db.commit()
    return {"ok": True}
