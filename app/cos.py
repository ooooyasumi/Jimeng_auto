import os
import uuid
from datetime import datetime
from qcloud_cos import CosConfig, CosS3Client

REGION = os.environ.get("COS_REGION", "ap-chongqing")
BUCKET = "jimengauto-1372876299"
CUSTOM_DOMAIN = os.environ.get("COS_CUSTOM_DOMAIN", "jimengauto.cos.ooooyasumi.com")
SECRET_ID = os.environ.get("COS_SECRET_ID", "")
SECRET_KEY = os.environ.get("COS_SECRET_KEY", "")


def _client() -> CosS3Client | None:
    if not all([SECRET_ID, SECRET_KEY]):
        return None
    config = CosConfig(Region=REGION, SecretId=SECRET_ID, SecretKey=SECRET_KEY)
    return CosS3Client(config)


def _make_key(file_type: str, filename: str) -> str:
    """Generate organized storage key.

    Directory structure:
      uploads/images/2026-05-23/uuid.png
      uploads/videos/2026-05-23/uuid.mp4
      uploads/audio/2026-05-23/uuid.mp3
    """
    date_str = datetime.now().strftime("%Y-%m-%d")
    ext = os.path.splitext(filename)[1] or ".bin"
    return f"uploads/{file_type}s/{date_str}/{uuid.uuid4().hex}{ext}"


def _detect_type(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    if ext in (".mp4", ".mov", ".webm", ".avi"):
        return "video"
    if ext in (".mp3", ".wav", ".aac", ".m4a", ".ogg", ".flac"):
        return "audio"
    return "image"


def generate_presigned_upload(filename: str) -> dict:
    """Generate a presigned URL for direct upload to COS (private write)."""
    client = _client()
    if not client:
        raise RuntimeError("COS not configured: missing SECRET_ID/SECRET_KEY")

    file_type = _detect_type(filename)
    key = _make_key(file_type, filename)

    url = client.get_presigned_url(
        Method="PUT",
        Bucket=BUCKET,
        Key=key,
        Expired=600,
    )
    cos_url = f"https://{CUSTOM_DOMAIN}/{key}"
    return {"upload_url": url, "cos_url": cos_url, "key": key}


async def upload_to_cos(file) -> dict:
    """Upload a file through backend to COS (fallback for CORS issues)."""
    client = _client()
    if not client:
        raise RuntimeError("COS not configured: missing SECRET_ID/SECRET_KEY")

    filename = file.filename or "untitled"
    file_type = _detect_type(filename)
    key = _make_key(file_type, filename)

    data = await file.read()
    client.put_object(Bucket=BUCKET, Key=key, Body=data)
    cos_url = f"https://{CUSTOM_DOMAIN}/{key}"
    return {"cos_url": cos_url, "key": key}


def download_from_cos(cos_url: str, local_path: str):
    """Download a file from COS to a local path."""
    client = _client()
    if not client:
        raise RuntimeError("COS not configured: missing SECRET_ID/SECRET_KEY")

    prefix = f"https://{CUSTOM_DOMAIN}/"
    if cos_url.startswith(prefix):
        key = cos_url[len(prefix):]
    else:
        raise ValueError(f"Unexpected COS URL format: {cos_url}")

    client.download_file(Bucket=BUCKET, Key=key, DestFilePath=local_path)
