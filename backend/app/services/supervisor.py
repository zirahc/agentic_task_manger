"""Starts queued sessions within the per-tool concurrency limits and watches them run."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..db import Database, Row, dumps, loads, now_iso
from ..errors import ApiError
from ..runner import git, parse
from ..runner.adapters import CommandSpec, build_command
from ..runner.git import GitError
from ..runner.parse import Event, StreamParser, stderr_event
from ..runner.process import ManagedProcess
from ..runner.resolve import resolve_tool
from . import orchestrator, runs, settings, submissions

log = logging.getLogger(__name__)

MAX_EVENTS_PER_SESSION = 5000
MAX_PATCH_IN_REPORT = 30_000
RESTART_ERROR = (
    "The dashboard restarted while this session was running, so its process was stopped or lost."
)


@dataclass
class Active:
    session_id: str
    cancel_requested: bool = False
    timed_out: bool = False
    shutdown: bool = False
    process: ManagedProcess | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


@dataclass(frozen=True)
class _Isolation:
    root: Path
    path: Path
    base: str
    run_dir: Path


class Supervisor:
    def __init__(self, db: Database, *, fake: bool, work_root: Path, tick_seconds: float = 1.0):
        self._db = db
        self._fake = fake
        self._work_root = work_root
        """Where worktrees and patches of isolated sessions live (inside the data folder)."""
        self._tick = tick_seconds
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None
        self._active: dict[str, Active] = {}
        self._active_lock = threading.Lock()
        self._workers: list[threading.Thread] = []

    # ---- lifecycle -------------------------------------------------------------------

    def start(self) -> None:
        self.recover()
        self._thread = threading.Thread(target=self._loop, name="supervisor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop dispatching, kill running CLIs, and wait until their outcome is recorded.

        Sessions stopped this way are marked failed (not cancelled), so a planned
        workflow can retry the step after the next start.
        """
        self._stopping.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
        with self._active_lock:
            active = list(self._active.values())
        for item in active:
            with item.lock:
                item.shutdown = True
            self._kill(item)
        for worker in list(self._workers):
            worker.join(timeout=20)

    def wake(self) -> None:
        self._wake.set()

    def recover(self) -> None:
        """Sessions marked running from a previous process cannot be resumed safely."""
        now = now_iso()
        self._db.execute(
            "UPDATE sessions SET status = 'failed', error = ?, finished_at = ? "
            "WHERE status = 'running'",
            (RESTART_ERROR, now),
        )
        for row in self._db.all(
            "SELECT id FROM runs WHERE status IN ('queued', 'running', 'planning')"
        ):
            orchestrator.refresh(self._db, row["id"])

    # ---- control ---------------------------------------------------------------------

    def cancel_session(self, session_id: str) -> None:
        row = runs.get_session_row(self._db, session_id)
        if row["status"] == "queued":
            changed = self._db.execute(
                "UPDATE sessions SET status = 'cancelled', finished_at = ? "
                "WHERE id = ? AND status = 'queued'",
                (now_iso(), session_id),
            )
            if changed:
                orchestrator.refresh(self._db, row["run_id"])
                return
        with self._active_lock:
            active = self._active.get(session_id)
        if active is None:
            if row["status"] in runs.ACTIVE:
                raise ApiError(
                    "session-not-cancellable",
                    "This session is starting; try again in a moment.",
                    409,
                )
            return
        self._kill(active, cancel=True)

    def cancel_run(self, run_id: str) -> None:
        for row in self._db.all(
            "SELECT id FROM sessions WHERE run_id = ? AND status IN ('queued', 'running')",
            (run_id,),
        ):
            self.cancel_session(row["id"])

    def active_count(self) -> int:
        with self._active_lock:
            return len(self._active)

    def _kill(self, active: Active, *, cancel: bool = False, timeout: bool = False) -> None:
        with active.lock:
            if cancel:
                active.cancel_requested = True
            if timeout:
                active.timed_out = True
            process = active.process
        if process is not None:
            process.kill_tree()

    # ---- dispatch loop ---------------------------------------------------------------

    def _loop(self) -> None:
        while not self._stopping.is_set():
            try:
                self._dispatch()
            except Exception:
                log.exception("dispatch failed")
            self._wake.wait(self._tick)
            self._wake.clear()

    def _dispatch(self) -> None:
        limits = settings.get_limits(self._db)
        to_start: list[str] = []
        with self._db.transaction():
            running: dict[str, int] = {tool: 0 for tool in limits}
            for row in self._db.all(
                "SELECT tool, COUNT(*) AS n FROM sessions WHERE status = 'running' GROUP BY tool"
            ):
                running[row["tool"]] = row["n"]
            queued = self._db.all(
                "SELECT id, tool FROM sessions WHERE status = 'queued' ORDER BY created_at, id"
            )
            now = now_iso()
            for row in queued:
                tool = row["tool"]
                if running.get(tool, 0) >= limits.get(tool, 1):
                    continue
                running[tool] = running.get(tool, 0) + 1
                self._db.execute(
                    "UPDATE sessions SET status = 'running', started_at = ? WHERE id = ?",
                    (now, row["id"]),
                )
                with self._active_lock:
                    self._active[row["id"]] = Active(row["id"])
                to_start.append(row["id"])
        for session_id in to_start:
            with self._active_lock:
                active = self._active[session_id]
            run_row = self._db.one("SELECT run_id FROM sessions WHERE id = ?", (session_id,))
            if run_row:
                orchestrator.refresh(self._db, run_row["run_id"])
            worker = threading.Thread(
                target=self._worker, args=(active,), name=f"session-{session_id}", daemon=True
            )
            self._workers = [w for w in self._workers if w.is_alive()]
            self._workers.append(worker)
            worker.start()

    # ---- one session -----------------------------------------------------------------

    def _worker(self, active: Active) -> None:
        try:
            self._execute(active)
        except Exception as exc:
            log.exception("session %s crashed", active.session_id)
            self._finish(active.session_id, "failed", error=f"Internal error: {exc}")
        finally:
            with self._active_lock:
                self._active.pop(active.session_id, None)
            self._wake.set()

    def _execute(self, active: Active) -> None:
        sid = active.session_id
        row = self._db.one("SELECT * FROM sessions WHERE id = ?", (sid,))
        if row is None:
            return
        profile: dict[str, Any] = loads(row["profile_snapshot"], {})
        try:
            launch = resolve_tool(
                row["tool"], str(profile.get("executable") or ""), fake=self._fake
            )
            spec = build_command(profile, row["prompt"], launch)
        except ApiError as err:
            self._finish(sid, "failed", error=err.message)
            return
        workdir = Path(row["workdir"])
        if not workdir.is_dir():
            self._finish(sid, "failed", error=f"The working folder {workdir} no longer exists.")
            return

        isolation: _Isolation | None = None
        if row["isolated"]:
            try:
                isolation = self._isolate(sid, workdir)
            except GitError as err:
                self._finish(sid, "failed", error=f"Could not create the isolated copy: {err}")
                return
        try:
            self._run_process(active, row, profile, spec, isolation)
        finally:
            if isolation is not None:
                git.remove_worktree(isolation.root, isolation.path)

    def _isolate(self, sid: str, workdir: Path) -> _Isolation:
        location = git.locate(workdir)
        if location is None:
            raise GitError(f"{workdir} is not inside a git repository; isolated steps need git.")
        base = git.snapshot(location.root, self._work_root / "tmp" / sid)
        path = self._work_root / "worktrees" / sid
        git.add_worktree(location.root, path, base)
        run_dir = path / location.prefix if location.prefix else path
        return _Isolation(location.root, path, base, run_dir)

    def _collect_changes(
        self, row: Row, isolation: _Isolation, text: str | None
    ) -> tuple[str, str | None]:
        patch = git.diff_since(isolation.path, isolation.base)
        report = text or ""
        if not patch.strip():
            return report + "\n\n## Changes\nNo files were changed in the isolated copy.", None
        name = f"{row['step_id'] or 'session'}-{row['id']}.patch"
        out = self._work_root / "patches" / row["run_id"] / name
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(patch.encode("utf-8"))
        shown = (
            patch
            if len(patch) <= MAX_PATCH_IN_REPORT
            else (patch[:MAX_PATCH_IN_REPORT] + "\n… (truncated; the full patch is in the file)")
        )
        report += (
            "\n\n## Changes made in the isolated copy\n"
            f"Patch file: {out}\n"
            f"Paths are relative to the repository root ({isolation.root}); apply with "
            f'`git apply "{out}"` from there.\n\n```diff\n{shown}\n```'
        )
        return report, str(out)

    def _run_process(
        self,
        active: Active,
        row: Row,
        profile: dict[str, Any],
        spec: CommandSpec,
        isolation: _Isolation | None,
    ) -> None:
        sid = active.session_id
        run_dir = isolation.run_dir if isolation else Path(row["workdir"])
        events = _EventSink(self._db, sid)
        try:
            process = ManagedProcess(
                spec.argv,
                run_dir,
                spec.stdin_text,
                on_stderr=lambda line: events.add(stderr_event(line)),
            )
        except OSError as err:
            self._finish(sid, "failed", error=f"Could not start {row['tool']}: {err}")
            return
        with active.lock:
            active.process = process
            cancel_now = active.cancel_requested
        if cancel_now:
            process.kill_tree()
        self._db.execute("UPDATE sessions SET pid = ? WHERE id = ?", (process.pid, sid))

        timeout = int(profile.get("timeoutSeconds") or 3600)
        timer = threading.Timer(timeout, lambda: self._kill(active, timeout=True))
        timer.daemon = True
        timer.start()

        parser = StreamParser(row["tool"])
        saved_cli_id: str | None = None
        try:
            for line in process.lines():
                for event in parser.feed(line):
                    events.add(event)
                if parser.cli_session_id and parser.cli_session_id != saved_cli_id:
                    saved_cli_id = parser.cli_session_id
                    self._db.execute(
                        "UPDATE sessions SET cli_session_id = ? WHERE id = ?", (saved_cli_id, sid)
                    )
            code = process.wait()
        finally:
            timer.cancel()

        status, error = _outcome(code, parser, active, timeout, process.stderr_tail)
        result_text = parser.result_text
        self._record_submissions(row, run_dir, [*parser.submission_texts, result_text or ""])
        patch_path: str | None = None
        if isolation is not None and status == "succeeded":
            try:
                result_text, patch_path = self._collect_changes(row, isolation, result_text)
            except (GitError, OSError) as err:
                status, error = "failed", f"Could not capture the changes: {err}"
        self._finish(
            sid,
            status,
            error=error,
            exit_code=code,
            result_text=result_text,
            usage=parser.usage,
            patch_path=patch_path,
        )

    def _record_submissions(self, row: Row, folder: Path, texts: list[str]) -> None:
        """Store ```submission blocks (before the session is marked finished, so a run that
        shows as done already has its submissions), with the commit that was checked out."""
        if not any(parse.SUBMISSION_MARK in text for text in texts):
            return
        submissions.record(
            self._db,
            session_id=row["id"],
            run_id=row["run_id"],
            texts=texts,
            commit=git.head(folder),
        )

    def _finish(
        self,
        session_id: str,
        status: str,
        *,
        error: str | None = None,
        exit_code: int | None = None,
        result_text: str | None = None,
        usage: dict[str, Any] | None = None,
        patch_path: str | None = None,
    ) -> None:
        self._db.execute(
            "UPDATE sessions SET status = ?, error = ?, exit_code = ?, result_text = ?, usage = ?, "
            "patch_path = ?, finished_at = ? WHERE id = ?",
            (
                status,
                error,
                exit_code,
                result_text,
                dumps(usage or {}),
                patch_path,
                now_iso(),
                session_id,
            ),
        )
        row = self._db.one("SELECT run_id FROM sessions WHERE id = ?", (session_id,))
        if row:
            orchestrator.refresh(self._db, row["run_id"])


