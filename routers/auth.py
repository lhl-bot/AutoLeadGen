from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

import models
from database import get_db
from services.auth import (
    hash_password, verify_password, create_access_token,
    cache_auth_user, get_current_user, require_admin
)
from middleware.rate_limit import check_login_rate_limit
from services.credits import default_initial_balance, ensure_credit_wallet

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str

class CreateUserRequest(BaseModel):
    username: str
    password: str
    display_name: Optional[str] = None
    is_admin: bool = False
    initial_credits: Optional[int] = None

class UserResponse(BaseModel):
    id: int
    username: str
    display_name: Optional[str]
    is_admin: bool
    is_active: bool = True
    credit_balance: int = 0

    class Config:
        from_attributes = True


@router.post("/login")
def login(request: LoginRequest, db: Session = Depends(get_db), req: Request = None):
    """Authenticate user and return JWT token."""
    if req:
        check_login_rate_limit(req)
    user = db.query(models.User).filter(models.User.username == request.username).first()
    if not user or not verify_password(request.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="账号已被禁用")
    
    token = create_access_token(user.id, user.is_admin)
    wallet = ensure_credit_wallet(db, user.id)
    cache_auth_user(user, credit_balance=wallet.balance)
    db.commit()
    return {
        "token": token,
        "user": {
            "id": user.id,
            "username": user.username,
            "display_name": user.display_name,
            "is_admin": user.is_admin,
            "is_active": user.is_active,
            "credit_balance": wallet.balance,
        }
    }


@router.get("/me", response_model=UserResponse)
def get_me(user: models.User = Depends(get_current_user)):
    """Get current authenticated user info."""
    credit_balance = getattr(user, "_cached_credit_balance", None)
    if credit_balance is None:
        credit_balance = user.credit_wallet.balance if user.credit_wallet else 0
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "is_admin": user.is_admin,
        "is_active": user.is_active,
        "credit_balance": credit_balance,
    }


@router.post("/users", response_model=UserResponse)
def create_user(
    request: CreateUserRequest,
    db: Session = Depends(get_db),
    admin: models.User = Depends(require_admin)
):
    """Create a new user (admin only)."""
    username = request.username.strip()
    existing = db.query(models.User).filter(models.User.username == username).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"用户名 '{request.username}' 已存在")
    
    new_user = models.User(
        username=username,
        hashed_password=hash_password(request.password),
        display_name=request.display_name,
        is_admin=request.is_admin,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    initial_credits = request.initial_credits
    if initial_credits is None:
        initial_credits = default_initial_balance()
    wallet = ensure_credit_wallet(db, new_user.id, initial_balance=initial_credits)
    db.commit()
    return {
        "id": new_user.id,
        "username": new_user.username,
        "display_name": new_user.display_name,
        "is_admin": new_user.is_admin,
        "is_active": new_user.is_active,
        "credit_balance": wallet.balance,
    }


@router.get("/users")
def list_users(
    db: Session = Depends(get_db),
    admin: models.User = Depends(require_admin)
):
    """List all users (admin only)."""
    users = db.query(models.User).order_by(models.User.id).all()
    results = []
    for u in users:
        wallet = ensure_credit_wallet(db, u.id)
        results.append({
            "id": u.id,
            "username": u.username,
            "display_name": u.display_name,
            "is_admin": u.is_admin,
            "is_active": u.is_active,
            "credit_balance": wallet.balance,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        })
    db.commit()
    return results


@router.post("/users/{user_id}/toggle-active")
def toggle_user_active(
    user_id: int,
    db: Session = Depends(get_db),
    admin: models.User = Depends(require_admin)
):
    """Toggle is_active state of a user (admin only). Cannot toggle self."""
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="不能禁用自己")
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    user.is_active = not user.is_active
    db.commit()
    return {"id": user.id, "is_active": user.is_active}


@router.delete("/users/{user_id}")
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    admin: models.User = Depends(require_admin)
):
    """Delete a user (admin only). Cannot delete self."""
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="不能删除自己")
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    db.delete(user)
    db.commit()
    return {"ok": True}
