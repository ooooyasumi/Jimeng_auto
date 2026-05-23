import os
import uuid
from qcloud_cos import CosConfig, CosS3Client

REGION = os.environ.get("COS_REGION", "")
BUCKET = os.environ.get("COS_BUCKET", "")
SECRET_ID = os.environ.get("COS_SECRET_ID", "")
SECRET_KEY = os.environ.get("COS_SECRET_KEY", "")


def _client() -> CosS3Client | None:
    if not all([SECRET_ID, SECRET_KEY, REGION]):
        return None
    config = CosConfig(Region=REGION, SecretId=SECRET_ID, SecretKey=SECRET_KEY)
    return CosS3Client(config)


def generate_presigned_upload(filename: str) -> dict:
    """Generate a presigned URL for direct upload to COS."""
    client = _client()
    if not client:
        raise RuntimeError("COS not configured")

    ext = os.path.splitext(filename)[1] or ".bin"
    key = f"jimeng-queue/uploads/{uuid.uuid4().hex}{ext}"

    url = client.get_presigned_url(
        Method="PUT",
        Bucket=BUCKET,
        Key=key,
        Expired=600,
    )
    cos_url = f"https://{BUCKET}.cos.{REGION}.myqcloud.com/{key}"
    return {"upload_url": url, "cos_url": cos_url, "key": key}


def download_from_cos(cos_url: str, local_path: str):
    """Download a file from COS to a local path."""
    client = _client()
    if not client:
        raise RuntimeError("COS not configured")

    prefix = f"https://{BUCKET}.cos.{REGION}.myqcloud.com/"
    if cos_url.startswith(prefix):
        key = cos_url[len(prefix):]
    else:
        raise ValueError(f"Unexpected COS URL format: {cos_url}")

    client.download_file(Bucket=BUCKET, Key=key, DestFilePath=local_path)
