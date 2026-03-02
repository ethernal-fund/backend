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
    datefmt="%Y-%m-%d %H:%M:%S",
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.DEBUG:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logging.info("Tablas creadas (modo DEBUG)")

    logging.info("Starting Ethernal Backend API [%s]", settings.ENVIRONMENT)
    logging.info("Database ready at %s", datetime.utcnow().isoformat())
    yield
    await close_db()
    logging.info("Application shutdown complete")

app = FastAPI(
    title="Ethernal Backend API",
    description="API para gestión de fondos de retiro con integración blockchain (USDC, protocols, treasury)",
    version="production",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
    openapi_tags=[
        {"name": "users", "description": "Autenticación y perfil de usuarios (wallet-based)"},
        {"name": "funds", "description": "Fondos personales de retiro"},
        {"name": "treasury", "description": "Gestión de fees y solicitudes de retiro anticipado"},
        {"name": "protocols", "description": "Protocolos DeFi soportados"},
        {"name": "admin", "description": "Panel administrativo (stats, indexer)"},
        {"name": "contact", "description": "Formulario de contacto"},
        {"name": "survey", "description": "Encuestas anónimas y follow-up"},
    ],
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

app.include_router(users.router, prefix="/v1/users", tags=["users"])
app.include_router(funds.router, prefix="/v1/funds", tags=["funds"])
app.include_router(treasury.router, prefix="/v1/treasury", tags=["treasury"])
app.include_router(protocols.router, prefix="/v1/protocols", tags=["protocols"])
app.include_router(admin.router, prefix="/v1/admin", tags=["admin"])
app.include_router(contact.router, prefix="/v1/contact", tags=["contact"])
app.include_router(survey.router, prefix="/v1/surveys", tags=["survey"])

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

@app.get("/")
async def root():
    return {
        "message": "Ethernal Backend API - v1",
        "docs": "/docs",
        "health": "/health",
        "environment": settings.ENVIRONMENT,
    }