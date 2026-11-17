"""Run the dashboard: `python -m app` (from the backend folder)."""

from __future__ import annotations

import logging

import uvicorn

from .config import load_config
from .main import create_app


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    config = load_config()
    mode = " (demo mode: fake CLIs)" if config.fake_cli else ""
    print(f"Dashboard: http://127.0.0.1:{config.port}{mode}")
    print(f"Data: {config.db_path}")
    uvicorn.run(create_app(config), host="127.0.0.1", port=config.port, log_level="warning")


if __name__ == "__main__":
    main()
