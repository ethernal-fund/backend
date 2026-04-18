"""
api/main.py

Punto de entrada de la aplicación FastAPI.

Responsabilidades de este archivo:
  - Configurar logging antes de cualquier import que loguee
  - Inicializar Sentry si está habilitado
  - Definir el lifespan (startup / shutdown de recursos)
  - Registrar middlewares en el orden correcto
  - Registrar routers y exception handlers
  - Exponer endpoints de sistema (/health, /)

Lo que NO hace este archivo:
  - Lógica de negocio (vive en services/)
  - Queries a DB (viven en repositories/)
  - Configuración de variables (vive en config.py)
"""

from __future__ import annotations

import logging
import logging.config
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from api.config import settings
from api.core.exceptions import (
    EthernalException,
    ethernal_exception_handler,
    global_exception_handler,
)
from api.core.redis import close_redis, get_redis, ping_redis
from api.db.base import Base
from api.db.session import close_db, engine, get_db
from api.v1.routers import admin, contact, funds, protocols, survey, treasury, users


# ── Logging ───────────────────────────────────────────────────────────────────
# Configurar ANTES de cualquier otro import que use logging.getLogger().
# En producción emite JSON para que Render / Datadog / cualquier agregador
# pueda parsear estructuradamente. En desarrollo, texto legible.

def _configure_logging() -> None:
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    if settings.ENVIRONMENT == "production":
        # JSON estructurado — python-json-logger está en requirements.txt
        try:
            from pythonjsonlogger import jsonlogger  # type: ignore

            handler = logging.StreamHandler()
            handler.setFormatter(
                jsonlogger.JsonFormatter(
                    fmt="%(asctime)s %(name)s %(levelname)s %(message)s",
                    datefmt="%Y-%m-%dT%H:%M:%S",
                )
            )
            logging.root.setLevel(log_level)
            logging.root.handlers = [handler]

            # Silenciar loggers muy verbosos de librerías externas
            logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
            logging.getLogger("web3").setLevel(logging.WARNING)
            logging.getLogger("urllib3").setLevel(logging.WARNING)
            return
        except ImportError:
            pass  # fallback a formato texto si no está instalado

    # Desarrollo: texto legible con colores implícitos del terminal
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)-8s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.INFO if settings.DEBUG else logging.WARNING
    )


_configure_logging()
logger = logging.getLogger(__name__)


# ── Sentry ────────────────────────────────────────────────────────────────────
# Inicializar antes del lifespan para capturar errores de startup.

