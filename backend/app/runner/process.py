"""A child process with piped I/O that can be stopped together with its children."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from pathlib import Path

MAX_STDERR_LINES = 200


class ManagedProcess:
    def __init__(
        self,
        argv: list[str],
        cwd: Path,
        stdin_text: str,
        on_stderr: Callable[[str], None] | None = None,
    ):
        env = dict(os.environ)
        env.setdefault("PYTHONIOENCODING", "utf-8")
        if sys.platform == "win32":
            self._proc = subprocess.Popen(
                argv,
                cwd=cwd,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
            )
        else:
            self._proc = subprocess.Popen(
                argv,
                cwd=cwd,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        self._killed = threading.Event()
        self._stdin_thread = threading.Thread(
            target=self._write_stdin, args=(stdin_text,), daemon=True
        )
        self._stdin_thread.start()
        self._stderr_tail: list[str] = []
        self._stderr_thread = threading.Thread(
            target=self._read_stderr, args=(on_stderr,), daemon=True
        )
        self._stderr_thread.start()

    @property
    def pid(self) -> int:
        return self._proc.pid

    @property
    def killed(self) -> bool:
        return self._killed.is_set()

    @property
    def stderr_tail(self) -> str:
        return "\n".join(self._stderr_tail[-20:])

    def _write_stdin(self, text: str) -> None:
        stdin = self._proc.stdin
        if stdin is None:
            return
        with contextlib.suppress(OSError):
            stdin.write(text.encode("utf-8"))
        with contextlib.suppress(OSError):
            stdin.close()

    def _read_stderr(self, on_line: Callable[[str], None] | None) -> None:
        stream = self._proc.stderr
        if stream is None:
            return
        count = 0
        for raw in stream:
            line = raw.decode("utf-8", errors="replace").rstrip()
            if not line:
                continue
            self._stderr_tail = [*self._stderr_tail[-49:], line]
            count += 1
            if on_line is not None and count <= MAX_STDERR_LINES:
                on_line(line)

    def lines(self) -> Iterator[str]:
        stream = self._proc.stdout
        if stream is None:
            return
        for raw in stream:
            yield raw.decode("utf-8", errors="replace")

    def wait(self) -> int:
        code = self._proc.wait()
        self._stderr_thread.join(timeout=5)
        return code

    def kill_tree(self) -> None:
        """Stop the process and everything it started. Safe to call more than once."""
        if self._proc.poll() is not None:
            return
        self._killed.set()
        if sys.platform == "win32":
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                subprocess.run(
                    ["taskkill", "/PID", str(self._proc.pid), "/T", "/F"],
                    capture_output=True,
                    timeout=15,
                    check=False,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
        else:
            with contextlib.suppress(OSError):
                os.killpg(self._proc.pid, signal.SIGTERM)
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(OSError):
                    os.killpg(self._proc.pid, signal.SIGKILL)
        if self._proc.poll() is None:
            with contextlib.suppress(OSError):
                self._proc.kill()


def run_quick(argv: list[str], timeout: float = 8.0) -> tuple[int, str]:
    """Run a short probe like `--version`. Returns (exit code, combined output)."""
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    result = subprocess.run(
        argv,
        capture_output=True,
        timeout=timeout,
        check=False,
        stdin=subprocess.DEVNULL,
        creationflags=flags,
    )
    output = (result.stdout or b"").decode("utf-8", errors="replace").strip()
    if not output:
        output = (result.stderr or b"").decode("utf-8", errors="replace").strip()
    return result.returncode, output
