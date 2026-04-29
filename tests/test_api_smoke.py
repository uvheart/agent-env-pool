import httpx
import pytest


@pytest.mark.asyncio
async def test_list_servers_smoke(api: httpx.AsyncClient):
    response = await api.get("/api/v1/servers")

    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["trace_id"]
    assert isinstance(body["data"]["items"], list)
    assert isinstance(body["data"]["total"], int)
