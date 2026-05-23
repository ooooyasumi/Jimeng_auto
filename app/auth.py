import json
import os
from datetime import datetime, timedelta
from passlib.context import CryptContext
from jose import jwt, JWTError

from app.database import get_db

SECRET_KEY = os.environ.get("JWT_SECRET", "jimeng-queue-secret-change-in-production")
ALGORITHM = "HS256"
EXPIRE_HOURS = 24

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return pwd_context.verify(password, hashed)


def create_token() -> str:
    expires = datetime.utcnow() + timedelta(hours=EXPIRE_HOURS)
    return jwt.encode({"exp": expires, "sub": "admin"}, SECRET_KEY, algorithm=ALGORITHM)


def verify_token(token: str) -> bool:
    try:
        jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return True
    except JWTError:
        return False


def get_password_hash_from_db() -> str | None:
    db = get_db()
    row = db.execute("SELECT value FROM config WHERE key='password_hash'").fetchone()
    db.close()
    return row["value"] if row else None


def set_password_hash_in_db(hashed: str):
    db = get_db()
    db.execute(
        "INSERT OR REPLACE INTO config (key, value) VALUES ('password_hash', ?)",
        (hashed,)
    )
    db.commit()
    db.close()


def set_initial_password(password: str):
    """Call once to set the initial password from env or default."""
    existing = get_password_hash_from_db()
    if not existing:
        set_password_hash_in_db(hash_password(password))
