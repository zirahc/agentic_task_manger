from __future__ import annotations

import logging
import mimetypes
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

from .api.routes import ROUTES, Context
from .config import Config, load_config
from .db import Database
from .errors import ApiError
from .services.supervisor import Supervisor

log = logging.getLogger(__name__)

API_HEADER = b"x-dash"


class Guard:
    """Blocks DNS rebinding (Host check) and cross-site requests to the API.

    A browser page on another site cannot add the custom `X-Dash` header without a
    CORS preflight, which this server never approves. That keeps other websites
    from starting CLI sessions on this machine.
    """

    def __init__(self, app: ASGIApp, allowed_hosts: tuple[str, ...]):
        self.app = app
        self.allowed_hosts = allowed_hosts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = dict(scope["headers"])
            host = headers.get(b"host", b"").decode("latin-1")
            hostname = host.rsplit(":", 1)[0] if not host.endswith("]") else host
            if hostname not in self.allowed_hosts:
                await _deny("host-not-allowed", "Requests must use localhost.")(
                    scope, receive, send
                )
                return
            if scope["path"].startswith("/api/") and headers.get(API_HEADER) != b"1":
                await _deny("missing-header", "API requests need the X-Dash: 1 header.")(
                    scope, receive, send
                )
                return
        await self.app(scope, receive, send)


def _deny(code: str, message: str) -> JSONResponse:
    return JSONResponse({"code": code, "message": message}, status_code=403)


async def api_error(_request: Request, exc: Exception) -> Response:
    if isinstance(exc, ApiError):
        return JSONResponse(exc.to_json(), status_code=exc.status)
    return JSONResponse({"code": "internal-error", "message": "Unexpected error."}, status_code=500)


async def http_error(request: Request, exc: Exception) -> Response:
    status = exc.status_code if isinstance(exc, HTTPException) else 500
    if request.url.path.startswith("/api/"):
        code = "not-found" if status == 404 else "http-error"
        return JSONResponse(
            {"code": code, "message": str(getattr(exc, "detail", ""))}, status_code=status
        )
    return Response(status_code=status)


def create_app(config: Config | None = None) -> Starlette:
    config = config or load_config()
    # Windows can map .js to text/plain in the registry; module scripts then fail to load.
    mimetypes.add_type("text/javascript", ".js")
    mimetypes.add_type("text/css", ".css")

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        config.data_dir.mkdir(parents=True, exist_ok=True)
        db = Database(config.db_path)
        supervisor = Supervisor(db, fake=config.fake_cli, work_root=config.data_dir)
        supervisor.start()
        app.state.ctx = Context(config=config, db=db, supervisor=supervisor)
        try:
            yield
        finally:
            supervisor.stop()
            db.close()

    app = Starlette(
        routes=[
            *ROUTES,
            Mount("/", StaticFiles(directory=config.frontend_dir, html=True), name="ui"),
        ],
        exception_handlers={ApiError: api_error, HTTPException: http_error},
        lifespan=lifespan,
    )
    app.add_middleware(Guard, allowed_hosts=config.allowed_hosts)
    return app
