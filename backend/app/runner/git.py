"""Isolated copies for parallel write steps, built on git worktrees.

`snapshot()` records the task folder exactly as it is (committed, uncommitted and
untracked files, honouring .gitignore) as a dangling commit. It uses a temporary
index, so the user's index, branch and files are never touched. Each isolated step
gets a detached worktree at that commit; afterwards its changes are captured as a
patch and the worktree is removed.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# Identity for the snapshot commits only; they are never pushed or put on a branch.
GIT_ENV = {
    "GIT_AUTHOR_NAME": "agent-dashboard",
    "GIT_AUTHOR_EMAIL": "agent-dashboard@localhost",
    "GIT_COMMITTER_NAME": "agent-dashboard",
    "GIT_COMMITTER_EMAIL": "agent-dashboard@localhost",
    "GIT_TERMINAL_PROMPT": "0",
}


class GitError(Exception):
    pass


@dataclass(frozen=True)
class RepoLocation:
    root: Path
    prefix: str
    """The task folder relative to the repository root ('' when it is the root)."""


def _git(args: list[str], cwd: Path, env_extra: dict[str, str] | None = None) -> str:
    exe = shutil.which("git")
    if exe is None:
        raise GitError("git is not installed or not on PATH.")
    env = {**os.environ, **GIT_ENV, **(env_extra or {})}
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        result = subprocess.run(
            [exe, *args],
            cwd=cwd,
            env=env,
            capture_output=True,
            timeout=300,
            check=False,
            stdin=subprocess.DEVNULL,
            creationflags=flags,
        )
    except (OSError, subprocess.SubprocessError) as err:
        raise GitError(f"git {args[0]} could not run: {err}") from err
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip().splitlines()
        raise GitError(f"git {args[0]} failed: {detail[-1] if detail else result.returncode}")
    return result.stdout.decode("utf-8", errors="replace")


def locate(folder: Path) -> RepoLocation | None:
    """The git repository containing `folder`, or None when it is not in one."""
    try:
        root = _git(["rev-parse", "--show-toplevel"], folder).strip()
        prefix = _git(["rev-parse", "--show-prefix"], folder).strip()
    except GitError:
        return None
    return RepoLocation(Path(root), prefix.rstrip("/"))


def snapshot(root: Path, scratch: Path) -> str:
    scratch.mkdir(parents=True, exist_ok=True)
    index = scratch / "snapshot.index"
    env = {"GIT_INDEX_FILE": str(index)}
    try:
        head: str | None = _git(["rev-parse", "--verify", "HEAD"], root).strip()
    except GitError:
        head = None  # a repository without commits yet
    try:
        if head:
            _git(["read-tree", head], root, env)
        _git(["add", "-A"], root, env)
        tree = _git(["write-tree"], root, env).strip()
        args = ["commit-tree", tree, "-m", "agent-dashboard snapshot"]
        if head:
            args += ["-p", head]
        return _git(args, root).strip()
    finally:
        index.unlink(missing_ok=True)


def add_worktree(root: Path, path: Path, commit: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _git(["worktree", "add", "--detach", str(path), commit], root)


def diff_since(worktree: Path, base: str) -> str:
    """Everything changed in the worktree since `base`, as a patch (new files included)."""
    _git(["add", "-A"], worktree)
    return _git(["diff", "--cached", base], worktree)


def remove_worktree(root: Path, path: Path) -> None:
    try:
        _git(["worktree", "remove", "--force", str(path)], root)
    except GitError:
        shutil.rmtree(path, ignore_errors=True)
        with contextlib.suppress(GitError):
            _git(["worktree", "prune"], root)
