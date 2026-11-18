from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from starlette.testclient import TestClient

from app.runner.parse import StreamParser
from app.services import submissions
from tests.conftest import git
from tests.test_api import make_profile, make_role, make_task, run_and_wait


def block(payload: object) -> str:
    body = payload if isinstance(payload, str) else json.dumps(payload)
    return f"Done.\n\n```submission\n{body}\n```\n"


def one(payload: object) -> submissions.Parsed:
    found = submissions.extract([block(payload)])
    assert len(found) == 1
    return found[0]


# ---- parsing & validation -------------------------------------------------------------


def test_valid_test_result_is_normalised() -> None:
    parsed = one(
        {
            "type": "test-result",
            "status": "FAIL",
            "suite": " adblock ",
            "summary": "1 missed",
            "confusion": {"TP": 8, "fp": 1, "tn": 5, "fn": 2},
            "counts": {"passed": 13, "failed": 3},
        }
    )
    assert parsed.error is None
    assert parsed.type == "test-result"
    assert parsed.payload["status"] == "fail"
    assert parsed.payload["suite"] == "adblock"
    assert parsed.payload["confusion"] == {"tp": 8, "fp": 1, "tn": 5, "fn": 2}
    assert parsed.payload["counts"] == {"passed": 13, "failed": 3, "skipped": 0}


def test_confusion_and_counts_are_derived_from_cases() -> None:
    cases = [
        {"name": "a", "outcome": "tp"},
        {"name": "b", "outcome": "TP"},
        {"name": "c", "outcome": "fn", "expected": "detected", "actual": "missed"},
        {"name": "d", "outcome": "pass"},
        {"name": "e", "outcome": "skip", "note": 42},
    ]
    parsed = one({"type": "test-result", "status": "fail", "cases": cases})
    assert parsed.error is None
    assert parsed.payload["confusion"] == {"tp": 2, "fp": 0, "tn": 0, "fn": 1}
    assert parsed.payload["counts"] == {"passed": 1, "failed": 0, "skipped": 1}
    assert parsed.payload["cases"][2]["actual"] == "missed"
    assert parsed.payload["cases"][4]["note"] == "42"


def test_metrics() -> None:
    m = submissions.metrics({"confusion": {"tp": 8, "fp": 2, "tn": 6, "fn": 4}})
    assert m == {
        "precision": 0.8,
        "recall": 0.6667,
        "f1": 0.7273,
        "accuracy": 0.7,
        "fpr": 0.25,
    }
    empty = submissions.metrics({"confusion": {"tp": 0, "fp": 0, "tn": 0, "fn": 0}})
    assert empty["precision"] is None
    assert submissions.metrics({"status": "pass"}) == {}


def test_invalid_blocks_are_kept_with_the_reason() -> None:
    bad_json = one('{"type": "test-result", status: pass}')
    assert bad_json.type == "test-result"  # still listed in the Tests table
    assert bad_json.error is not None and "not valid JSON" in bad_json.error
    assert bad_json.payload["raw"].startswith('{"type"')
    assert one("{oops").type == "unknown"
    assert one('["type", "x"]').error == "a submission must be a JSON object"

    unknown = one({"type": "progress", "percent": 10})
    assert unknown.type == "progress"
    assert unknown.error is not None and "not supported" in unknown.error

    wrong = one(
        {
            "type": "test-result",
            "status": "green",
            "confusion": {"tp": -1, "xx": 1},
            "cases": [{"outcome": "tp"}, {"name": "x", "outcome": "maybe"}],
        }
    )
    assert wrong.error is not None
    for part in ("'status'", "confusion.tp", "unknown key 'xx'", "'name' is required", "case 2"):
        assert part in wrong.error


def test_extract_finds_every_block_once() -> None:
    a = block({"type": "test-result", "status": "pass"})
    b = block({"type": "test-result", "status": "fail"})
    found = submissions.extract([a + b, b, "no blocks here", "```json\n{}\n```"])
    assert [f.payload["status"] for f in found] == ["pass", "fail"]


