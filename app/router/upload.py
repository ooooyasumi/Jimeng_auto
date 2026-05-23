from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel

from app.cos import generate_presigned_upload, upload_to_cos

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


@router.post("/proxy")
async def proxy_upload(file: UploadFile = File(...)):
    """Fallback: upload through backend to COS, avoiding CORS issues."""
    try:
        result = await upload_to_cos(file)
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
