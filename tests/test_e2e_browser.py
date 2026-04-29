"""End-to-end test: boot browser sandbox -> CDP screenshot -> shutdown.

Requires:
- Docker daemon accessible
- browser-use-chrome:latest image available locally
"""

import asyncio
import base64
import json
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pytest
import pytest_asyncio
import websockets

from agent_env_pool.app import app
from agent_env_pool.core.database import init_db

BROWSER_IMAGE = "zenika/alpine-chrome:124"
CDP_CONTAINER_PORT = 9222
SCREENSHOT_PATH = Path(__file__).parent / "screenshot_baidu.png"

CONTAINER_READY_TIMEOUT = 60
PAGE_LOAD_TIMEOUT = 30


@pytest_asyncio.fixture
async def api():
    await init_db()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


async def _wait_for_cdp(cdp_url: str, timeout: int = CONTAINER_READY_TIMEOUT) -> dict:
    """Poll the CDP HTTP endpoint until the browser is ready."""
    deadline = asyncio.get_event_loop().time() + timeout
    async with httpx.AsyncClient(verify=False) as http:
        while asyncio.get_event_loop().time() < deadline:
            try:
                resp = await http.get(f"{cdp_url}/json/version", timeout=3)
                if resp.status_code == 200:
                    return resp.json()
            except Exception:
                pass
            await asyncio.sleep(1)
    raise TimeoutError(f"browser not ready at {cdp_url} after {timeout}s")


async def _get_ws_url(cdp_url: str) -> str:
    """Discover the browser-level CDP websocket URL and rewrite host/port."""
    async with httpx.AsyncClient(verify=False) as http:
        version_resp = await http.get(f"{cdp_url}/json/version", timeout=5)
        version = version_resp.json()

    ws_url = version["webSocketDebuggerUrl"]

    # Rewrite host:port to match our externally accessible cdp_url,
    # and force ws:// (container reverse proxy advertises wss:// but
    # Docker port mapping exposes plain TCP).
    parsed_cdp = urlparse(cdp_url)
    parsed_ws = urlparse(ws_url)
    ws_url = ws_url.replace(
        f"{parsed_ws.hostname}:{parsed_ws.port}",
        f"{parsed_cdp.hostname}:{parsed_cdp.port}",
    )
    ws_url = ws_url.replace("wss://", "ws://")
    return ws_url


async def _cdp_screenshot(
    ws_url: str,
    target_url: str = (
        "data:text/html,%3Chtml%3E%3Cbody%20style%3D%27font-family%3Asans-serif%3B"
        "padding%3A48px%27%3E%3Ch1%3EAgentEnvPool%20CDP%20OK%3C%2Fh1%3E"
        "%3Cp%3ELocal%20render%20page%20for%20stable%20E2E%20screenshots.%3C%2Fp%3E"
        "%3C%2Fbody%3E%3C%2Fhtml%3E"
    ),
) -> bytes:
    """Connect to Chrome via browser-level CDP, navigate and capture a screenshot."""

    msg_id = 0
    pending: dict[int, asyncio.Future] = {}

    async def _reader(ws):
        """Background task that dispatches incoming messages."""
        try:
            async for raw in ws:
                data = json.loads(raw)
                mid = data.get("id")
                if mid and mid in pending:
                    pending[mid].set_result(data)
        except websockets.ConnectionClosed:
            pass

    async def send_cmd(ws, method: str, params: dict | None = None, session_id: str | None = None) -> dict:
        nonlocal msg_id
        msg_id += 1
        fut = asyncio.get_event_loop().create_future()
        pending[msg_id] = fut
        payload = {"id": msg_id, "method": method, "params": params or {}}
        if session_id:
            payload["sessionId"] = session_id
        await ws.send(json.dumps(payload))
        return await asyncio.wait_for(fut, timeout=PAGE_LOAD_TIMEOUT)

    async def send_cmd_best_effort(
        ws,
        method: str,
        params: dict | None = None,
        session_id: str | None = None,
    ) -> None:
        try:
            await send_cmd(ws, method, params, session_id)
        except Exception as exc:
            print(f"[CDP]  best-effort {method} failed: {exc}")

    async with websockets.connect(ws_url, max_size=50 * 1024 * 1024) as ws:
        reader_task = asyncio.create_task(_reader(ws))
        try:
            target = await send_cmd(ws, "Target.createTarget", {"url": "about:blank"})
            target_id = target["result"]["targetId"]
            attached = await send_cmd(
                ws,
                "Target.attachToTarget",
                {"targetId": target_id, "flatten": True},
            )
            session_id = attached["result"]["sessionId"]

            await send_cmd(ws, "Page.enable", session_id=session_id)
            await send_cmd(ws, "Page.bringToFront", session_id=session_id)
            await send_cmd(
                ws,
                "Emulation.setDeviceMetricsOverride",
                {"width": 1280, "height": 720, "deviceScaleFactor": 1, "mobile": False},
                session_id=session_id,
            )
            nav = await send_cmd(ws, "Page.navigate", {"url": target_url}, session_id=session_id)
            print(f"[CDP]  navigate result: {nav.get('result', {})}")

            # Give the page time to load and render
            await asyncio.sleep(5)
            await send_cmd_best_effort(ws, "Page.stopLoading", session_id=session_id)

            result = await send_cmd(
                ws,
                "Page.captureScreenshot",
                {"format": "png", "fromSurface": True},
                session_id=session_id,
            )
            return base64.b64decode(result["result"]["data"])
        finally:
            await send_cmd_best_effort(ws, "Target.closeTarget", {"targetId": target_id} if "target_id" in locals() else None)
            reader_task.cancel()
            try:
                await reader_task
            except (asyncio.CancelledError, Exception):
                pass


