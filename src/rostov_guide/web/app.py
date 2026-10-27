"""FastAPI-приложение: один асинхронный процесс отдаёт API, поток SSE и статику фронтенда."""

from __future__ import annotations

import logging
import mimetypes
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..agent.factory import build_runner
from ..config import get_settings
from ..services import build_services
from . import auth, routes
from .runs import RunManager

log = logging.getLogger(__name__)
STATIC = Path(__file__).resolve().parents[1] / "static"

# На Windows реестр часто отдаёт .js как text/plain, а браузер не запускает ES-модули
# с таким типом. Типы статики задаём явно, не полагаясь на систему.
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    # litellm импортируется ~5 с: платим один раз при старте, до приёма запросов, а не на первом
    # вопросе. Импорт в фоновом потоке не годится — он гоняется с импортами из цикла событий.
    import litellm  # noqa: F401

    services = await build_services(settings)
    await services.ensure_index(background=True)
    users = auth.UserStore(settings.app_db)
    await users.init()
    runner = build_runner(services)
    app.state.settings = settings
    app.state.services = services
    app.state.users = users
    app.state.jwt_secret = settings.jwt_secret()
    app.state.login_limiter = auth.LoginLimiter()
    app.state.runner = runner
    app.state.runs = RunManager(runner, services.places, settings)
    log.info("Ростов-гид готов: http://%s:%s · модель %s · %d мест",
             settings.host, settings.port, settings.llm_model, len(services.places))
    try:
        yield
    finally:
        await app.state.runs.shutdown()
        await runner.close()
        await services.aclose()


def create_app() -> FastAPI:
    app = FastAPI(title="Ростов-гид", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")

    @app.middleware("http")
    async def same_origin_guard(request: Request, call_next):
        """Изменяющие запросы к API — только со своего же сайта (защита от CSRF поверх SameSite)."""
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.url.path.startswith("/api/"):
            origin = request.headers.get("origin")
            if origin and urlparse(origin).netloc != request.headers.get("host"):
                return JSONResponse({"detail": "Запрос с чужого сайта отклонён"}, status_code=403)
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            # без сборщика у файлов нет хешей в именах: браузер переспрашивает по ETag (304),
            # поэтому после обновления кода никто не застревает на старом JS
            response.headers["Cache-Control"] = "no-cache"
        return response

    app.include_router(auth.router)
    app.include_router(routes.router)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    return app
