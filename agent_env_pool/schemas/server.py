"""Pydantic request/response models for AgentEnvPool."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


RuntimeName = Literal["docker"]
EndpointProtocol = Literal["cdp", "http", "sse", "ws", "tcp"]
ReadyCheckType = Literal["none", "tcp", "http", "cdp"]


# ── Requests ─────────────────────────────────────────────────────────

class ReadyCheck(BaseModel):
    """Readiness check for an exposed endpoint."""

    type: ReadyCheckType = "none"
    path: str = "/"
    expected_status: int = Field(default=200, ge=100, le=599)


class EndpointSpec(BaseModel):
    """Container endpoint to publish with a Docker-assigned host port."""

    name: str
    container_port: int = Field(ge=1, le=65535)
    protocol: EndpointProtocol = "http"
    ready_check: ReadyCheck | None = None


class BootServerRequest(BaseModel):
    """Boot a single sandbox environment."""

    env_type: str = "browser-use"
    runtime: RuntimeName = "docker"
    image: str | None = None
    server_id: str | None = None
    endpoints: list[EndpointSpec] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RolloutBootRequest(BaseModel):
    """Boot multiple sandbox environments for parallel rollout."""

    count: int = Field(default=1, ge=1, le=256)
    env_type: str = "browser-use"
    runtime: RuntimeName = "docker"
    image: str | None = None
    endpoints: list[EndpointSpec] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


# ── Responses ────────────────────────────────────────────────────────

class EndpointResponse(BaseModel):
    """Resolved endpoint returned to rollout workers."""

    name: str
    protocol: str
    container_port: int
    host: str
    host_port: int
    url: str
    ready_check: dict[str, Any] = Field(default_factory=dict)


class ServerResponse(BaseModel):
    """Detail of a single sandbox environment."""

    server_id: str
    env_type: str
    runtime: str
    image: str
    status: str
    resource_id: str | None = None
    cdp_url: str | None = None
    host: str | None = None
    port: int | None = None
    endpoints: list[EndpointResponse] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


class ServerListResponse(BaseModel):
    """Paginated list of sandbox environments."""

    items: list[ServerResponse]
    total: int


class ServerSummaryResponse(BaseModel):
    """Compact server fields for CLI-friendly rollout responses."""

    server_id: str
    status: str
    cdp_url: str | None = None
    host: str | None = None
    port: int | None = None
    error_message: str | None = None


class RolloutBootSimpleResponse(BaseModel):
    """Compact result of a batch rollout boot operation."""

    rollout_id: str
    server_ids: list[str]
    servers: list[ServerSummaryResponse]
    total: int
    status_counts: dict[str, int] = Field(default_factory=dict)


class RolloutBootResponse(BaseModel):
    """Result of a batch rollout boot operation."""

    rollout_id: str
    server_ids: list[str]
    servers: list[ServerResponse]


class RolloutDetailResponse(BaseModel):
    """Rollout-level server inventory and status counts."""

    rollout_id: str
    servers: list[ServerResponse]
    total: int
    status_counts: dict[str, int] = Field(default_factory=dict)


class RolloutDetailSimpleResponse(BaseModel):
    """Compact rollout inventory for CLI-friendly status checks."""

    rollout_id: str
    servers: list[ServerSummaryResponse]
    total: int
    status_counts: dict[str, int] = Field(default_factory=dict)
