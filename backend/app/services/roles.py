"""Roles: reusable session definitions (what a session does and how it runs)."""

from __future__ import annotations

import re
from typing import Any

from ..db import Database, Row, new_id, now_iso
from ..errors import not_found
from ..validate import Body
from . import profiles

WORKDIR_MODES = ("task", "fixed")
COLOR = re.compile(r"#[0-9a-fA-F]{6}")


def serialize(row: Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "color": row["color"],
        "profileId": row["profile_id"],
        "instructions": row["instructions"],
        "workdirMode": row["workdir_mode"],
        "fixedDir": row["fixed_dir"],
        "timeoutSeconds": row["timeout_s"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _parse(db: Database, data: object) -> dict[str, Any]:
    body = Body(data)
    profile_id = body.text("profileId", required=True, max_len=40)
    profiles.get_row(db, profile_id)
    mode = body.choice("workdirMode", WORKDIR_MODES, "task")
    fixed_dir = body.text("fixedDir", required=mode == "fixed", max_len=500)
    return {
        "name": body.text("name", required=True, max_len=80),
        "description": body.text("description", max_len=500),
        "color": body.text("color", max_len=7, pattern=COLOR) or "#6ea8fe",
        "profile_id": profile_id,
        "instructions": body.text("instructions", max_len=20000),
        "workdir_mode": mode,
        "fixed_dir": fixed_dir if mode == "fixed" else "",
        "timeout_s": body.integer(
            "timeoutSeconds",
            minimum=profiles.MIN_TIMEOUT,
            maximum=profiles.MAX_TIMEOUT,
            default=None,
        ),
    }


def list_roles(db: Database) -> list[dict[str, Any]]:
    return [serialize(r) for r in db.all("SELECT * FROM roles ORDER BY name COLLATE NOCASE")]


def get_row(db: Database, role_id: str) -> Row:
    row = db.one("SELECT * FROM roles WHERE id = ?", (role_id,))
    if row is None:
        raise not_found("role", role_id)
    return row


def create_role(db: Database, data: object) -> dict[str, Any]:
    fields = _parse(db, data)
    now = now_iso()
    fields |= {"id": new_id("role"), "created_at": now, "updated_at": now}
    cols = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    db.execute(f"INSERT INTO roles ({cols}) VALUES ({marks})", tuple(fields.values()))  # noqa: S608
    return serialize(get_row(db, fields["id"]))


def update_role(db: Database, role_id: str, data: object) -> dict[str, Any]:
    get_row(db, role_id)
    fields = _parse(db, data) | {"updated_at": now_iso()}
    sets = ", ".join(f"{col} = ?" for col in fields)
    db.execute(
        f"UPDATE roles SET {sets} WHERE id = ?",  # noqa: S608 - column names are fixed
        (*fields.values(), role_id),
    )
    return serialize(get_row(db, role_id))


def delete_role(db: Database, role_id: str) -> None:
    get_row(db, role_id)
    db.execute("DELETE FROM roles WHERE id = ?", (role_id,))
