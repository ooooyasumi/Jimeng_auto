import hashlib
import os
import secrets
from datetime import datetime, timedelta
from jose import jwt, JWTError

from app.database import get_db

SECRET_KEY = os.environ.get("JWT_SECRET", "jimeng-queue-secret-change-in-production")
ALGORITHM = "HS256"
EXPIRE_HOURS = 24


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000)
    return f"pbkdf2:{salt}:{dk.hex()}"


def verify_password(password: str, hashed: str) -> bool:
    parts = hashed.split(":")
    if len(parts) != 3 or parts[0] != "pbkdf2":
        return False
    _, salt, expected = parts
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000)
    return secrets.compare_digest(dk.hex(), expected)


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
    existing = get_password_hash_from_db()
    if not existing:
        set_password_hash_in_db(hash_password(password))
