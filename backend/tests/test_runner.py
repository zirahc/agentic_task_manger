from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.errors import ApiError
from app.runner.adapters import build_command, tool_args
from app.runner.resolve import Launch, launch_for, parse_npm_shim, resolve_tool

NPM_SHIM = r"""@ECHO off
GOTO start
:find_dp0
SET dp0=%~dp0
EXIT /b
:start
SETLOCAL
CALL :find_dp0
endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & ^
"%_prog%"  "%dp0%\node_modules\@openai\codex\bin\codex.js" %*
"""


def test_parse_npm_shim() -> None:
    script = parse_npm_shim(NPM_SHIM, Path("C:/npm"))
    assert script == Path("C:/npm") / r"node_modules\@openai\codex\bin\codex.js"
    assert parse_npm_shim("@echo off\nrem nothing", Path(".")) is None


def test_unparseable_cmd_falls_back_to_cmd_with_plain_args_only(tmp_path: Path) -> None:
    shim = tmp_path / "tool.cmd"
    shim.write_text("@echo off\nsomething %*\n")
    launch = launch_for(shim)
    assert launch.via_cmd
    assert launch.argv(["-p", "--model", "opus"])[-3:] == ["-p", "--model", "opus"]
    with pytest.raises(ApiError) as err:
        launch.argv(["--allowedTools", "Bash(git status *)"])
    assert err.value.code == "cli-args-unsafe"


def test_exe_launches_directly(tmp_path: Path) -> None:
    exe = tmp_path / "claude.exe"
    exe.write_bytes(b"")
    assert launch_for(exe) == Launch([str(exe)])


def test_claude_args() -> None:
    profile = {"tool": "claude", "model": "opus", "effort": "high", "permission": "full"}
    assert tool_args(profile) == [
        "-p", "--output-format", "stream-json", "--verbose",
        "--model", "opus", "--effort", "high",
        "--permission-mode", "bypassPermissions",
    ]  # fmt: skip
    allowed = {
        "tool": "claude",
        "permission": "allowedTools",
        "allowedTools": ["Edit", "Bash(git *)"],
    }
    assert tool_args(allowed)[-2:] == ["--allowedTools", "Edit,Bash(git *)"]


def test_codex_args_read_prompt_from_stdin() -> None:
    profile = {"tool": "codex", "model": "gpt-6.1-sol", "effort": "high", "permission": "full"}
    args = tool_args(profile)
    assert args[:2] == ["exec", "--json"]
    assert "--dangerously-bypass-approvals-and-sandbox" in args
    assert "model_reasoning_effort=high" in args
    assert args[-1] == "-"


def test_prompt_never_in_argv() -> None:
    spec = build_command({"tool": "claude"}, "secret prompt text", Launch(["claude"]))
    assert "secret prompt text" not in spec.argv
    assert spec.stdin_text == "secret prompt text"
    assert "<prompt via stdin, 18 chars>" in spec.display()


def test_antigravity_streams_prompt_as_stdin_event() -> None:
    profile = {
        "tool": "antigravity",
        "model": "gemini-3-pro",
        "effort": "max",
        "permission": "full",
    }
    spec = build_command(profile, 'say "hi"\nthen stop', Launch(["agy"]))
    assert spec.argv == [
        "agy", "--input-format", "stream-json", "--output-format", "stream-json",
        "--model", "gemini-3-pro", "--effort", "max", "--dangerously-skip-permissions",
    ]  # fmt: skip
    assert "-p" not in spec.argv  # a -p prompt would be dropped in stdin streaming mode
    assert spec.stdin_text.endswith("\n") and spec.stdin_text.count("\n") == 1
    assert json.loads(spec.stdin_text) == {
        "event": "user",
        "message": {"content": 'say "hi"\nthen stop'},
    }


def test_antigravity_cli_settings_adds_no_permission_flag() -> None:
    assert "--dangerously-skip-permissions" not in tool_args({"tool": "antigravity"})


def test_resolve_uses_agy_command_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def fake_find(name: str, override: str = "") -> None:
        seen.append(name)

    monkeypatch.setattr("app.runner.resolve.find_executable", fake_find)
    with pytest.raises(ApiError) as err:
        resolve_tool("antigravity")
    assert seen == ["agy"]
    assert "Antigravity" in err.value.message and "'agy'" in err.value.message
