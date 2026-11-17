"""Runs and sessions: starting a task with a role, and reading what happened."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..db import Database, Row, dumps, loads, new_id, now_iso
from ..errors import ApiError, not_found
from ..runner.adapters import build_command
from ..runner.resolve import resolve_tool
from ..validate import Body
from . import profiles, roles, tasks

MODES = ("single",)
ACTIVE = ("queued", "running")
TERMINAL = ("succeeded", "failed", "cancelled")


# ---- planning ----------------------------------------------------------------------


def build_prompt(task: Row, role: Row) -> str:
    parts: list[str] = []
    parts.append(f"# Your role: {role['name']}")
    if role["instructions"]:
        parts.append(role["instructions"])
    parts.append(f"# Task: {task['title']}")
    if task["description"]:
        parts.append(task["description"])
    criteria = loads(task["criteria"], [])
    if criteria:
        parts.append("## Acceptance criteria\n" + "\n".join(f"- {c}" for c in criteria))
    if task["branch"]:
        parts.append(f"Work on the git branch `{task['branch']}`.")
    return "\n\n".join(parts) + "\n"


def resolve_workdir(task: Row, role: Row) -> Path:
    if role["workdir_mode"] == "fixed":
        raw, source = role["fixed_dir"], f"role '{role['name']}'"
    else:
        raw, source = task["repo_dir"], "this task"
    if not raw:
        raise ApiError(
            "workdir-missing",
            f"No working folder: set a repository folder on {source}.",
            field="repoDir",
        )
    path = Path(raw).expanduser()
    if not path.is_dir():
        raise ApiError("workdir-not-found", f"The folder {raw} (from {source}) does not exist.")
    return path


def plan_run(db: Database, task_id: str, data: object, *, fake: bool) -> dict[str, Any]:
    """Everything a run would do, without starting it."""
    body = Body(data)
    body.choice("mode", MODES, "single")
    task = tasks.get_row(db, task_id)
    role = roles.get_row(db, body.text("roleId", required=True, max_len=40))
    profile = profiles.serialize(profiles.get_row(db, role["profile_id"]))
    if role["timeout_s"]:
        profile["timeoutSeconds"] = role["timeout_s"]
    workdir = resolve_workdir(task, role)
    prompt = build_prompt(task, role)
    launch = resolve_tool(profile["tool"], profile["executable"], fake=fake)
    command = build_command(profile, prompt, launch)
    return {
        "task": task,
        "role": role,
        "profile": profile,
        "workdir": workdir,
        "prompt": prompt,
        "command": command.display(),
    }


def preview(db: Database, task_id: str, data: object, *, fake: bool) -> dict[str, Any]:
    plan = plan_run(db, task_id, data, fake=fake)
    return {
        "mode": "single",
        "sessions": [
            {
                "roleId": plan["role"]["id"],
                "roleName": plan["role"]["name"],
                "tool": plan["profile"]["tool"],
                "model": plan["profile"]["model"],
                "permission": plan["profile"]["permission"],
                "timeoutSeconds": plan["profile"]["timeoutSeconds"],
                "workdir": str(plan["workdir"]),
                "command": plan["command"],
                "prompt": plan["prompt"],
            }
        ],
    }


def start_run(db: Database, task_id: str, data: object, *, fake: bool) -> dict[str, Any]:
    plan = plan_run(db, task_id, data, fake=fake)
    role, profile = plan["role"], plan["profile"]
    now = now_iso()
    run_id = new_id("run")
    snapshot = {"mode": "single", "roles": [roles.serialize(role)], "profiles": [profile]}
    with db.transaction():
        db.execute(
            "INSERT INTO runs (id, task_id, mode, status, snapshot, created_at) "
            "VALUES (?, ?, 'single', 'queued', ?, ?)",
            (run_id, task_id, dumps(snapshot), now),
        )
        db.execute(
            "INSERT INTO sessions (id, run_id, role_id, role_name, tool, profile_snapshot, prompt, "
            "workdir, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?)",
            (
                new_id("sess"),
                run_id,
                role["id"],
                role["name"],
                profile["tool"],
                dumps(profile),
                plan["prompt"],
                str(plan["workdir"]),
                now,
            ),
        )
        db.execute(
            "UPDATE tasks SET status = 'running', updated_at = ? WHERE id = ?", (now, task_id)
        )
    return get_run(db, run_id)


# ---- status ------------------------------------------------------------------------


def refresh_run(db: Database, run_id: str) -> None:
    """Derive a run's status from its sessions; move the task when the run ends."""
    with db.transaction():
        run = db.one("SELECT * FROM runs WHERE id = ?", (run_id,))
        if run is None:
            return
        statuses = [
            r["status"] for r in db.all("SELECT status FROM sessions WHERE run_id = ?", (run_id,))
        ]
        if any(s == "running" for s in statuses):
            status = "running"
        elif any(s == "queued" for s in statuses):
            status = "queued"
        elif any(s == "failed" for s in statuses):
            status = "failed"
        elif any(s == "cancelled" for s in statuses):
            status = "cancelled"
        else:
            status = "succeeded"
        if status == run["status"]:
            return
        now = now_iso()
        finished = now if status in TERMINAL else None
        db.execute(
            "UPDATE runs SET status = ?, finished_at = ? WHERE id = ?", (status, finished, run_id)
        )
        if status in TERMINAL:
            task_status = {"succeeded": "review", "failed": "needs-you", "cancelled": "ready"}[
                status
            ]
            db.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ? AND status = 'running'",
                (task_status, now, run["task_id"]),
            )


