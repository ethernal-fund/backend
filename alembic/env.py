"""
alembic/env.py — Backend (app principal)

Configuración de Alembic para el backend principal.
Apunta al schema 'public' de PostgreSQL.
"""
import asyncio
import os
import ssl
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import create_async_engine

from api.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

_SCHEMA = "public"  # ← corregido


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
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def run_migrations_offline() -> None:
    url = _get_db_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table="alembic_version",
        version_table_schema=_SCHEMA,
        include_schemas=True,
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
            "statement_cache_size": 0,  # ← fix para pgbouncer de Supabase
        },
    )

    async with connectable.connect() as connection:
        await connection.execute(text(f"SET search_path TO {_SCHEMA}"))
        await connection.run_sync(_configure_and_run)

    await connectable.dispose()


def _configure_and_run(sync_conn) -> None:
    context.configure(
        connection=sync_conn,
        target_metadata=target_metadata,
        version_table="alembic_version",
        version_table_schema=_SCHEMA,
        include_schemas=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    asyncio.run(_run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()