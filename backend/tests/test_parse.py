from __future__ import annotations

from pathlib import Path

from app.runner.parse import StreamParser, stderr_event

FIXTURES = Path(__file__).with_name("fixtures")


def feed_file(tool: str, name: str) -> tuple[StreamParser, list[str]]:
    parser = StreamParser(tool)
    kinds: list[str] = []
    for line in (FIXTURES / name).read_text(encoding="utf-8").splitlines():
        kinds += [event.kind for event in parser.feed(line)]
    return parser, kinds


def test_claude_stream_fixture() -> None:
    parser, kinds = feed_file("claude", "claude-code-2.1.303-stream.jsonl")
    assert parser.cli_session_id == "bff48ee0-0203-487d-be5a-2bc2d7fb24bf"
    assert parser.finished and not parser.is_error
    assert parser.result_text is not None and parser.result_text.endswith("DONE")
    assert parser.usage["costUsd"] > 0
    assert kinds[0] == "init"
    assert kinds[-1] == "result"
    assert "command" in kinds  # Bash calls
    assert "file" in kinds  # Write / Edit
    assert "tool-error" in kinds  # `false` failed


def test_codex_stream_fixture() -> None:
    parser, kinds = feed_file("codex", "codex-0.166.0-stream.jsonl")
    assert parser.cli_session_id == "01a1cb6c-a085-7031-9da7-f88ebf3f3b50"
    assert parser.finished and not parser.is_error
    assert parser.result_text == "DONE"
    assert parser.usage["inputTokens"] == 153156
    assert kinds.count("command") == 7  # completed commands only, not item.started
    assert kinds.count("file") == 2
    assert kinds[-1] == "result"


def test_non_json_line_is_kept_as_output() -> None:
    events = StreamParser("claude").feed("npm WARN something\n")
    assert [e.kind for e in events] == ["output"]
    assert events[0].data["text"] == "npm WARN something"


def test_claude_error_result_and_permission_denials() -> None:
    parser = StreamParser("claude")
    parser.feed(
        '{"type":"result","subtype":"success","is_error":false,"result":"ok",'
        '"permission_denials":[{"tool_name":"Write"}]}'
    )
    assert parser.permission_denials == ["Write"]
    parser = StreamParser("claude")
    parser.feed('{"type":"result","subtype":"error_max_turns","is_error":true,"result":""}')
    assert parser.is_error


def test_codex_turn_failed() -> None:
    parser = StreamParser("codex")
    events = parser.feed('{"type":"turn.failed","error":{"message":"usage limit reached"}}')
    assert parser.is_error and parser.error == "usage limit reached"
    assert events[0].kind == "error"


# Built from the event shapes in the official headless docs
# (antigravity.google/docs/cli/headless), not captured from a real `agy` run.
def test_antigravity_stream_fixture() -> None:
    parser = StreamParser("antigravity")
    events = []
    for line in (FIXTURES / "antigravity-docs-stream.jsonl").read_text("utf-8").splitlines():
        events += parser.feed(line)
    kinds = [e.kind for e in events]
    assert parser.cli_session_id == "9ec58bfd-4d67-4f5e-83a5-9d907e9c6b1f"
    assert parser.model == "gemini-3-pro"
    assert kinds == ["init", "message", "command", "file", "tool-error", "message", "result"]
    assert events[1].data["text"] == "I'll run the command."  # deltas joined once, on DONE
    assert events[2].data == {
        "command": "echo hello_headless_demo",
        "output": "hello_headless_demo",
    }
    assert events[3].data["path"] == "/home/user/project/notes.txt"
    assert events[5].data["text"] == "apple"  # never DONE: flushed at result
    assert parser.finished and not parser.is_error
    assert parser.result_text == "apple"
    assert parser.usage["inputTokens"] == 10800
    assert parser.usage["thinkingTokens"] == 200


def test_antigravity_error_status() -> None:
    parser = StreamParser("antigravity")
    parser.feed(
        '{"event":"result","result":{"status":"ERROR","error":{"message":"unknown model"}}}'
    )
    assert parser.is_error and parser.error == "unknown model"
    parser = StreamParser("antigravity")
    parser.feed('{"event":"result","result":{"status":"CANCELED"}}')
    assert parser.is_error and parser.error == "Antigravity reported status CANCELED."


def test_permission_notices_on_stderr_become_warnings() -> None:
    assert stderr_event("Permission denied: run_command was not approved").kind == "warning"
    assert stderr_event("loading model…").kind == "stderr"
