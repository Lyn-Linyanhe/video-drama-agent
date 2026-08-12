"""Public visual-style catalog routes."""

from fastapi import APIRouter

from src.services.visual_styles import get_visual_styles

router = APIRouter()


@router.get("/api/visual-styles")
async def list_visual_styles():
    return {"styles": get_visual_styles()}
