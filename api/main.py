from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from api.config import settings
from api.core.exceptions import (
    EthernalException,
    ethernal_exception_handler,
    global_exception_handler,
)
from api.core.redis import close_redis, get_redis
from api.db.base import Base
from api.db.session import close_db, engine, AsyncSessionLocal
from api.v1.routers.routers import api_router

def _configure_logging() -> None:
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    if settings.ENVIRONMENT == "production":
        try:
            from pythonjsonlogger import jsonlogger
            handler = logging.StreamHandler()
            handler.setFormatter(
                jsonlogger.JsonFormatter(
                    fmt="%(asctime)s %(name)s %(levelname)s %(message)s",
                    datefmt="%Y-%m-%dT%H:%M:%S",
                )
            )
            logging.root.setLevel(log_level)
            logging.root.handlers = [handler]
            logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
            logging.getLogger("web3").setLevel(logging.WARNING)
            return
        except ImportError:
            pass

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)-8s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )

_configure_logging()
logger = logging.getLogger(__name__)


async def _indexer_loop() -> None:
    from api.db.session import get_db_context
    from api.services.indexer_service import IndexerService

    logger.info(
        "Indexer loop started (interval=%ds)",
        settings.INDEXER_INTERVAL_SECONDS,
    )

    while True:
        cycle_start = asyncio.get_event_loop().time()

        try:
            async with _indexer_lock():
                async with get_db_context() as db:
                    result = await IndexerService(db).run_cycle()

            indexed = result.get("indexed", 0)
            if indexed > 0:
                logger.info(
                    "Indexer cycle OK — %d events indexed | to_block=%s factory=%s fund=%s fee=%s",
                    indexed,
                    result.get("to_block"),
                    result.get("fund_created"),
                    result.get("fund_events"),
                    result.get("fee_events"),
                )
            else:
                logger.debug("Indexer cycle OK — no new events (up to date)")

        except asyncio.CancelledError:
            logger.info("Indexer loop cancelled — shutting down")
            raise

        except Exception as exc:
            logger.error("Indexer cycle failed: %s", exc, exc_info=True)

        elapsed = asyncio.get_event_loop().time() - cycle_start
        sleep_for = max(0.0, settings.INDEXER_INTERVAL_SECONDS - elapsed)

        if elapsed > settings.INDEXER_INTERVAL_SECONDS:
            logger.warning(
                "Indexer cycle took %.1fs — longer than interval (%ds). "
                "Starting next cycle immediately.",
                elapsed,
                settings.INDEXER_INTERVAL_SECONDS,
            )
        else:
            logger.debug(
                "Indexer cycle took %.1fs — sleeping %.1fs until next cycle",
                elapsed,
                sleep_for,
            )

        await asyncio.sleep(sleep_for)

_INDEXER_ASYNCIO_LOCK = asyncio.Lock()

@asynccontextmanager
async def _indexer_lock():
    """
    Context manager que adquiere el lock global del indexer.

    Si el lock ya está tomado (ej: ciclo manual en progreso), el await aquí
    bloqueará la corutina hasta que el lock se libere, en lugar de solapar
    dos ciclos de indexado simultáneos sobre la misma DB.

    El lock se libera automáticamente al salir del bloque `async with`, incluso
    si hay una excepción (asyncio.Lock garantiza esto).
    """
    async with _INDEXER_ASYNCIO_LOCK:
        yield

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info(
        "🚀 Starting %s v%s [%s]",
        settings.APP_NAME,
        settings.APP_VERSION,
        settings.ENVIRONMENT,
    )
    if settings.DEBUG:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("✅ Database tables created/verified")

    try:
        redis = await get_redis()
        await redis.ping()
        logger.info("✅ Redis OK")
    except Exception as e:
        logger.error("Redis unavailable: %s", e)
        if settings.ENVIRONMENT == "production":
            raise RuntimeError("Redis required in production") from e

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        logger.info("✅ Database OK")
    except Exception as e:
        logger.critical("Database unavailable: %s", e)
        raise

    indexer_task = asyncio.create_task(_indexer_loop())
    logger.info("✅ Indexer background task started")
    logger.info("✅ Application started successfully")

    yield

    logger.info("Shutting down...")
    indexer_task.cancel()
    try:
        await indexer_task
    except asyncio.CancelledError:
        pass
    logger.info("Indexer task stopped")

    await close_redis()
    await close_db()


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    docs_url="/docs"  if settings.ENVIRONMENT != "production" else None,
    redoc_url="/redoc" if settings.ENVIRONMENT != "production" else None,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_exception_handler(EthernalException, ethernal_exception_handler)
app.add_exception_handler(Exception, global_exception_handler)
API_PREFIX = "/api/v1"
app.include_router(api_router, prefix=API_PREFIX)

@app.get("/health")
async def health():
    return {
        "status":      "healthy",
        "service":     settings.APP_NAME,
        "version":     settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
    }

@app.get("/")
async def root():
    return {
        "service":     settings.APP_NAME,
        "version":     settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
        "health":      "/health",
    }