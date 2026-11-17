"""Workflow plans: what the planner must answer, how it is checked, and the prompts.

A plan is untrusted model output. It is used only after `parse_plan` has validated it
against the real profiles, and the owner must approve it before anything runs.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..db import Row, loads
from ..tools import LABELS

MAX_STEPS = 30
MAX_INPUT_CHARS = 40_000
STEP_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
FENCE = re.compile(r"```(?:json|plan)?\s*\n(.*?)\n```", re.DOTALL)
KINDS = ("agent", "gate")
ACCESS = ("read", "write", "isolated")


class PlanError(Exception):
    """The planner's answer is not a usable plan. The message lists every problem."""


# ---- parsing & validation -------------------------------------------------------------


def extract_json(text: str) -> object:
    candidates = [m.group(1) for m in FENCE.finditer(text)]
    candidates.reverse()  # the last block is the final answer
    candidates.append(text.strip())
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    raise PlanError("no JSON plan was found (expected one ```json block)")


def parse_plan(text: str, profiles: dict[str, Row]) -> dict[str, Any]:
    return validate_plan(extract_json(text), profiles)


def validate_plan(raw: object, profiles: dict[str, Row]) -> dict[str, Any]:
    problems: list[str] = []
    if not isinstance(raw, dict):
        raise PlanError("the plan must be a JSON object with 'summary' and 'steps'")
    summary = raw.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        problems.append("'summary' must be a non-empty string")
        summary = ""
    raw_steps = raw.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise PlanError("; ".join([*problems, "'steps' must be a non-empty list"]))
    if len(raw_steps) > MAX_STEPS:
        problems.append(f"at most {MAX_STEPS} steps are allowed (got {len(raw_steps)})")

    steps: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_steps[:MAX_STEPS], start=1):
        where = f"step {index}"
        if not isinstance(item, dict):
            problems.append(f"{where} is not an object")
            continue
        step_id = item.get("id")
        if not isinstance(step_id, str) or not STEP_ID.fullmatch(step_id):
            problems.append(f"{where}: 'id' must be lowercase letters, digits and dashes")
            continue
        where = f"step '{step_id}'"
        if step_id in seen:
            problems.append(f"{where}: duplicate id")
            continue
        seen.add(step_id)
        step = _validate_step(item, step_id, where, profiles, problems)
        steps.append(step)

    ids = {s["id"] for s in steps}
    for step in steps:
        for dep in step["inputs"]:
            if dep == step["id"]:
                problems.append(f"step '{step['id']}' cannot use itself as an input")
            elif dep not in ids:
                problems.append(f"step '{step['id']}' uses unknown input '{dep}'")
        if step["kind"] == "gate":
            if not step["inputs"]:
                problems.append(f"gate '{step['id']}' needs at least one input to review")
            elif step["reviews"] not in step["inputs"]:
                problems.append(f"gate '{step['id']}': 'reviews' must be one of its inputs")
            elif _kind_of(steps, step["reviews"]) != "agent":
                problems.append(f"gate '{step['id']}' must review an agent step")
    if not problems:
        cycle = _find_cycle(steps)
        if cycle:
            problems.append("the steps form a cycle: " + " → ".join(cycle))
    if problems:
        raise PlanError("; ".join(problems))
    return {"summary": summary.strip()[:4000], "steps": steps}


def _validate_step(
    item: dict[str, Any], step_id: str, where: str, profiles: dict[str, Row], problems: list[str]
) -> dict[str, Any]:
    kind = item.get("kind", "agent")
    if kind not in KINDS:
        problems.append(f"{where}: 'kind' must be 'agent' or 'gate'")
        kind = "agent"
    title = item.get("title")
    if not isinstance(title, str) or not title.strip():
        problems.append(f"{where}: 'title' is required")
        title = step_id
    raw_inputs = item.get("inputs", [])
    inputs = [i for i in raw_inputs if isinstance(i, str)] if isinstance(raw_inputs, list) else []
    if not isinstance(raw_inputs, list) or len(inputs) != len(raw_inputs):
        problems.append(f"{where}: 'inputs' must be a list of step ids")
    step: dict[str, Any] = {
        "id": step_id,
        "title": title.strip()[:120],
        "kind": kind,
        "inputs": list(dict.fromkeys(inputs)),
    }
    if kind == "gate":
        reviews = item.get("reviews")
        step["reviews"] = reviews if isinstance(reviews, str) else (inputs[0] if inputs else "")
        note = item.get("instructions")
        step["instructions"] = note.strip()[:4000] if isinstance(note, str) else ""
        return step
    profile_id = item.get("profileId")
    profile = profiles.get(profile_id) if isinstance(profile_id, str) else None
    if profile is None:
        problems.append(f"{where}: 'profileId' must be one of the listed profile ids")
    access = item.get("access", "read")
    if access not in ACCESS:
        problems.append(f"{where}: 'access' must be 'read', 'write' or 'isolated'")
        access = "read"
    instructions = item.get("instructions")
    if not isinstance(instructions, str) or not instructions.strip():
        problems.append(f"{where}: 'instructions' are required")
        instructions = ""
    step.update(
        {
            "profileId": profile_id if profile else "",
            "profileName": profile["name"] if profile else "",
            "tool": profile["tool"] if profile else "",
            "access": access,
            "instructions": instructions.strip()[:20000],
        }
    )
    return step


