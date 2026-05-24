"""Alembic migration environment.

Reads the database URL from the KBO_DATABASE_URL environment variable,
falling back to the alembic.ini sqlalchemy.url value only as a last resort.
All ORM models are imported here so that Base.metadata is fully populated
before autogenerate inspects it.
"""

import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

import app.models  # noqa: F401 — side-effect import populates Base.metadata
from alembic import context

# Import Base and all models so metadata is registered
from app.db.base import Base

# Alembic Config object gives access to alembic.ini values
config = context.config

# Override sqlalchemy.url from environment if set
_db_url = os.environ.get("KBO_DATABASE_URL")
if _db_url:
    config.set_main_option("sqlalchemy.url", _db_url)

# Set up Python logging from the config file
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (no live DB connection required)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # required for SQLite ALTER support
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode with a live engine connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # required for SQLite ALTER support
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
