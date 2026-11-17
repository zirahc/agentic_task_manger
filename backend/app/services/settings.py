from __future__ import annotations

from typing import Any

from ..db import Database, dumps, loads
from ..errors import invalid
from ..tools import AGENT_TOOLS as TOOLS
from ..validate import Body

DEFAULT_MAX = 2
MAX_LIMIT = 16

DEFAULT_GUIDANCE = "\n".join(
    [
        "- Codex is strong at reading and writing code: use it to analyse existing code "
        "and write a technical report, and to implement changes.",
        "- Use parallel sessions to go faster: when several extensions, modules or files "
        "must be analysed, give each its own Codex read session; when the code splits into "
        "parts, give each part its own isolated Codex session, then one Codex write session "
        "merges the parts into one codebase.",
        "- Antigravity (Gemini) has broad technical and ecosystem knowledge: use it to split "
        "a problem into parts, compare approaches and recommend methods.",
        "- Claude Code is strong at review and synthesis: use it to combine reports into a "
        "final implementation plan, and to review code and test results.",
        "- Before any step that writes code, add a gate so the owner can approve the "
        "implementation plan.",
        "- When code is written, finish with steps that run the tests and review the result.",
    ]
)


def get_limits(db: Database) -> dict[str, int]:
    row = db.one("SELECT value FROM settings WHERE key = 'maxConcurrent'")
    stored = loads(row["value"] if row else None, {})
    limits: dict[str, int] = {}
    for tool in TOOLS:
        value = stored.get(tool) if isinstance(stored, dict) else None
        limits[tool] = value if isinstance(value, int) and 1 <= value <= MAX_LIMIT else DEFAULT_MAX
    return limits


def get_planner(db: Database) -> dict[str, str]:
    row = db.one("SELECT value FROM settings WHERE key = 'planner'")
    stored = loads(row["value"] if row else None, {})
    if not isinstance(stored, dict):
        stored = {}
    profile_id = stored.get("profileId")
    guidance = stored.get("guidance")
    return {
        "profileId": profile_id if isinstance(profile_id, str) else "",
        "guidance": guidance if isinstance(guidance, str) else DEFAULT_GUIDANCE,
    }


def get_settings(db: Database) -> dict[str, Any]:
    return {
        "maxConcurrent": get_limits(db),
        "planner": get_planner(db),
        "defaultGuidance": DEFAULT_GUIDANCE,
    }


def _store(db: Database, key: str, value: object) -> None:
    db.execute(
        "INSERT INTO settings(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, dumps(value)),
    )


def update_settings(db: Database, data: object) -> dict[str, Any]:
    body = Body(data)
    raw_limits = body.data.get("maxConcurrent")
    if raw_limits is not None:
        limits_body = Body(raw_limits)
        current = get_limits(db)
        limits = {
            tool: limits_body.integer(tool, minimum=1, maximum=MAX_LIMIT, default=current[tool])
            or 1
            for tool in TOOLS
        }
        _store(db, "maxConcurrent", limits)
    raw_planner = body.data.get("planner")
    if raw_planner is not None:
        planner_body = Body(raw_planner)
        profile_id = planner_body.text("profileId", max_len=40)
        if profile_id and db.one("SELECT 1 FROM profiles WHERE id = ?", (profile_id,)) is None:
            raise invalid("profileId", "is not an existing profile")
        guidance = planner_body.text("guidance", max_len=5000)
        _store(db, "planner", {"profileId": profile_id, "guidance": guidance or DEFAULT_GUIDANCE})
    return get_settings(db)
