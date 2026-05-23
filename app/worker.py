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
    """Submit a task to dreamina CLI. Returns submit_id or None."""
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
        output = await run_dreamina(*cmd)
        result = parse_submit_output(output)
        return result.get("submit_id")
    except Exception as e:
        logger.error(f"Submit failed for task {task['id']}: {e}")
        return None
    finally:
        if tmp_dir and os.path.isdir(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)


async def check_task_result(submit_id: str) -> dict:
    """Check the result of a submitted task."""
    try:
        output = await run_dreamina("query_result", "--submit_id", submit_id)
        return parse_submit_output(output)
    except Exception as e:
        logger.error(f"Query failed for {submit_id}: {e}")
        return {"gen_status": "querying"}


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

            running = db.execute(
                "SELECT * FROM tasks WHERE status = 'running' ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()

            if running:
                db.close()
                result = await check_task_result(running["submit_id"])

                db = get_db()
                if result.get("gen_status") == "success":
                    db.execute(
                        """UPDATE tasks SET status='done', gen_status='success',
                           result_url=?, updated_at=datetime('now')
                           WHERE id=?""",
                        (result.get("result_url"), running["id"])
                    )
                    db.commit()
                    logger.info(f"Task {running['id']} completed successfully")
                elif result.get("gen_status") == "fail":
                    db.execute(
                        """UPDATE tasks SET status='failed', gen_status='fail',
                           error_message=?, updated_at=datetime('now')
                           WHERE id=?""",
                        (result.get("fail_reason", "Unknown error"), running["id"])
                    )
                    db.commit()
                    logger.info(f"Task {running['id']} failed: {result.get('fail_reason')}")
                else:
                    db.execute(
                        "UPDATE tasks SET gen_status='querying', updated_at=datetime('now') WHERE id=?",
                        (running["id"],)
                    )
                    db.commit()
                db.close()

                if result.get("gen_status") in ("success", "fail"):
                    continue  # Task done, immediately try to submit next
                else:
                    await asyncio.sleep(POLL_INTERVAL)  # Still running, wait
                    continue

            # No running task, pick next pending
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
                    logger.error(f"Task {next_task['id']} submit failed")
                db.close()
                await asyncio.sleep(POLL_INTERVAL)
            else:
                db.close()
                await asyncio.sleep(POLL_INTERVAL)

        except Exception as e:
            logger.exception(f"Worker error: {e}")
            await asyncio.sleep(POLL_INTERVAL)
