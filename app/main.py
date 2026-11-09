"""Aplicação web (painel). Inicie com `make dev`."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import OperationalError, ProgrammingError
from starlette.middleware.sessions import SessionMiddleware

from app.agents.registry import ensure_agents
from app.config import get_settings
from app.core.db import get_sessionmaker
from app.fake_data.seed import refresh_demo_if_stale
from app.web.auth import CsrfFailed, LoginRequired, csrf_failed_handler, login_required_handler
from app.web.middleware import SecurityHeadersMiddleware
from app.web.routes import agents, auth, dashboard, integrations, questions, reports, sections

logger = logging.getLogger("painel")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    settings.check_ready()
    with get_sessionmaker()() as db:
        try:
            ensure_agents(db)
        except (OperationalError, ProgrammingError) as exc:
            raise RuntimeError("Banco sem tabelas. Rode `make migrate` antes de subir o painel.") from exc
        if settings.dry_run and refresh_demo_if_stale(db):
            logger.info("Modo simulação: dados de exemplo de hoje gerados.")
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Painel de Agentes", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    # Ordem: o último adicionado roda primeiro.
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key or "inseguro-apenas-para-erro-de-config",
        session_cookie="painel_sessao",
        max_age=settings.session_max_age_hours * 3600,
        same_site="lax",
        https_only=settings.session_cookie_secure,
    )

    app.add_exception_handler(LoginRequired, login_required_handler)
    app.add_exception_handler(CsrfFailed, csrf_failed_handler)

    static_dir = Path(__file__).parent / "web" / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    app.include_router(auth.router)
    app.include_router(dashboard.router)
    app.include_router(sections.router)
    app.include_router(integrations.router)
    app.include_router(questions.router)
    app.include_router(agents.router)
    app.include_router(reports.router)

    @app.get("/saude", include_in_schema=False)
    def health():
        """Verificação de saúde usada pelo Docker. Não exige login e não expõe dados."""
        try:
            with get_sessionmaker()() as db:
                db.execute(text("SELECT 1"))
        except Exception:  # noqa: BLE001 - qualquer falha de banco = fora do ar
            return JSONResponse({"status": "erro", "banco": "indisponível"}, status_code=503)
        return {"status": "ok"}

    return app


app = create_app()
