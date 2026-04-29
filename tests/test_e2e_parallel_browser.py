"""Parallel E2E: 10 concurrent boot requests each run a full browser lifecycle."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

import httpx
import pytest
import pytest_asyncio

from agent_env_pool.app import app
from agent_env_pool.core.database import init_db
from tests.test_e2e_browser import (
    BROWSER_IMAGE,
    CDP_CONTAINER_PORT,
    STABLE_E2E_TARGET_URL,
    _cdp_screenshot,
    _get_ws_url,
    _wait_for_cdp,
)

PARALLEL_REQUESTS = 10
PARALLEL_TARGET_URL = os.getenv("AGENT_ENV_POOL_PARALLEL_E2E_TARGET_URL", STABLE_E2E_TARGET_URL)
SCREENSHOT_DIR = Path(__file__).parent


@dataclass
class WorkerResult:
    index: int
    success: bool
    server_id: str | None = None
    cdp_url: str | None = None
    screenshot_bytes: int = 0
    error: str | None = None


@pytest_asyncio.fixture
async def api():
    await init_db()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver", timeout=180) as client:
        yield client


def _boot_payload() -> dict:
    return {
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
                "--ignore-certificate-errors",
                "--remote-debugging-address=0.0.0.0",
                f"--remote-debugging-port={CDP_CONTAINER_PORT}",
                "about:blank",
            ],
            "security_opt": ["seccomp=unconfined"],
        },
    }


async def _run_worker(api: httpx.AsyncClient, index: int) -> WorkerResult:
    server_id: str | None = None
    try:
        boot_resp = await api.post("/api/v1/servers/boot", json=_boot_payload())
        assert boot_resp.status_code == 200, f"boot failed: {boot_resp.text}"
        server = boot_resp.json()["data"]
        server_id = server["server_id"]
        cdp_url = server["cdp_url"]
        assert server["status"] == "running"
        assert cdp_url

        await _wait_for_cdp(cdp_url)
        ws_url = await _get_ws_url(cdp_url)
        screenshot = await _cdp_screenshot(ws_url, target_url=PARALLEL_TARGET_URL)
        screenshot_path = SCREENSHOT_DIR / f"screenshot_parallel_{index:02d}.png"
        screenshot_path.write_bytes(screenshot)
        assert len(screenshot) > 1000, "screenshot too small, likely blank"

        detail_resp = await api.get(f"/api/v1/servers/{server_id}")
        assert detail_resp.status_code == 200
        assert detail_resp.json()["data"]["status"] == "running"

        list_resp = await api.get("/api/v1/servers")
        assert list_resp.status_code == 200
        server_ids = [item["server_id"] for item in list_resp.json()["data"]["items"]]
        assert server_id in server_ids

        return WorkerResult(
            index=index,
            success=True,
            server_id=server_id,
            cdp_url=cdp_url,
            screenshot_bytes=len(screenshot),
        )
    except Exception as exc:
        return WorkerResult(index=index, success=False, server_id=server_id, error=repr(exc))
    finally:
        if server_id:
            try:
                shutdown_resp = await api.post(
                    f"/api/v1/servers/{server_id}/shutdown",
                    params={"force": True},
                )
                assert shutdown_resp.status_code == 200
            except Exception as exc:
                print(f"[PARALLEL][{index:02d}] shutdown failed for {server_id}: {exc!r}")


@pytest.mark.asyncio
async def test_parallel_full_lifecycle_success_rate(api: httpx.AsyncClient):
    """Send 10 concurrent requests; every sandbox must complete the lifecycle."""
    print(f"\n[PARALLEL] target_url={PARALLEL_TARGET_URL}")
    start = perf_counter()
    results = await asyncio.gather(*(_run_worker(api, idx) for idx in range(PARALLEL_REQUESTS)))
    elapsed = perf_counter() - start

    successes = [result for result in results if result.success]
    failures = [result for result in results if not result.success]
    success_rate = len(successes) / PARALLEL_REQUESTS

    print(
        f"\n[PARALLEL] success={len(successes)}/{PARALLEL_REQUESTS} "
        f"success_rate={success_rate:.0%} elapsed={elapsed:.1f}s"
    )
    for result in successes:
        print(
            f"[PARALLEL][{result.index:02d}] OK "
            f"server_id={result.server_id} cdp_url={result.cdp_url} "
            f"screenshot={result.screenshot_bytes} bytes"
        )
    for result in failures:
        print(f"[PARALLEL][{result.index:02d}] FAIL server_id={result.server_id} error={result.error}")

    assert not failures, f"{len(failures)} parallel lifecycle workers failed"
    assert success_rate == 1.0
