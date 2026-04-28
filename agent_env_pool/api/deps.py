"""Shared FastAPI dependencies for all API versions."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from agent_env_pool.core.database import get_db
from agent_env_pool.services.env_service import EnvService

_env_service = EnvService()


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async for session in get_db():
        yield session


def get_env_service() -> EnvService:
    return _env_service
