"""Async service layer for AgentEnvPool.

Quota enforcement: a BEGIN IMMEDIATE transaction atomically checks the
active-env count and inserts a STARTING record.  Once the row exists the
quota slot is claimed; subsequent container creation happens outside the
transaction so the DB write-lock is held only briefly.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from functools import partial
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from agent_env_pool.core.config import get_settings
from agent_env_pool.core.database import AsyncSessionLocal
from agent_env_pool.core.logging import logger_manager
from agent_env_pool.models import EnvServer, ServerStatus
from agent_env_pool.providers.docker import DockerProvider

logger = logger_manager.get_logger(__name__)
settings = get_settings()


class EnvService:
    """Manages the full lifecycle of sandbox environments."""

    def __init__(self) -> None:
        self.provider = DockerProvider()

    # ------------------------------------------------------------------
    # Boot (single) — quota-aware
    # ------------------------------------------------------------------

    async def boot(
        self,
        db: AsyncSession,
        *,
        env_type: str = "browser-use",
        runtime: str = "docker",
        image: str | None = None,
        server_id: str | None = None,
        endpoints: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> EnvServer:
        server_id = server_id or uuid.uuid4().hex
        image = image or settings.docker_browser_image
        metadata = metadata or {}

        # ── Atomic quota check + insert ──────────────────────────────
        # Use a dedicated short-lived session so the write-lock is
        # released as soon as possible.
        record = await self._acquire_quota_and_insert(
            server_id=server_id,
            env_type=env_type,
            runtime=runtime,
            image=image,
            metadata=metadata,
        )

        # ── Create container (outside the quota transaction) ─────────
        instance = None
        try:
            await self._update_status(db, server_id, ServerStatus.STARTING)

            loop = asyncio.get_running_loop()
            instance = await loop.run_in_executor(
                None,
                partial(self.provider.create_browser, server_id, image, metadata, endpoints),
            )
            record.resource_id = instance.resource_id
            for endpoint in instance.endpoints:
                await loop.run_in_executor(
                    None,
                    partial(
                        self.provider.wait_endpoint_ready,
                        endpoint,
                        settings.ready_check_timeout_seconds,
                        settings.ready_check_interval_seconds,
                    ),
                )

            record.resource_id = instance.resource_id
            record.cdp_url = instance.cdp_url
            record.host = instance.host
            record.port = instance.port
            record.endpoints_json = json.dumps(instance.endpoints)
            record.metadata_json = json.dumps({**metadata, **instance.metadata})
            record.status = ServerStatus.RUNNING
            record.updated_at = datetime.utcnow()
            db.add(record)
            await db.merge(record)
            await db.commit()

            # Re-fetch to get a clean attached instance
            result = await db.scalars(
                select(EnvServer).where(EnvServer.server_id == server_id)
            )
            record = result.first()

            logger.info("booted server %s -> resource %s", server_id, instance.resource_id)
            return record

        except HTTPException:
            raise
        except Exception as e:
            logger.error("boot failed for %s: %s", server_id, e)
            await self._mark_error(db, server_id, str(e))
            await self._rollback_resource(instance.resource_id if instance else record.resource_id)
            raise HTTPException(status_code=500, detail=f"boot failed: {e}")

    async def _acquire_quota_and_insert(
        self,
        *,
        server_id: str,
        env_type: str,
        runtime: str,
        image: str,
        metadata: dict[str, Any],
    ) -> EnvServer:
        """Atomically check quota and insert a STARTING record.

        Uses a raw connection with BEGIN IMMEDIATE to grab the SQLite
        write-lock upfront, counts active envs, and inserts if within
        quota — all in one short transaction.
        """
        from agent_env_pool.core.database import engine

        active_statuses = list(ServerStatus.ACTIVE_STATUSES)

        async with engine.connect() as conn:
            # BEGIN IMMEDIATE grabs the write-lock immediately,
            # serializing concurrent quota checks.
            await conn.execute(text("BEGIN IMMEDIATE"))

            placeholders = ", ".join(f":s{i}" for i in range(len(active_statuses)))
            params = {f"s{i}": s for i, s in enumerate(active_statuses)}
            result = await conn.execute(
                text(f"SELECT COUNT(*) FROM env_servers WHERE status IN ({placeholders})"),
                params,
            )
            active_count = result.scalar() or 0

            if active_count >= settings.max_pool_size:
                await conn.execute(text("ROLLBACK"))
                raise HTTPException(
                    status_code=429,
                    detail=f"pool quota exceeded: {active_count}/{settings.max_pool_size} active",
                )

            metadata_str = json.dumps(metadata)
            await conn.execute(
                text(
                    "INSERT INTO env_servers "
                    "(server_id, env_type, runtime, image, status, endpoints_json, metadata_json, created_at, updated_at) "
                    "VALUES (:server_id, :env_type, :runtime, :image, :status, :endpoints_json, :metadata_json, :created_at, :updated_at)"
                ),
                {
                    "server_id": server_id,
                    "env_type": env_type,
                    "runtime": runtime,
                    "image": image,
                    "status": ServerStatus.STARTING,
                    "endpoints_json": "[]",
                    "metadata_json": metadata_str,
                    "created_at": datetime.utcnow(),
                    "updated_at": datetime.utcnow(),
                },
            )
            await conn.execute(text("COMMIT"))

        logger.info(
            "quota acquired for %s (%d/%d active)",
            server_id, active_count + 1, settings.max_pool_size,
        )

        # Fetch the ORM record from the main session pool
        async with AsyncSessionLocal() as session:
            result = await session.scalars(
                select(EnvServer).where(EnvServer.server_id == server_id)
            )
            return result.first()

    # ------------------------------------------------------------------
    # Shutdown (single)
    # ------------------------------------------------------------------

    async def shutdown(self, db: AsyncSession, server_id: str, *, force: bool = False) -> dict:
        record = await self._get_or_404(db, server_id)

        if record.status == ServerStatus.STOPPED:
            return {"message": "already stopped"}

        await self._update_status(db, server_id, ServerStatus.STOPPING)

        try:
            if record.resource_id:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self.provider.destroy, record.resource_id)
            await self._update_status(db, server_id, ServerStatus.STOPPED)
            logger.info("shutdown server %s", server_id)
            return {"message": "success"}
        except Exception as e:
            logger.error("shutdown failed for %s: %s", server_id, e)
            if force:
                await self._update_status(db, server_id, ServerStatus.STOPPED)
                return {"message": "force stopped (resource may leak)"}
            await self._mark_error(db, server_id, str(e))
            raise HTTPException(status_code=500, detail=f"shutdown failed: {e}")

    async def acquire(
        self,
        db: AsyncSession,
        *,
        env_type: str = "browser-use",
        runtime: str = "docker",
        image: str | None = None,
        endpoints: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> EnvServer:
        """Acquire an idle environment or boot a new one, then mark it busy."""
        await self.reconcile_runtime_state(db)
        image = image or settings.docker_browser_image
        record = None
        if not endpoints:
            result = await db.scalars(
                select(EnvServer)
                .where(
                    EnvServer.status == ServerStatus.RUNNING,
                    EnvServer.env_type == env_type,
                    EnvServer.runtime == runtime,
                    EnvServer.image == image,
                )
                .order_by(EnvServer.updated_at.asc())
            )
            record = result.first()
        if record is None:
            record = await self.boot(
                db,
                env_type=env_type,
                runtime=runtime,
                image=image,
                endpoints=endpoints,
                metadata=metadata,
            )

        await self._update_status(db, record.server_id, ServerStatus.OCCUPIED)
        return await self._get_or_404(db, record.server_id)

    async def release(self, db: AsyncSession, server_id: str) -> EnvServer:
        """Release a busy environment back to the idle pool."""
        record = await self._get_or_404(db, server_id)
        if record.status == ServerStatus.STOPPED:
            raise HTTPException(status_code=409, detail="cannot release a stopped environment")
        if record.status == ServerStatus.ERROR:
            raise HTTPException(status_code=409, detail="cannot release a failed environment")
        await self._update_status(db, server_id, ServerStatus.RUNNING)
        return await self._get_or_404(db, server_id)

    # ------------------------------------------------------------------
    # List / Detail
    # ------------------------------------------------------------------

    async def list_servers(
        self,
        db: AsyncSession,
        *,
        status: str | None = None,
        env_type: str | None = None,
    ) -> list[EnvServer]:
        await self.reconcile_runtime_state(db)
        stmt = select(EnvServer).where(EnvServer.status != ServerStatus.STOPPED)
        if status:
            stmt = stmt.where(EnvServer.status == status)
        if env_type:
            stmt = stmt.where(EnvServer.env_type == env_type)
        stmt = stmt.order_by(EnvServer.created_at.desc())
        result = await db.scalars(stmt)
        return list(result.all())

    async def get_detail(self, db: AsyncSession, server_id: str) -> EnvServer:
        await self.reconcile_runtime_state(db, server_id=server_id)
        return await self._get_or_404(db, server_id)

    async def container_logs(self, db: AsyncSession, server_id: str, *, tail: int = 200) -> dict:
        record = await self._get_or_404(db, server_id)
        if not record.resource_id:
            return {"server_id": server_id, "items": ""}
        loop = asyncio.get_running_loop()
        logs = await loop.run_in_executor(None, partial(self.provider.logs, record.resource_id, tail))
        return {"server_id": server_id, "resource_id": record.resource_id, "items": logs}

    async def reconcile_runtime_state(self, db: AsyncSession, *, server_id: str | None = None) -> int:
        """Mark active Docker records as error if their container disappeared."""
        stmt = select(EnvServer).where(EnvServer.status.in_(ServerStatus.ACTIVE_STATUSES))
        if server_id:
            stmt = stmt.where(EnvServer.server_id == server_id)
        records = list((await db.scalars(stmt)).all())

        changed = 0
        loop = asyncio.get_running_loop()
        for record in records:
            if record.runtime != "docker" or not record.resource_id:
                continue
            exists = await loop.run_in_executor(None, self.provider.exists, record.resource_id)
            if exists:
                continue
            record.status = ServerStatus.ERROR
            record.error_message = "runtime resource missing; container may have been removed externally"
            record.updated_at = datetime.utcnow()
            db.add(record)
            changed += 1

        if changed:
            await db.commit()
            logger.warning("reconciled %d missing runtime resources", changed)
        return changed

    # ------------------------------------------------------------------
    # Rollout boot (batch) — serial to avoid SQLite write-lock contention
    # ------------------------------------------------------------------

    async def rollout_boot(
        self,
        db: AsyncSession,
        *,
        count: int = 1,
        env_type: str = "browser-use",
        runtime: str = "docker",
        image: str | None = None,
        endpoints: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict:
        rollout_id = uuid.uuid4().hex[:16]
        image = image or settings.docker_browser_image
        metadata = metadata or {}
        metadata["rollout_id"] = rollout_id

        servers: list[EnvServer] = []
        for idx in range(count):
            sid = f"{rollout_id}-{idx:04d}"
            try:
                record = await self.boot(
                    db,
                    env_type=env_type,
                    runtime=runtime,
                    image=image,
                    server_id=sid,
                    endpoints=endpoints,
                    metadata=metadata,
                )
                servers.append(record)
            except HTTPException as e:
                if e.status_code == 429:
                    logger.warning("rollout stopped at index %d: quota exceeded", idx)
                    break
                logger.error("rollout boot index %d failed: %s", idx, e.detail)
            except Exception as e:
                logger.error("rollout boot index %d failed: %s", idx, e)

        return {
            "rollout_id": rollout_id,
            "server_ids": [s.server_id for s in servers],
            "servers": servers,
        }

    async def list_rollout(self, db: AsyncSession, rollout_id: str) -> dict:
        await self.reconcile_runtime_state(db)
        records = list((await db.scalars(select(EnvServer).order_by(EnvServer.created_at.asc()))).all())
        servers = [record for record in records if self._record_rollout_id(record) == rollout_id]
        status_counts: dict[str, int] = {}
        for server in servers:
            status_counts[server.status] = status_counts.get(server.status, 0) + 1
        return {
            "rollout_id": rollout_id,
            "servers": servers,
            "total": len(servers),
            "status_counts": status_counts,
        }

    async def shutdown_rollout(self, db: AsyncSession, rollout_id: str, *, force: bool = False) -> dict:
        rollout = await self.list_rollout(db, rollout_id)
        results = []
        for server in rollout["servers"]:
            if server.status == ServerStatus.STOPPED:
                results.append({"server_id": server.server_id, "message": "already stopped"})
                continue
            try:
                result = await self.shutdown(db, server.server_id, force=force)
                results.append({"server_id": server.server_id, **result})
            except HTTPException as exc:
                results.append({"server_id": server.server_id, "error": exc.detail})
        return {"rollout_id": rollout_id, "results": results}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_or_404(self, db: AsyncSession, server_id: str) -> EnvServer:
        result = await db.scalars(
            select(EnvServer).where(EnvServer.server_id == server_id)
        )
        record = result.first()
        if not record:
            raise HTTPException(status_code=404, detail=f"server {server_id} not found")
        return record

    async def _update_status(self, db: AsyncSession, server_id: str, status: str) -> None:
        await db.execute(
            update(EnvServer)
            .where(EnvServer.server_id == server_id)
            .values(status=status, updated_at=datetime.utcnow())
        )
        await db.commit()

    async def _mark_error(self, db: AsyncSession, server_id: str, msg: str) -> None:
        await db.execute(
            update(EnvServer)
            .where(EnvServer.server_id == server_id)
            .values(
                status=ServerStatus.ERROR,
                error_message=msg[:1024],
                updated_at=datetime.utcnow(),
            )
        )
        await db.commit()

    async def _rollback_resource(self, resource_id: str | None) -> None:
        if not resource_id:
            return
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self.provider.destroy, resource_id)
        except Exception as e:
            logger.warning("rollback destroy failed for %s: %s", resource_id, e)

    @staticmethod
    def _record_rollout_id(record: EnvServer) -> str | None:
        try:
            metadata = json.loads(record.metadata_json or "{}")
        except (json.JSONDecodeError, TypeError):
            return None
        rollout_id = metadata.get("rollout_id")
        return str(rollout_id) if rollout_id else None
