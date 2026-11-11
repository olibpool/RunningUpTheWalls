from __future__ import annotations

from fastapi import FastAPI

from runcoach.web.routes import router as api_router


def create_app() -> FastAPI:
    app = FastAPI(title="Coachal")
    app.include_router(api_router)
    return app


app = create_app()
