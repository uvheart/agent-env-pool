from fastapi import APIRouter

from agent_env_pool.api.v1.servers import router as servers_router

router = APIRouter(prefix="/api/v1")
router.include_router(servers_router)
