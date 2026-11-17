from __future__ import annotations

import subprocess
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from app.config import DEFAULT_ALLOWED_HOSTS, Config
from app.main import create_app


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    config = Config(
        data_dir=tmp_path / "data",
        fake_cli=True,
        allowed_hosts=(*DEFAULT_ALLOWED_HOSTS, "testserver"),
    )
    with TestClient(create_app(config), headers={"X-Dash": "1"}) as test_client:
        yield test_client


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    folder = tmp_path / "repo"
    folder.mkdir()
    return folder


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True, timeout=60
    )
    return result.stdout


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """A git repository with one commit, plus an uncommitted change and an untracked file."""
    folder = tmp_path / "gitrepo"
    folder.mkdir()
    git(folder, "init", "-q")
    git(folder, "config", "user.email", "test@example.com")
    git(folder, "config", "user.name", "Test")
    git(folder, "config", "core.autocrlf", "false")
    (folder / "app.js").write_text("console.log('v1');\n", encoding="utf-8")
    git(folder, "add", "app.js")
    git(folder, "commit", "-q", "-m", "init")
    (folder / "app.js").write_text("console.log('v2');\n", encoding="utf-8")  # uncommitted
    (folder / "draft.md").write_text("notes\n", encoding="utf-8")  # untracked
    return folder


def wait_for(check: Callable[[], Any], timeout: float = 15.0) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("condition not met in time")
