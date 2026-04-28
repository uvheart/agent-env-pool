"""SQLAlchemy ORM models for AgentEnvPool."""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ServerStatus:
    """Lifecycle states for browser sandbox environments.

    Quota semantics: rows in ACTIVE_STATUSES occupy a pool slot.
    """

    STARTING = "starting"    # quota claimed, container not yet ready
    RUNNING = "running"      # container up, CDP ready, idle
    OCCUPIED = "occupied"    # handed to a rollout worker
    ERROR = "error"          # creation or runtime failure
    STOPPING = "stopping"    # teardown in progress
    STOPPED = "stopped"      # released, slot freed

    ACTIVE_STATUSES = {STARTING, RUNNING, OCCUPIED, STOPPING}


class EnvServer(Base):
    """Browser sandbox record managed by the local Docker provider."""

    __tablename__ = "env_servers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    server_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)

    env_type: Mapped[str] = mapped_column(String(64), nullable=False, comment="browser-use")
    runtime: Mapped[str] = mapped_column(String(32), nullable=False, comment="docker")
    image: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), index=True, nullable=False, default=ServerStatus.STARTING)

    resource_id: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="docker container id")
    cdp_url: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="Chrome DevTools Protocol endpoint")
    host: Mapped[str | None] = mapped_column(String(128), nullable=True)
    port: Mapped[int | None] = mapped_column(Integer, nullable=True)

    endpoints_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None, comment="soft delete")
