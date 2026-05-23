import asyncio
import json
import logging
import os
import shutil
import tempfile
import time

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
MAX_QUERY_NOT_FOUND_RETRIES = 5  # give up after ~2.5 min of "record not found"

_paused = False


def get_worker_status() -> bool:
    return _paused


def set_worker_paused(paused: bool):
    global _paused
    _paused = paused


async def submit_task_to_dreamina(task) -> str | None:
    """Submit a task to dreamina CLI. Returns full submit_id or None on failure."""
    params = json.loads(task["params"] or "{}")
    refs = json.loads(task["references"] or "[]")
    tmp_dir = None
    ref_files = []
    try:
        if refs:
            tmp_dir = tempfile.mkdtemp(prefix="jimeng_refs_")
            for ref in refs:
                local_path = os.path.join(tmp_dir, ref["filename"])
                for attempt in range(3):
                    try:
                        download_from_cos(ref["cos_url"], local_path)
                        break
                    except Exception:
                        if attempt == 2:
                            raise
                        logger.warning(f"COS download retry {attempt+1} for {ref['filename']}")
                        await asyncio.sleep(2)
                ref_files.append(local_path)

        cmd = build_submit_command(task["type"], task["prompt"], params, ref_files)
        stdout, stderr, rc = await run_dreamina(*cmd)
        combined = stdout + "\n" + stderr
        result = parse_submit_output(combined)

        if result["compliance_required"]:
            logger.error(f"Task {task['id']}: AigcComplianceConfirmationRequired")
            raise RuntimeError("Compliance confirmation required — authorize on Dreamina Web first")

        submit_id = result.get("submit_id")
        if result.get("gen_status") == "fail" and result.get("fail_reason"):
            reason = result["fail_reason"]
            logger.error(f"Task {task['id']} submit rejected: {reason}")
            raise RuntimeError(reason)

        if not submit_id:
            raise RuntimeError("No submit_id in output")

        return submit_id
    except Exception as e:
        logger.error(f"Submit failed for task {task['id']}: {e}")
        # Store the real failure reason so it appears in the DB
        task["_submit_error"] = str(e)
        return None
    finally:
        if tmp_dir and os.path.isdir(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)


async def check_task_via_query(submit_id: str) -> dict | None:
    """Check single task via query_result. Returns None if record not found."""
    try:
        stdout, stderr, rc = await run_dreamina("query_result", "--submit_id", submit_id)
    except RuntimeError:
        return None  # timeout
    combined = stdout + "\n" + stderr
    if "record not found" in combined.lower():
        return None
    return parse_submit_output(combined)


async def sync_running_task(task) -> bool:
    """Check one running task. Returns True if task should no longer be polled."""
    submit_id = task["submit_id"]
    if not submit_id:
        db = get_db()
        try:
            db.execute("UPDATE tasks SET status='failed', error_message='missing submit_id' WHERE id=?", (task["id"],))
            db.commit()
        finally:
            db.close()
        return True

    result = await check_task_via_query(submit_id)
    if result is None:
        # Count consecutive "not found" retries
        not_found_count = int(task.get("_nf_retries") or 0) + 1
        if not_found_count >= MAX_QUERY_NOT_FOUND_RETRIES:
            db = get_db()
            try:
                db.execute(
                    "UPDATE tasks SET status='failed', error_message='task record expired in dreamina' WHERE id=?",
                    (task["id"],)
                )
                db.commit()
            finally:
                db.close()
            logger.error(f"Task {task['id']} record not found after {not_found_count} retries, marking failed")
            return True
        # Track retry count in-memory on the task dict (not persisted)
        task["_nf_retries"] = not_found_count
        logger.warning(f"Task {task['id']} record not found (retry {not_found_count}/{MAX_QUERY_NOT_FOUND_RETRIES})")
        return False

    gs = result.get("gen_status")
    db = get_db()
    try:
        if gs == "success":
            db.execute(
                "UPDATE tasks SET status='done', gen_status='success', result_url=?, updated_at=datetime('now') WHERE id=?",
                (result.get("result_url"), task["id"])
            )
            db.commit()
            logger.info(f"Task {task['id']} completed")
            return True
        elif gs == "fail":
            db.execute(
                "UPDATE tasks SET status='failed', gen_status='fail', error_message=?, updated_at=datetime('now') WHERE id=?",
                (result.get("fail_reason", "unknown"), task["id"])
            )
            db.commit()
            logger.info(f"Task {task['id']} failed: {result.get('fail_reason')}")
            return True
        else:
            db.execute("UPDATE tasks SET gen_status='querying', updated_at=datetime('now') WHERE id=?", (task["id"],))
            db.commit()
            return False
    finally:
        db.close()


async def queue_worker():
    global _paused
    logger.info("Queue worker started")
    while True:
        try:
            if _paused:
                await asyncio.sleep(5)
                continue

            db = get_db()
            try:
                running = db.execute(
                    "SELECT * FROM tasks WHERE status = 'running' ORDER BY updated_at ASC LIMIT 1"
                ).fetchone()
            finally:
                db.close()

            if running:
                # Convert row to mutable dict for retry tracking
                task_dict = dict(running)
                done = await sync_running_task(task_dict)
                if done:
                    continue
                await asyncio.sleep(POLL_INTERVAL)
                continue

            # No running task — submit next pending
            db = get_db()
            try:
                next_task = db.execute(
                    "SELECT * FROM tasks WHERE status = 'pending' ORDER BY position ASC LIMIT 1"
                ).fetchone()

                if next_task:
                    if next_task["submit_id"]:
                        # Already submitted, move to running
                        db.execute(
                            "UPDATE tasks SET status='running', gen_status='querying', updated_at=datetime('now') WHERE id=?",
                            (next_task["id"],)
                        )
                        db.commit()
                        logger.info(f"Task {next_task['id']} already submitted, moved to running")
                    else:
                        # Submit now
                        submit_id = await submit_task_to_dreamina(dict(next_task))
                        if submit_id:
                            db.execute(
                                """UPDATE tasks SET status='running', submit_id=?,
                                   gen_status='querying', updated_at=datetime('now') WHERE id=?""",
                                (submit_id, next_task["id"])
                            )
                            db.commit()
                            logger.info(f"Task {next_task['id']} submitted: {submit_id}")
                        else:
                            error_msg = getattr(next_task, '_submit_error', None) or "Submit failed"
                            db.execute(
                                "UPDATE tasks SET status='failed', error_message=?, updated_at=datetime('now') WHERE id=?",
                                (str(error_msg), next_task["id"])
                            )
                            db.commit()
                            logger.error(f"Task {next_task['id']} submit failed: {error_msg}")
            finally:
                db.close()

            await asyncio.sleep(POLL_INTERVAL)

        except Exception as e:
            logger.exception(f"Worker error: {e}")
            await asyncio.sleep(POLL_INTERVAL)
