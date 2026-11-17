"""Turn CLI event streams into readable events.

Claude Code `stream-json`, Codex `exec --json`, and Antigravity (`agy`) `stream-json`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

MAX_TEXT = 8000
MAX_SUMMARY = 400


def _clip(text: str, limit: int = MAX_TEXT) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


@dataclass
class Event:
    kind: str
    """init | message | command | file | tool | tool-error | warning | error | result | output |
    stderr"""
    data: dict[str, Any]


@dataclass
class StreamParser:
    tool: str
    cli_session_id: str | None = None
    model: str | None = None
    result_text: str | None = None
    is_error: bool = False
    error: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    permission_denials: list[str] = field(default_factory=list)
    finished: bool = False
    _last_message: str = ""
    _agy_text: dict[int, str] = field(default_factory=dict)

    def feed(self, line: str) -> list[Event]:
        line = line.strip()
        if not line:
            return []
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            return [Event("output", {"text": _clip(line)})]
        if not isinstance(obj, dict):
            return [Event("output", {"text": _clip(line)})]
        if self.tool == "claude":
            return self._claude(obj)
        if self.tool == "antigravity":
            return self._antigravity(obj)
        return self._codex(obj)

    # ---- Antigravity (agy) -----------------------------------------------------------

    def _antigravity(self, obj: dict[str, Any]) -> list[Event]:
        kind = obj.get("event")
        if kind == "init":
            self.cli_session_id = _str(obj.get("conversation_id")) or self.cli_session_id
            init = _dict(obj.get("init"))
            self.model = _str(init.get("model")) or self.model
            data: dict[str, Any] = {"sessionId": self.cli_session_id, "model": self.model}
            if _str(init.get("permission_mode")):
                data["permissionMode"] = init["permission_mode"]
            return [Event("init", data)]
        if kind == "step_update":
            return self._agy_step(_dict(obj.get("step_update")))
        if kind == "result":
            return self._agy_result(_dict(obj.get("result")))
        return []

    def _agy_step(self, step: dict[str, Any]) -> list[Event]:
        if not self.cli_session_id:
            self.cli_session_id = _str(step.get("conversation_id")) or None
        index = step.get("step_index")
        key = index if isinstance(index, int) and not isinstance(index, bool) else -1
        done = step.get("state") == "DONE"
        step_type = step.get("step_type")
        if step_type == "agent_response":
            delta = _str(step.get("text_delta"))
            if delta:
                self._agy_text[key] = self._agy_text.get(key, "") + delta
            return self._agy_flush(key) if done else []
        if step_type == "tool" and done:
            return _agy_tool_events(step)
        return []

    def _agy_flush(self, key: int) -> list[Event]:
        text = self._agy_text.pop(key, "")
        if not text.strip():
            return []
        self._last_message = text
        return [Event("message", {"text": _clip(text)})]

    def _agy_result(self, result: dict[str, Any]) -> list[Event]:
        events: list[Event] = []
        for key in sorted(self._agy_text):
            events += self._agy_flush(key)
        self.finished = True
        self.cli_session_id = _str(result.get("conversation_id")) or self.cli_session_id
        status = _str(result.get("status")) or "UNKNOWN"
        self.result_text = _str(result.get("response")) or self._last_message
        self.is_error = status != "SUCCESS"
        if self.is_error:
            error = result.get("error")
            message = _str(error.get("message")) if isinstance(error, dict) else _str(error)
            self.error = message or f"Antigravity reported status {status}."
        usage = _dict(result.get("usage"))
        self.usage = {
            "inputTokens": _int(usage.get("input_tokens")),
            "cacheReadTokens": _int(usage.get("cache_read_tokens")),
            "outputTokens": _int(usage.get("output_tokens")),
            "thinkingTokens": _int(usage.get("thinking_tokens")),
        }
        events.append(
            Event(
                "result",
                {
                    "text": _clip(self.result_text or ""),
                    "isError": self.is_error,
                    "status": status,
                    "usage": self.usage,
                },
            )
        )
        return events

    # ---- Claude Code ---------------------------------------------------------------

    def _claude(self, obj: dict[str, Any]) -> list[Event]:
        kind = obj.get("type")
        if kind == "system" and obj.get("subtype") == "init":
            self.cli_session_id = _str(obj.get("session_id")) or self.cli_session_id
            self.model = _str(obj.get("model")) or self.model
            return [Event("init", {"sessionId": self.cli_session_id, "model": self.model})]
        if kind == "assistant":
            return self._claude_content(obj)
        if kind == "user":
            return self._claude_tool_errors(obj)
        if kind == "result":
            return [self._claude_result(obj)]
        return []

    def _claude_content(self, obj: dict[str, Any]) -> list[Event]:
        events: list[Event] = []
        for item in _content(obj):
            item_type = item.get("type")
            if item_type == "text":
                text = _str(item.get("text"))
                if text:
                    self._last_message = text
                    events.append(Event("message", {"text": _clip(text)}))
            elif item_type == "tool_use":
                events.append(_claude_tool_event(item))
        return events

    def _claude_tool_errors(self, obj: dict[str, Any]) -> list[Event]:
        events: list[Event] = []
        for item in _content(obj):
            if item.get("type") == "tool_result" and item.get("is_error") is True:
                events.append(Event("tool-error", {"text": _clip(_flatten(item.get("content")))}))
        return events

    def _claude_result(self, obj: dict[str, Any]) -> Event:
        self.finished = True
        self.cli_session_id = _str(obj.get("session_id")) or self.cli_session_id
        self.result_text = _str(obj.get("result")) or self._last_message
        self.is_error = obj.get("is_error") is True or obj.get("subtype") not in (None, "success")
        if self.is_error:
            self.error = self.result_text or f"Claude reported {obj.get('subtype')}"
        usage = _dict(obj.get("usage"))
        self.usage = {
            "inputTokens": _int(usage.get("input_tokens")),
            "cacheReadTokens": _int(usage.get("cache_read_input_tokens")),
            "cacheWriteTokens": _int(usage.get("cache_creation_input_tokens")),
            "outputTokens": _int(usage.get("output_tokens")),
        }
        cost = obj.get("total_cost_usd")
        if isinstance(cost, int | float):
            self.usage["costUsd"] = float(cost)
        for denial in obj.get("permission_denials") or []:
            if isinstance(denial, dict):
                self.permission_denials.append(_str(denial.get("tool_name")) or "unknown tool")
        return Event(
            "result",
            {
                "text": _clip(self.result_text or ""),
                "isError": self.is_error,
                "usage": self.usage,
                "permissionDenials": self.permission_denials,
            },
        )

    # ---- Codex -----------------------------------------------------------------------

    def _codex(self, obj: dict[str, Any]) -> list[Event]:
        kind = obj.get("type")
        if kind == "thread.started":
            self.cli_session_id = _str(obj.get("thread_id")) or self.cli_session_id
            return [Event("init", {"sessionId": self.cli_session_id})]
        if kind == "item.completed":
            item = obj.get("item")
            return self._codex_item(item) if isinstance(item, dict) else []
        if kind == "turn.completed":
            usage = _dict(obj.get("usage"))
            self.usage = {
                "inputTokens": _int(usage.get("input_tokens")),
                "cacheReadTokens": _int(usage.get("cached_input_tokens")),
                "outputTokens": _int(usage.get("output_tokens")),
            }
            self.finished = True
            self.result_text = self._last_message
            return [
                Event(
                    "result",
                    {"text": _clip(self._last_message), "isError": False, "usage": self.usage},
                )
            ]
        if kind == "turn.failed":
            error = obj.get("error")
            message = _str(error.get("message")) if isinstance(error, dict) else ""
            self.finished = True
            self.is_error = True
            self.error = message or "Codex reported a failed turn."
            return [Event("error", {"text": _clip(self.error)})]
        if kind == "error":
            return [Event("error", {"text": _clip(_str(obj.get("message")) or line_of(obj))})]
        return []

    def _codex_item(self, item: dict[str, Any]) -> list[Event]:
        item_type = item.get("type")
        if item_type == "agent_message":
            text = _str(item.get("text"))
            if not text:
                return []
            self._last_message = text
            return [Event("message", {"text": _clip(text)})]
        if item_type == "command_execution":
            return [
                Event(
                    "command",
                    {
                        "command": _clip(_str(item.get("command")), MAX_SUMMARY),
                        "exitCode": item.get("exit_code"),
                        "output": _clip(_str(item.get("aggregated_output")), 2000),
                    },
                )
            ]
        if item_type == "file_change":
            changes = item.get("changes")
            if not isinstance(changes, list):
                return []
            return [
                Event("file", {"path": _str(c.get("path")), "action": _str(c.get("kind"))})
                for c in changes
                if isinstance(c, dict)
            ]
        if item_type == "mcp_tool_call":
            name = f"{_str(item.get('server'))}.{_str(item.get('tool'))}"
            return [Event("tool", {"name": name, "summary": ""})]
        if item_type == "error":
            return [Event("error", {"text": _clip(_str(item.get("message")))})]
        return []


_PERMISSION_NOTICE = re.compile(
    r"\b(denied|not approved|requires approval|permission)\b", re.IGNORECASE
)


def stderr_event(line: str) -> Event:
    """Stderr is noise by default, but permission notices must stay visible.

    Headless `agy` soft-denies tools it may not use: the run still exits 0 and only a
    stderr notice says something was skipped.
    """
    kind = "warning" if _PERMISSION_NOTICE.search(line) else "stderr"
    return Event(kind, {"text": _clip(line, 2000)})


_AGY_PATH_KEYS = ("TargetFile", "AbsolutePath", "FilePath", "file_path", "path", "Path")
_AGY_FILE_WORDS = ("write", "edit", "replace", "create", "delete")


def _agy_tool_events(step: dict[str, Any]) -> list[Event]:
    info = _dict(step.get("tool_info"))
    name = _str(info.get("name")) or _str(step.get("tool_name")) or "tool"
    params = _dict(info.get("parameters"))
    events: list[Event] = []
    if name == "run_command":
        command = _str(params.get("CommandLine")) or _str(params.get("command"))
        output = _clip(_flatten(info.get("output")), 2000)
        events.append(Event("command", {"command": _clip(command, MAX_SUMMARY), "output": output}))
    else:
        path = next((params[k] for k in _AGY_PATH_KEYS if _str(params.get(k))), "")
        if path and any(word in name.lower() for word in _AGY_FILE_WORDS):
            events.append(Event("file", {"path": path, "action": name}))
        else:
            summary = path or next((v for v in params.values() if isinstance(v, str)), "")
            events.append(Event("tool", {"name": name, "summary": _clip(summary, MAX_SUMMARY)}))
    error = info.get("error")
    message = _str(error.get("message")) if isinstance(error, dict) else _str(error)
    if message:
        events.append(Event("tool-error", {"text": _clip(f"{name}: {message}")}))
    return events


_FILE_TOOLS = {"Write": "write", "Edit": "edit", "MultiEdit": "edit", "NotebookEdit": "edit"}


def _claude_tool_event(item: dict[str, Any]) -> Event:
    name = _str(item.get("name"))
    raw_input = item.get("input")
    args: dict[str, Any] = raw_input if isinstance(raw_input, dict) else {}
    if name == "Bash":
        return Event("command", {"command": _clip(_str(args.get("command")), MAX_SUMMARY)})
    if name in _FILE_TOOLS:
        path = _str(args.get("file_path") or args.get("notebook_path"))
        return Event("file", {"path": path, "action": _FILE_TOOLS[name]})
    summary = ""
    for key in ("description", "file_path", "pattern", "query", "url", "subject", "prompt"):
        if isinstance(args.get(key), str):
            summary = args[key]
            break
    return Event("tool", {"name": name, "summary": _clip(summary, MAX_SUMMARY)})


def _content(obj: dict[str, Any]) -> list[dict[str, Any]]:
    message = obj.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return []
    return [item for item in content if isinstance(item, dict)]


def _flatten(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(_flatten(v) for v in value)
    if isinstance(value, dict):
        return _str(value.get("text"))
    return ""


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def line_of(obj: dict[str, Any]) -> str:
    return json.dumps(obj, ensure_ascii=False)[:MAX_SUMMARY]
