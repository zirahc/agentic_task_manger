"""The agent CLIs this dashboard drives. Add a tool here first, then an adapter and a parser."""

from __future__ import annotations

AGENT_TOOLS = ("claude", "codex", "antigravity")

EXECUTABLES = {
    "claude": "claude",
    "codex": "codex",
    "antigravity": "agy",
}
"""The command name each tool is installed as."""

LABELS = {
    "claude": "Claude Code",
    "codex": "Codex",
    "antigravity": "Antigravity",
}

ALLOWED_TOOLS_SUPPORT = ("claude",)
"""Tools whose allow-list can be passed per run on the command line."""
