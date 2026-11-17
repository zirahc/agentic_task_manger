"""Find CLI executables and work out how to launch them without a shell.

On Windows, npm installs CLIs as `.cmd` shims. Running a `.cmd` needs `cmd.exe`,
whose argument parsing is unsafe for arbitrary text. So a shim is resolved to
`node <script.js>` when possible; the cmd.exe fallback only accepts plain arguments.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from ..errors import ApiError
from ..tools import EXECUTABLES, LABELS

FAKE_CLI = Path(__file__).with_name("fake_cli.py")

_SHIM_SCRIPT = re.compile(r'"%~?dp0%?\\([^"]+?\.[cm]?js)"', re.IGNORECASE)
_CMD_SAFE_ARG = re.compile(r"[A-Za-z0-9_.,:=/\\@+\-]*")


@dataclass(frozen=True)
class Launch:
    prefix: list[str]
    """argv elements that start the program (before the tool's own arguments)."""
    via_cmd: bool = False
    """True when cmd.exe parses the command line; arguments must then be plain."""

    def argv(self, args: list[str]) -> list[str]:
        if self.via_cmd:
            for arg in args:
                if not _CMD_SAFE_ARG.fullmatch(arg):
                    raise ApiError(
                        "cli-args-unsafe",
                        "This CLI can only be started through cmd.exe, which cannot safely pass "
                        f"the argument {arg!r}. Set the profile's executable to the CLI's .exe "
                        "or install Node.js next to the npm shim.",
                    )
        return [*self.prefix, *args]


def _extra_dirs() -> list[Path]:
    home = Path.home()
    dirs = [home / ".local" / "bin", home / ".npm-global" / "bin"]
    appdata = os.environ.get("APPDATA")
    if appdata:
        dirs.append(Path(appdata) / "npm")
    if sys.platform != "win32":
        dirs += [Path("/usr/local/bin"), Path("/opt/homebrew/bin")]
    return dirs


def _candidates(name: str) -> list[str]:
    if sys.platform == "win32":
        return [f"{name}.exe", f"{name}.cmd", f"{name}.bat", name]
    return [name]


def find_executable(name: str, override: str = "") -> Path | None:
    if override:
        path = Path(override).expanduser()
        return path if path.is_file() else None
    found = shutil.which(name)
    if found:
        return Path(found)
    for folder in _extra_dirs():
        for candidate in _candidates(name):
            path = folder / candidate
            if path.is_file():
                return path
    return None


def parse_npm_shim(text: str, shim_dir: Path) -> Path | None:
    match = _SHIM_SCRIPT.search(text)
    if match is None:
        return None
    return shim_dir / match.group(1)


def launch_for(exe: Path) -> Launch:
    if exe.suffix.lower() not in (".cmd", ".bat"):
        return Launch([str(exe)])
    try:
        text = exe.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    script = parse_npm_shim(text, exe.parent)
    if script is not None and script.is_file():
        local_node = exe.parent / "node.exe"
        node = str(local_node) if local_node.is_file() else shutil.which("node")
        if node:
            return Launch([node, str(script)])
    return Launch(["cmd.exe", "/d", "/s", "/c", str(exe)], via_cmd=True)


def resolve_tool(tool: str, override: str = "", *, fake: bool = False) -> Launch:
    if fake:
        return Launch([sys.executable, "-u", str(FAKE_CLI), tool])
    command = EXECUTABLES.get(tool, tool)
    exe = find_executable(command, override)
    if exe is None:
        where = f"at {override}" if override else f"('{command}') on PATH"
        raise ApiError(
            "cli-not-found",
            f"Could not find the {LABELS.get(tool, tool)} CLI {where}. Install it and sign "
            "in, or set the profile's executable path.",
        )
    return launch_for(exe)
