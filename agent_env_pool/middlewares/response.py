"""Middleware that wraps JSON responses with a standard envelope.

Every JSON response is transformed into::

    {
        "meta": {"trace_id": "<current trace_id>"},
        "data": <original body>
    }
"""

import json

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, _StreamingResponse
from starlette.responses import JSONResponse, StreamingResponse

from agent_env_pool.core.context import get_trace_id
from agent_env_pool.core.logging import logger_manager

logger = logger_manager.get_logger(__name__)


def _wrap(data):
    return {
        "meta": {"trace_id": get_trace_id()},
        "data": data,
    }


class ResponseMiddleware(BaseHTTPMiddleware):

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        original_headers = dict(response.headers)

        if isinstance(response, (StreamingResponse, _StreamingResponse)) and \
                str(original_headers.get("content-type")) == "application/json":
            body = b"".join([chunk async for chunk in response.body_iterator])
            try:
                original_headers.pop("content-length", None)
                wrapped = _wrap(json.loads(body))
                return JSONResponse(
                    content=wrapped,
                    status_code=response.status_code,
                    headers=original_headers,
                )
            except json.JSONDecodeError:
                return response

        elif isinstance(response, JSONResponse):
            try:
                raw = response.body
                if isinstance(raw, (memoryview,)):
                    raw = raw.tobytes()
                if isinstance(raw, (bytes, bytearray)):
                    raw = raw.decode("utf-8")
                data = json.loads(raw)
            except Exception:
                return response

            original_headers.pop("content-length", None)
            return JSONResponse(
                content=_wrap(data),
                status_code=response.status_code,
                headers=original_headers,
            )

        return response
