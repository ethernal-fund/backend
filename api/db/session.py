from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from typing import AsyncGenerator
import logging

from api.config import settings

logger = logging.getLogger(__name__)

# Supabase free tier: máx 15 conexiones simultáneas en pooler Transaction mode
# pool_size=5 + max_overflow=10 = 15 conexiones máx → seguro para free tier
# En plan Pro de Supabase podés subir a pool_size=10, max_overflow=20
# Mejor opcion Railway en produccion, no tiene limitaciones de conexiones simultáneas 
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,       # verifica conexiones muertas antes de usarlas
    pool_recycle=1800,        # recicla conexiones cada 30min (Supabase cierra idle a los 60min)
    pool_timeout=30,    
    connect_args={
        "statement_cache_size":0,
    },   
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

async def close_db():
    await engine.dispose()
    logger.info("Database connection pool closed")

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()