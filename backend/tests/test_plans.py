from __future__ import annotations

import json
from typing import Any

import pytest

from app.services.plans import PlanError, extract_json, parse_plan, step_prompt, validate_plan

PROFILES: dict[str, Any] = {
    "prof_aaa": {"id": "prof_aaa", "name": "Codex", "tool": "codex"},
    "prof_bbb": {"id": "prof_bbb", "name": "Claude", "tool": "claude"},
}


def agent(step_id: str, inputs: list[str] | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "id": step_id,
        "title": step_id.title(),
        "kind": "agent",
        "profileId": "prof_aaa",
        "inputs": inputs or [],
        "instructions": "Do it.",
        **extra,
    }


def plan(*steps: dict[str, Any]) -> dict[str, Any]:
    return {"summary": "A plan.", "steps": list(steps)}


def problems(raw: object) -> str:
    with pytest.raises(PlanError) as err:
        validate_plan(raw, PROFILES)
    return str(err.value)


def test_valid_plan_is_normalized() -> None:
    gate = {"id": "ok", "title": "Approve", "kind": "gate", "inputs": ["a"]}
    result = validate_plan(plan(agent("a"), gate, agent("b", ["ok"], access="write")), PROFILES)
    steps = {s["id"]: s for s in result["steps"]}
    assert steps["a"]["access"] == "read"  # default
    assert steps["a"]["tool"] == "codex" and steps["a"]["profileName"] == "Codex"
    assert steps["ok"]["reviews"] == "a"  # defaults to the gate's first input
    assert steps["b"]["access"] == "write"


def test_last_fenced_block_wins() -> None:
    text = 'Draft:\n```json\n{"x": 1}\n```\nFinal:\n```json\n{"x": 2}\n```'
    assert extract_json(text) == {"x": 2}
    assert extract_json('{"x": 3}') == {"x": 3}
    with pytest.raises(PlanError):
        extract_json("no plan here")


def test_parse_plan_from_prose() -> None:
    text = "Here you go.\n```json\n" + json.dumps(plan(agent("a"))) + "\n```"
    assert parse_plan(text, PROFILES)["steps"][0]["id"] == "a"


def test_unknown_profile_and_missing_instructions() -> None:
    msg = problems(plan(agent("a", profileId="prof_zzz", instructions="")))
    assert "'profileId' must be one of the listed profile ids" in msg
    assert "'instructions' are required" in msg


def test_duplicate_ids_unknown_inputs_and_bad_ids() -> None:
    msg = problems(plan(agent("a"), agent("a"), agent("b", ["nope"]), agent("Bad Id")))
    assert "duplicate id" in msg
    assert "unknown input 'nope'" in msg
    assert "lowercase letters" in msg


def test_cycle_is_rejected() -> None:
    msg = problems(plan(agent("a", ["c"]), agent("b", ["a"]), agent("c", ["b"])))
    assert "cycle" in msg


def test_gate_rules() -> None:
    no_inputs = {"id": "g", "title": "G", "kind": "gate", "inputs": []}
    assert "needs at least one input" in problems(plan(agent("a"), no_inputs))
    wrong = {"id": "g", "title": "G", "kind": "gate", "inputs": ["a"], "reviews": "b"}
    assert "'reviews' must be one of its inputs" in problems(plan(agent("a"), agent("b"), wrong))
    g1 = {"id": "g1", "title": "G1", "kind": "gate", "inputs": ["a"]}
    g2 = {"id": "g2", "title": "G2", "kind": "gate", "inputs": ["g1"]}
    assert "must review an agent step" in problems(plan(agent("a"), g1, g2))


def test_limits_and_shape() -> None:
    assert "non-empty list" in problems({"summary": "x", "steps": []})
    assert "JSON object" in problems(["not", "an", "object"])
    many = plan(*[agent(f"s{i}") for i in range(31)])
    assert "at most 30 steps" in problems(many)
    assert "'read', 'write' or 'isolated'" in problems(plan(agent("a", access="exec")))


def test_isolated_access_is_accepted() -> None:
    parts = [agent(f"part-{n}", access="isolated") for n in (1, 2, 3)]
    merge = agent("merge", ["part-1", "part-2", "part-3"], access="write")
    result = validate_plan(plan(*parts, merge), PROFILES)
    assert [s["access"] for s in result["steps"]] == ["isolated"] * 3 + ["write"]


def test_step_prompt_carries_inputs_and_access() -> None:
    task = {"title": "T", "description": "D", "criteria": "[]", "branch": ""}
    step = {"title": "Write it", "instructions": "Go.", "access": "read"}
    source = {"title": "Analysis", "tool": "codex"}
    text = step_prompt(task, {"summary": "S"}, step, [(source, "codex report")])
    assert "## Input from 'Analysis' (Codex)\ncodex report" in text
    assert "Do not modify any files" in text
    isolated = step_prompt(task, {"summary": "S"}, {**step, "access": "isolated"}, [])
    assert "your own isolated copy" in isolated
    merge_step = {**step, "access": "write"}
    part = {"title": "Part", "tool": "codex", "access": "isolated"}
    merge = step_prompt(task, {"summary": "S"}, merge_step, [(part, "patch here")])
    assert "Integrate them into the repository" in merge
    revised = step_prompt(task, {"summary": "S"}, step, [], revision=("old", "make it shorter"))
    assert "## Your previous output\nold" in revised
    assert "## Owner feedback\nmake it shorter" in revised
