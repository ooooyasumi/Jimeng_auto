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
        submit_id = result.get("submit_id")
        # If submit succeeded but dreamina returned fail status (e.g. concurrency), return None
        if result.get("gen_status") == "fail" and result.get("fail_reason"):
            logger.error(f"Submit rejected for task {task['id']}: {result['fail_reason']}")
            return None
        return submit_id
    except Exception as e:
        logger.error(f"Submit failed for task {task['id']}: {e}")
        return None
    finally:
        if tmp_dir and os.path.isdir(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)


async def check_task_via_query(submit_id: str) -> dict | None:
    """Check a single task via query_result. Returns parsed result or None."""
    stdout, stderr, rc = await run_dreamina("query_result", "--submit_id", submit_id)
    combined = stdout + "\n" + stderr
    if "record not found" in combined.lower():
        return None
    return parse_submit_output(combined)


async def sync_running_task(task) -> bool:
    """Check one running task's real status. Returns True if task is done/failed (no longer running)."""
    submit_id = task["submit_id"]
    if not submit_id:
        logger.warning(f"Task {task['id']} is running but has no submit_id, marking failed")
        db = get_db()
        db.execute("UPDATE tasks SET status='failed', error_message='missing submit_id' WHERE id=?", (task["id"],))
        db.commit()
        db.close()
        return True

    result = await check_task_via_query(submit_id)
    if result is None:
        logger.error(f"Task {task['id']} submit_id {submit_id} not found in dreamina")
        return False  # Keep retrying, maybe it's a transient error

    gs = result.get("gen_status")
    db = get_db()
    if gs == "success":
        db.execute(
            "UPDATE tasks SET status='done', gen_status='success', result_url=?, updated_at=datetime('now') WHERE id=?",
            (result.get("result_url"), task["id"])
        )
        db.commit()
        logger.info(f"Task {task['id']} completed")
        db.close()
        return True
    elif gs == "fail":
        db.execute(
            "UPDATE tasks SET status='failed', gen_status='fail', error_message=?, updated_at=datetime('now') WHERE id=?",
            (result.get("fail_reason", "unknown"), task["id"])
        )
        db.commit()
        db.close()
        logger.info(f"Task {task['id']} failed: {result.get('fail_reason')}")
        return True
    else:
        # Still querying
        db.execute("UPDATE tasks SET gen_status='querying', updated_at=datetime('now') WHERE id=?", (task["id"],))
        db.commit()
        db.close()
        return False


async def queue_worker():
    """Background worker: one running at a time, poll -> complete -> submit next."""
    global _paused
    logger.info("Queue worker started")
    while True:
        try:
            if _paused:
                await asyncio.sleep(5)
                continue

            db = get_db()

            running = db.execute(
                "SELECT * FROM tasks WHERE status = 'running' ORDER BY updated_at ASC LIMIT 1"
            ).fetchone()
            db.close()

            if running:
                done = await sync_running_task(running)
                if done:
                    continue  # Immediately try to submit next
                await asyncio.sleep(POLL_INTERVAL)
                continue

            # No running task, submit next pending
            db = get_db()
            next_task = db.execute(
                "SELECT * FROM tasks WHERE status = 'pending' ORDER BY position ASC LIMIT 1"
            ).fetchone()

            if next_task:
                # If it already has a submit_id, it was already submitted — move to running
                if next_task["submit_id"]:
                    db.execute(
                        "UPDATE tasks SET status='running', gen_status='querying', updated_at=datetime('now') WHERE id=?",
                        (next_task["id"],)
                    )
                    db.commit()
                    logger.info(f"Task {next_task['id']} already submitted, moved to running")
                    db.close()
                    continue

                submit_id = await submit_task_to_dreamina(next_task)
                if submit_id:
                    db.execute(
                        """UPDATE tasks SET status='running', submit_id=?,
                           gen_status='querying', updated_at=datetime('now') WHERE id=?""",
                        (submit_id, next_task["id"])
                    )
                    db.commit()
                    logger.info(f"Task {next_task['id']} submitted: {submit_id}")
                else:
                    db.execute(
                        """UPDATE tasks SET status='failed',
                           error_message='Submit failed — check concurrency or compliance',
                           updated_at=datetime('now') WHERE id=?""",
                        (next_task["id"],)
                    )
                    db.commit()
                    logger.error(f"Task {next_task['id']} submit failed")

            db.close()
            await asyncio.sleep(POLL_INTERVAL)

        except Exception as e:
            logger.exception(f"Worker error: {e}")
            await asyncio.sleep(POLL_INTERVAL)
