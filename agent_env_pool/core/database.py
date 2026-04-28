"""Async database engine backed by aiosqlite + SQLAlchemy 2.x async."""

from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent_env_pool.core.config import get_settings
from agent_env_pool.models import Base

settings = get_settings()

connect_args = {"check_same_thread": False} if "sqlite" in settings.database_url else {}

engine = create_async_engine(
    settings.database_url,
    connect_args=connect_args,
    echo=False,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def init_db() -> None:
    sqlite_path = settings.sqlite_path
    if sqlite_path and sqlite_path.parent != sqlite_path:
        sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if "sqlite" in settings.database_url:
            columns = await conn.execute(text("PRAGMA table_info(env_servers)"))
            column_names = {row[1] for row in columns}
            if "endpoints_json" not in column_names:
                await conn.execute(
                    text("ALTER TABLE env_servers ADD COLUMN endpoints_json TEXT NOT NULL DEFAULT '[]'")
                )


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
