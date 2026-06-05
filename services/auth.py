import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from passlib.context import CryptContext
from jose import JWTError, jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import joinedload
from sqlalchemy.orm import Session

from database import get_db
import models

# ─── Config ───
SECRET_KEY = os.environ.get("JWT_SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("JWT_SECRET_KEY must be set in .env")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 7
AUTH_USER_CACHE_TTL_SECONDS = float(os.environ.get("AUTH_USER_CACHE_TTL_SECONDS", "300"))
_auth_user_cache: dict[int, tuple[float, dict]] = {}

# ─── Password Hashing ───
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

# ─── JWT Token ───
def create_access_token(user_id: int, is_admin: bool = False) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
    payload = {
        "sub": str(user_id),
        "admin": is_admin,
        "exp": expire
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def cache_auth_user(user: models.User, credit_balance: Optional[int] = None) -> None:
    if AUTH_USER_CACHE_TTL_SECONDS <= 0:
        return
    wallet = getattr(user, "credit_wallet", None)
    if credit_balance is None:
        credit_balance = wallet.balance if wallet else 0
    _auth_user_cache[user.id] = (
        time.monotonic() + AUTH_USER_CACHE_TTL_SECONDS,
        {
            "username": user.username,
            "display_name": user.display_name,
            "is_admin": bool(user.is_admin),
            "is_active": bool(user.is_active),
            "credit_balance": credit_balance,
        },
    )


def _cached_user(user_id: int) -> Optional[models.User]:
    cached = _auth_user_cache.get(user_id)
    if not cached:
        return None
    expires_at, data = cached
    if expires_at <= time.monotonic():
        _auth_user_cache.pop(user_id, None)
        return None
    user = models.User(
        id=user_id,
        username=data["username"],
        display_name=data["display_name"],
        is_admin=data["is_admin"],
        is_active=data["is_active"],
    )
    setattr(user, "_cached_credit_balance", data["credit_balance"])
    return user

# ─── FastAPI Dependency ───
security = HTTPBearer(auto_error=False)

def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db)
) -> models.User:
    """Extract and validate JWT token from Authorization header, return User object."""
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录，请先登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    token = credentials.credentials
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = int(payload.get("sub"))
    except (JWTError, ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token 无效或已过期，请重新登录",
            headers={"WWW-Authenticate": "Bearer"},
        )

    cached_user = _cached_user(user_id)
    if cached_user:
        return cached_user
    
    try:
        user = (
            db.query(models.User)
            .options(joinedload(models.User.credit_wallet))
            .filter(models.User.id == user_id)
            .first()
        )
    except Exception as e:
        print(f"Database error during user check: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="认证服务暂时不可用，请稍后重试",
        )
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在或已被禁用",
        )
    cache_auth_user(user)
    return user

def require_admin(user: models.User = Depends(get_current_user)) -> models.User:
    """Dependency that requires admin privileges."""
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限",
        )
    return user

# ─── SMTP Password Encryption ───
from cryptography.fernet import Fernet, InvalidToken

_smtp_cipher: Optional[Fernet] = None

def _get_smtp_cipher() -> Fernet:
    global _smtp_cipher
    if _smtp_cipher is None:
        key = os.environ.get("SMTP_ENCRYPTION_KEY")
        if not key:
            key = os.environ.get("JWT_SECRET_KEY", "fallback-smtp-key")
        key_bytes = key.encode("utf-8")
        if len(key_bytes) < 32:
            key_bytes = key_bytes.ljust(32, b"0")
        else:
            key_bytes = key_bytes[:32]
        import base64
        fernet_key = base64.urlsafe_b64encode(key_bytes)
        _smtp_cipher = Fernet(fernet_key)
    return _smtp_cipher

def encrypt_smtp_pass(plaintext: str) -> str:
    return _get_smtp_cipher().encrypt(plaintext.encode("utf-8")).decode("utf-8")

def decrypt_smtp_pass(ciphertext: str) -> str:
    try:
        return _get_smtp_cipher().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        # Plaintext password — encrypt it for future use (best-effort)
        return ciphertext
