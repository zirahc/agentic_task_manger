from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from ..config import Config
from ..db import Database
from ..errors import ApiError
from ..services import (
    environment,
    orchestrator,
    profiles,
    roles,
    runs,
    settings,
    submissions,
    tasks,
)
from ..services.supervisor import Supervisor


@dataclass
class Context:
    config: Config
    db: Database
    supervisor: Supervisor


def ctx(request: Request) -> Context:
    context: Context = request.app.state.ctx
    return context


async def body(request: Request) -> object:
    raw = await request.body()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as err:
        raise ApiError("invalid-json", "Request body is not valid JSON.") from err


def ok(data: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status)


def no_content() -> Response:
    return Response(status_code=204)


def param(request: Request, name: str) -> str:
    return str(request.path_params[name])


# ---- environment & settings ---------------------------------------------------------


async def get_environment(request: Request) -> Response:
    fake = ctx(request).config.fake_cli
    return ok(await run_in_threadpool(environment.environment, fake=fake))


async def get_settings(request: Request) -> Response:
    return ok(settings.get_settings(ctx(request).db))


async def put_settings(request: Request) -> Response:
    c = ctx(request)
    result = settings.update_settings(c.db, await body(request))
    c.supervisor.wake()
    return ok(result)


# ---- profiles -----------------------------------------------------------------------


async def list_profiles(request: Request) -> Response:
    return ok(profiles.list_profiles(ctx(request).db))


async def create_profile(request: Request) -> Response:
    return ok(profiles.create_profile(ctx(request).db, await body(request)), 201)


async def update_profile(request: Request) -> Response:
    return ok(profiles.update_profile(ctx(request).db, param(request, "id"), await body(request)))


async def delete_profile(request: Request) -> Response:
    profiles.delete_profile(ctx(request).db, param(request, "id"))
    return no_content()


# ---- roles --------------------------------------------------------------------------


async def list_roles(request: Request) -> Response:
    return ok(roles.list_roles(ctx(request).db))


async def create_role(request: Request) -> Response:
    return ok(roles.create_role(ctx(request).db, await body(request)), 201)


async def update_role(request: Request) -> Response:
    return ok(roles.update_role(ctx(request).db, param(request, "id"), await body(request)))


async def delete_role(request: Request) -> Response:
    roles.delete_role(ctx(request).db, param(request, "id"))
    return no_content()


# ---- tasks --------------------------------------------------------------------------


async def list_tasks(request: Request) -> Response:
    return ok(tasks.list_tasks(ctx(request).db))


async def create_task(request: Request) -> Response:
    return ok(tasks.create_task(ctx(request).db, await body(request)), 201)


async def get_task(request: Request) -> Response:
    return ok(tasks.get_task(ctx(request).db, param(request, "id")))


async def update_task(request: Request) -> Response:
    return ok(tasks.update_task(ctx(request).db, param(request, "id"), await body(request)))


async def move_task(request: Request) -> Response:
    return ok(tasks.move_task(ctx(request).db, param(request, "id"), await body(request)))


async def delete_task(request: Request) -> Response:
    tasks.delete_task(ctx(request).db, param(request, "id"))
    return no_content()


async def preview_run(request: Request) -> Response:
    c = ctx(request)
    data = await body(request)
    result = await run_in_threadpool(
        runs.preview, c.db, param(request, "id"), data, fake=c.config.fake_cli
    )
    return ok(result)


async def start_run(request: Request) -> Response:
    c = ctx(request)
    data = await body(request)
    result = await run_in_threadpool(
        runs.start_run, c.db, param(request, "id"), data, fake=c.config.fake_cli
    )
    c.supervisor.wake()
    return ok(result, 201)


# ---- runs & sessions ----------------------------------------------------------------


async def list_runs(request: Request) -> Response:
    task_id = request.query_params.get("taskId") or None
    return ok(runs.list_runs(ctx(request).db, task_id))


async def get_run(request: Request) -> Response:
    return ok(runs.get_run(ctx(request).db, param(request, "id")))


async def cancel_run(request: Request) -> Response:
    c = ctx(request)
    run_id = param(request, "id")
    runs.get_run(c.db, run_id)
    await run_in_threadpool(c.supervisor.cancel_run, run_id)
    orchestrator.cancel(c.db, run_id)
    return ok(runs.get_run(c.db, run_id))


# ---- AI-planned runs ------------------------------------------------------------------


async def start_planning(request: Request) -> Response:
    c = ctx(request)
    data = await body(request)
    result = await run_in_threadpool(
        orchestrator.start_planning, c.db, param(request, "id"), data, fake=c.config.fake_cli
    )
    c.supervisor.wake()
    return ok(result, 201)


