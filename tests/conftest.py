import pytest
import httpx

from agent_env_pool.app import app
from agent_env_pool.core.database import init_db


@pytest.fixture
async def api():
    """Async test client that talks to the FastAPI app in-process."""
    await init_db()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
