"""Middleware that assigns a trace_id to every incoming request.

If the client sends an ``x-trace-id`` header the value is reused; otherwise a
new UUID-hex is generated. The id is stored in a ContextVar so that all
downstream logging and response wrapping can read it.
"""

import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from agent_env_pool.core.context import set_trace_id
from agent_env_pool.core.logging import logger_manager

logger = logger_manager.get_logger(__name__)


class TraceMiddleware(BaseHTTPMiddleware):

    async def dispatch(self, request: Request, call_next):
        try:
            raw = dict(request.scope.get("headers", [])).get(b"x-trace-id")
            trace_id = raw.decode() if raw else uuid.uuid4().hex
            set_trace_id(trace_id)
        except Exception as e:
            logger.error("set trace_id failed: %s", e)
        response = await call_next(request)
        return response
