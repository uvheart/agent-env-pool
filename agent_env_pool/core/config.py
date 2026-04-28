from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for the standalone AgentEnvPool service."""

    database_url: str = "sqlite+aiosqlite:///./agent_env_pool.db"
    docker_browser_image: str = "browser-use-chrome:latest"
    docker_browser_port: int = 9223
    public_host: str = "127.0.0.1"
    docker_ready_host: str | None = None
    container_name_prefix: str = "agent-env-pool"

    max_pool_size: int = 32
    ready_check_timeout_seconds: int = 60
    ready_check_interval_seconds: float = 1.0

    model_config = SettingsConfigDict(
        env_prefix="AGENT_ENV_POOL_",
        env_file=".env",
        extra="ignore",
    )

    @property
    def sqlite_path(self) -> Path | None:
        url = self.database_url
        for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
            if url.startswith(prefix):
                return Path(url[len(prefix):]).expanduser()
        return None


@lru_cache
def get_settings() -> Settings:
    return Settings()
