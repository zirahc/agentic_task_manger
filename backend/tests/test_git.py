from __future__ import annotations

from pathlib import Path

from app.runner import git as g
from tests.conftest import git


def test_locate(git_repo: Path, tmp_path: Path) -> None:
    sub = git_repo / "src"
    sub.mkdir()
    location = g.locate(sub)
    assert location is not None
    assert location.root.resolve() == git_repo.resolve()
    assert location.prefix == "src"
    plain = tmp_path / "plain"
    plain.mkdir()
    assert g.locate(plain) is None


def test_snapshot_worktree_and_diff_leave_the_user_alone(git_repo: Path, tmp_path: Path) -> None:
    status_before = git(git_repo, "status", "--porcelain")
    head_before = git(git_repo, "rev-parse", "HEAD")

    base = g.snapshot(git_repo, tmp_path / "scratch")
    worktree = tmp_path / "wt"
    g.add_worktree(git_repo, worktree, base)

    # The copy contains uncommitted and untracked work, exactly as on disk.
    assert (worktree / "app.js").read_text(encoding="utf-8") == "console.log('v2');\n"
    assert (worktree / "draft.md").exists()

    (worktree / "part.js").write_text("export const part = 1;\n", encoding="utf-8")
    (worktree / "app.js").write_text("console.log('v3');\n", encoding="utf-8")
    patch = g.diff_since(worktree, base)
    assert "+export const part = 1;" in patch
    assert "-console.log('v2');" in patch and "+console.log('v3');" in patch
    assert "draft.md" not in patch  # unchanged since the snapshot

    g.remove_worktree(git_repo, worktree)
    assert not worktree.exists()
    # The user's repository is exactly as before: files, index, branch.
    assert git(git_repo, "status", "--porcelain") == status_before
    assert git(git_repo, "rev-parse", "HEAD") == head_before
    assert not (git_repo / "part.js").exists()
    assert "wt" not in git(git_repo, "worktree", "list")

    # The patch applies cleanly to the original folder.
    patch_file = tmp_path / "part.patch"
    patch_file.write_bytes(patch.encode("utf-8"))  # text mode would add CRs on Windows
    git(git_repo, "apply", str(patch_file))
    assert (git_repo / "part.js").exists()


def test_snapshot_of_repo_without_commits(tmp_path: Path) -> None:
    folder = tmp_path / "fresh"
    folder.mkdir()
    git(folder, "init", "-q")
    (folder / "a.txt").write_text("a\n", encoding="utf-8")
    base = g.snapshot(folder, tmp_path / "scratch")
    worktree = tmp_path / "wt"
    g.add_worktree(folder, worktree, base)
    assert (worktree / "a.txt").exists()
    g.remove_worktree(folder, worktree)