def _kind_of(steps: list[dict[str, Any]], step_id: str) -> str:
    return next((s["kind"] for s in steps if s["id"] == step_id), "")


def _find_cycle(steps: list[dict[str, Any]]) -> list[str]:
    inputs = {s["id"]: s["inputs"] for s in steps}
    state: dict[str, int] = {}  # 1 = visiting, 2 = done
    path: list[str] = []

    def visit(node: str) -> list[str]:
        state[node] = 1
        path.append(node)
        for dep in inputs.get(node, []):
            if state.get(dep) == 1:
                return [*path[path.index(dep) :], dep]
            if state.get(dep) is None:
                found = visit(dep)
                if found:
                    return found
        path.pop()
        state[node] = 2
        return []

    for step_id in inputs:
        if state.get(step_id) is None:
            found = visit(step_id)
            if found:
                return found
    return []


# ---- prompts ---------------------------------------------------------------------------

PLAN_FORMAT = """\
```json
{
  "summary": "One paragraph: the approach and why these tools.",
  "steps": [
    {"id": "analyze-code", "title": "Analyse the current code", "kind": "agent",
     "profileId": "<a listed profile id>", "access": "read", "inputs": [],
     "instructions": "What this session must do and what its report must contain."},
    {"id": "final-plan", "title": "Write the implementation plan", "kind": "agent",
     "profileId": "<a listed profile id>", "access": "read", "inputs": ["analyze-code"],
     "instructions": "..."},
    {"id": "approve-plan", "title": "Owner approves the implementation plan", "kind": "gate",
     "inputs": ["final-plan"], "reviews": "final-plan"},
    {"id": "code-part-a", "title": "Implement part A", "kind": "agent",
     "profileId": "<a listed profile id>", "access": "isolated", "inputs": ["approve-plan"],
     "instructions": "..."},
    {"id": "code-part-b", "title": "Implement part B", "kind": "agent",
     "profileId": "<a listed profile id>", "access": "isolated", "inputs": ["approve-plan"],
     "instructions": "..."},
    {"id": "merge", "title": "Merge the parts into one codebase", "kind": "agent",
     "profileId": "<a listed profile id>", "access": "write",
     "inputs": ["code-part-a", "code-part-b", "approve-plan"], "instructions": "..."}
  ]
}
```"""


def task_section(task: Row) -> str:
    parts = [f"# Task: {task['title']}"]
    if task["description"]:
        parts.append(task["description"])
    criteria = loads(task["criteria"], [])
    if criteria:
        parts.append("## Acceptance criteria\n" + "\n".join(f"- {c}" for c in criteria))
    if task["branch"]:
        parts.append(f"Work on the git branch `{task['branch']}`.")
    return "\n\n".join(parts)


def profile_lines(profiles: list[Row]) -> str:
    lines = []
    for p in profiles:
        details = [f"tool: {p['tool']} ({LABELS.get(p['tool'], p['tool'])})"]
        if p["model"]:
            details.append(f"model: {p['model']}")
        if p["effort"]:
            details.append(f"effort: {p['effort']}")
        details.append(f"permissions: {p['permission']}")
        lines.append(f"- `{p['id']}` · {p['tool']} · {p['name']} · " + ", ".join(details))
    return "\n".join(lines)


