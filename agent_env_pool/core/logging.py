"""Centralised logging with trace_id injection.

Every log record gets ``trace_id`` from the current request context so the
formatter can include it automatically.
"""

import logging
import os

from agent_env_pool.core.context import get_trace_id


class _TraceIdFilter(logging.Filter):
    """Inject ``trace_id`` into every LogRecord."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = get_trace_id()  # type: ignore[attr-defined]
        return True


class LoggerManager:
    """Provides loggers whose output includes ``trace_id``."""

    _NAME = "agent_env_pool"

    def __init__(self, log_dir: str = "logs") -> None:
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)

        self.formatter = logging.Formatter(
            "[%(levelname)-5s][%(asctime)s][%(filename)s:%(lineno)d %(funcName)s()]"
            "[trace_id=%(trace_id)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        self._trace_filter = _TraceIdFilter()

        level = os.getenv("AGENT_ENV_POOL_LOG_LEVEL", "INFO")

        file_handler = logging.FileHandler(
            os.path.join(self.log_dir, f"{self._NAME}.log"),
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(self.formatter)
        file_handler.addFilter(self._trace_filter)

        console_handler = logging.StreamHandler()
        console_handler.setLevel(level)
        console_handler.setFormatter(self.formatter)
        console_handler.addFilter(self._trace_filter)

        root = logging.getLogger("")
        root.setLevel(level)
        root.addHandler(file_handler)
        root.addHandler(console_handler)

    def get_logger(self, name: str) -> logging.Logger:
        return logging.getLogger(name)


logger_manager = LoggerManager()
