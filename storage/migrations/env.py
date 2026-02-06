"""Alembic environment configuration.

This module reads database configuration from environment variables
to avoid storing credentials in version control.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from env_config import get_database_url

# Get database URL from environment
DATABASE_URL = get_database_url()

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Metadata for autogenerate support (not used in Phase 1)
target_metadata = None


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    Uses DATABASE_URL from environment variables.
    """
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    Uses DATABASE_URL from environment variables.
    """
    connectable = engine_from_config(
        {"sqlalchemy.url": DATABASE_URL},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
