from __future__ import annotations

import json
import secrets
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

Row = dict[str, Any]
Params = Sequence[Any]

# Append-only: never edit a migration that has shipped.
MIGRATIONS: list[str] = [
    """
    CREATE TABLE settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    CREATE TABLE profiles (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        tool TEXT NOT NULL,
        model TEXT NOT NULL DEFAULT '',
        effort TEXT NOT NULL DEFAULT '',
        permission TEXT NOT NULL DEFAULT 'cli',
        allowed_tools TEXT NOT NULL DEFAULT '[]',
        timeout_s INTEGER NOT NULL DEFAULT 3600,
        executable TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE roles (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        color TEXT NOT NULL DEFAULT '#6ea8fe',
        profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
        instructions TEXT NOT NULL DEFAULT '',
        workdir_mode TEXT NOT NULL DEFAULT 'task',
        fixed_dir TEXT NOT NULL DEFAULT '',
        timeout_s INTEGER,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE tasks (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        repo_dir TEXT NOT NULL DEFAULT '',
        branch TEXT NOT NULL DEFAULT '',
        criteria TEXT NOT NULL DEFAULT '[]',
        priority TEXT NOT NULL DEFAULT 'normal',
        status TEXT NOT NULL DEFAULT 'backlog',
        labels TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE runs (
        id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
        mode TEXT NOT NULL,
        status TEXT NOT NULL,
        snapshot TEXT NOT NULL,
        created_at TEXT NOT NULL,
        finished_at TEXT
    );
    CREATE INDEX runs_task ON runs(task_id);
    CREATE TABLE sessions (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        role_id TEXT NOT NULL,
        role_name TEXT NOT NULL,
        tool TEXT NOT NULL,
        profile_snapshot TEXT NOT NULL,
        prompt TEXT NOT NULL,
        workdir TEXT NOT NULL,
        status TEXT NOT NULL,
        cli_session_id TEXT,
        pid INTEGER,
        exit_code INTEGER,
        error TEXT,
        result_text TEXT,
        usage TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        started_at TEXT,
        finished_at TEXT
    );
    CREATE INDEX sessions_run ON sessions(run_id);
    CREATE INDEX sessions_status ON sessions(status);
    CREATE TABLE events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        kind TEXT NOT NULL,
        data TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE INDEX events_session ON events(session_id, id);
    """,
    # 2: AI-planned runs (planner → owner approval → workflow of steps)
    """
    ALTER TABLE runs ADD COLUMN stage TEXT NOT NULL DEFAULT 'execute';
    ALTER TABLE runs ADD COLUMN plan TEXT;
    ALTER TABLE runs ADD COLUMN plan_version INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE runs ADD COLUMN error TEXT;
    ALTER TABLE runs ADD COLUMN notes TEXT NOT NULL DEFAULT '';
    ALTER TABLE sessions ADD COLUMN step_id TEXT;
    ALTER TABLE sessions ADD COLUMN purpose TEXT NOT NULL DEFAULT 'role';
    CREATE INDEX sessions_step ON sessions(run_id, step_id);
    CREATE TABLE plan_versions (
        run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        version INTEGER NOT NULL,
        plan TEXT NOT NULL,
        session_id TEXT,
        created_at TEXT NOT NULL,
        PRIMARY KEY (run_id, version)
    );
    CREATE TABLE run_steps (
        run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        step_id TEXT NOT NULL,
        status TEXT NOT NULL,
        output TEXT,
        error TEXT,
        attempts INTEGER NOT NULL DEFAULT 0,
        started_at TEXT,
        finished_at TEXT,
        PRIMARY KEY (run_id, step_id)
    );
    CREATE TABLE run_feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        target TEXT NOT NULL,
        text TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    """,
    # 3: isolated sessions (own git worktree; changes captured as a patch)
    """
    ALTER TABLE sessions ADD COLUMN isolated INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE sessions ADD COLUMN patch_path TEXT;
    """,
]


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


def dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def loads(text: str | None, default: Any) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return default


class Database:
    """One SQLite connection shared by request handlers and session workers.

    Every call holds a re-entrant lock, so worker threads and handlers never interleave
    inside a transaction.
    """

    def __init__(self, path: Path | str):
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            if str(path) != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._migrate()

    def _migrate(self) -> None:
        version = int(self._conn.execute("PRAGMA user_version").fetchone()[0])
        for number, sql in enumerate(MIGRATIONS[version:], start=version + 1):
            self._conn.executescript(f"BEGIN;{sql}\nPRAGMA user_version={number};COMMIT;")

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    def execute(self, sql: str, params: Params = ()) -> int:
        with self._lock:
            return self._conn.execute(sql, params).rowcount

    def insert(self, sql: str, params: Params = ()) -> int:
        with self._lock:
            return int(self._conn.execute(sql, params).lastrowid or 0)

    def all(self, sql: str, params: Params = ()) -> list[Row]:
        with self._lock:
            return [dict(row) for row in self._conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params: Params = ()) -> Row | None:
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
            return dict(row) if row is not None else None

    def close(self) -> None:
        with self._lock:
            self._conn.close()