@pytest.mark.asyncio
async def test_full_lifecycle(api: httpx.AsyncClient):
    """Boot -> CDP render local page & screenshot -> Shutdown."""

    server_id = None
    try:
        # ── 1. Boot browser sandbox ──────────────────────────────────
        boot_resp = await api.post("/api/v1/servers/boot", json={
            "env_type": "browser-use",
            "runtime": "docker",
            "image": BROWSER_IMAGE,
            "endpoints": [
                {
                    "name": "cdp",
                    "container_port": CDP_CONTAINER_PORT,
                    "protocol": "cdp",
                    "ready_check": {"type": "cdp"},
                }
            ],
            "metadata": {
                "command": [
                    "--no-sandbox",
                    "--disable-gpu",
                    "--disable-dev-shm-usage",
                    "--remote-debugging-address=0.0.0.0",
                    f"--remote-debugging-port={CDP_CONTAINER_PORT}",
                    "about:blank",
                ],
                "security_opt": ["seccomp=unconfined"],
            },
        })
        assert boot_resp.status_code == 200, f"boot failed: {boot_resp.text}"

        body = boot_resp.json()
        server_data = body["data"]
        server_id = server_data["server_id"]
        cdp_url = server_data["cdp_url"]
        endpoints = server_data["endpoints"]
        trace_id = body["meta"]["trace_id"]

        assert server_data["status"] == "running"
        assert cdp_url is not None
        assert endpoints[0]["name"] == "cdp"
        assert endpoints[0]["container_port"] == CDP_CONTAINER_PORT
        assert endpoints[0]["host_port"] > 0
        assert endpoints[0]["url"] == cdp_url
        assert trace_id
        print(f"\n[BOOT] server_id={server_id}")
        print(f"[BOOT] cdp_url={cdp_url}")
        print(f"[BOOT] trace_id={trace_id}")

        # ── 2. Wait for browser container to become ready ────────────
        version_info = await _wait_for_cdp(cdp_url)
        print(f"[CDP]  browser ready: {version_info.get('Browser', 'unknown')}")

        # ── 3. Discover websocket debugger URL ───────────────────────
        ws_url = await _get_ws_url(cdp_url)
        print(f"[CDP]  ws_url={ws_url}")

        # ── 4. Render a local page and take a screenshot ─────────────
        screenshot_bytes = await _cdp_screenshot(ws_url)
        SCREENSHOT_PATH.write_bytes(screenshot_bytes)
        size_kb = len(screenshot_bytes) / 1024
        print(f"[SCREENSHOT] saved {SCREENSHOT_PATH} ({size_kb:.1f} KB)")
        assert len(screenshot_bytes) > 1000, "screenshot too small, likely blank"

        # ── 5. Verify server detail via API ──────────────────────────
        detail_resp = await api.get(f"/api/v1/servers/{server_id}")
        assert detail_resp.status_code == 200
        detail_body = detail_resp.json()
        assert detail_body["data"]["status"] == "running"
        assert detail_body["meta"]["trace_id"]
        print(f"[DETAIL] status={detail_body['data']['status']}, trace_id={detail_body['meta']['trace_id']}")

        # ── 6. Verify server appears in list ─────────────────────────
        list_resp = await api.get("/api/v1/servers")
        assert list_resp.status_code == 200
        ids = [s["server_id"] for s in list_resp.json()["data"]["items"]]
        assert server_id in ids
        print(f"[LIST]  server visible in list ({len(ids)} total)")

    finally:
        # ── 7. Shutdown & cleanup ────────────────────────────────────
        if server_id:
            shutdown_resp = await api.post(
                f"/api/v1/servers/{server_id}/shutdown",
                params={"force": True},
            )
            assert shutdown_resp.status_code == 200
            result = shutdown_resp.json()
            print(f"[SHUTDOWN] {result['data']}, trace_id={result['meta']['trace_id']}")
