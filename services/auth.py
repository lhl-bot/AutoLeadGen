import hmac
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from jwt.exceptions import InvalidTokenError
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError
from pwdlib.hashers.argon2 import Argon2Hasher
from pwdlib.hashers.bcrypt import BcryptHasher
from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import joinedload
from sqlalchemy.orm import Session

from database import get_db
import models
from runtime_config import is_production_like, read_int, read_secret

# ─── Config ───
SECRET_KEY = read_secret("JWT_SECRET_KEY", required=True)
if is_production_like() and len(SECRET_KEY.encode("utf-8")) < 32:
    raise RuntimeError("JWT_SECRET_KEY must contain at least 32 bytes")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = read_int(
    "ACCESS_TOKEN_EXPIRE_MINUTES",
    default=720 if is_production_like() else 7 * 24 * 60,
    minimum=5,
    maximum=7 * 24 * 60,
)
AUTH_SESSION_COOKIE = "autoleadgen_session"
AUTH_CSRF_COOKIE = "autoleadgen_csrf"
AUTH_CSRF_HEADER = "X-CSRF-Token"
# Disabled by default so account disable/delete takes effect across all app workers.
# Single-process deployments may explicitly opt in to a short TTL.
AUTH_USER_CACHE_TTL_SECONDS = float(os.environ.get("AUTH_USER_CACHE_TTL_SECONDS", "0"))
_auth_user_cache: dict[int, tuple[float, dict]] = {}

# ─── Password Hashing ───
# New passwords use Argon2. Bcrypt remains verification-only compatibility for
# existing accounts and can be upgraded at a later authenticated write.
password_hash = PasswordHash((Argon2Hasher(), BcryptHasher()))

def hash_password(password: str) -> str:
    return password_hash.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return password_hash.verify(plain_password, hashed_password)
    except (UnknownHashError, ValueError):
        return False

# ─── JWT Token ───
def create_access_token(user_id: int, is_admin: bool = False) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": str(user_id),
        "admin": is_admin,
        "exp": expire
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def set_auth_cookies(response: Response, token: str) -> None:
    """Issue a first-party browser session without exposing it to JavaScript."""
    max_age = ACCESS_TOKEN_EXPIRE_MINUTES * 60
    secure = is_production_like()
    response.set_cookie(
        AUTH_SESSION_COOKIE,
        token,
        max_age=max_age,
        httponly=True,
        secure=secure,
        samesite="strict",
        path="/",
    )
    response.set_cookie(
        AUTH_CSRF_COOKIE,
        secrets.token_urlsafe(32),
        max_age=max_age,
        httponly=False,
        secure=secure,
        samesite="strict",
        path="/",
    )


def clear_auth_cookies(response: Response) -> None:
    secure = is_production_like()
    response.delete_cookie(
        AUTH_SESSION_COOKIE,
        httponly=True,
        secure=secure,
        samesite="strict",
        path="/",
    )
    response.delete_cookie(
        AUTH_CSRF_COOKIE,
        httponly=False,
        secure=secure,
        samesite="strict",
        path="/",
    )


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


def invalidate_auth_user(user_id: int) -> None:
    """Immediately remove a user from the process-local authentication cache."""
    _auth_user_cache.pop(user_id, None)


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
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db)
) -> models.User:
    """Validate Bearer clients or the first-party HttpOnly browser session."""
    token = credentials.credentials if credentials else request.cookies.get(AUTH_SESSION_COOKIE)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录，请先登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if not credentials and request.method.upper() not in {"GET", "HEAD", "OPTIONS"}:
        expected_csrf = request.cookies.get(AUTH_CSRF_COOKIE, "")
        presented_csrf = request.headers.get(AUTH_CSRF_HEADER, "")
        if not expected_csrf or not presented_csrf or not hmac.compare_digest(
            expected_csrf,
            presented_csrf,
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "CSRF_CHECK_FAILED",
                    "message": "Session request failed CSRF validation",
                },
            )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = int(payload.get("sub"))
    except (InvalidTokenError, ValueError, TypeError):
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
        key = read_secret("SMTP_ENCRYPTION_KEY")
        if not key:
            if is_production_like():
                raise RuntimeError(
                    "SMTP_ENCRYPTION_KEY or SMTP_ENCRYPTION_KEY_FILE is required "
                    "in staging and production"
                )
            # Preserve compatibility with existing local encrypted credentials.
            key = SECRET_KEY
        if is_production_like() and key == SECRET_KEY:
            raise RuntimeError(
                "SMTP_ENCRYPTION_KEY must be independent from JWT_SECRET_KEY"
            )
        if is_production_like() and len(key.encode("utf-8")) < 32:
            raise RuntimeError("SMTP_ENCRYPTION_KEY must contain at least 32 bytes")
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
        if is_production_like():
            raise RuntimeError("Stored SMTP credential cannot be decrypted")
        # Local compatibility only; production never treats an undecryptable
        # database value as plaintext credentials.
        return ciphertext
