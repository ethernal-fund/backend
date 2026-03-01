from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from datetime import datetime
import logging

from api.v1.routers import users, funds, treasury, protocols, admin, contact, survey
from api.config import settings
from api.db.session import engine, close_db
from api.db.base import Base
from api.core.exceptions import (
    EthernalException,
    ethernal_exception_handler,
    global_exception_handler,
)

if settings.SENTRY_ENABLED and settings.SENTRY_DSN:
    import sentry_sdk
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
        environment=settings.ENVIRONMENT,
    )

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Starting {settings.APP_NAME} [{settings.ENVIRONMENT}]")
    logger.info("Database ready")
    yield
    await close_db()
    logger.info("Shutdown complete")

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    lifespan=lifespan,
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_exception_handler(EthernalException, ethernal_exception_handler)
app.add_exception_handler(Exception, global_exception_handler)

from api.v1.routers import users, funds, treasury, protocols, admin

app.include_router(users.router,     prefix="/v1")
app.include_router(funds.router,     prefix="/v1")
app.include_router(treasury.router,  prefix="/v1")
app.include_router(protocols.router, prefix="/v1")
app.include_router(admin.router,     prefix="/v1")
app.include_router(contact.router, prefix="/v1")
app.include_router(survey.router,  prefix="/v1")

@app.get("/")
async def root():
    return {
        "name":        settings.APP_NAME,
        "version":     settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
        "chain_id":    settings.CHAIN_ID,
        "endpoints": {
            "auth":         "POST /v1/users/nonce  →  POST /v1/users/auth",
            "profile":      "GET  /v1/users/me",
            "survey":       "POST /v1/users/survey",
            "fund":         "GET  /v1/funds/me",
            "transactions": "GET  /v1/funds/me/transactions",
            "sync":         "POST /v1/funds/sync",
            "treasury":     "GET  /v1/treasury/stats",
            "early_retire": "POST /v1/treasury/early-retirement/request",
            "protocols":    "GET  /v1/protocols/",
            "admin":        "GET  /v1/admin/stats",
            "health":       "GET  /health",
        },
    }

@app.get("/health")
async def health():
    try:
        from api.services.blockchain_service import BlockchainService
        blockchain = BlockchainService()
        rpc_ok = blockchain.is_connected()
    except Exception:
        rpc_ok = False

    return {
        "status":        "healthy" if rpc_ok else "degraded",
        "rpc_connected": rpc_ok,
        "environment":   settings.ENVIRONMENT,
        "timestamp":     datetime.utcnow().isoformat(),
    }