if settings.SENTRY_ENABLED and settings.SENTRY_DSN:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

        sentry_sdk.init(
            dsn=settings.SENTRY_DSN,
            environment=settings.ENVIRONMENT,
            traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
            integrations=[
                FastApiIntegration(transaction_style="endpoint"),
                SqlalchemyIntegration(),
            ],
            # No enviar datos personales a Sentry
            send_default_pii=False,
        )
        logger.info("Sentry initialized (environment=%s)", settings.ENVIRONMENT)
    except ImportError:
        logger.warning("sentry-sdk not installed — Sentry disabled")


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Gestiona el ciclo de vida de los recursos compartidos.

    Startup:
      1. Crear tablas si DEBUG=True (solo para desarrollo local)
      2. Verificar conectividad con Redis (crítico — auth depende de él)
      3. Verificar conectividad con DB (crítico)
      4. Loguear estado inicial

    Shutdown:
      1. Cerrar pool de Redis
      2. Cerrar pool de DB
    """
    # ── Startup ──────────────────────────────────────────────────────────────
    logger.info(
        "Starting %s v%s [%s]",
        settings.APP_NAME,
        settings.APP_VERSION,
        settings.ENVIRONMENT,
    )

    # Crear tablas en modo DEBUG (nunca en producción — usar Alembic)
    if settings.DEBUG:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Tables created/verified (DEBUG mode)")

    # Verificar Redis — crítico: la auth depende de él
    try:
        redis = await get_redis()
        await redis.ping()
        logger.info("Redis OK")
    except Exception as exc:
        # En producción, si Redis no arranca, el deploy debe fallar
        # para que Render reinicie el servicio en lugar de servir
        # un API que no puede autenticar a nadie.
        logger.critical("Redis unavailable at startup: %s", exc)
        if settings.ENVIRONMENT == "production":
            raise RuntimeError(f"Redis required in production: {exc}") from exc
        logger.warning("Continuing without Redis (non-production environment)")

    # Verificar DB
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        logger.info("Database OK")
    except Exception as exc:
        logger.critical("Database unavailable at startup: %s", exc)
        raise RuntimeError(f"Database connection failed: {exc}") from exc

    logger.info("Startup complete — ready to serve requests")

    # ── Yield: aplicación corriendo ───────────────────────────────────────────
    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    logger.info("Shutting down...")
    await close_redis()
    await close_db()
    logger.info("Shutdown complete")


# ── Aplicación ────────────────────────────────────────────────────────────────

app = FastAPI(
    title=settings.APP_NAME,
    description=(
        "API para gestión de fondos de retiro con integración blockchain. "
        "Autenticación basada en firma ECDSA de wallet Ethereum."
    ),
    version=settings.APP_VERSION,
    docs_url="/docs" if not settings.ENVIRONMENT == "production" else None,
    redoc_url="/redoc" if not settings.ENVIRONMENT == "production" else None,
    openapi_url="/openapi.json" if not settings.ENVIRONMENT == "production" else None,
    lifespan=lifespan,
    openapi_tags=[
        {"name": "users",     "description": "Autenticación y perfil de usuarios (wallet-based)"},
        {"name": "funds",     "description": "Fondos personales de retiro"},
        {"name": "treasury",  "description": "Gestión de fees y solicitudes de retiro anticipado"},
        {"name": "protocols", "description": "Protocolos DeFi soportados"},
        {"name": "admin",     "description": "Panel administrativo (stats, indexer)"},
        {"name": "contact",   "description": "Formulario de contacto"},
        {"name": "survey",    "description": "Encuestas anónimas y follow-up"},
    ],
)


# ── Middlewares ───────────────────────────────────────────────────────────────
# El orden importa: se ejecutan en orden inverso al de registro.
# El último en registrarse es el primero en ejecutarse.

# CORS — debe ser el primero en procesar (último en registrarse)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=[
        "X-RateLimit-Limit",
        "X-RateLimit-Remaining",
        "X-RateLimit-Reset",
    ],
)


# ── Exception handlers ────────────────────────────────────────────────────────

app.add_exception_handler(EthernalException, ethernal_exception_handler)
app.add_exception_handler(Exception, global_exception_handler)


# ── Routers ───────────────────────────────────────────────────────────────────

API_PREFIX = "/api/v1"

app.include_router(users.router,     prefix=API_PREFIX, tags=["users"])
app.include_router(funds.router,     prefix=API_PREFIX, tags=["funds"])
app.include_router(treasury.router,  prefix=API_PREFIX, tags=["treasury"])
app.include_router(protocols.router, prefix=API_PREFIX, tags=["protocols"])
app.include_router(admin.router,     prefix=API_PREFIX, tags=["admin"])
app.include_router(contact.router,   prefix=API_PREFIX, tags=["contact"])
app.include_router(survey.router,    prefix=API_PREFIX, tags=["survey"])


# ── Endpoints de sistema ──────────────────────────────────────────────────────

@app.get("/health", tags=["system"], include_in_schema=True)
async def health(request: Request) -> JSONResponse:
    """
    Health check con diagnóstico de todos los componentes críticos.

    Usado por Render para determinar si el servicio está listo.
    Retorna 200 si todos los componentes críticos están OK.
    Retorna 503 si algún componente crítico falló.

    Componentes:
      - database: crítico (503 si falla)
      - redis:    crítico (503 si falla)
      - rpc:      no crítico (el API funciona sin RPC, degradado)
    """
    checks: dict[str, str] = {}
    critical_ok = True

    # ── Database ──────────────────────────────────────────────────────────────
    try:
        # Usar la sesión del pool existente — no crear una nueva conexión
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {type(exc).__name__}"
        critical_ok = False
        logger.error("Health check — database failed: %s", exc)

    # ── Redis ─────────────────────────────────────────────────────────────────
    redis_ok = await ping_redis()
    checks["redis"] = "ok" if redis_ok else "error"
    if not redis_ok:
        critical_ok = False
        logger.error("Health check — Redis failed")

    # ── RPC (Blockchain) ──────────────────────────────────────────────────────
    try:
        from api.services.blockchain_service import get_blockchain_service
        checks["rpc"] = "ok" if get_blockchain_service().is_connected() else "degraded"
    except Exception as exc:
        checks["rpc"] = f"degraded: {type(exc).__name__}"
        # RPC no es crítico — el API puede funcionar sin él (sin sync blockchain)

    status_code = 200 if critical_ok else 503
    body = {
        "status":      "healthy" if critical_ok else "degraded",
        "checks":      checks,
        "environment": settings.ENVIRONMENT,
        "version":     settings.APP_VERSION,
        "timestamp":   datetime.now(timezone.utc).isoformat(),
    }

    return JSONResponse(status_code=status_code, content=body)


@app.get("/", tags=["system"], include_in_schema=False)
async def root() -> dict:
    """Endpoint raíz — útil para verificar que el servicio responde."""
    return {
        "service":     settings.APP_NAME,
        "version":     settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
        "health":      "/health",
        # Docs solo en desarrollo
        "docs": "/docs" if settings.ENVIRONMENT != "production" else None,
    }