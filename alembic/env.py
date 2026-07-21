from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool, text

from database import Base
from runtime_config import read_secret
import models  # noqa: F401 - registers the legacy schema
import product_v2.models  # noqa: F401 - registers Product V2 schema


config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

database_url = read_secret("DATABASE_URL", required=True)
config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))

target_metadata = Base.metadata
MIGRATION_LOCK_NAME = "autoleadgen_alembic_migration"


def run_migrations_offline() -> None:
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        is_mysql = connection.dialect.name == "mysql"
        has_lock = False
        if is_mysql:
            has_lock = connection.execute(
                text("SELECT GET_LOCK(:lock_name, 0)"),
                {"lock_name": MIGRATION_LOCK_NAME},
            ).scalar() == 1
            if not has_lock:
                raise RuntimeError("Another AutoLeadGen migration already holds the advisory lock")
            # GET_LOCK starts SQLAlchemy's implicit transaction.  Clear it so
            # Alembic owns the migration transaction boundary below.
            connection.commit()

        try:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
                transaction_per_migration=True,
            )
            with context.begin_transaction():
                context.run_migrations()
            # MySQL DDL is non-transactional, so Alembic does not open a real
            # transaction for the migration context.  Its alembic_version DML
            # is nevertheless in SQLAlchemy's implicit transaction and must
            # be committed explicitly before the connection is closed.
            if is_mysql and connection.in_transaction():
                connection.commit()
        except Exception:
            if connection.in_transaction():
                connection.rollback()
            raise
        finally:
            if has_lock:
                if connection.in_transaction():
                    connection.rollback()
                connection.execute(
                    text("SELECT RELEASE_LOCK(:lock_name)"),
                    {"lock_name": MIGRATION_LOCK_NAME},
                )
                connection.commit()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
