"""End-to-end AI-planned runs through the API, with the fake CLI as every agent."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from starlette.testclient import TestClient

from tests.conftest import git, wait_for


def setup_team(client: TestClient) -> dict[str, str]:
    ids = {}
    for tool, name in (("claude", "Claude"), ("codex", "Codex"), ("antigravity", "Antigravity")):
        res = client.post("/api/profiles", json={"name": name, "tool": tool})
        assert res.status_code == 201, res.text
        ids[tool] = res.json()["id"]
    return ids


def new_task(client: TestClient, repo: Path, **extra: Any) -> dict[str, Any]:
    body = {
        "title": "adblock.js detection",
        "description": "Detect adblock groups.",
        "repoDir": str(repo),
        **extra,
    }
    res = client.post("/api/tasks", json=body)
    assert res.status_code == 201, res.text
    return dict(res.json())


def get_run(client: TestClient, run_id: str) -> dict[str, Any]:
    return dict(client.get(f"/api/runs/{run_id}").json())


def wait_status(client: TestClient, run_id: str, *statuses: str) -> dict[str, Any]:
    def check() -> dict[str, Any] | None:
        run = get_run(client, run_id)
        return run if run["status"] in statuses else None

    result: dict[str, Any] = wait_for(check)
    return result


def step_session(run: dict[str, Any], step_id: str) -> dict[str, Any]:
    sessions: list[dict[str, Any]] = [s for s in run["sessions"] if s["stepId"] == step_id]
    return sessions[-1]


def test_plan_feedback_approve_gate_and_finish(client: TestClient, repo: Path) -> None:
    ids = setup_team(client)
    task = new_task(client, repo)

    res = client.post(f"/api/tasks/{task['id']}/plans", json={"notes": "Prefer small files."})
    assert res.status_code == 201, res.text
    run = res.json()
    assert run["mode"] == "planned" and run["stage"] == "plan"
    planner = run["sessions"][0]
    assert planner["purpose"] == "planner" and planner["tool"] == "claude"  # Claude by default
    assert "Prefer small files." in planner["prompt"]
    assert f"`{ids['codex']}` · codex" in planner["prompt"]

    # 1. The planner's proposal waits for the owner.
    run = wait_status(client, run["id"], "awaiting-approval")
    assert run["planVersion"] == 1 and run["error"] is None
    # Not a git repository: no isolated steps, a single write step implements.
    assert "`access: isolated` is not available" in planner["prompt"]
    assert "Codex 2" in planner["prompt"]  # the planner knows the session limits
    assert [s["id"] for s in run["plan"]["steps"]] == [
        "analyze-group-1",
        "analyze-group-2",
        "analyze-group-3",
        "research-methods",
        "final-plan",
        "approve-plan",
        "merge",
        "test-review",
    ]
    assert client.get(f"/api/tasks/{task['id']}").json()["status"] == "needs-you"

    # 2. Feedback goes back to the planner, which produces version 2.
    res = client.post(
        f"/api/runs/{run['id']}/feedback", json={"target": "plan", "text": "Add a step for Firefox"}
    )
    assert res.status_code == 200, res.text
    run = wait_status(client, run["id"], "awaiting-approval")
    assert run["planVersion"] == 2
    assert "Add a step for Firefox" in run["plan"]["summary"]
    assert [v["version"] for v in run["planVersions"]] == [1, 2]
    assert run["feedback"][0]["target"] == "plan"

    # 3. Approve: steps run in dependency order until the gate.
    assert client.post(f"/api/runs/{run['id']}/approve").status_code == 200
    run = wait_status(client, run["id"], "awaiting-approval")
    assert run["stage"] == "execute"
    assert run["steps"]["approve-plan"]["status"] == "awaiting-approval"
    assert run["steps"]["merge"]["status"] == "pending"
    research = step_session(run, "research-methods")
    assert research["tool"] == "antigravity"
    for n in (1, 2, 3):
        assert f"## Input from 'Analyse extension group {n}' (Codex)" in research["prompt"]
    final = step_session(run, "final-plan")
    assert final["tool"] == "claude"
    assert "Split into methods and recommend approaches." in run["steps"]["final-plan"]["output"]

    # 4. Fix instructions at the gate revise the reviewed step, then ask again.
    res = client.post(
        f"/api/runs/{run['id']}/feedback",
        json={"target": "approve-plan", "text": "Use MutationObserver"},
    )
    assert res.status_code == 200, res.text
    run = wait_status(client, run["id"], "awaiting-approval")
    assert run["steps"]["final-plan"]["attempts"] == 2
    assert "applying: Use MutationObserver" in run["steps"]["final-plan"]["output"]
    assert step_session(run, "final-plan")["purpose"] == "revision"

    # 5. Approve the gate: implementation, tests and review run to the end.
    res = client.post(f"/api/runs/{run['id']}/steps/approve-plan/approve")
    assert res.status_code == 200, res.text
    run = wait_status(client, run["id"], "succeeded", "failed")
    assert run["status"] == "succeeded", run
    assert all(s["status"] == "succeeded" for s in run["steps"].values())
    implement = step_session(run, "merge")
    assert "applying: Use MutationObserver" in implement["prompt"]  # approved output flows on
    assert "You may modify files" in implement["prompt"]
    assert (repo / "implement-the-plan.txt").exists()  # the write step changed the folder
    assert client.get(f"/api/tasks/{task['id']}").json()["status"] == "review"


def test_parallel_analysis_and_isolated_coding_then_merge(
    client: TestClient, git_repo: Path
) -> None:
    client.put("/api/settings", json={"maxConcurrent": {"codex": 3}})
    setup_team(client)
    task = new_task(client, git_repo)
    run = client.post(f"/api/tasks/{task['id']}/plans", json={}).json()
    assert "The task folder is a git repository." in run["sessions"][0]["prompt"]
    assert "Codex 3" in run["sessions"][0]["prompt"]
    run = wait_status(client, run["id"], "awaiting-approval")
    steps = {s["id"]: s for s in run["plan"]["steps"]}
    assert steps["code-part-1"]["access"] == steps["code-part-2"]["access"] == "isolated"
    assert steps["merge"]["inputs"] == ["code-part-1", "code-part-2", "approve-plan"]

    client.post(f"/api/runs/{run['id']}/approve")
    run = wait_status(client, run["id"], "awaiting-approval")
    # The three analyses were all started before any of them finished: a parallel fan-out.
    analyses = [step_session(run, f"analyze-group-{n}") for n in (1, 2, 3)]
    assert max(s["createdAt"] for s in analyses) <= min(s["finishedAt"] for s in analyses)

    client.post(f"/api/runs/{run['id']}/steps/approve-plan/approve")
    run = wait_status(client, run["id"], "succeeded", "failed")
    assert run["status"] == "succeeded", run

    parts = [step_session(run, f"code-part-{n}") for n in (1, 2)]
    for n, part in enumerate(parts, start=1):
        assert part["isolated"] is True
        assert "your own isolated copy" in part["prompt"]
        patch = Path(part["patchPath"])
        assert patch.is_file()
        assert f"+written for 'Implement part {n}'" in patch.read_text(encoding="utf-8")
        output = run["steps"][f"code-part-{n}"]["output"]
        assert "## Changes made in the isolated copy" in output and str(patch) in output
        # Parts never touched the real folder.
        assert not (git_repo / f"implement-part-{n}.txt").exists()

    # Both parts ran at the same time (isolated steps do not wait for each other).
    assert max(p["startedAt"] for p in parts) < min(p["finishedAt"] for p in parts)
    merge = step_session(run, "merge")
    assert merge["isolated"] is False
    assert "Integrate them into the repository" in merge["prompt"]
    assert all(p["patchPath"] in merge["prompt"] for p in parts)
    assert (git_repo / "merge-the-parts-into-one-codebase.txt").exists()

    # Worktrees are cleaned up; the user's branch and files are untouched otherwise.
    assert git(git_repo, "worktree", "list").count("\n") == 1
    assert (git_repo / "app.js").read_text(encoding="utf-8") == "console.log('v2');\n"


def test_isolated_steps_need_git(client: TestClient, repo: Path) -> None:
    ids = setup_team(client)
    task = new_task(client, repo)
    run = client.post(f"/api/tasks/{task['id']}/plans", json={}).json()
    run = wait_status(client, run["id"], "awaiting-approval")
    plan = {
        "summary": "Isolated without git.",
        "steps": [
            {
                "id": "part",
                "title": "Part",
                "kind": "agent",
                "profileId": ids["codex"],
                "access": "isolated",
                "inputs": [],
                "instructions": "Write it.",
            }
        ],
    }
    db = client.app.state.ctx.db  # type: ignore[attr-defined]
    db.execute("UPDATE runs SET plan = ? WHERE id = ?", (json.dumps(plan), run["id"]))
    res = client.post(f"/api/runs/{run['id']}/approve")
    assert res.status_code == 409 and res.json()["code"] == "isolation-needs-git"


def test_unusable_plan_can_be_retried_with_feedback(client: TestClient, repo: Path) -> None:
    setup_team(client)
    task = new_task(client, repo, description="FAKE_PLAN_BAD")
    run = client.post(f"/api/tasks/{task['id']}/plans", json={}).json()
    run = wait_status(client, run["id"], "awaiting-approval")
    assert run["plan"] is None
    assert "could not be used" in run["error"]
    res = client.post(f"/api/runs/{run['id']}/approve")
    assert res.status_code == 409 and res.json()["code"] == "plan-missing"
    # The retry prompt tells the planner what went wrong.
    client.post(f"/api/runs/{run['id']}/feedback", json={"target": "plan", "text": "Try again"})
    run = wait_status(client, run["id"], "awaiting-approval")
    assert "The previous answer could not be used" in run["sessions"][-1]["prompt"]


def test_write_steps_never_overlap(client: TestClient, repo: Path) -> None:
    ids = setup_team(client)
    task = new_task(client, repo)
    run = client.post(f"/api/tasks/{task['id']}/plans", json={}).json()
    run = wait_status(client, run["id"], "awaiting-approval")
    # Replace the proposal with two independent, slow write steps.
    plan = {
        "summary": "Two writers.",
        "steps": [
            {
                "id": "w1",
                "title": "Writer one",
                "kind": "agent",
                "profileId": ids["codex"],
                "access": "write",
                "inputs": [],
                "instructions": "FAKE_SLOW",
            },
            {
                "id": "w2",
                "title": "Writer two",
                "kind": "agent",
                "profileId": ids["claude"],
                "access": "write",
                "inputs": [],
                "instructions": "FAKE_SLOW",
            },
        ],
    }
    db = client.app.state.ctx.db  # type: ignore[attr-defined]
    db.execute("UPDATE runs SET plan = ? WHERE id = ?", (json.dumps(plan), run["id"]))
    client.post(f"/api/runs/{run['id']}/approve")
    run = wait_status(client, run["id"], "running")
    assert sorted(s["status"] for s in run["steps"].values()) == ["pending", "queued"] or sorted(
        s["status"] for s in run["steps"].values()
    ) == ["pending", "running"]
    # Cancelling stops everything and frees the task.
    client.post(f"/api/runs/{run['id']}/cancel")
    run = wait_status(client, run["id"], "cancelled")
    assert {s["status"] for s in run["steps"].values()} == {"cancelled"}
    assert client.get(f"/api/tasks/{task['id']}").json()["status"] == "ready"


def test_failed_step_can_be_retried(client: TestClient, repo: Path) -> None:
    ids = setup_team(client)
    task = new_task(client, repo)
    run = client.post(f"/api/tasks/{task['id']}/plans", json={}).json()
    run = wait_status(client, run["id"], "awaiting-approval")
    plan = {
        "summary": "One step that fails.",
        "steps": [
            {
                "id": "boom",
                "title": "Boom",
                "kind": "agent",
                "profileId": ids["codex"],
                "inputs": [],
                "instructions": "FAKE_FAIL",
            },
            {
                "id": "after",
                "title": "After",
                "kind": "agent",
                "profileId": ids["claude"],
                "inputs": ["boom"],
                "instructions": "Report.",
            },
        ],
    }
    db = client.app.state.ctx.db  # type: ignore[attr-defined]
    db.execute("UPDATE runs SET plan = ? WHERE id = ?", (json.dumps(plan), run["id"]))
    client.post(f"/api/runs/{run['id']}/approve")
    run = wait_status(client, run["id"], "failed")
    assert run["steps"]["boom"]["status"] == "failed"
    assert run["steps"]["boom"]["error"] == "Fake failure"
    assert run["steps"]["after"]["status"] == "pending"  # never started
    assert client.get(f"/api/tasks/{task['id']}").json()["status"] == "needs-you"

    res = client.post(f"/api/runs/{run['id']}/steps/after/retry")
    assert res.status_code == 409 and res.json()["code"] == "step-not-retryable"
    assert client.post(f"/api/runs/{run['id']}/steps/boom/retry").status_code == 200
    run = wait_status(client, run["id"], "failed")
    assert run["steps"]["boom"]["attempts"] == 2


def test_planning_needs_a_folder_and_a_profile(client: TestClient, repo: Path) -> None:
    task = new_task(client, repo)
    res = client.post(f"/api/tasks/{task['id']}/plans", json={})
    assert res.status_code == 400 and res.json()["code"] == "planner-profile-missing"
    setup_team(client)
    no_folder = client.post("/api/tasks", json={"title": "x"}).json()
    res = client.post(f"/api/tasks/{no_folder['id']}/plans", json={})
    assert res.status_code == 400 and res.json()["code"] == "workdir-missing"


def test_planner_settings(client: TestClient) -> None:
    ids = setup_team(client)
    settings = client.get("/api/settings").json()
    assert "Codex is strong at reading and writing code" in settings["planner"]["guidance"]
    res = client.put("/api/settings", json={"planner": {"profileId": "prof_nope"}})
    assert res.status_code == 400 and res.json()["field"] == "profileId"
    res = client.put(
        "/api/settings", json={"planner": {"profileId": ids["codex"], "guidance": "Be brief."}}
    )
    assert res.json()["planner"] == {"profileId": ids["codex"], "guidance": "Be brief."}
    assert res.json()["maxConcurrent"]["claude"] == 2  # untouched
