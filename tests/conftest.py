import pytest
import httpx

from agent_env_pool.app import app


@pytest.fixture
async def api():
    """Async test client that talks to the FastAPI app in-process."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
