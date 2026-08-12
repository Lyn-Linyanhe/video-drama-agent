"""FastAPI routes extracted from the original server entrypoint."""

from fastapi import APIRouter
from ..context import *

router = APIRouter()

@router.get("/api/health")
async def health():
    """健康检查。"""
    return {"status": "ok", "provider": video_gen_agent.api_client.provider_name}

__all__ = [name for name in globals() if not name.startswith("__")]
