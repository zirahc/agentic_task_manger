"""A stand-in for `claude` / `codex` / `agy` used in demo mode and tests.

Usage: fake_cli.py <claude|codex|antigravity> [real CLI args...]  (prompt on stdin)

Prompt keywords change behaviour:
  FAKE_FAIL      -> exit 1 with an error
  FAKE_SLOW      -> keep running for a long time (for cancel/timeout tests)
  FAKE_DENY      -> (antigravity) soft-deny a tool: stderr notice, still exits 0
  FAKE_PLAN_BAD  -> as planner, answer without a JSON plan

As the workflow planner it answers with a plan built from the listed profiles; as a
workflow step it reports which inputs it received; as a revision it echoes the feedback.
"""

from __future__ import annotations

import json
import re
import sys
import time
import uuid
from pathlib import Path

PROFILE_LINE = re.compile(r"^- `(prof_[0-9a-f]+)` · (\w+) · ", re.MULTILINE)
INPUT_HEADER = re.compile(r"^## Input from '([^']+)'", re.MULTILINE)


def _after(prompt: str, header: str) -> str:
    rest = prompt.split(header, 1)[1].strip()
    return rest.splitlines()[0].strip() if rest else ""


def _reply(prompt: str) -> str:
    if "# Your role: workflow planner" in prompt:
        return _plan_reply(prompt)
    step = next(
        (ln[13:].strip() for ln in prompt.splitlines() if ln.startswith("# Your step: ")), None
    )
    if step is None:
        return "Done. DONE"
    if "## Owner feedback" in prompt:
        return f"Revised report for '{step}', applying: {_after(prompt, '## Owner feedback')}"
    inputs = INPUT_HEADER.findall(prompt)
    used = f" Built on: {', '.join(inputs)}." if inputs else ""
    if "You may modify files" in prompt:
        # Leave a visible change so isolation and patch capture can be checked.
        slug = re.sub(r"[^a-z0-9]+", "-", step.lower()).strip("-") or "work"
        Path(f"{slug}.txt").write_text(f"written for '{step}'\n", encoding="utf-8")
        used += f" Wrote {slug}.txt."
    return f"Report for '{step}'.{used}"


def _plan_reply(prompt: str) -> str:
    if "FAKE_PLAN_BAD" in prompt:
        return "I could not settle on a plan."
    ids: dict[str, str] = {}
    for profile_id, tool in PROFILE_LINE.findall(prompt):
        ids.setdefault(tool, profile_id)
    if not ids:
        return "There are no agent profiles to plan with."
    fallback = next(iter(ids.values()))

    def pick(tool: str) -> str:
        return ids.get(tool, fallback)

    git_repo = "The task folder is a git repository." in prompt
    summary = (
        "Three Codex sessions analyse the extension groups in parallel, Antigravity splits "
        "the problem into methods, Claude merges everything into an implementation plan for "
        "your approval; then "
        + (
            "two isolated Codex sessions implement the parts in parallel and one Codex "
            "session merges them"
            if git_repo
            else "Codex implements it"
        )
        + ", and Claude tests and reviews."
    )
    if "## Owner feedback" in prompt:
        summary += f" Revised for your feedback: {_after(prompt, '## Owner feedback')}"

    def agent(
        step_id: str, title: str, tool: str, access: str, inputs: list[str]
    ) -> dict[str, object]:
        return {
            "id": step_id,
            "title": title,
            "kind": "agent",
            "profileId": pick(tool),
            "access": access,
            "inputs": inputs,
            "instructions": f"Do this: {title}.",
        }

    analyses = [f"analyze-group-{n}" for n in (1, 2, 3)]
    steps = [
        *(
            agent(step_id, f"Analyse extension group {n}", "codex", "read", [])
            for n, step_id in enumerate(analyses, start=1)
        ),
        agent(
            "research-methods",
            "Split into methods and recommend approaches",
            "antigravity",
            "read",
            analyses,
        ),
        agent(
            "final-plan",
            "Review the reports and write the implementation plan",
            "claude",
            "read",
            [*analyses, "research-methods"],
        ),
        {
            "id": "approve-plan",
            "title": "Owner approves the implementation plan",
            "kind": "gate",
            "inputs": ["final-plan"],
            "reviews": "final-plan",
        },
    ]
    if git_repo:
        steps += [
            agent("code-part-1", "Implement part 1", "codex", "isolated", ["approve-plan"]),
            agent("code-part-2", "Implement part 2", "codex", "isolated", ["approve-plan"]),
            agent(
                "merge",
                "Merge the parts into one codebase",
                "codex",
                "write",
                ["code-part-1", "code-part-2", "approve-plan"],
            ),
        ]
        last = "merge"
    else:
        steps.append(agent("merge", "Implement the plan", "codex", "write", ["approve-plan"]))
        last = "merge"
    steps.append(
        agent("test-review", "Run the tests and review the result", "claude", "read", [last])
    )
    plan = json.dumps({"summary": summary, "steps": steps}, indent=2)
    return f"Here is the workflow.\n\n```json\n{plan}\n```"


