"""Profiles: how to invoke one CLI (tool, model, effort, permissions, timeout)."""

from __future__ import annotations

import re
from typing import Any

from ..db import Database, Row, dumps, loads, new_id, now_iso
from ..errors import ApiError, invalid, not_found
from ..runner.adapters import tool_args
from ..tools import AGENT_TOOLS as TOOLS
from ..tools import ALLOWED_TOOLS_SUPPORT, LABELS
from ..validate import Body

EFFORTS = ("", "low", "medium", "high", "xhigh", "max")
NO_MAX_EFFORT = ("codex",)
PERMISSIONS = ("cli", "allowedTools", "full")
MODEL_PATTERN = re.compile(r"[A-Za-z0-9._:/\[\]\-]+")
MIN_TIMEOUT = 30
MAX_TIMEOUT = 43200


def serialize(row: Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "tool": row["tool"],
        "model": row["model"],
        "effort": row["effort"],
        "permission": row["permission"],
        "allowedTools": loads(row["allowed_tools"], []),
        "timeoutSeconds": row["timeout_s"],
        "executable": row["executable"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _parse(data: object) -> dict[str, Any]:
    body = Body(data)
    tool = body.choice("tool", TOOLS, "claude")
    permission = body.choice("permission", PERMISSIONS, "cli")
    allowed = body.text_list("allowedTools", max_items=64, max_len=200)
    effort = body.choice("effort", EFFORTS, "")
    if effort == "max" and tool in NO_MAX_EFFORT:
        raise invalid("effort", f"'max' is not available for {LABELS[tool]}; use 'xhigh'")
    if permission == "allowedTools":
        if tool not in ALLOWED_TOOLS_SUPPORT:
            raise invalid(
                "permission",
                f"'allowedTools' is not available for {LABELS[tool]}; "
                "set its allow rules in the CLI's own settings",
            )
        if not allowed:
            raise invalid("allowedTools", "needs at least one rule")
        for rule in allowed:
            if "," in rule:
                raise invalid("allowedTools", "rules cannot contain commas")
        if len(set(allowed)) != len(allowed):
            raise invalid("allowedTools", "has duplicate rules")
    else:
        allowed = []
    return {
        "name": body.text("name", required=True, max_len=80),
        "tool": tool,
        "model": body.text("model", max_len=100, pattern=MODEL_PATTERN),
        "effort": effort,
        "permission": permission,
        "allowed_tools": dumps(allowed),
        "timeout_s": body.integer(
            "timeoutSeconds", minimum=MIN_TIMEOUT, maximum=MAX_TIMEOUT, default=3600
        ),
        "executable": body.text("executable", max_len=500),
    }


def list_profiles(db: Database) -> list[dict[str, Any]]:
    return [serialize(r) for r in db.all("SELECT * FROM profiles ORDER BY name COLLATE NOCASE")]


def get_row(db: Database, profile_id: str) -> Row:
    row = db.one("SELECT * FROM profiles WHERE id = ?", (profile_id,))
    if row is None:
        raise not_found("profile", profile_id)
    return row


def create_profile(db: Database, data: object) -> dict[str, Any]:
    fields = _parse(data)
    now = now_iso()
    fields |= {"id": new_id("prof"), "created_at": now, "updated_at": now}
    cols = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    db.execute(f"INSERT INTO profiles ({cols}) VALUES ({marks})", tuple(fields.values()))  # noqa: S608
    return serialize(get_row(db, fields["id"]))


def update_profile(db: Database, profile_id: str, data: object) -> dict[str, Any]:
    get_row(db, profile_id)
    fields = _parse(data) | {"updated_at": now_iso()}
    sets = ", ".join(f"{col} = ?" for col in fields)
    db.execute(
        f"UPDATE profiles SET {sets} WHERE id = ?",  # noqa: S608 - column names are fixed
        (*fields.values(), profile_id),
    )
    return serialize(get_row(db, profile_id))


def delete_profile(db: Database, profile_id: str) -> None:
    get_row(db, profile_id)
    used = db.all("SELECT name FROM roles WHERE profile_id = ? ORDER BY name", (profile_id,))
    if used:
        names = ", ".join(r["name"] for r in used)
        raise ApiError(
            "profile-in-use", f"This profile is used by: {names}. Change those roles first.", 409
        )
    db.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))


def preview_args(profile: dict[str, Any]) -> list[str]:
    return [profile["tool"], *tool_args(profile)]
