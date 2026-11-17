from __future__ import annotations

from pathlib import Path
from typing import Any

from starlette.testclient import TestClient

from tests.conftest import wait_for


def make_profile(client: TestClient, **extra: Any) -> dict[str, Any]:
    body = {"name": "Claude impl", "tool": "claude", "model": "opus", **extra}
    res = client.post("/api/profiles", json=body)
    assert res.status_code == 201, res.text
    return dict(res.json())


def make_role(client: TestClient, profile_id: str, **extra: Any) -> dict[str, Any]:
    body = {
        "name": "Implementer",
        "profileId": profile_id,
        "instructions": "Implement it.",
        **extra,
    }
    res = client.post("/api/roles", json=body)
    assert res.status_code == 201, res.text
    return dict(res.json())


def make_task(client: TestClient, repo: Path, **extra: Any) -> dict[str, Any]:
    body = {"title": "Add notes", "description": "Write notes.txt", "repoDir": str(repo), **extra}
    res = client.post("/api/tasks", json=body)
    assert res.status_code == 201, res.text
    return dict(res.json())


def run_and_wait(client: TestClient, task_id: str, role_id: str) -> dict[str, Any]:
    res = client.post(f"/api/tasks/{task_id}/runs", json={"roleId": role_id})
    assert res.status_code == 201, res.text
    run_id = res.json()["id"]

    def finished() -> dict[str, Any] | None:
        run = client.get(f"/api/runs/{run_id}").json()
        return run if run["status"] in ("succeeded", "failed", "cancelled") else None

    return dict(wait_for(finished))


# ---- guard ---------------------------------------------------------------------------


def test_api_requires_header(client: TestClient) -> None:
    res = client.get("/api/tasks", headers={"X-Dash": ""})
    assert res.status_code == 403
    assert res.json()["code"] == "missing-header"


def test_foreign_host_is_rejected(client: TestClient) -> None:
    res = client.get("/api/tasks", headers={"Host": "evil.example:8765"})
    assert res.status_code == 403
    assert res.json()["code"] == "host-not-allowed"


def test_frontend_is_served(client: TestClient) -> None:
    res = client.get("/")
    assert res.status_code == 200
    assert '<div id="app">' in res.text
    js = client.get("/src/main.js")
    assert js.headers["content-type"].startswith("text/javascript")


def test_unknown_api_route_is_json_404(client: TestClient) -> None:
    res = client.get("/api/nope")
    assert res.status_code == 404
    assert res.json()["code"] == "not-found"


# ---- profiles & roles ------------------------------------------------------------------


def test_profile_validation(client: TestClient) -> None:
    res = client.post("/api/profiles", json={"name": "", "tool": "claude"})
    assert res.status_code == 400
    assert res.json() == {"code": "invalid-field", "message": "name is required.", "field": "name"}
    res = client.post(
        "/api/profiles",
        json={"name": "x", "tool": "codex", "permission": "allowedTools", "allowedTools": ["Edit"]},
    )
    assert res.json()["field"] == "permission"
    res = client.post("/api/profiles", json={"name": "x", "model": "opus; rm -rf /"})
    assert res.json()["field"] == "model"


def test_profile_crud_and_in_use(client: TestClient) -> None:
    profile = make_profile(client)
    assert profile["timeoutSeconds"] == 3600
    updated = client.put(
        f"/api/profiles/{profile['id']}", json={**profile, "name": "Renamed"}
    ).json()
    assert updated["name"] == "Renamed"
    role = make_role(client, profile["id"])
    res = client.delete(f"/api/profiles/{profile['id']}")
    assert res.status_code == 409 and res.json()["code"] == "profile-in-use"
    assert client.delete(f"/api/roles/{role['id']}").status_code == 204
    assert client.delete(f"/api/profiles/{profile['id']}").status_code == 204


def test_role_needs_existing_profile(client: TestClient) -> None:
    res = client.post("/api/roles", json={"name": "R", "profileId": "prof_missing"})
    assert res.status_code == 404 and res.json()["code"] == "profile-not-found"


def test_antigravity_profile_rules(client: TestClient) -> None:
    agy = make_profile(client, tool="antigravity", model="gemini-3-pro", effort="max")
    assert agy["tool"] == "antigravity" and agy["effort"] == "max"
    res = client.post(
        "/api/profiles",
        json={"name": "x", "tool": "antigravity", "permission": "allowedTools",
              "allowedTools": ["command(git)"]},
    )  # fmt: skip
    assert res.status_code == 400 and res.json()["field"] == "permission"
    assert "CLI's own settings" in res.json()["message"]
    res = client.post("/api/profiles", json={"name": "x", "tool": "codex", "effort": "max"})
    assert res.status_code == 400 and res.json()["field"] == "effort"


