"""AI-planned runs.

Lifecycle of a run with mode 'planned':

  stage 'plan'     planning ──► awaiting-approval ──(feedback)──► planning …
                                      │ approve
  stage 'execute'  running ◄──► awaiting-approval (a gate) ──► succeeded / failed / cancelled

`advance()` is the single place that moves a planned run forward. It is called whenever
a session finishes and after every owner action, always inside one DB transaction.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..db import Database, Row, dumps, loads, new_id, now_iso
from ..errors import ApiError, invalid, not_found
from ..runner import git
from ..runner.resolve import resolve_tool
from ..validate import Body
from . import plans, profiles, runs, settings, tasks

ACTIVE = ("queued", "running")
RUN_TERMINAL = ("succeeded", "failed", "cancelled")
TASK_STATUS = {
    "planning": "running",
    "running": "running",
    "awaiting-approval": "needs-you",
    "succeeded": "review",
    "failed": "needs-you",
    "cancelled": "ready",
}


# ---- entry points ----------------------------------------------------------------------


def refresh(db: Database, run_id: str) -> None:
    """Call after any session of a run changes state."""
    row = db.one("SELECT mode FROM runs WHERE id = ?", (run_id,))
    if row is None:
        return
    if row["mode"] == "planned":
        advance(db, run_id)
    else:
        runs.refresh_run(db, run_id)


def start_planning(db: Database, task_id: str, data: object, *, fake: bool) -> dict[str, Any]:
    body = Body(data)
    task = tasks.get_row(db, task_id)
    workdir = task_workdir(task)
    profile_id = body.text("profileId", max_len=40) or default_planner_profile(db)
    if not profile_id:
        raise ApiError(
            "planner-profile-missing",
            "Create a profile for the planner first (Claude Code is recommended).",
        )
    profile = session_profile(db, profile_id)
    notes = body.text("notes", max_len=5000)
    resolve_tool(
        profile["tool"], profile["executable"], fake=fake
    )  # fail now if the CLI is missing
    guidance = settings.get_planner(db)["guidance"]
    prompt = plans.planner_prompt(
        task,
        all_profiles(db),
        guidance,
        notes,
        limits=settings.get_limits(db),
        git_repo=git.locate(workdir) is not None,
    )
    run_id = new_id("run")
    now = now_iso()
    with db.transaction():
        db.execute(
            "INSERT INTO runs (id, task_id, mode, status, stage, snapshot, notes, created_at) "
            "VALUES (?, ?, 'planned', 'planning', 'plan', ?, ?, ?)",
            (run_id, task_id, dumps({"mode": "planned", "planner": profile}), notes, now),
        )
        _add_session(db, run_id, None, "planner", "Planner", profile, prompt, workdir)
        _set_task_status(db, task_id, "planning")
    return runs.get_run(db, run_id)


def approve_plan(db: Database, run_id: str) -> None:
    with db.transaction():
        run = _planned_run(db, run_id)
        if run["stage"] != "plan" or run["status"] != "awaiting-approval":
            raise ApiError(
                "run-not-awaiting-plan", "This run is not waiting for plan approval.", 409
            )
        if not run["plan"]:
            raise ApiError(
                "plan-missing",
                "There is no usable plan yet. Send feedback so the planner can try again.",
                409,
            )
        try:
            plan = plans.validate_plan(loads(run["plan"], {}), profiles_by_id(db))
        except plans.PlanError as err:
            raise ApiError("plan-invalid", f"The plan is no longer valid: {err}", 409) from err
        isolated = [s["title"] for s in plan["steps"] if s.get("access") == "isolated"]
        if isolated:
            folder = task_workdir(tasks.get_row(db, run["task_id"]))
            if git.locate(folder) is None:
                raise ApiError(
                    "isolation-needs-git",
                    "These steps need their own copy of the repository, which requires the "
                    f"task folder to be a git repository: {', '.join(isolated)}. Run "
                    "`git init` there, or ask the planner to avoid isolated steps.",
                    409,
                )
        db.execute(
            "UPDATE runs SET stage = 'execute', plan = ?, error = NULL WHERE id = ?",
            (dumps(plan), run_id),
        )
        for step in plan["steps"]:
            db.execute(
                "INSERT INTO run_steps (run_id, step_id, status) VALUES (?, ?, 'pending')",
                (run_id, step["id"]),
            )
    advance(db, run_id)


def send_feedback(db: Database, run_id: str, data: object) -> None:
    body = Body(data)
    target = body.text("target", required=True, max_len=40)
    text = body.text("text", required=True, max_len=10000)
    with db.transaction():
        run = _planned_run(db, run_id)
        if target == "plan":
            _revise_plan(db, run, text)
        else:
            _revise_step(db, run, target, text)
        db.execute(
            "INSERT INTO run_feedback (run_id, target, text, created_at) VALUES (?, ?, ?, ?)",
            (run_id, target, text, now_iso()),
        )
    advance(db, run_id)


def approve_step(db: Database, run_id: str, step_id: str) -> None:
    with db.transaction():
        run = _planned_run(db, run_id)
        plan = loads(run["plan"], {})
        step = _step(plan, step_id)
        state = _step_row(db, run_id, step_id)
        if step["kind"] != "gate" or state["status"] != "awaiting-approval":
            raise ApiError("step-not-awaiting", "This step is not waiting for approval.", 409)
        reviewed = _outputs_for(db, run_id, plan, step["inputs"])
        output = "\n\n".join(f"### {src['title']}\n{text}" for src, text in reviewed)
        db.execute(
            "UPDATE run_steps SET status = 'succeeded', output = ?, finished_at = ? "
            "WHERE run_id = ? AND step_id = ?",
            (output, now_iso(), run_id, step_id),
        )
    advance(db, run_id)


def retry_step(db: Database, run_id: str, step_id: str) -> None:
    with db.transaction():
        run = _planned_run(db, run_id)
        if run["stage"] != "execute":
            raise ApiError(
                "run-not-executing", "Only steps of an approved workflow can be retried.", 409
            )
        _step(loads(run["plan"], {}), step_id)
        state = _step_row(db, run_id, step_id)
        if state["status"] not in ("failed", "cancelled"):
            raise ApiError(
                "step-not-retryable", "Only failed or cancelled steps can be retried.", 409
            )
        if _active_sessions(db, run_id):
            raise ApiError("run-busy", "Wait for the running steps to finish first.", 409)
        db.execute(
            "UPDATE run_steps SET status = 'pending', error = NULL "
            "WHERE run_id = ? AND step_id = ?",
            (run_id, step_id),
        )
        # Other cancelled steps would block the run forever; reopen them too.
        db.execute(
            "UPDATE run_steps SET status = 'pending', error = NULL "
            "WHERE run_id = ? AND status = 'cancelled'",
            (run_id,),
        )
        db.execute(
            "UPDATE runs SET status = 'running', finished_at = NULL, error = NULL WHERE id = ?",
            (run_id,),
        )
        _set_task_status(db, run["task_id"], "running")
    advance(db, run_id)


def cancel(db: Database, run_id: str) -> None:
    """Mark a planned run cancelled. The caller has already stopped its sessions."""
    with db.transaction():
        run = db.one("SELECT * FROM runs WHERE id = ?", (run_id,))
        if run is None or run["mode"] != "planned" or run["status"] in RUN_TERMINAL:
            return
        now = now_iso()
        db.execute(
            "UPDATE run_steps SET status = 'cancelled', finished_at = ? WHERE run_id = ? "
            "AND status IN ('pending', 'queued', 'running', 'awaiting-approval')",
            (now, run_id),
        )
        db.execute(
            "UPDATE sessions SET status = 'cancelled', finished_at = ? "
            "WHERE run_id = ? AND status = 'queued'",
            (now, run_id),
        )
        _set_run_status(db, run, "cancelled")


# ---- the state machine ---------------------------------------------------------------


def advance(db: Database, run_id: str) -> None:
    with db.transaction():
        run = db.one("SELECT * FROM runs WHERE id = ?", (run_id,))
        if run is None or run["mode"] != "planned" or run["status"] in RUN_TERMINAL:
            return
        if run["stage"] == "plan":
            _advance_plan(db, run)
        else:
            _advance_steps(db, run)


def _advance_plan(db: Database, run: Row) -> None:
    session = db.one(
        "SELECT * FROM sessions WHERE run_id = ? AND purpose = 'planner' "
        "ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (run["id"],),
    )
    if session is None:
        return
    if session["status"] in ACTIVE:
        _set_run_status(db, run, "planning")
        return
    if session["status"] == "succeeded":
        parsed = db.one(
            "SELECT 1 FROM plan_versions WHERE run_id = ? AND session_id = ?",
            (run["id"], session["id"]),
        )
        if parsed is None:
            try:
                plan = plans.parse_plan(session["result_text"] or "", profiles_by_id(db))
            except plans.PlanError as err:
                db.execute(
                    "UPDATE runs SET error = ? WHERE id = ?",
                    (f"The planner's answer could not be used: {err}", run["id"]),
                )
            else:
                version = int(run["plan_version"]) + 1
                db.execute(
                    "INSERT INTO plan_versions (run_id, version, plan, session_id, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (run["id"], version, dumps(plan), session["id"], now_iso()),
                )
                db.execute(
                    "UPDATE runs SET plan = ?, plan_version = ?, error = NULL WHERE id = ?",
                    (dumps(plan), version, run["id"]),
                )
    else:
        message = session["error"] or "The planner session did not finish."
        db.execute("UPDATE runs SET error = ? WHERE id = ?", (f"Planner: {message}", run["id"]))
    _set_run_status(db, run, "awaiting-approval")


def _advance_steps(db: Database, run: Row) -> None:
    run_id = run["id"]
    plan = loads(run["plan"], {})
    steps: list[dict[str, Any]] = plan.get("steps", [])
    states = {
        r["step_id"]: r for r in db.all("SELECT * FROM run_steps WHERE run_id = ?", (run_id,))
    }
    now = now_iso()

    # 1. Pick up finished sessions.
    for step in steps:
        state = states.get(step["id"])
        if state is None or state["status"] not in ACTIVE:
            continue
        session = _latest_step_session(db, run_id, step["id"])
        if session is None:
            continue
        if session["status"] == "running" and state["status"] == "queued":
            _update_step(db, states, run_id, step["id"], status="running")
        elif session["status"] == "succeeded":
            output = session["result_text"] or ""
            _update_step(
                db,
                states,
                run_id,
                step["id"],
                status="succeeded",
                output=output,
                error=None,
                finished_at=now,
            )
        elif session["status"] in ("failed", "cancelled"):
            _update_step(
                db,
                states,
                run_id,
                step["id"],
                status=session["status"],
                error=session["error"],
                finished_at=now,
            )

    # 2. Start whatever is ready, unless something already went wrong.
    blocked = any(s["status"] in ("failed", "cancelled") for s in states.values())
    if not blocked:
        task = tasks.get_row(db, run["task_id"])
        for step in steps:
            state = states.get(step["id"])
            if state is None or state["status"] != "pending":
                continue
            if not all(states[d]["status"] == "succeeded" for d in step["inputs"] if d in states):
                continue
            if step["kind"] == "gate":
                _update_step(db, states, run_id, step["id"], status="awaiting-approval")
                continue
            if step["access"] == "write" and _write_in_progress(steps, states):
                continue
            inputs = _outputs_for(db, run_id, plan, step["inputs"])
            prompt = plans.step_prompt(task, plan, step, inputs)
            _start_step_session(db, run_id, task, step, prompt, "step")
            _update_step(
                db,
                states,
                run_id,
                step["id"],
                status="queued",
                attempts=int(state["attempts"]) + 1,
                started_at=now,
                finished_at=None,
            )

    # 3. Derive the run status.
    statuses = [s["status"] for s in states.values()]
    if any(s in ACTIVE for s in statuses):
        status = "running"
    elif "awaiting-approval" in statuses:
        status = "awaiting-approval"
    elif "failed" in statuses:
        status = "failed"
    elif "cancelled" in statuses:
        status = "cancelled"
    elif statuses and all(s == "succeeded" for s in statuses):
        status = "succeeded"
    else:
        status = "failed"
        db.execute(
            "UPDATE runs SET error = ? WHERE id = ?",
            ("Some steps could not start because their inputs never finished.", run_id),
        )
    _set_run_status(db, run, status)


# ---- revisions -----------------------------------------------------------------------


def _revise_plan(db: Database, run: Row, feedback: str) -> None:
    if run["stage"] != "plan" or run["status"] != "awaiting-approval":
        raise ApiError("run-not-awaiting-plan", "The plan is not waiting for your review.", 409)
    task = tasks.get_row(db, run["task_id"])
    snapshot = loads(run["snapshot"], {})
    planner = snapshot.get("planner") or {}
    profile = session_profile(db, str(planner.get("id", ""))) if planner else None
    if profile is None:
        raise ApiError("planner-profile-missing", "The planner's profile no longer exists.", 409)
    prompt = plans.planner_prompt(
        task,
        all_profiles(db),
        settings.get_planner(db)["guidance"],
        run["notes"],
        limits=settings.get_limits(db),
        git_repo=git.locate(task_workdir(task)) is not None,
        previous_plan=loads(run["plan"], None),
        feedback=feedback,
        problem=run["error"],
    )
    _add_session(db, run["id"], None, "planner", "Planner", profile, prompt, task_workdir(task))
    db.execute("UPDATE runs SET error = NULL WHERE id = ?", (run["id"],))
    _set_run_status(db, run, "planning")


def _revise_step(db: Database, run: Row, gate_id: str, feedback: str) -> None:
    plan = loads(run["plan"], {})
    gate = _step(plan, gate_id)
    gate_state = _step_row(db, run["id"], gate_id)
    if gate["kind"] != "gate" or gate_state["status"] != "awaiting-approval":
        raise ApiError("step-not-awaiting", "This step is not waiting for your review.", 409)
    target = _step(plan, gate["reviews"])
    target_state = _step_row(db, run["id"], target["id"])
    task = tasks.get_row(db, run["task_id"])
    inputs = _outputs_for(db, run["id"], plan, target["inputs"])
    prompt = plans.step_prompt(
        task, plan, target, inputs, revision=(target_state["output"] or "", feedback)
    )
    _start_step_session(db, run["id"], task, target, prompt, "revision")
    now = now_iso()
    db.execute(
        "UPDATE run_steps SET status = 'queued', attempts = attempts + 1, started_at = ?, "
        "finished_at = NULL WHERE run_id = ? AND step_id = ?",
        (now, run["id"], target["id"]),
    )
    db.execute(
        "UPDATE run_steps SET status = 'pending' WHERE run_id = ? AND step_id = ?",
        (run["id"], gate_id),
    )
    _set_run_status(db, run, "running")


# ---- helpers ---------------------------------------------------------------------------


def task_workdir(task: Row) -> Path:
    raw = task["repo_dir"]
    if not raw:
        raise ApiError(
            "workdir-missing", "Set the task's repository folder first.", field="repoDir"
        )
    path = Path(raw).expanduser()
    if not path.is_dir():
        raise ApiError("workdir-not-found", f"The folder {raw} does not exist.")
    return path


def all_profiles(db: Database) -> list[Row]:
    return db.all("SELECT * FROM profiles ORDER BY tool, name COLLATE NOCASE")


def profiles_by_id(db: Database) -> dict[str, Row]:
    return {p["id"]: p for p in all_profiles(db)}


def default_planner_profile(db: Database) -> str:
    configured = settings.get_planner(db)["profileId"]
    if configured and db.one("SELECT 1 FROM profiles WHERE id = ?", (configured,)):
        return configured
    row = db.one(
        "SELECT id FROM profiles ORDER BY CASE tool WHEN 'claude' THEN 0 ELSE 1 END, "
        "name COLLATE NOCASE LIMIT 1"
    )
    return str(row["id"]) if row else ""


def session_profile(db: Database, profile_id: str) -> dict[str, Any]:
    try:
        return profiles.serialize(profiles.get_row(db, profile_id))
    except ApiError as err:
        raise invalid("profileId", "is not an existing profile") from err


def _planned_run(db: Database, run_id: str) -> Row:
    run = db.one("SELECT * FROM runs WHERE id = ?", (run_id,))
    if run is None:
        raise not_found("run", run_id)
    if run["mode"] != "planned":
        raise ApiError("run-not-planned", "This run was not planned by AI.", 409)
    return run


def _step(plan: dict[str, Any], step_id: str) -> dict[str, Any]:
    for step in plan.get("steps", []):
        if step["id"] == step_id:
            return dict(step)
    raise not_found("step", step_id)


def _step_row(db: Database, run_id: str, step_id: str) -> Row:
    row = db.one("SELECT * FROM run_steps WHERE run_id = ? AND step_id = ?", (run_id, step_id))
    if row is None:
        raise not_found("step", step_id)
    return row


def _update_step(
    db: Database, states: dict[str, Row], run_id: str, step_id: str, **fields: object
) -> None:
    sets = ", ".join(f"{col} = ?" for col in fields)
    db.execute(
        f"UPDATE run_steps SET {sets} WHERE run_id = ? AND step_id = ?",  # noqa: S608
        (*fields.values(), run_id, step_id),
    )
    states[step_id] = {**states[step_id], **fields}


def _write_in_progress(steps: list[dict[str, Any]], states: dict[str, Row]) -> bool:
    return any(
        s.get("access") == "write" and states.get(s["id"], {}).get("status") in ACTIVE
        for s in steps
    )


def _latest_step_session(db: Database, run_id: str, step_id: str) -> Row | None:
    return db.one(
        "SELECT * FROM sessions WHERE run_id = ? AND step_id = ? "
        "ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (run_id, step_id),
    )


def _active_sessions(db: Database, run_id: str) -> int:
    row = db.one(
        "SELECT COUNT(*) AS n FROM sessions WHERE run_id = ? AND status IN ('queued', 'running')",
        (run_id,),
    )
    return int(row["n"]) if row else 0


def _outputs_for(
    db: Database, run_id: str, plan: dict[str, Any], step_ids: list[str]
) -> list[tuple[dict[str, Any], str]]:
    out: list[tuple[dict[str, Any], str]] = []
    for step_id in step_ids:
        state = db.one(
            "SELECT output FROM run_steps WHERE run_id = ? AND step_id = ?", (run_id, step_id)
        )
        out.append((_step(plan, step_id), (state["output"] if state else None) or ""))
    return out


def _start_step_session(
    db: Database, run_id: str, task: Row, step: dict[str, Any], prompt: str, purpose: str
) -> None:
    profile = session_profile(db, step["profileId"])
    _add_session(
        db,
        run_id,
        step["id"],
        purpose,
        step["title"],
        profile,
        prompt,
        task_workdir(task),
        isolated=step.get("access") == "isolated",
    )


def _add_session(
    db: Database,
    run_id: str,
    step_id: str | None,
    purpose: str,
    name: str,
    profile: dict[str, Any],
    prompt: str,
    workdir: Path,
    *,
    isolated: bool = False,
) -> None:
    db.execute(
        "INSERT INTO sessions (id, run_id, role_id, role_name, tool, profile_snapshot, prompt, "
        "workdir, status, created_at, step_id, purpose, isolated) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)",
        (
            new_id("sess"),
            run_id,
            step_id or purpose,
            name,
            profile["tool"],
            dumps(profile),
            prompt,
            str(workdir),
            now_iso(),
            step_id,
            purpose,
            int(isolated),
        ),
    )


def _set_run_status(db: Database, run: Row, status: str) -> None:
    current = db.one("SELECT status FROM runs WHERE id = ?", (run["id"],))
    if current is None or current["status"] == status:
        return
    finished = now_iso() if status in RUN_TERMINAL else None
    db.execute(
        "UPDATE runs SET status = ?, finished_at = ? WHERE id = ?", (status, finished, run["id"])
    )
    _set_task_status(db, run["task_id"], status)


def _set_task_status(db: Database, task_id: str, run_status: str) -> None:
    db.execute(
        "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ? AND status != 'done'",
        (TASK_STATUS[run_status], now_iso(), task_id),
    )
