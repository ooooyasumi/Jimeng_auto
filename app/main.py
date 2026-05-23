import asyncio
import logging
import os
import shutil
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# Load .env from project root
env_path = Path(__file__).resolve().parent.parent / ".env"
if env_path.exists():
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip())

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.database import init_db
from app.auth import verify_token, set_initial_password
from app.router import auth, tasks, upload, queue
from app.worker import queue_worker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("main")

FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend", "dist")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    pwd = os.environ.get("PASSWORD", "admin123")
    set_initial_password(pwd)
    logger.info("Database initialized, password set")
    worker_task = asyncio.create_task(queue_worker())
    logger.info("Queue worker started")
    yield
    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="即梦视频队列", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    # Paths that don't require auth
    if path in ("/api/auth/login", "/api/system/health", "/docs", "/openapi.json"):
        return await call_next(request)
    # Non-API paths are static files
    if not path.startswith("/api/"):
        return await call_next(request)
    # API paths require token
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    token = auth_header[7:]
    if not verify_token(token):
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return await call_next(request)


app.include_router(auth.router)
app.include_router(tasks.router)
app.include_router(upload.router)
app.include_router(queue.router)


@app.get("/api/system/health")
def health():
    return {
        "ok": True,
        "cli_installed": shutil.which("dreamina") is not None,
        "login_status": "unknown",
    }


if os.path.isdir(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