def _outcome(
    code: int, parser: StreamParser, active: Active, timeout: int, stderr_tail: str
) -> tuple[str, str | None]:
    if active.cancel_requested:
        return "cancelled", "Cancelled."
    if active.shutdown:
        return "failed", "The dashboard shut down while this session was running; retry the step."
    if active.timed_out:
        return "failed", f"Timed out after {timeout} s; partial output was kept."
    if parser.permission_denials:
        tools = ", ".join(sorted(set(parser.permission_denials)))
        return "failed", f"Tools were refused by the CLI's permission settings: {tools}."
    if parser.is_error:
        return "failed", parser.error or "The CLI reported an error."
    if code != 0:
        detail = f": {stderr_tail.splitlines()[-1]}" if stderr_tail else ""
        return "failed", f"The CLI exited with code {code}{detail}"
    if not parser.finished:
        return "failed", "The CLI ended without reporting a result."
    return "succeeded", None


class _EventSink:
    def __init__(self, db: Database, session_id: str):
        self._db = db
        self._sid = session_id
        self._count = 0
        self._lock = threading.Lock()

    def add(self, event: Event) -> None:
        with self._lock:
            self._count += 1
            if self._count > MAX_EVENTS_PER_SESSION and event.kind not in ("result", "error"):
                return
        self._db.insert(
            "INSERT INTO events (session_id, kind, data, created_at) VALUES (?, ?, ?, ?)",
            (self._sid, event.kind, dumps(event.data), now_iso()),
        )