# ---- reading -----------------------------------------------------------------------

SESSION_SELECT = (
    "SELECT s.*, r.task_id, t.title AS task_title FROM sessions s "
    "JOIN runs r ON r.id = s.run_id JOIN tasks t ON t.id = r.task_id"
)


def serialize_session(row: Row, *, detail: bool = False) -> dict[str, Any]:
    profile = loads(row["profile_snapshot"], {})
    out: dict[str, Any] = {
        "id": row["id"],
        "runId": row["run_id"],
        "taskId": row["task_id"],
        "taskTitle": row["task_title"],
        "roleId": row["role_id"],
        "roleName": row["role_name"],
        "tool": row["tool"],
        "model": profile.get("model", ""),
        "permission": profile.get("permission", "cli"),
        "status": row["status"],
        "cliSessionId": row["cli_session_id"],
        "exitCode": row["exit_code"],
        "error": row["error"],
        "resultText": row["result_text"],
        "usage": loads(row["usage"], {}),
        "workdir": row["workdir"],
        "createdAt": row["created_at"],
        "startedAt": row["started_at"],
        "finishedAt": row["finished_at"],
        "stepId": row["step_id"],
        "purpose": row["purpose"],
        "isolated": bool(row["isolated"]),
        "patchPath": row["patch_path"],
    }
    if detail:
        out["prompt"] = row["prompt"]
        out["profile"] = profile
    return out


def serialize_run(row: Row, sessions: list[dict[str, Any]]) -> dict[str, Any]:
    plan = loads(row["plan"], None)
    return {
        "id": row["id"],
        "taskId": row["task_id"],
        "taskTitle": row["task_title"],
        "mode": row["mode"],
        "status": row["status"],
        "stage": row["stage"],
        "plan": plan,
        "planVersion": row["plan_version"],
        "error": row["error"],
        "notes": row["notes"],
        "createdAt": row["created_at"],
        "finishedAt": row["finished_at"],
        "sessions": sessions,
    }