# ---- tasks ---------------------------------------------------------------------------------


def test_task_crud_and_move(client: TestClient, repo: Path) -> None:
    task = make_task(client, repo, criteria=["Has notes", " "], labels=["docs"])
    assert task["criteria"] == ["Has notes"]
    assert task["status"] == "backlog"
    moved = client.post(f"/api/tasks/{task['id']}/move", json={"status": "ready"}).json()
    assert moved["status"] == "ready"
    bad = client.post(f"/api/tasks/{task['id']}/move", json={"status": "nope"})
    assert bad.status_code == 400
    assert client.get("/api/tasks").json()[0]["runCount"] == 0
    assert client.delete(f"/api/tasks/{task['id']}").status_code == 204


# ---- runs ----------------------------------------------------------------------------------


def test_preview_shows_command_and_prompt(client: TestClient, repo: Path) -> None:
    role = make_role(client, make_profile(client)["id"])
    task = make_task(client, repo, criteria=["Has notes"])
    res = client.post(f"/api/tasks/{task['id']}/runs/preview", json={"roleId": role["id"]})
    assert res.status_code == 200, res.text
    session = res.json()["sessions"][0]
    assert "<prompt via stdin" in session["command"]
    assert "# Your role: Implementer" in session["prompt"]
    assert "- Has notes" in session["prompt"]


def test_run_without_workdir_is_rejected(client: TestClient) -> None:
    role = make_role(client, make_profile(client)["id"])
    task = client.post("/api/tasks", json={"title": "No folder"}).json()
    res = client.post(f"/api/tasks/{task['id']}/runs", json={"roleId": role["id"]})
    assert res.status_code == 400 and res.json()["code"] == "workdir-missing"


def test_claude_run_succeeds_and_records_events(client: TestClient, repo: Path) -> None:
    role = make_role(client, make_profile(client)["id"])
    task = make_task(client, repo)
    run = run_and_wait(client, task["id"], role["id"])
    assert run["status"] == "succeeded", run
    session = run["sessions"][0]
    assert session["cliSessionId"]
    assert session["resultText"] == "Done. DONE"
    assert session["usage"]["costUsd"] == 0.0123
    events = client.get(f"/api/sessions/{session['id']}/events").json()["events"]
    kinds = [e["kind"] for e in events]
    assert kinds[0] == "init" and kinds[-1] == "result"
    assert "command" in kinds and "file" in kinds
    later = client.get(f"/api/sessions/{session['id']}/events?after={events[-1]['id']}").json()
    assert later["events"] == []
    assert client.get(f"/api/tasks/{task['id']}").json()["status"] == "review"


def test_codex_run_succeeds(client: TestClient, repo: Path) -> None:
    role = make_role(client, make_profile(client, tool="codex", model="")["id"])
    run = run_and_wait(client, make_task(client, repo)["id"], role["id"])
    assert run["status"] == "succeeded", run
    assert run["sessions"][0]["usage"]["inputTokens"] == 150


def test_antigravity_run_succeeds(client: TestClient, repo: Path) -> None:
    role = make_role(client, make_profile(client, tool="antigravity", model="gemini-3-pro")["id"])
    task = make_task(client, repo)
    preview = client.post(f"/api/tasks/{task['id']}/runs/preview", json={"roleId": role["id"]})
    assert "--input-format stream-json" in preview.json()["sessions"][0]["command"]
    run = run_and_wait(client, task["id"], role["id"])
    assert run["status"] == "succeeded", run
    session = run["sessions"][0]
    assert session["tool"] == "antigravity"
    assert session["resultText"] == "Done. DONE"
    assert session["usage"]["thinkingTokens"] == 50
    events = client.get(f"/api/sessions/{session['id']}/events").json()["events"]
    kinds = [e["kind"] for e in events]
    assert kinds == ["init", "message", "command", "file", "message", "result"]
    assert events[1]["data"]["text"] == "Looking at: Add notes"


def test_antigravity_soft_deny_is_visible(client: TestClient, repo: Path) -> None:
    role = make_role(client, make_profile(client, tool="antigravity")["id"])
    task = make_task(client, repo, description="FAKE_DENY")
    run = run_and_wait(client, task["id"], role["id"])
    assert run["status"] == "succeeded"  # agy still exits 0; the warning is what tells you
    events = client.get(f"/api/sessions/{run['sessions'][0]['id']}/events").json()["events"]
    warnings = [e for e in events if e["kind"] == "warning"]
    assert warnings and "not approved" in warnings[0]["data"]["text"]


