"""FastAPI application entry point for AgentEnvPool."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from starlette.responses import JSONResponse

from agent_env_pool.api import router
from agent_env_pool.core.context import get_trace_id
from agent_env_pool.core.database import init_db
from agent_env_pool.core.logging import logger_manager
from agent_env_pool.middlewares import ResponseMiddleware, TraceMiddleware

logger = logger_manager.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    logger.info("AgentEnvPool started — database initialised")
    yield
    logger.info("AgentEnvPool shutting down")


def create_app() -> FastAPI:
    application = FastAPI(
        title="AgentEnvPool",
        description="Lightweight Docker browser sandbox pool for Agent RL rollout.",
        version="0.1.0",
        lifespan=lifespan,
    )

    @application.exception_handler(HTTPException)
    async def _http_exc(request: Request, exc: HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "meta": {"trace_id": get_trace_id()},
                "error": exc.detail,
            },
        )

    # Middleware execution order: TraceMiddleware → ResponseMiddleware → routes
    application.add_middleware(ResponseMiddleware)
    application.add_middleware(TraceMiddleware)

    application.include_router(router)
    return application


app = create_app()


if __name__ == "__main__":
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(description="AgentEnvPool API Server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8100)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    logger.info("Starting AgentEnvPool on %s:%s", args.host, args.port)
    uvicorn.run(app, host=args.host, port=args.port)