async def approve_plan(request: Request) -> Response:
    c = ctx(request)
    run_id = param(request, "id")
    await run_in_threadpool(orchestrator.approve_plan, c.db, run_id)
    c.supervisor.wake()
    return ok(runs.get_run(c.db, run_id))


async def send_feedback(request: Request) -> Response:
    c = ctx(request)
    run_id = param(request, "id")
    data = await body(request)
    await run_in_threadpool(orchestrator.send_feedback, c.db, run_id, data)
    c.supervisor.wake()
    return ok(runs.get_run(c.db, run_id))


async def approve_step(request: Request) -> Response:
    c = ctx(request)
    run_id = param(request, "id")
    await run_in_threadpool(orchestrator.approve_step, c.db, run_id, param(request, "step"))
    c.supervisor.wake()
    return ok(runs.get_run(c.db, run_id))


async def retry_step(request: Request) -> Response:
    c = ctx(request)
    run_id = param(request, "id")
    await run_in_threadpool(orchestrator.retry_step, c.db, run_id, param(request, "step"))
    c.supervisor.wake()
    return ok(runs.get_run(c.db, run_id))


async def delete_run(request: Request) -> Response:
    runs.delete_run(ctx(request).db, param(request, "id"))
    return no_content()


async def list_sessions(request: Request) -> Response:
    active = request.query_params.get("active") == "1"
    return ok(runs.list_sessions(ctx(request).db, active_only=active))


async def session_events(request: Request) -> Response:
    try:
        after = int(request.query_params.get("after", "0"))
    except ValueError as err:
        raise ApiError("invalid-field", "after must be a whole number.", field="after") from err
    return ok(runs.list_events(ctx(request).db, param(request, "id"), after))


async def cancel_session(request: Request) -> Response:
    c = ctx(request)
    session_id = param(request, "id")
    await run_in_threadpool(c.supervisor.cancel_session, session_id)
    return ok(runs.serialize_session(runs.get_session_row(c.db, session_id)))


# ---- submissions -------------------------------------------------------------------


async def list_submissions(request: Request) -> Response:
    query = request.query_params
    return ok(
        submissions.list_submissions(
            ctx(request).db,
            kind=(query.get("type") or "")[:40],
            task_id=(query.get("taskId") or "")[:40],
        )
    )


async def delete_submission(request: Request) -> Response:
    submissions.delete_submission(ctx(request).db, param(request, "id"))
    return no_content()


ROUTES = [
    Route("/api/environment", get_environment, methods=["GET"]),
    Route("/api/settings", get_settings, methods=["GET"]),
    Route("/api/settings", put_settings, methods=["PUT"]),
    Route("/api/profiles", list_profiles, methods=["GET"]),
    Route("/api/profiles", create_profile, methods=["POST"]),
    Route("/api/profiles/{id}", update_profile, methods=["PUT"]),
    Route("/api/profiles/{id}", delete_profile, methods=["DELETE"]),
    Route("/api/roles", list_roles, methods=["GET"]),
    Route("/api/roles", create_role, methods=["POST"]),
    Route("/api/roles/{id}", update_role, methods=["PUT"]),
    Route("/api/roles/{id}", delete_role, methods=["DELETE"]),
    Route("/api/tasks", list_tasks, methods=["GET"]),
    Route("/api/tasks", create_task, methods=["POST"]),
    Route("/api/tasks/{id}", get_task, methods=["GET"]),
    Route("/api/tasks/{id}", update_task, methods=["PUT"]),
    Route("/api/tasks/{id}", delete_task, methods=["DELETE"]),
    Route("/api/tasks/{id}/move", move_task, methods=["POST"]),
    Route("/api/tasks/{id}/runs/preview", preview_run, methods=["POST"]),
    Route("/api/tasks/{id}/runs", start_run, methods=["POST"]),
    Route("/api/tasks/{id}/plans", start_planning, methods=["POST"]),
    Route("/api/runs", list_runs, methods=["GET"]),
    Route("/api/runs/{id}", get_run, methods=["GET"]),
    Route("/api/runs/{id}", delete_run, methods=["DELETE"]),
    Route("/api/runs/{id}/cancel", cancel_run, methods=["POST"]),
    Route("/api/runs/{id}/approve", approve_plan, methods=["POST"]),
    Route("/api/runs/{id}/feedback", send_feedback, methods=["POST"]),
    Route("/api/runs/{id}/steps/{step}/approve", approve_step, methods=["POST"]),
    Route("/api/runs/{id}/steps/{step}/retry", retry_step, methods=["POST"]),
    Route("/api/sessions", list_sessions, methods=["GET"]),
    Route("/api/sessions/{id}/events", session_events, methods=["GET"]),
    Route("/api/sessions/{id}/cancel", cancel_session, methods=["POST"]),
    Route("/api/submissions", list_submissions, methods=["GET"]),
    Route("/api/submissions/{id}", delete_submission, methods=["DELETE"]),
]
