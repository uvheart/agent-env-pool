"""Docker runtime provider.

All Docker SDK calls are synchronous/blocking. The service layer wraps
them with ``asyncio.loop.run_in_executor`` to avoid blocking the FastAPI
event loop.

Port allocation is delegated entirely to Docker (ephemeral host ports).
"""

from __future__ import annotations

import json
import socket
import time
import urllib.request

import docker
from docker.errors import NotFound

from agent_env_pool.core.config import get_settings
from agent_env_pool.core.logging import logger_manager
from agent_env_pool.providers.base import RuntimeInstance

logger = logger_manager.get_logger(__name__)


class DockerProvider:
    """Docker runtime provider for the browser-use sandbox engine."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.client = docker.from_env()

    def create_browser(
        self,
        server_id: str,
        image: str,
        metadata: dict,
        endpoints: list[dict] | None = None,
    ) -> RuntimeInstance:
        endpoint_specs = self._normalize_endpoints(metadata, endpoints)
        name = f"{self.settings.container_name_prefix}-{server_id[:12]}"
        labels = {
            "agent-env-pool": "true",
            "agent-env-pool.server_id": server_id,
            "agent-env-pool.env_type": "browser-use",
        }

        # Let Docker auto-assign ephemeral host ports.
        port_bindings = {
            f"{endpoint['container_port']}/tcp": None
            for endpoint in endpoint_specs
        }

        run_kwargs: dict = dict(
            detach=True,
            name=name,
            ports=port_bindings,
            labels=labels,
            shm_size=metadata.get("shm_size", "1g"),
            environment=metadata.get("environment", {}),
        )
        if metadata.get("command"):
            run_kwargs["command"] = metadata["command"]

        container = self.client.containers.run(image, **run_kwargs)
        container.reload()

        host = self.settings.public_host
        resolved_endpoints = [
            self._resolve_endpoint(container.attrs, endpoint, host)
            for endpoint in endpoint_specs
        ]
        cdp_endpoint = next(
            (endpoint for endpoint in resolved_endpoints if endpoint["protocol"] == "cdp"),
            None,
        )
        primary_endpoint = cdp_endpoint or (resolved_endpoints[0] if resolved_endpoints else None)
        cdp_url = cdp_endpoint["url"] if cdp_endpoint else None
        host_port = primary_endpoint["host_port"] if primary_endpoint else None

        return RuntimeInstance(
            resource_id=container.id,
            cdp_url=cdp_url,
            host=host,
            port=host_port,
            endpoints=resolved_endpoints,
            metadata={
                "container_name": name,
                "endpoints": resolved_endpoints,
                "container_port": primary_endpoint["container_port"] if primary_endpoint else None,
                "host_port": host_port,
                "cdp_http_url": cdp_url,
                "cdp_ws_url": cdp_url.replace("http://", "ws://") if cdp_url else None,
            },
        )

    def destroy(self, resource_id: str) -> None:
        try:
            container = self.client.containers.get(resource_id)
        except NotFound:
            return
        container.stop(timeout=10)
        container.remove(v=True, force=True)

    def exists(self, resource_id: str) -> bool:
        try:
            self.client.containers.get(resource_id)
            return True
        except NotFound:
            return False

    def logs(self, resource_id: str, tail: int = 200) -> str:
        try:
            container = self.client.containers.get(resource_id)
        except NotFound:
            return ""
        data = container.logs(tail=tail)
        return data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(data)

    def wait_cdp_ready(self, cdp_url: str, timeout_seconds: int, interval_seconds: float) -> dict:
        deadline = time.monotonic() + timeout_seconds
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"{cdp_url}/json/version", timeout=3) as response:
                    if response.status == 200:
                        return json.loads(response.read().decode("utf-8"))
            except Exception as exc:
                last_error = exc
            time.sleep(interval_seconds)
        raise RuntimeError(f"CDP endpoint not ready at {cdp_url}: {last_error}")

    def wait_endpoint_ready(self, endpoint: dict, timeout_seconds: int, interval_seconds: float) -> None:
        ready_check = endpoint.get("ready_check") or {}
        check_type = ready_check.get("type") or "none"
        if check_type == "none":
            return

        check_host = self.settings.docker_ready_host or endpoint["host"]
        deadline = time.monotonic() + timeout_seconds
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                if check_type == "tcp":
                    with socket.create_connection(
                        (check_host, int(endpoint["host_port"])),
                        timeout=3,
                    ):
                        return
                elif check_type == "http":
                    path = ready_check.get("path") or "/"
                    expected_status = int(ready_check.get("expected_status") or 200)
                    url = self._ready_url(endpoint, check_host, path)
                    with urllib.request.urlopen(url, timeout=3) as response:
                        if response.status == expected_status:
                            return
                        last_error = RuntimeError(f"unexpected status {response.status}")
                elif check_type == "cdp":
                    with urllib.request.urlopen(
                        self._ready_url(endpoint, check_host, "/json/version"),
                        timeout=3,
                    ) as response:
                        if response.status == 200:
                            return
                else:
                    raise RuntimeError(f"unsupported ready_check type: {check_type}")
            except Exception as exc:
                last_error = exc
            time.sleep(interval_seconds)
        raise RuntimeError(
            f"endpoint {endpoint.get('name')} not ready at "
            f"{check_host}:{endpoint.get('host_port')}: {last_error}"
        )

    def _normalize_endpoints(self, metadata: dict, endpoints: list[dict] | None) -> list[dict]:
        if endpoints:
            return [self._normalize_endpoint(endpoint) for endpoint in endpoints]

        container_port = int(metadata.get("container_port") or self.settings.docker_browser_port)
        return [
            {
                "name": "cdp",
                "container_port": container_port,
                "protocol": "cdp",
                "ready_check": {"type": "cdp", "path": "/json/version", "expected_status": 200},
            }
        ]

    @staticmethod
    def _normalize_endpoint(endpoint: dict) -> dict:
        protocol = endpoint.get("protocol") or "http"
        ready_check = endpoint.get("ready_check") or {}
        if "type" not in ready_check:
            ready_check["type"] = "cdp" if protocol == "cdp" else "none"
        if protocol == "cdp":
            ready_check.setdefault("path", "/json/version")
            ready_check.setdefault("expected_status", 200)
        elif ready_check.get("type") == "http":
            ready_check.setdefault("path", "/")
            ready_check.setdefault("expected_status", 200)
        return {
            "name": str(endpoint.get("name") or protocol),
            "container_port": int(endpoint["container_port"]),
            "protocol": protocol,
            "ready_check": ready_check,
        }

    def _resolve_endpoint(self, container_attrs: dict, endpoint: dict, host: str) -> dict:
        container_port = int(endpoint["container_port"])
        host_port = self._resolve_host_port(container_attrs, container_port)
        scheme = self._url_scheme(endpoint["protocol"])
        return {
            "name": endpoint["name"],
            "protocol": endpoint["protocol"],
            "container_port": container_port,
            "host": host,
            "host_port": host_port,
            "url": f"{scheme}://{host}:{host_port}",
            "ready_check": endpoint.get("ready_check") or {},
        }

    def _ready_url(self, endpoint: dict, check_host: str, path: str) -> str:
        scheme = self._url_scheme(endpoint["protocol"])
        if scheme == "tcp":
            scheme = "http"
        return f"{scheme}://{check_host}:{endpoint['host_port']}".rstrip("/") + "/" + path.lstrip("/")

    @staticmethod
    def _url_scheme(protocol: str) -> str:
        if protocol == "tcp":
            return "tcp"
        if protocol == "ws":
            return "ws"
        return "http"

    @staticmethod
    def _resolve_host_port(container_attrs: dict, container_port: int, retries: int = 3) -> int:
        """Read the host port Docker assigned, with brief retries."""
        for attempt in range(retries):
            ports = container_attrs.get("NetworkSettings", {}).get("Ports", {})
            binding = ports.get(f"{container_port}/tcp")
            if binding:
                return int(binding[0]["HostPort"])
            if attempt < retries - 1:
                time.sleep(0.5)
        raise RuntimeError(f"container port {container_port} is not published")
