"""Tasks: units of work the owner defines and starts runs for."""

from __future__ import annotations

from typing import Any

from ..db import Database, Row, dumps, loads, new_id, now_iso
from ..errors import ApiError, not_found
from ..validate import Body

STATUSES = ("backlog", "ready", "running", "needs-you", "review", "done")
PRIORITIES = ("low", "normal", "high", "urgent")


def serialize(row: Row, run_count: int | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": row["id"],
        "title": row["title"],
        "description": row["description"],
        "repoDir": row["repo_dir"],
        "branch": row["branch"],
        "criteria": loads(row["criteria"], []),
        "priority": row["priority"],
        "status": row["status"],
        "labels": loads(row["labels"], []),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }
    if run_count is not None:
        out["runCount"] = run_count
    return out


def _parse(data: object) -> dict[str, Any]:
    body = Body(data)
    return {
        "title": body.text("title", required=True, max_len=200),
        "description": body.text("description", max_len=50000),
        "repo_dir": body.text("repoDir", max_len=500),
        "branch": body.text("branch", max_len=200),
        "criteria": dumps(body.text_list("criteria", max_items=50, max_len=500)),
        "priority": body.choice("priority", PRIORITIES, "normal"),
        "status": body.choice("status", STATUSES, "backlog"),
        "labels": dumps(body.text_list("labels", max_items=20, max_len=40)),
    }


def list_tasks(db: Database) -> list[dict[str, Any]]:
    rows = db.all(
        "SELECT t.*, (SELECT COUNT(*) FROM runs r WHERE r.task_id = t.id) AS run_count "
        "FROM tasks t ORDER BY t.updated_at DESC"
    )
    return [serialize(r, r["run_count"]) for r in rows]


def get_row(db: Database, task_id: str) -> Row:
    row = db.one("SELECT * FROM tasks WHERE id = ?", (task_id,))
    if row is None:
        raise not_found("task", task_id)
    return row


def get_task(db: Database, task_id: str) -> dict[str, Any]:
    return serialize(get_row(db, task_id))


def create_task(db: Database, data: object) -> dict[str, Any]:
    fields = _parse(data)
    now = now_iso()
    fields |= {"id": new_id("task"), "created_at": now, "updated_at": now}
    cols = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    db.execute(f"INSERT INTO tasks ({cols}) VALUES ({marks})", tuple(fields.values()))  # noqa: S608
    return get_task(db, fields["id"])


def update_task(db: Database, task_id: str, data: object) -> dict[str, Any]:
    get_row(db, task_id)
    fields = _parse(data) | {"updated_at": now_iso()}
    sets = ", ".join(f"{col} = ?" for col in fields)
    db.execute(
        f"UPDATE tasks SET {sets} WHERE id = ?",  # noqa: S608 - column names are fixed
        (*fields.values(), task_id),
    )
    return get_task(db, task_id)


def move_task(db: Database, task_id: str, data: object) -> dict[str, Any]:
    get_row(db, task_id)
    status = Body(data).choice("status", STATUSES, "backlog")
    db.execute(
        "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?", (status, now_iso(), task_id)
    )
    return get_task(db, task_id)


def delete_task(db: Database, task_id: str) -> None:
    get_row(db, task_id)
    active = db.one(
        "SELECT COUNT(*) AS n FROM sessions s JOIN runs r ON r.id = s.run_id "
        "WHERE r.task_id = ? AND s.status IN ('queued', 'running')",
        (task_id,),
    )
    if active and active["n"]:
        raise ApiError("task-busy", "Stop this task's running sessions before deleting it.", 409)
    db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
