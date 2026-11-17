"""Detect the local tools the dashboard drives."""

from __future__ import annotations

import subprocess
from typing import Any

from ..errors import ApiError
from ..runner.process import run_quick
from ..runner.resolve import find_executable, launch_for, resolve_tool
from ..tools import AGENT_TOOLS, EXECUTABLES

TOOLS = (*AGENT_TOOLS, "git", "gh")


def detect(name: str, *, fake: bool) -> dict[str, Any]:
    command = EXECUTABLES.get(name, name)
    info: dict[str, Any] = {
        "name": name,
        "command": command,
        "found": False,
        "path": None,
        "version": None,
        "error": None,
    }
    try:
        if fake and name in AGENT_TOOLS:
            launch = resolve_tool(name, fake=True)
            info["path"] = "(demo mode: fake CLI)"
        else:
            exe = find_executable(command)
            if exe is None:
                info["error"] = f"'{command}' not found on PATH."
                return info
            info["path"] = str(exe)
            launch = launch_for(exe)
        info["found"] = True
        code, output = run_quick(launch.argv(["--version"]))
    except ApiError as err:
        info["error"] = err.message
        return info
    except (OSError, subprocess.SubprocessError) as err:
        info["error"] = f"Found, but could not run it: {err}"
        return info
    first_line = output.splitlines()[0] if output else ""
    if code == 0 and first_line:
        info["version"] = first_line
    else:
        info["error"] = f"Found, but '--version' did not report a version ({first_line or code})."
    return info


def environment(*, fake: bool) -> dict[str, Any]:
    return {"demoMode": fake, "tools": [detect(name, fake=fake) for name in TOOLS]}
