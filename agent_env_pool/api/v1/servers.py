"""API routes for Docker browser sandbox management."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from agent_env_pool.api.deps import get_env_service, get_session
from agent_env_pool.schemas import (
    BootServerRequest,
    RolloutBootRequest,
    RolloutBootResponse,
    RolloutBootSimpleResponse,
    RolloutDetailResponse,
    RolloutDetailSimpleResponse,
    ServerListResponse,
    ServerResponse,
    ServerSummaryResponse,
)
from agent_env_pool.services.env_service import EnvService

router = APIRouter(tags=["env"])


def _to_response(record) -> ServerResponse:
    try:
        meta = json.loads(record.metadata_json) if record.metadata_json else {}
    except (json.JSONDecodeError, TypeError):
        meta = {}
    try:
        endpoints = json.loads(record.endpoints_json) if record.endpoints_json else []
    except (json.JSONDecodeError, TypeError):
        endpoints = meta.get("endpoints", [])
    return ServerResponse(
        server_id=record.server_id,
        env_type=record.env_type,
        runtime=record.runtime,
        image=record.image,
        status=record.status,
        resource_id=record.resource_id,
        cdp_url=record.cdp_url,
        host=record.host,
        port=record.port,
        endpoints=endpoints,
        metadata=meta,
        error_message=record.error_message,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _to_summary(record) -> ServerSummaryResponse:
    return ServerSummaryResponse(
        server_id=record.server_id,
        status=record.status,
        cdp_url=record.cdp_url,
        host=record.host,
        port=record.port,
        error_message=record.error_message,
    )


def _status_counts(records) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        counts[record.status] = counts.get(record.status, 0) + 1
    return counts


@router.post("/servers/boot", response_model=ServerResponse)
async def boot_server(
    req: BootServerRequest,
    db: AsyncSession = Depends(get_session),
    svc: EnvService = Depends(get_env_service),
):
    """Create and start a single browser sandbox."""
    record = await svc.boot(
        db,
        env_type=req.env_type,
        runtime=req.runtime,
        image=req.image,
        server_id=req.server_id,
        endpoints=[endpoint.model_dump() for endpoint in req.endpoints] if req.endpoints else None,
        metadata=req.metadata,
    )
    return _to_response(record)


@router.post("/pool/acquire", response_model=ServerResponse)
async def acquire_server(
    req: BootServerRequest,
    db: AsyncSession = Depends(get_session),
    svc: EnvService = Depends(get_env_service),
):
    """Acquire an idle browser sandbox or boot a new one."""
    record = await svc.acquire(
        db,
        env_type=req.env_type,
        runtime=req.runtime,
        image=req.image,
        endpoints=[endpoint.model_dump() for endpoint in req.endpoints] if req.endpoints else None,
        metadata=req.metadata,
    )
    return _to_response(record)


@router.post("/pool/release/{server_id}", response_model=ServerResponse)
async def release_server(
    server_id: str,
    db: AsyncSession = Depends(get_session),
    svc: EnvService = Depends(get_env_service),
):
    """Return an occupied sandbox to the idle pool."""
    record = await svc.release(db, server_id)
    return _to_response(record)


@router.post("/servers/{server_id}/shutdown")
async def shutdown_server(
    server_id: str,
    force: bool = Query(False),
    db: AsyncSession = Depends(get_session),
    svc: EnvService = Depends(get_env_service),
):
    """Stop and destroy a browser sandbox."""
    return await svc.shutdown(db, server_id, force=force)


@router.get("/servers", response_model=ServerListResponse)
async def list_servers(
    status: str | None = Query(None),
    env_type: str | None = Query(None),
    db: AsyncSession = Depends(get_session),
    svc: EnvService = Depends(get_env_service),
):
    """List active browser sandboxes."""
    records = await svc.list_servers(db, status=status, env_type=env_type)
    items = [_to_response(r) for r in records]
    return ServerListResponse(items=items, total=len(items))


@router.get("/servers/{server_id}", response_model=ServerResponse)
async def get_server_detail(
    server_id: str,
    db: AsyncSession = Depends(get_session),
    svc: EnvService = Depends(get_env_service),
):
    """Get details for one sandbox."""
    record = await svc.get_detail(db, server_id)
    return _to_response(record)


@router.get("/servers/{server_id}/logs")
async def get_server_logs(
    server_id: str,
    tail: int = Query(200, ge=1, le=5000),
    db: AsyncSession = Depends(get_session),
    svc: EnvService = Depends(get_env_service),
):
    """Return recent Docker logs for one sandbox container."""
    return await svc.container_logs(db, server_id, tail=tail)


@router.post("/rollout/boot", response_model=RolloutBootSimpleResponse | RolloutBootResponse)
async def rollout_boot(
    req: RolloutBootRequest,
    verbose: bool = Query(False, description="Return full server objects"),
    db: AsyncSession = Depends(get_session),
    svc: EnvService = Depends(get_env_service),
):
    """Boot multiple browser sandboxes for a rollout."""
    result = await svc.rollout_boot(
        db,
        count=req.count,
        env_type=req.env_type,
        runtime=req.runtime,
        image=req.image,
        endpoints=[endpoint.model_dump() for endpoint in req.endpoints] if req.endpoints else None,
        metadata=req.metadata,
    )
    if not verbose:
        return RolloutBootSimpleResponse(
            rollout_id=result["rollout_id"],
            server_ids=result["server_ids"],
            servers=[_to_summary(s) for s in result["servers"]],
            total=len(result["servers"]),
            status_counts=_status_counts(result["servers"]),
        )
    return RolloutBootResponse(
        rollout_id=result["rollout_id"],
        server_ids=result["server_ids"],
        servers=[_to_response(s) for s in result["servers"]],
    )


@router.get("/rollout/{rollout_id}", response_model=RolloutDetailSimpleResponse | RolloutDetailResponse)
async def get_rollout(
    rollout_id: str,
    verbose: bool = Query(False, description="Return full server objects"),
    db: AsyncSession = Depends(get_session),
    svc: EnvService = Depends(get_env_service),
):
    """List all sandboxes created for one rollout."""
    result = await svc.list_rollout(db, rollout_id)
    if not verbose:
        return RolloutDetailSimpleResponse(
            rollout_id=result["rollout_id"],
            servers=[_to_summary(s) for s in result["servers"]],
            total=result["total"],
            status_counts=result["status_counts"],
        )
    return RolloutDetailResponse(
        rollout_id=result["rollout_id"],
        servers=[_to_response(s) for s in result["servers"]],
        total=result["total"],
        status_counts=result["status_counts"],
    )


@router.post("/rollout/{rollout_id}/shutdown")
async def shutdown_rollout(
    rollout_id: str,
    force: bool = Query(False),
    db: AsyncSession = Depends(get_session),
    svc: EnvService = Depends(get_env_service),
):
    """Shutdown all sandboxes in one rollout."""
    return await svc.shutdown_rollout(db, rollout_id, force=force)
