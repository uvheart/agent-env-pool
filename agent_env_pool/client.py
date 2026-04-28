"""Minimal synchronous Python SDK for rollout workers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


@dataclass
class EnvHandle:
    """Acquired browser sandbox handle."""

    client: "EnvPoolClient"
    server_id: str
    cdp_url: str | None
    endpoints: list[dict[str, Any]]
    data: dict[str, Any]

    def release(self) -> dict[str, Any]:
        return self.client.release(self.server_id)

    def shutdown(self, *, force: bool = True) -> dict[str, Any]:
        return self.client.shutdown(self.server_id, force=force)

    def __enter__(self) -> "EnvHandle":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


class EnvPoolClient:
    """Small client for Agent RL rollout scripts."""

    def __init__(self, base_url: str = "http://localhost:8100", *, timeout: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.http = httpx.Client(timeout=timeout)

    def close(self) -> None:
        self.http.close()

    def _unwrap(self, response: httpx.Response) -> Any:
        response.raise_for_status()
        body = response.json()
        return body.get("data", body)

    def boot(
        self,
        *,
        env_type: str = "browser-use",
        runtime: str = "docker",
        image: str | None = None,
        endpoints: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._unwrap(
            self.http.post(
                f"{self.base_url}/api/v1/servers/boot",
                json={
                    "env_type": env_type,
                    "runtime": runtime,
                    "image": image,
                    "endpoints": endpoints,
                    "metadata": metadata or {},
                },
            )
        )

    def acquire(
        self,
        *,
        env_type: str = "browser-use",
        runtime: str = "docker",
        image: str | None = None,
        endpoints: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> EnvHandle:
        data = self._unwrap(
            self.http.post(
                f"{self.base_url}/api/v1/pool/acquire",
                json={
                    "env_type": env_type,
                    "runtime": runtime,
                    "image": image,
                    "endpoints": endpoints,
                    "metadata": metadata or {},
                },
            )
        )
        return EnvHandle(
            client=self,
            server_id=data["server_id"],
            cdp_url=data.get("cdp_url"),
            endpoints=data.get("endpoints", []),
            data=data,
        )

    def release(self, server_id: str) -> dict[str, Any]:
        return self._unwrap(self.http.post(f"{self.base_url}/api/v1/pool/release/{server_id}"))

    def shutdown(self, server_id: str, *, force: bool = True) -> dict[str, Any]:
        return self._unwrap(
            self.http.post(f"{self.base_url}/api/v1/servers/{server_id}/shutdown", params={"force": force})
        )

    def list(self, *, status: str | None = None, env_type: str | None = None) -> dict[str, Any]:
        params = {k: v for k, v in {"status": status, "env_type": env_type}.items() if v is not None}
        return self._unwrap(self.http.get(f"{self.base_url}/api/v1/servers", params=params))

    def get(self, server_id: str) -> dict[str, Any]:
        return self._unwrap(self.http.get(f"{self.base_url}/api/v1/servers/{server_id}"))

    def rollout_boot(
        self,
        *,
        count: int,
        env_type: str = "browser-use",
        runtime: str = "docker",
        image: str | None = None,
        endpoints: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._unwrap(
            self.http.post(
                f"{self.base_url}/api/v1/rollout/boot",
                json={
                    "count": count,
                    "env_type": env_type,
                    "runtime": runtime,
                    "image": image,
                    "endpoints": endpoints,
                    "metadata": metadata or {},
                },
            )
        )
