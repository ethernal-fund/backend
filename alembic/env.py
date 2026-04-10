"""
alembic/env.py — App Principal

Configuración de Alembic para la app principal (usuarios, fondos, protocolos).
Apunta exclusivamente al schema 'public' de PostgreSQL.
Nunca toca el schema 'faucet' que pertenece al servicio faucet-api.
"""
import asyncio
import os
import ssl
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import create_async_engine

# ── Importar modelos de la app principal ─────────────────────────────────────
# Ajustar el import según la estructura real del repo
from app.models import Base

config          = context.config
target_metadata = Base.metadata

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _get_db_url() -> str:
    url = os.environ.get("DATABASE_URL") or config.get_main_option("sqlalchemy.url")
    if not url:
        raise ValueError("DATABASE_URL no configurada")

    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

    return url


def _get_ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode    = ssl.CERT_NONE
    return ctx


def run_migrations_offline() -> None:
    url = _get_db_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # Solo operar en 'public' — nunca tocar 'faucet'
        version_table="alembic_version",
        version_table_schema="public",
        include_schemas=False,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    connectable = create_async_engine(
        _get_db_url(),
        poolclass=pool.NullPool,
        connect_args={
            "ssl": _get_ssl_context(),
            "server_settings": {"client_encoding": "utf8"},
        },
    )

    async with connectable.connect() as connection:
        # search_path solo en 'public' — el schema 'faucet' es invisible
        await connection.execute(text("SET search_path TO public"))

        await connection.run_sync(_configure_and_run)

    await connectable.dispose()


def _configure_and_run(sync_conn) -> None:
    context.configure(
        connection=sync_conn,
        target_metadata=target_metadata,
        # alembic_version vive en 'public'
        version_table="alembic_version",
        version_table_schema="public",
        # include_schemas=False garantiza que Alembic ignore schemas ajenos
        include_schemas=False,
        # Excluir explícitamente el schema 'faucet' del autogenerate
        include_name=lambda name, type_, parent_names: (
            False if type_ == "schema" and name == "faucet"
            else True
        ),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    asyncio.run(_run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()