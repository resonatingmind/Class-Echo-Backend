from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context
from dotenv import load_dotenv

load_dotenv()

from app.database import Base
import app.models  # noqa: F401 — ensure all models are registered

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override sqlalchemy.url from env (sync driver for migrations).
#
# settings.sync_migration_url prefers MIGRATION_DATABASE_URL over DATABASE_URL
# and converts asyncpg -> psycopg2. On Supabase the two differ: the app runs
# over the transaction pooler (6543), but DDL and the alembic_version lock need
# the session pooler (5432), where a migration keeps one backend for its whole
# transaction.
from app.config import settings  # noqa: E402

sync_url = settings.sync_migration_url
config.set_main_option("sqlalchemy.url", sync_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
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
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
