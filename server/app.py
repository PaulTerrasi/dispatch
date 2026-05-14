from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from digest.log import configure_logging
from server.config import auth_token, make_store, resolve_ssm_env_vars, static_dir
from server.middleware import BearerAuthMiddleware
from server.routes_chat import router as chat_router
from server.routes_digest import router as digest_router
from server.routes_feedback import router as feedback_router

configure_logging()
log = structlog.get_logger(__name__)


resolve_ssm_env_vars()  # no-op locally; resolves SSM paths before any module-level code runs


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    store = make_store()
    store.ensure_layout()
    app.state.store = store
    log.info("server.startup", store=type(store).__name__)
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Dispatch", version="0.1.0", lifespan=lifespan)
    app.add_middleware(BearerAuthMiddleware, token=auth_token())

    api_prefix = "/api"
    from fastapi import APIRouter

    api = APIRouter(prefix=api_prefix)
    api.include_router(digest_router)
    api.include_router(feedback_router)
    api.include_router(chat_router)
    app.include_router(api)

    # Mount the built PWA at root if it exists. Falls back to a JSON note so
    # `make dev` (where vite serves the PWA on its own port) still works.
    sd = static_dir()
    if sd.exists() and (sd / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=str(sd / "assets")), name="assets")

        @app.get("/")
        async def root_index() -> FileResponse:
            return FileResponse(str(sd / "index.html"))

        @app.get("/{path:path}")
        async def spa_fallback(path: str) -> FileResponse:
            target = sd / path
            if target.is_file():
                return FileResponse(str(target))
            return FileResponse(str(sd / "index.html"))
    else:

        @app.get("/")
        def root_dev() -> dict[str, str]:
            return {
                "status": "dev",
                "note": (
                    "PWA not built; run `make build` or use `make dev` "
                    "for the Vite dev server on port 5173."
                ),
            }

    return app


app = create_app()


# Lambda invocation goes through AWS Lambda Web Adapter (see Dockerfile.lambda):
# LWA proxies HTTP requests to uvicorn running on PORT=8080. There is no
# Python handler to register — `app` above is what serves both Lambda and
# `make dev` (uvicorn with --reload).
