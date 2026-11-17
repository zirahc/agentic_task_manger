"""Build the exact command for a profile. The only place that knows CLI flags."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .resolve import Launch


@dataclass(frozen=True)
class CommandSpec:
    argv: list[str]
    stdin_text: str

    def display(self) -> str:
        stdin_note = f"<prompt via stdin, {len(self.stdin_text)} chars>"
        return f"{subprocess.list2cmdline(self.argv)}  {stdin_note}"


def tool_args(profile: Mapping[str, Any]) -> list[str]:
    tool = profile["tool"]
    model = str(profile.get("model") or "")
    effort = str(profile.get("effort") or "")
    permission = str(profile.get("permission") or "cli")
    if tool == "claude":
        args = ["-p", "--output-format", "stream-json", "--verbose"]
        if model:
            args += ["--model", model]
        if effort:
            args += ["--effort", effort]
        if permission == "full":
            args += ["--permission-mode", "bypassPermissions"]
        elif permission == "allowedTools":
            rules = [str(rule) for rule in profile.get("allowedTools") or []]
            if rules:
                args += ["--allowedTools", ",".join(rules)]
        return args
    if tool == "codex":
        args = ["exec", "--json"]
        if model:
            args += ["--model", model]
        if effort:
            args += ["-c", f"model_reasoning_effort={effort}"]
        if permission == "full":
            args.append("--dangerously-bypass-approvals-and-sandbox")
        args.append("-")
        return args
    if tool == "antigravity":
        # Stdin streaming keeps the prompt off argv; closing stdin ends the session after
        # the turn. Per-tool allow rules live in agy's own settings.json, not in flags.
        args = ["--input-format", "stream-json", "--output-format", "stream-json"]
        if model:
            args += ["--model", model]
        if effort:
            args += ["--effort", effort]
        if permission == "full":
            args.append("--dangerously-skip-permissions")
        return args
    raise ValueError(f"unknown tool {tool!r}")


def stdin_for(tool: str, prompt: str) -> str:
    if tool == "antigravity":
        event = {"event": "user", "message": {"content": prompt}}
        return json.dumps(event, ensure_ascii=False) + "\n"
    return prompt


def build_command(profile: Mapping[str, Any], prompt: str, launch: Launch) -> CommandSpec:
    tool = str(profile["tool"])
    return CommandSpec(launch.argv(tool_args(profile)), stdin_for(tool, prompt))
