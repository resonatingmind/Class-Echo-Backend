import uuid
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.config import settings

db_url = settings.DATABASE_URL
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)
if db_url.startswith("postgresql://"):
    db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)


def _connect_args(url: str) -> dict:
    """
    Extra asyncpg options required when Postgres sits behind a connection
    pooler (Supabase's Supavisor, PgBouncer, and friends).

    In transaction pooling mode the pooler hands each statement to whichever
    backend is free, so a server-side prepared statement created on one backend
    is missing on the next. That surfaces as intermittent
    DuplicatePreparedStatementError / "prepared statement __asyncpg_stmt_x__
    does not exist" under any real concurrency -- the kind of failure that never
    reproduces against a local Postgres.

    Disabling both caches and giving every statement a unique name makes the
    driver pooler-agnostic. The cost is re-planning each query, which is
    negligible here and far cheaper than the failure mode.

    Applied only when the host looks like a pooler, so local and direct
    connections keep the statement cache.
    """
    host = (urlparse(url).hostname or "").lower()
    if "pooler." not in host and "pgbouncer" not in host:
        return {}
    return {
        # SQLAlchemy's own dialect-level cache.
        "prepared_statement_cache_size": 0,
        # asyncpg's driver-level cache.
        "statement_cache_size": 0,
        # Collision-proof names, in case anything still prepares a statement.
        "prepared_statement_name_func": lambda: f"__asyncpg_{uuid.uuid4()}__",
    }


# NullPool: the pooler is doing the pooling. A second pool on this side would
# hold connections open against Supabase's per-project connection ceiling.
engine = create_async_engine(
    db_url,
    echo=False,
    poolclass=NullPool,
    connect_args=_connect_args(db_url),
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

class Base(DeclarativeBase):
    pass

async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
