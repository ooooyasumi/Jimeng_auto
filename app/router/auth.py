from fastapi import APIRouter, HTTPException
from datetime import datetime, timedelta

from app.models import LoginRequest, LoginResponse
from app.auth import verify_password, get_password_hash_from_db, create_token

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
def login(req: LoginRequest):
    stored = get_password_hash_from_db()
    if not stored:
        raise HTTPException(status_code=500, detail="No password configured on server")
    if not verify_password(req.password, stored):
        raise HTTPException(status_code=401, detail="Incorrect password")

    token = create_token()
    expires = datetime.utcnow() + timedelta(hours=24)
    return LoginResponse(token=token, expires_at=expires.isoformat())


@router.get("/check")
def check():
    return {"valid": True}