def test_parser_keeps_long_submission_messages_unclipped() -> None:
    cases = [{"name": f"case {n}", "outcome": "tp"} for n in range(400)]
    text = block({"type": "test-result", "status": "pass", "cases": cases})
    assert len(text) > 8000
    parser = StreamParser("claude")
    line = json.dumps(
        {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}
    )
    events = parser.feed(line)
    assert len(events[0].data["text"]) < len(text)  # the visible event is clipped
    assert parser.submission_texts == [text]
    assert len(submissions.extract(parser.submission_texts)[0].payload["cases"]) == 400


# ---- end to end ------------------------------------------------------------------------


def make_tester(client: TestClient, tool: str = "claude") -> dict[str, Any]:
    profile = make_profile(client, name=f"{tool} tester", tool=tool)
    return make_role(client, profile["id"], name="Tester", instructions="Run the tests.")


def test_session_submission_shows_up_with_metrics_and_commit(
    client: TestClient, git_repo: Path
) -> None:
    role = make_tester(client)
    task = make_task(client, git_repo, title="Run tests", description="FAKE_TEST_RESULT")
    run = run_and_wait(client, task["id"], role["id"])
    assert run["status"] == "succeeded"

    res = client.get("/api/submissions", params={"type": "test-result"})
    assert res.status_code == 200
    [item] = res.json()
    assert item["valid"] is True
    assert item["taskTitle"] == "Run tests"
    assert item["roleName"] == "Tester"
    assert item["runId"] == run["id"]
    assert item["payload"]["confusion"] == {"tp": 2, "fp": 1, "tn": 1, "fn": 1}
    assert item["payload"]["counts"] == {"passed": 3, "failed": 2, "skipped": 0}
    assert item["metrics"]["precision"] == 0.6667
    assert item["metrics"]["recall"] == 0.6667
    head = git(git_repo, "rev-parse", "--short", "HEAD").strip()
    assert item["commit"] == f"{head}-dirty"  # the fixture leaves uncommitted changes


def test_codex_and_antigravity_submissions_are_recorded(client: TestClient, repo: Path) -> None:
    for tool in ("codex", "antigravity"):
        role = make_tester(client, tool)
        task = make_task(client, repo, title=f"{tool} tests", description="FAKE_TEST_RESULT")
        run_and_wait(client, task["id"], role["id"])
        [item] = client.get("/api/submissions", params={"taskId": task["id"]}).json()
        assert item["tool"] == tool
        assert item["valid"] is True
        assert item["commit"] is None  # not a git repository


def test_invalid_submission_is_listed_with_its_error(client: TestClient, repo: Path) -> None:
    role = make_tester(client)
    task = make_task(client, repo, description="FAKE_SUBMISSION_BAD")
    run_and_wait(client, task["id"], role["id"])
    [item] = client.get("/api/submissions", params={"type": "test-result"}).json()
    assert item["valid"] is False
    assert "not valid JSON" in item["error"]
    assert item["metrics"] == {}
    assert "status: pass" in item["payload"]["raw"]


def test_sessions_without_blocks_record_nothing(client: TestClient, repo: Path) -> None:
    role = make_tester(client)
    task = make_task(client, repo)
    run_and_wait(client, task["id"], role["id"])
    assert client.get("/api/submissions").json() == []


def test_filter_delete_and_cascade(client: TestClient, repo: Path) -> None:
    role = make_tester(client)
    first = make_task(client, repo, title="First", description="FAKE_TEST_RESULT")
    second = make_task(client, repo, title="Second", description="FAKE_TEST_RESULT")
    run_and_wait(client, first["id"], role["id"])
    run = run_and_wait(client, second["id"], role["id"])

    assert len(client.get("/api/submissions").json()) == 2
    [only] = client.get("/api/submissions", params={"taskId": first["id"]}).json()
    assert only["taskTitle"] == "First"
    assert client.get("/api/submissions", params={"type": "progress"}).json() == []

    assert client.delete(f"/api/submissions/{only['id']}").status_code == 204
    missing = client.delete(f"/api/submissions/{only['id']}")
    assert missing.status_code == 404
    assert missing.json()["code"] == "submission-not-found"

    assert client.delete(f"/api/runs/{run['id']}").status_code == 204
    assert client.get("/api/submissions").json() == []