def test_antigravity_failure(client: TestClient, repo: Path) -> None:
    role = make_role(client, make_profile(client, tool="antigravity")["id"])
    run = run_and_wait(client, make_task(client, repo, description="FAKE_FAIL")["id"], role["id"])
    assert run["status"] == "failed"
    assert run["sessions"][0]["error"] == "Fake failure"


def test_failed_run_moves_task_to_needs_you(client: TestClient, repo: Path) -> None:
    role = make_role(client, make_profile(client)["id"])
    task = make_task(client, repo, description="FAKE_FAIL please")
    run = run_and_wait(client, task["id"], role["id"])
    assert run["status"] == "failed"
    assert run["sessions"][0]["error"] == "Fake failure"
    assert client.get(f"/api/tasks/{task['id']}").json()["status"] == "needs-you"


def test_cancel_running_session(client: TestClient, repo: Path) -> None:
    role = make_role(client, make_profile(client)["id"])
    task = make_task(client, repo, description="FAKE_SLOW")
    run = client.post(f"/api/tasks/{task['id']}/runs", json={"roleId": role["id"]}).json()
    session_id = run["sessions"][0]["id"]
    wait_for(lambda: client.get(f"/api/sessions/{session_id}/events").json()["events"])
    client.post(f"/api/runs/{run['id']}/cancel")
    final = wait_for(
        lambda: (r := client.get(f"/api/runs/{run['id']}").json())["status"] == "cancelled" and r
    )
    assert final["sessions"][0]["status"] == "cancelled"
    assert client.get(f"/api/tasks/{task['id']}").json()["status"] == "ready"
    assert client.delete(f"/api/runs/{run['id']}").status_code == 204


def test_concurrency_limit_queues_extra_sessions(client: TestClient, repo: Path) -> None:
    client.put("/api/settings", json={"maxConcurrent": {"claude": 1, "codex": 1}})
    role = make_role(client, make_profile(client)["id"])
    slow = make_task(client, repo, description="FAKE_SLOW")
    second = make_task(client, repo, title="Second")
    first_run = client.post(f"/api/tasks/{slow['id']}/runs", json={"roleId": role["id"]}).json()
    wait_for(lambda: client.get(f"/api/runs/{first_run['id']}").json()["status"] == "running")
    second_run = client.post(f"/api/tasks/{second['id']}/runs", json={"roleId": role["id"]}).json()
    active = client.get("/api/sessions?active=1").json()
    assert sorted(s["status"] for s in active) == ["queued", "running"]
    client.post(f"/api/runs/{first_run['id']}/cancel")
    done = wait_for(
        lambda: (r := client.get(f"/api/runs/{second_run['id']}").json())["status"] == "succeeded"
        and r
    )
    assert done["sessions"][0]["status"] == "succeeded"


def test_timeout_kills_session(client: TestClient, repo: Path) -> None:
    profile = make_profile(client)
    role = make_role(client, profile["id"])
    db = client.app.state.ctx.db  # type: ignore[attr-defined]
    db.execute("UPDATE profiles SET timeout_s = 1 WHERE id = ?", (profile["id"],))
    task = make_task(client, repo, description="FAKE_SLOW")
    run = run_and_wait(client, task["id"], role["id"])
    assert run["status"] == "failed"
    assert run["sessions"][0]["error"].startswith("Timed out after 1 s")


def test_settings_validation(client: TestClient) -> None:
    limits = client.get("/api/settings").json()["maxConcurrent"]
    assert limits == {"claude": 2, "codex": 2, "antigravity": 2}
    res = client.put("/api/settings", json={"maxConcurrent": {"claude": 0}})
    assert res.status_code == 400 and res.json()["field"] == "claude"


def test_environment_in_demo_mode(client: TestClient) -> None:
    env = client.get("/api/environment").json()
    assert env["demoMode"] is True
    claude = next(t for t in env["tools"] if t["name"] == "claude")
    assert claude["found"] and claude["version"] == "claude 0.0.0-fake"
    agy = next(t for t in env["tools"] if t["name"] == "antigravity")
    assert agy["command"] == "agy" and agy["found"]
    assert [t["name"] for t in env["tools"]] == ["claude", "codex", "antigravity", "git", "gh"]
