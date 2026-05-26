import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from passlib.context import CryptContext
from jose import JWTError, jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from database import get_db
import models

# ─── Config ───
SECRET_KEY = os.environ.get("JWT_SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("JWT_SECRET_KEY must be set in .env")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 7

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
    
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在或已被禁用",
        )
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
