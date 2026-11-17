from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
"""The `dashboard/` folder."""

DEFAULT_ALLOWED_HOSTS = ("127.0.0.1", "localhost", "[::1]")


@dataclass(frozen=True)
class Config:
    data_dir: Path
    frontend_dir: Path = ROOT / "frontend"
    port: int = 8765
    fake_cli: bool = False
    """Demo/test mode: run the bundled fake CLI instead of the real `claude` / `codex`."""
    allowed_hosts: tuple[str, ...] = field(default=DEFAULT_ALLOWED_HOSTS)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "dashboard.db"


def default_data_dir() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "claude-codex-dash"
    return Path.home() / ".local" / "share" / "claude-codex-dash"


def load_config() -> Config:
    data_dir = os.environ.get("DASH_DATA_DIR")
    port = int(os.environ.get("DASH_PORT", "8765"))
    return Config(
        data_dir=Path(data_dir) if data_dir else default_data_dir(),
        port=port,
        fake_cli=os.environ.get("DASH_FAKE_CLI", "") == "1",
    )
