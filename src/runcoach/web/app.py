from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from runcoach.config import ConfigNode, load_config
from runcoach.web.routes import router
from runcoach.web.routes.settings import _sync_in_background, begin_sync, sync_due

log = logging.getLogger(__name__)


async def same_origin_posts(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Reject cross-site form posts: a POST's Origin/Referer host must match the Host."""
    if request.method == "POST":
        source = request.headers.get("origin") or request.headers.get("referer")
        # shortcut: trusts X-Forwarded-Host from the local proxy; verify behind tailscale serve.
        host = request.headers.get("x-forwarded-host") or request.headers.get("host")
        if source and urlsplit(source).netloc != host:
            return PlainTextResponse("Cross-site request blocked", status_code=403)
    return await call_next(request)


class CachedStaticFiles(StaticFiles):
    """Static files with a one-day browser cache; HTML pages stay uncached."""

    def file_response(self, *args: Any, **kwargs: Any) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "public, max-age=86400"
        return response


@asynccontextmanager
async def _auto_sync(app: FastAPI) -> AsyncIterator[None]:
    """Sync in the background every `[sync] auto_minutes` while the app runs."""

    async def loop() -> None:
        while True:
            state = app.state
            try:
                if sync_due(state, datetime.now(timezone.utc)) and begin_sync(state):
                    await asyncio.to_thread(_sync_in_background, state.db_path, state.cfg, state)
            except Exception:  # e.g. the database is briefly locked; try again next minute
                log.exception("automatic sync check failed")
            await asyncio.sleep(60)

    task = asyncio.create_task(loop())
    yield
    task.cancel()


def create_app(db_path: Path | str | None = None, cfg: ConfigNode | None = None) -> FastAPI:
    """Build the web app; tests pass their own database path and config."""
    app = FastAPI(title="Coachal", lifespan=_auto_sync)
    app.state.db_path = db_path
    app.state.cfg = cfg if cfg is not None else load_config()
    app.state.sync_lock = threading.Lock()  # one worker process, so an in-memory lock is enough
    app.state.garmin_pending = None  # a Garmin login waiting for its one-time code
    app.middleware("http")(same_origin_posts)
    app.add_middleware(GZipMiddleware, minimum_size=500)
    static = CachedStaticFiles(directory=Path(__file__).parent / "static")
    app.mount("/static", static, name="static")
    app.include_router(router)
    return app


app = create_app()
