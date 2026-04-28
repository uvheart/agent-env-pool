from fastapi import APIRouter

from agent_env_pool.api.v1 import router as v1_router

router = APIRouter()
router.include_router(v1_router)
