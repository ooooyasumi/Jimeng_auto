import json
from fastapi import APIRouter, HTTPException
from typing import Optional

from app.database import get_db
from app.models import TaskCreate, TaskUpdate, ReorderRequest, TaskResponse, TaskParams, Reference
from app.dreamina import determine_task_type

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

VALID_RATIOS = {"1:1", "3:4", "4:3", "16:9", "9:16", "21:9"}
VALID_MODELS = {"seedance2.0fast", "seedance2.0"}
VALID_SORT = {"position", "created_at", "updated_at", "id", "status"}


def row_to_task(row) -> TaskResponse:
    if row is None:
        return None
    params = json.loads(row["params"] or "{}")
    refs = json.loads(row["refs"] or "[]")
    return TaskResponse(
        id=row["id"], type=row["type"], status=row["status"],
        prompt=row["prompt"],
        params=TaskParams(**params) if params else TaskParams(),
        references=[Reference(**r) for r in refs],
        submit_id=row["submit_id"], submitted_at=row["submitted_at"],
        result_url=row["result_url"],
        gen_status=row["gen_status"], error_message=row["error_message"],
        position=row["position"], session_id=row["session_id"] or 0,
        created_at=row["created_at"], updated_at=row["updated_at"],
    )


@router.get("", response_model=list[TaskResponse])
def list_tasks(status: Optional[str] = None,
               sort: Optional[str] = "position",
               limit: int = 50,
               offset: int = 0):
    if sort not in VALID_SORT:
        raise HTTPException(status_code=400, detail=f"Invalid sort field: {sort}")
    db = get_db()
    try:
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
        return [row_to_task(r) for r in rows]
    finally:
        db.close()


@router.post("", response_model=TaskResponse)
def create_task(req: TaskCreate):
    # Backend validation
    if not req.prompt or not req.prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt cannot be empty")
    if req.duration < 4 or req.duration > 15:
        raise HTTPException(status_code=400, detail="Duration must be 4-15 seconds")
    if req.ratio not in VALID_RATIOS:
        raise HTTPException(status_code=400, detail=f"Invalid ratio: {req.ratio}")
    if req.model_version not in VALID_MODELS:
        raise HTTPException(status_code=400, detail=f"Invalid model: {req.model_version}")

    task_type = determine_task_type(req.references)
    params_json = json.dumps(TaskParams(
        duration=req.duration, ratio=req.ratio, model_version=req.model_version
    ).model_dump(), ensure_ascii=False)
    refs_json = json.dumps([r.model_dump() for r in req.references], ensure_ascii=False)

    db = get_db()
    try:
        max_pos = db.execute("SELECT COUNT(*) FROM tasks WHERE status = 'pending'").fetchone()[0]
        cur = db.execute(
            "INSERT INTO tasks (type, status, prompt, params, refs, position) VALUES (?, 'pending', ?, ?, ?, ?)",
            (task_type, req.prompt.strip(), params_json, refs_json, max_pos)
        )
        task_id = cur.lastrowid
        db.commit()
        row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return row_to_task(row)
    finally:
        db.close()


@router.get("/{task_id}", response_model=TaskResponse)
def get_task(task_id: int):
    db = get_db()
    try:
        row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Task not found")
        return row_to_task(row)
    finally:
        db.close()


@router.patch("/{task_id}", response_model=TaskResponse)
def update_task(task_id: int, req: TaskUpdate):
    db = get_db()
    try:
        row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Task not found")
        if row["status"] not in ("pending",):
            raise HTTPException(status_code=400, detail="Only pending tasks can be edited")

        updates = {}
        if req.prompt is not None:
            if not req.prompt.strip():
                raise HTTPException(status_code=400, detail="Prompt cannot be empty")
            updates["prompt"] = req.prompt.strip()
        if req.duration is not None:
            if req.duration < 4 or req.duration > 15:
                raise HTTPException(status_code=400, detail="Duration must be 4-15")
            params = json.loads(row["params"] or "{}")
            params["duration"] = req.duration
            updates["params"] = json.dumps(params, ensure_ascii=False)
        if req.ratio is not None:
            if req.ratio not in VALID_RATIOS:
                raise HTTPException(status_code=400, detail=f"Invalid ratio: {req.ratio}")
            params = json.loads(row["params"] or "{}")
            params["ratio"] = req.ratio
            updates["params"] = json.dumps(params, ensure_ascii=False)
        if req.model_version is not None:
            if req.model_version not in VALID_MODELS:
                raise HTTPException(status_code=400, detail=f"Invalid model: {req.model_version}")
            params = json.loads(row["params"] or "{}")
            params["model_version"] = req.model_version
            updates["params"] = json.dumps(params, ensure_ascii=False)
        if req.references is not None:
            task_type = determine_task_type(req.references)
            updates["type"] = task_type
            updates["refs"] = json.dumps([r.model_dump() for r in req.references], ensure_ascii=False)

        if updates:
            set_parts = [f"{k} = ?" for k in updates]
            set_parts.append("updated_at = datetime('now')")
            set_clause = ", ".join(set_parts)
            vals = list(updates.values())
            vals.append(task_id)
            db.execute(f"UPDATE tasks SET {set_clause} WHERE id = ?", vals)
            db.commit()

        row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return row_to_task(row)
    finally:
        db.close()


@router.delete("/{task_id}")
def delete_task(task_id: int):
    db = get_db()
    try:
        row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Task not found")
        if row["status"] not in ("pending",):
            raise HTTPException(status_code=400, detail="Only pending tasks can be deleted")
        db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        db.commit()
        return {"ok": True}
    finally:
        db.close()


@router.patch("/{task_id}/reorder")
def reorder_task(task_id: int, req: ReorderRequest):
    db = get_db()
    try:
        row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Task not found")
        if row["status"] not in ("pending",):
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
        # Renumber all pending to keep positions continuous
        rows = db.execute("SELECT id FROM tasks WHERE status = 'pending' ORDER BY position ASC").fetchall()
        for i, r in enumerate(rows):
            db.execute("UPDATE tasks SET position = ? WHERE id = ?", (i, r["id"]))
        db.commit()
        return {"ok": True}
    finally:
        db.close()
