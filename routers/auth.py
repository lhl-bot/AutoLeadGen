from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session
from pydantic import BaseModel, ConfigDict, Field
from typing import Optional

import models
from database import get_db
from services.auth import (
    hash_password, verify_password, create_access_token,
    cache_auth_user, clear_auth_cookies, get_current_user, invalidate_auth_user,
    require_admin, set_auth_cookies,
)
from middleware.rate_limit import (
    check_login_rate_limit,
    record_login_failure,
    reset_login_identity_limit,
)
from product_v2.schemas import ErrorResponse
from services.credits import default_initial_balance, ensure_credit_wallet

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=128)

class CreateUserRequest(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=12, max_length=128)
    display_name: Optional[str] = None
    is_admin: bool = False
    initial_credits: Optional[int] = None

class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    display_name: Optional[str]
    is_admin: bool
    is_active: bool = True
    credit_balance: int = 0

class LoginResponse(BaseModel):
    token: str
    user: UserResponse


@router.post(
    "/login",
    response_model=LoginResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Username or password is invalid"},
        403: {"model": ErrorResponse, "description": "The account is disabled"},
        429: {"model": ErrorResponse, "description": "Too many login attempts"},
    },
)
def login(
    request: LoginRequest,
    response: Response,
    req: Request,
    db: Session = Depends(get_db),
):
    """Authenticate user and return JWT token."""
    check_login_rate_limit(req, request.username)
    user = db.query(models.User).filter(models.User.username == request.username).first()
    if not user or not verify_password(request.password, user.hashed_password):
        record_login_failure(req, request.username)
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="账号已被禁用")
    reset_login_identity_limit(req, request.username)
    
    token = create_access_token(user.id, user.is_admin)
    set_auth_cookies(response, token)
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


@router.post("/logout", status_code=204)
def logout(
    response: Response,
    _user: models.User = Depends(get_current_user),
):
    """Revoke the current browser session cookies on this device."""
    clear_auth_cookies(response)


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
    invalidate_auth_user(user.id)
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
    invalidate_auth_user(user_id)
    return {"ok": True}
