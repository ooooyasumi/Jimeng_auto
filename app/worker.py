import asyncio
import json
import logging
import os
import shutil
import tempfile

from app.database import get_db
from app.dreamina import (
    run_dreamina,
    parse_submit_output,
    build_submit_command,
    list_all_tasks,
)
from app.cos import download_from_cos

logger = logging.getLogger("worker")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "30"))

_paused = False


def get_worker_status() -> bool:
    return _paused


def set_worker_paused(paused: bool):
    global _paused
    _paused = paused


async def submit_task_to_dreamina(task) -> str | None:
    """Submit a task to dreamina CLI. Returns full submit_id or None."""
    params = json.loads(task["params"] or "{}")
    refs = json.loads(task["references"] or "[]")

    tmp_dir = None
    ref_files = []
    try:
        if refs:
            tmp_dir = tempfile.mkdtemp(prefix="jimeng_refs_")
            for ref in refs:
                local_path = os.path.join(tmp_dir, ref["filename"])
                download_from_cos(ref["cos_url"], local_path)
                ref_files.append(local_path)

        cmd = build_submit_command(task["type"], task["prompt"], params, ref_files)
        stdout, stderr, rc = await run_dreamina(*cmd)
        combined = stdout + "\n" + stderr
        result = parse_submit_output(combined)
        return result.get("submit_id")
    except Exception as e:
        logger.error(f"Submit failed for task {task['id']}: {e}")
        return None
    finally:
        if tmp_dir and os.path.isdir(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)


def sync_tasks_from_dreamina(dreamina_tasks: list[dict]):
    """Sync our DB running/pending tasks with dreamina's real status."""
    db = get_db()
    try:
        for dt in dreamina_tasks:
            sid = dt.get("submit_id", "")
            if not sid:
                continue
            gs = dt.get("gen_status", "")
            fr = dt.get("fail_reason", "")

            # Find our task by submit_id (full or partial match)
            row = db.execute(
                "SELECT id, status FROM tasks WHERE submit_id = ?",
                (sid,)
            ).fetchone()
            if not row:
                # Try partial match
                row = db.execute(
                    "SELECT id, status FROM tasks WHERE ? LIKE submit_id || '%'",
                    (sid,)
                ).fetchone()

            if not row:
                continue

            task_id = row["id"]

            if gs == "success":
                db.execute(
                    "UPDATE tasks SET status='done', gen_status='success', updated_at=datetime('now') WHERE id=? AND status='running'",
                    (task_id,)
                )
                logger.info(f"Task {task_id} completed (synced from list_task)")
            elif gs == "fail":
                db.execute(
                    "UPDATE tasks SET status='failed', gen_status='fail', error_message=?, updated_at=datetime('now') WHERE id=? AND status='running'",
                    (fr, task_id)
                )
                logger.info(f"Task {task_id} failed: {fr}")
        db.commit()
    finally:
        db.close()


async def queue_worker():
    """Background worker that manages the task queue."""
    global _paused
    logger.info("Queue worker started")
    while True:
        try:
            if _paused:
                await asyncio.sleep(5)
                continue

            db = get_db()

            # Step 1: Sync running tasks with dreamina's real status via list_task
            running_tasks = db.execute(
                "SELECT * FROM tasks WHERE status = 'running'"
            ).fetchall()

            if running_tasks:
                dm_tasks = await list_all_tasks()
                if dm_tasks:
                    sync_tasks_from_dreamina(dm_tasks)
                db.close()

                # Re-check if any running task is now done/failed
                db = get_db()
                still_running = db.execute(
                    "SELECT COUNT(*) FROM tasks WHERE status = 'running'"
                ).fetchone()[0]
                db.close()

                if still_running > 0:
                    await asyncio.sleep(POLL_INTERVAL)
                    continue

            # Step 2: No running tasks, submit next pending
            db = get_db()
            next_task = db.execute(
                "SELECT * FROM tasks WHERE status = 'pending' ORDER BY position ASC LIMIT 1"
            ).fetchone()

            if next_task:
                submit_id = await submit_task_to_dreamina(next_task)
                if submit_id:
                    db.execute(
                        """UPDATE tasks SET status='running', submit_id=?,
                           gen_status='querying', updated_at=datetime('now')
                           WHERE id=?""",
                        (submit_id, next_task["id"])
                    )
                    db.commit()
                    logger.info(f"Task {next_task['id']} submitted: {submit_id}")
                else:
                    db.execute(
                        """UPDATE tasks SET status='failed',
                           error_message='Submit returned no submit_id',
                           updated_at=datetime('now')
                           WHERE id=?""",
                        (next_task["id"],)
                    )
                    db.commit()
                    logger.error(f"Task {next_task['id']} submit returned no submit_id")
            db.close()
            await asyncio.sleep(POLL_INTERVAL)

        except Exception as e:
            logger.exception(f"Worker error: {e}")
            await asyncio.sleep(POLL_INTERVAL)
