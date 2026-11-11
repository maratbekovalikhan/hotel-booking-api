import os
import uuid
from collections.abc import AsyncGenerator

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

load_dotenv()


def _normalize(url: str) -> str:
    """Supabase отдаёт postgresql://..., а нам нужен драйвер asyncpg."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+asyncpg://" + url[len(prefix):]
    return url


# Без DATABASE_URL проект стартует на локальной SQLite (для разработки)
DATABASE_URL = _normalize(os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./hotel.db"))
IS_POSTGRES = DATABASE_URL.startswith("postgresql")

connect_args: dict = {}
engine_kwargs: dict = {}
if IS_POSTGRES:
    if "@localhost" not in DATABASE_URL and "@127.0.0.1" not in DATABASE_URL:
        connect_args["ssl"] = "require"  # Supabase требует SSL
    if ":6543/" in DATABASE_URL:  # Supabase transaction pooler (pgbouncer)
        connect_args.update(
            statement_cache_size=0,
            prepared_statement_cache_size=0,
            prepared_statement_name_func=lambda: f"__asyncpg_{uuid.uuid4()}__",
        )
        engine_kwargs["poolclass"] = NullPool

engine = create_async_engine(
    DATABASE_URL, pool_pre_ping=True, connect_args=connect_args, **engine_kwargs
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