def planner_prompt(
    task: Row,
    profiles: list[Row],
    guidance: str,
    notes: str,
    *,
    limits: dict[str, int] | None = None,
    git_repo: bool = False,
    previous_plan: dict[str, Any] | None = None,
    feedback: str | None = None,
    problem: str | None = None,
) -> str:
    capacity = ", ".join(
        f"{LABELS.get(tool, tool)} {count}" for tool, count in (limits or {}).items()
    )
    parts = [
        "# Your role: workflow planner",
        "You design how a team of AI coding agents will complete the task below. Each "
        "agent is a headless CLI session (Claude Code, Codex or Antigravity) started by a "
        "local dashboard. Read the repository as needed, but do not modify any files.",
        task_section(task),
    ]
    if notes:
        parts.append(f"## Notes from the owner\n{notes}")
    parts += [
        f"## Available agents\nUse these profile ids exactly:\n{profile_lines(profiles)}",
        f"## Guidance\n{guidance}",
        "## How steps run\n"
        "- Every agent step is a separate session in the task's repository folder. It "
        "sees only its own instructions, the task, and the final reports of the steps "
        "listed in its `inputs`.\n"
        "- Steps whose inputs are done run in parallel"
        + (f", up to this many sessions per tool at once: {capacity}" if capacity else "")
        + ". Extra sessions wait for a free slot.\n"
        "- `access: read` steps only investigate and report.\n"
        "- `access: write` steps modify the task folder directly and never run at the "
        "same time as another write step.\n"
        + (
            "- `access: isolated` steps modify files in their own private copy of the "
            "repository, so several can run in parallel. Their changes are captured as a "
            "patch and appended to their report. A later `write` step must list them in "
            "`inputs` to integrate the patches into the task folder.\n"
            if git_repo
            else "- The task folder is not a git repository, so `access: isolated` is not "
            "available: use read steps in parallel and a single write step.\n"
        )
        + "- Speed matters: split independent work into parallel sessions. For example, "
        "analyse each extension or module in its own read session; implement each part in "
        "its own isolated session; then one write session merges the parts into one "
        "codebase.\n"
        "- A `gate` step pauses for the owner, who reads the output of the step named in "
        "`reviews` and either approves it or sends corrections back to that step.\n"
        "- Steps after a gate receive the approved output by listing the gate in `inputs`.\n"
        f"- Use at most {MAX_STEPS} steps.\n"
        "- Write each step's instructions so the session knows exactly what to produce, "
        "and for parallel steps exactly which part is theirs.",
        "The task folder is a git repository." if git_repo else "",
    ]
    parts = [p for p in parts if p]
    if previous_plan is not None or problem:
        parts.append("## Revision")
        if previous_plan is not None:
            parts.append(
                "This is the current plan:\n```json\n"
                + json.dumps(previous_plan, indent=2, ensure_ascii=False)
                + "\n```"
            )
        if problem:
            parts.append(f"The previous answer could not be used: {problem}")
        if feedback:
            parts.append(f"## Owner feedback\n{feedback}")
        parts.append("Return the complete updated plan, not only the changes.")
    parts.append(
        "## Answer format\nEnd your answer with exactly one fenced JSON block in this "
        f"format:\n{PLAN_FORMAT}"
    )
    return "\n\n".join(parts) + "\n"


def step_prompt(
    task: Row,
    plan: dict[str, Any],
    step: dict[str, Any],
    inputs: list[tuple[dict[str, Any], str]],
    *,
    revision: tuple[str, str] | None = None,
) -> str:
    parts = [f"# Your step: {step['title']}", step["instructions"], task_section(task)]
    parts.append(f"## The workflow you are part of\n{plan['summary']}")
    for source, output in inputs:
        clipped = output if len(output) <= MAX_INPUT_CHARS else output[:MAX_INPUT_CHARS] + "\n…"
        who = LABELS.get(source.get("tool", ""), "") or "owner-approved"
        parts.append(f"## Input from '{source['title']}' ({who})\n{clipped}")
    if step["access"] == "write":
        parts.append("You may modify files in the repository to complete this step.")
        if any(source.get("access") == "isolated" for source, _ in inputs):
            parts.append(
                "Some inputs were produced in isolated copies of the repository; their "
                "changes are included above as patches with a patch file path. Integrate "
                "them into the repository (for example `git apply`, resolving conflicts by "
                "hand) so the parts work together as one codebase, then verify the result."
            )
    elif step["access"] == "isolated":
        parts.append(
            "You work in your own isolated copy of the repository, in parallel with other "
            "sessions. Change only what your part needs; your changes are captured as a "
            "patch and integrated by a later step. You may modify files in this copy."
        )
    else:
        parts.append("Do not modify any files in this step; investigate and report only.")
    if revision is not None:
        previous, feedback = revision
        parts.append(f"## Your previous output\n{previous}")
        parts.append(f"## Owner feedback\n{feedback}")
        parts.append("Revise your output to address the feedback. Return the complete version.")
    parts.append(
        "End with a clear, self-contained report: it is passed as-is to the next steps "
        "and shown to the owner."
    )
    return "\n\n".join(parts) + "\n"
