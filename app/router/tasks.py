import json
from fastapi import APIRouter, HTTPException
from typing import Optional

from app.database import get_db
from app.models import TaskCreate, TaskUpdate, ReorderRequest, TaskResponse, TaskParams, Reference
from app.dreamina import determine_task_type

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def row_to_task(row) -> TaskResponse:
    if row is None:
        return None
    params = json.loads(row["params"] or "{}")
    refs = json.loads(row["refs"] or "[]")
    return TaskResponse(
        id=row["id"],
        type=row["type"],
        status=row["status"],
        prompt=row["prompt"],
        params=TaskParams(**params) if params else TaskParams(),
        references=[Reference(**r) for r in refs],
        submit_id=row["submit_id"],
        result_url=row["result_url"],
        gen_status=row["gen_status"],
        error_message=row["error_message"],
        position=row["position"],
        session_id=row["session_id"] or 0,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@router.get("", response_model=list[TaskResponse])
def list_tasks(status: Optional[str] = None,
               sort: Optional[str] = "position",
               limit: int = 50,
               offset: int = 0):
    db = get_db()
    query = "SELECT * FROM tasks"
    conditions = []
    params = []
    if status:
        conditions.append("status = ?")
        params.append(status)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += f" ORDER BY {sort} ASC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    rows = db.execute(query, params).fetchall()
    db.close()
    return [row_to_task(r) for r in rows]


@router.post("", response_model=TaskResponse)
def create_task(req: TaskCreate):
    task_type = determine_task_type(req.references)
    params = TaskParams(
        duration=req.duration,
        ratio=req.ratio,
        model_version=req.model_version
    )
    refs_json = json.dumps([r.model_dump() for r in req.references], ensure_ascii=False)
    params_json = json.dumps(params.model_dump(), ensure_ascii=False)

    db = get_db()
    max_pos = db.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM tasks").fetchone()[0]
    cur = db.execute(
        """INSERT INTO tasks (type, status, prompt, params, refs, position)
           VALUES (?, 'pending', ?, ?, ?, ?)""",
        (task_type, req.prompt, params_json, refs_json, max_pos)
    )
    task_id = cur.lastrowid
    db.commit()

    row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    db.close()
    return row_to_task(row)


@router.get("/{task_id}", response_model=TaskResponse)
def get_task(task_id: int):
    db = get_db()
    row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    db.close()
    if not row:
        raise HTTPException(status_code=404, detail="Task not found")
    return row_to_task(row)


@router.patch("/{task_id}", response_model=TaskResponse)
def update_task(task_id: int, req: TaskUpdate):
    db = get_db()
    row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        db.close()
        raise HTTPException(status_code=404, detail="Task not found")
    if row["status"] not in ("pending",):
        db.close()
        raise HTTPException(status_code=400, detail="Only pending tasks can be edited")

    updates = {}
    if req.prompt is not None:
        updates["prompt"] = req.prompt
    if req.duration is not None or req.ratio is not None or req.model_version is not None:
        params = json.loads(row["params"] or "{}")
        if req.duration is not None:
            params["duration"] = req.duration
        if req.ratio is not None:
            params["ratio"] = req.ratio
        if req.model_version is not None:
            params["model_version"] = req.model_version
        updates["params"] = json.dumps(params, ensure_ascii=False)
    if req.references is not None:
        task_type = determine_task_type(req.references)
        updates["type"] = task_type
        updates["refs"] = json.dumps([r.model_dump() for r in req.references], ensure_ascii=False)

    if updates:
        updates["updated_at"] = "datetime('now')"
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        vals = list(updates.values())
        vals.append(task_id)
        db.execute(f"UPDATE tasks SET {set_clause} WHERE id = ?", vals)
        db.commit()

    row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    db.close()
    return row_to_task(row)


@router.delete("/{task_id}")
def delete_task(task_id: int):
    db = get_db()
    row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        db.close()
        raise HTTPException(status_code=404, detail="Task not found")
    if row["status"] not in ("pending",):
        db.close()
        raise HTTPException(status_code=400, detail="Only pending tasks can be deleted")
    db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    db.commit()
    db.close()
    return {"ok": True}


@router.patch("/{task_id}/reorder")
def reorder_task(task_id: int, req: ReorderRequest):
    db = get_db()
    row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        db.close()
        raise HTTPException(status_code=404, detail="Task not found")
    if row["status"] not in ("pending",):
        db.close()
        raise HTTPException(status_code=400, detail="Only pending tasks can be reordered")

    old_pos = row["position"]
    new_pos = req.position
    if old_pos < new_pos:
        db.execute(
            "UPDATE tasks SET position = position - 1 WHERE status = 'pending' AND position > ? AND position <= ?",
            (old_pos, new_pos)
        )
    elif old_pos > new_pos:
        db.execute(
            "UPDATE tasks SET position = position + 1 WHERE status = 'pending' AND position >= ? AND position < ?",
            (new_pos, old_pos)
        )
    db.execute("UPDATE tasks SET position = ?, updated_at = datetime('now') WHERE id = ?", (new_pos, task_id))
    db.commit()
    db.close()
    return {"ok": True}
