from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.cos import generate_presigned_upload

router = APIRouter(prefix="/api/upload", tags=["upload"])


class PresignRequest(BaseModel):
    filename: str


@router.post("/presign")
def presign_upload(req: PresignRequest):
    try:
        result = generate_presigned_upload(req.filename)
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
