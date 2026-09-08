import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from jose import JWTError, jwt
import bcrypt
from .config import get_settings

ALGORITHM = "HS256"

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())

def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()

def create_access_token(user_id: int, role: str) -> str:
    settings = get_settings()
    expires = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_minutes)
    return jwt.encode({"sub": str(user_id), "role": role, "type": "access", "exp": expires}, settings.jwt_secret, algorithm=ALGORITHM)

def create_refresh_token(user_id: int) -> tuple[str, datetime]:
    settings = get_settings()
    expires = datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_days)
    token = secrets.token_urlsafe(48)
    return token, expires.replace(tzinfo=None)

def decode_access_token(token: str) -> dict:
    settings = get_settings()
    payload = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
    if payload.get("type") != "access" or not payload.get("sub"):
        raise JWTError("Invalid access token")
    return payload
