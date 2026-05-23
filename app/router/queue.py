import json
import re
from fastapi import APIRouter

from app.database import get_db
from app.worker import get_worker_status, set_worker_paused
from app.models import QueueStatus, CreditResponse
from app.dreamina import run_dreamina

router = APIRouter(prefix="/api/queue", tags=["queue"])


@router.get("/status", response_model=QueueStatus)
def queue_status():
    db = get_db()
    try:
        running = db.execute(
            "SELECT * FROM tasks WHERE status = 'running' ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        pending = db.execute("SELECT COUNT(*) FROM tasks WHERE status = 'pending'").fetchone()[0]
        done = db.execute("SELECT COUNT(*) FROM tasks WHERE status = 'done'").fetchone()[0]
        failed = db.execute("SELECT COUNT(*) FROM tasks WHERE status = 'failed'").fetchone()[0]

        from app.router.tasks import row_to_task
        return QueueStatus(
            running=row_to_task(running) if running else None,
            pending_count=pending,
            done_count=done,
            failed_count=failed,
            paused=get_worker_status(),
        )
    finally:
        db.close()


@router.post("/pause")
def pause_queue():
    set_worker_paused(True)
    return {"ok": True, "paused": True}


@router.post("/resume")
def resume_queue():
    set_worker_paused(False)
    return {"ok": True, "paused": False}


@router.get("/credit", response_model=CreditResponse)
async def get_credit():
    out, stderr, rc = await run_dreamina("user_credit")
    if rc != 0:
        return CreditResponse(total_credit=0)
    m = re.search(r"total_credit\s*[:=]?\s*(\d+)", out)
    if m:
        return CreditResponse(total_credit=int(m.group(1)))
    m = re.search(r"(\d+)", out)
    if m:
        return CreditResponse(total_credit=int(m.group(1)))
    return CreditResponse(total_credit=0)