def _plan_details(db: Database, run_id: str) -> dict[str, Any]:
    steps = {
        r["step_id"]: {
            "status": r["status"],
            "output": r["output"],
            "error": r["error"],
            "attempts": r["attempts"],
            "startedAt": r["started_at"],
            "finishedAt": r["finished_at"],
        }
        for r in db.all("SELECT * FROM run_steps WHERE run_id = ?", (run_id,))
    }
    feedback = [
        {"id": r["id"], "target": r["target"], "text": r["text"], "createdAt": r["created_at"]}
        for r in db.all("SELECT * FROM run_feedback WHERE run_id = ? ORDER BY id", (run_id,))
    ]
    versions = [
        {"version": r["version"], "plan": loads(r["plan"], {}), "createdAt": r["created_at"]}
        for r in db.all("SELECT * FROM plan_versions WHERE run_id = ? ORDER BY version", (run_id,))
    ]
    return {"steps": steps, "feedback": feedback, "planVersions": versions}


def list_runs(db: Database, task_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    where, params = ("WHERE r.task_id = ?", [task_id]) if task_id else ("", [])
    rows = db.all(
        "SELECT r.*, t.title AS task_title FROM runs r JOIN tasks t ON t.id = r.task_id "  # noqa: S608
        f"{where} ORDER BY r.created_at DESC LIMIT ?",
        (*params, limit),
    )
    if not rows:
        return []
    ids = [r["id"] for r in rows]
    marks = ", ".join("?" for _ in ids)
    by_run: dict[str, list[dict[str, Any]]] = {}
    for s in db.all(f"{SESSION_SELECT} WHERE s.run_id IN ({marks}) ORDER BY s.created_at", ids):
        by_run.setdefault(s["run_id"], []).append(serialize_session(s))
    return [serialize_run(r, by_run.get(r["id"], [])) for r in rows]


def get_run(db: Database, run_id: str) -> dict[str, Any]:
    row = db.one(
        "SELECT r.*, t.title AS task_title FROM runs r JOIN tasks t ON t.id = r.task_id "
        "WHERE r.id = ?",
        (run_id,),
    )
    if row is None:
        raise not_found("run", run_id)
    sessions = [
        serialize_session(s, detail=True)
        for s in db.all(f"{SESSION_SELECT} WHERE s.run_id = ? ORDER BY s.created_at", (run_id,))
    ]
    out = serialize_run(row, sessions)
    if row["mode"] == "planned":
        out |= _plan_details(db, run_id)
    return out


def list_sessions(db: Database, *, active_only: bool) -> list[dict[str, Any]]:
    if active_only:
        rows = db.all(
            f"{SESSION_SELECT} WHERE s.status IN ('queued', 'running') ORDER BY s.created_at"
        )
    else:
        rows = db.all(f"{SESSION_SELECT} ORDER BY s.created_at DESC LIMIT 200")
    return [serialize_session(r) for r in rows]


def get_session_row(db: Database, session_id: str) -> Row:
    row = db.one(f"{SESSION_SELECT} WHERE s.id = ?", (session_id,))
    if row is None:
        raise not_found("session", session_id)
    return row


def list_events(db: Database, session_id: str, after: int, limit: int = 500) -> dict[str, Any]:
    session = get_session_row(db, session_id)
    rows = db.all(
        "SELECT id, kind, data, created_at FROM events WHERE session_id = ? AND id > ? "
        "ORDER BY id LIMIT ?",
        (session_id, after, limit),
    )
    return {
        "session": serialize_session(session),
        "events": [
            {
                "id": r["id"],
                "kind": r["kind"],
                "data": loads(r["data"], {}),
                "createdAt": r["created_at"],
            }
            for r in rows
        ],
    }


def delete_run(db: Database, run_id: str) -> None:
    row = db.one("SELECT status FROM runs WHERE id = ?", (run_id,))
    if row is None:
        raise not_found("run", run_id)
    busy = db.one(
        "SELECT COUNT(*) AS n FROM sessions WHERE run_id = ? AND status IN ('queued', 'running')",
        (run_id,),
    )
    if row["status"] in (*ACTIVE, "planning") or (busy and busy["n"]):
        raise ApiError("run-busy", "Cancel this run before deleting it.", 409)
    db.execute("DELETE FROM runs WHERE id = ?", (run_id,))