def emit(obj: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main() -> int:
    tool = sys.argv[1] if len(sys.argv) > 1 else "claude"
    if "--version" in sys.argv:
        print(f"{tool} 0.0.0-fake")
        return 0
    prompt = sys.stdin.read()
    if tool == "antigravity":
        prompt = _agy_prompt(prompt)
    delay = 0.05
    session = str(uuid.uuid4())
    title = next(
        (ln[8:].strip() for ln in prompt.splitlines() if ln.startswith("# Task: ")), "task"
    )

    if "FAKE_SLOW" in prompt:
        delay = 30.0

    reply = _reply(prompt)
    if tool == "antigravity":
        return _antigravity(prompt, title, session, delay, reply)

    if tool == "claude":
        emit({"type": "system", "subtype": "init", "session_id": session, "model": "fake-model"})
        emit(_assistant(session, {"type": "text", "text": f"Looking at: {title}"}))
        emit(
            _assistant(
                session, {"type": "tool_use", "name": "Bash", "input": {"command": "git status"}}
            )
        )
        time.sleep(delay)
        if "FAKE_FAIL" in prompt:
            sys.stderr.write("fake failure requested\n")
            emit(
                {
                    "type": "result",
                    "subtype": "error_during_execution",
                    "is_error": True,
                    "result": "Fake failure",
                    "session_id": session,
                }
            )
            return 1
        emit(
            _assistant(
                session,
                {
                    "type": "tool_use",
                    "name": "Write",
                    "input": {"file_path": "notes.txt", "content": "hi"},
                },
            )
        )
        emit(_assistant(session, {"type": "text", "text": reply}))
        emit(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": reply,
                "session_id": session,
                "total_cost_usd": 0.0123,
                "usage": {"input_tokens": 120, "output_tokens": 45, "cache_read_input_tokens": 300},
            }
        )
        return 0

    emit({"type": "thread.started", "thread_id": session})
    emit({"type": "turn.started"})
    emit(
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": f"Looking at: {title}"},
        }
    )
    emit(
        {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "command": "git status",
                "aggregated_output": "clean",
                "exit_code": 0,
            },
        }
    )
    time.sleep(delay)
    if "FAKE_FAIL" in prompt:
        emit({"type": "turn.failed", "error": {"message": "Fake failure"}})
        return 1
    emit(
        {
            "type": "item.completed",
            "item": {"type": "file_change", "changes": [{"path": "notes.txt", "kind": "add"}]},
        }
    )
    emit({"type": "item.completed", "item": {"type": "agent_message", "text": reply}})
    emit(
        {
            "type": "turn.completed",
            "usage": {"input_tokens": 150, "cached_input_tokens": 100, "output_tokens": 30},
        }
    )
    return 0


def _agy_prompt(raw: str) -> str:
    """agy reads `{"event":"user","message":{"content":...}}` lines on stdin."""
    for line in raw.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        message = event.get("message")
        if event.get("event") == "user" and isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                return content
    sys.stderr.write("invalid stdin event\n")
    sys.exit(1)


def _antigravity(prompt: str, title: str, conv: str, delay: float, reply: str) -> int:
    def step(index: int, step_type: str, state: str, **extra: object) -> None:
        payload = {
            "conversation_id": conv,
            "step_index": index,
            "state": state,
            "step_type": step_type,
            **extra,
        }
        emit({"event": "step_update", "step_update": payload})

    emit(
        {
            "event": "init",
            "conversation_id": conv,
            "init": {"cwd": ".", "model": "fake-gemini", "permission_mode": "request-review"},
        }
    )
    step(1, "agent_response", "ACTIVE", text_delta="Looking at: ")
    step(1, "agent_response", "ACTIVE", text_delta=title)
    step(1, "agent_response", "DONE")
    step(
        2,
        "tool",
        "DONE",
        tool_name="run_command",
        tool_info={
            "name": "run_command",
            "parameters": {"CommandLine": "git status"},
            "output": "clean",
        },
    )
    time.sleep(delay)
    if "FAKE_DENY" in prompt:
        sys.stderr.write("Permission denied: run_command (git push) was not approved\n")
    if "FAKE_FAIL" in prompt:
        emit(
            {
                "event": "result",
                "result": {
                    "conversation_id": conv,
                    "status": "ERROR",
                    "error": {"message": "Fake failure"},
                },
            }
        )
        return 1
    step(
        3,
        "tool",
        "DONE",
        tool_name="write_to_file",
        tool_info={"name": "write_to_file", "parameters": {"TargetFile": "notes.txt"}},
    )
    step(4, "agent_response", "ACTIVE", text_delta=reply)
    step(4, "agent_response", "DONE")
    emit(
        {
            "event": "result",
            "result": {
                "conversation_id": conv,
                "status": "SUCCESS",
                "response": reply,
                "num_turns": 1,
                "usage": {
                    "input_tokens": 200,
                    "output_tokens": 20,
                    "thinking_tokens": 50,
                    "cache_read_tokens": 80,
                    "total_tokens": 350,
                },
            },
        }
    )
    return 0


def _assistant(session: str, item: dict[str, object]) -> dict[str, object]:
    return {
        "type": "assistant",
        "session_id": session,
        "message": {"role": "assistant", "content": [item]},
    }


if __name__ == "__main__":
    sys.exit(main())
