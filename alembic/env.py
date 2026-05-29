import asyncio
import os
import sys
from logging.config import fileConfig

from sqlalchemy.ext.asyncio import create_async_engine
from alembic import context

# ── Path ──────────────────────────────────────────────────────────────────────
# Agrega backend/ al path para que los imports de api.* funcionen
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Settings (carga el .env automáticamente vía pydantic-settings) ────────────
from api.config import settings

# ── Importar todos los modelos para que Alembic los detecte ──────────────────
# El __init__.py registra todos los modelos en Base.metadata
from api.db.base import Base
import api.db.models  

# ── Alembic config ────────────────────────────────────────────────────────────
config = context.config

# Inyectar la DATABASE_URL desde settings (ignora lo que haya en alembic.ini)
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

# Configurar logging desde alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

def run_migrations_offline() -> None:
    """
    Genera el SQL sin conectarse a la DB.
    Útil para revisar las migraciones antes de aplicarlas.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()

def do_run_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
    )
    with context.begin_transaction():
        context.run_migrations()

async def run_migrations_online() -> None:
    connectable = create_async_engine(settings.DATABASE_URL)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()

if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())