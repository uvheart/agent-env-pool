from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class RuntimeInstance:
    resource_id: str
    cdp_url: str | None = None
    host: str | None = None
    port: int | None = None
    endpoints: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class RuntimeProvider(Protocol):
    """Sync interface — callers must wrap in run_in_executor."""

    def create_browser(
        self,
        server_id: str,
        image: str,
        metadata: dict[str, Any],
        endpoints: list[dict[str, Any]] | None = None,
    ) -> RuntimeInstance:
        ...

    def destroy(self, resource_id: str) -> None:
        ...

    def exists(self, resource_id: str) -> bool:
        ...

    def wait_cdp_ready(self, cdp_url: str, timeout_seconds: int, interval_seconds: float) -> None:
        ...

    def logs(self, resource_id: str, tail: int = 200) -> str:
        ...
