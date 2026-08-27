"""
Student profile routes.
"""
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from api.database.repository import get_repository
from api.dependencies import get_current_user
from api.models.user import UpdateProfileRequest, UserProfile

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/users", tags=["users"])

SUPPORTED_LANGUAGES = [
    "en-US", "es-ES", "es-MX", "vi-VN",
    "fr-FR", "de-DE", "ja-JP", "ko-KR", "zh-CN",
]
DEFAULT_LANGUAGE = "en-US"


class UpdateLanguageRequest(BaseModel):
    """Request model for updating language preference."""
    language: str  # e.g. "en-US", "es-ES", "vi-VN"


def _profile(user, record: dict | None) -> UserProfile:
    record = record or {}
    return UserProfile(
        id=user.id,
        email=record.get("email") or user.email or "",
        name=record.get("name") or user.name,
        preferences=record.get("preferences") or {},
        created_at=_created_at(record),
    )


def _created_at(record: dict) -> datetime:
    raw = record.get("created")
    if isinstance(raw, str) and raw:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00").replace(" ", "T", 1))
        except ValueError:
            pass
    return datetime.now()


@router.get("/me", response_model=UserProfile)
async def get_profile(user=Depends(get_current_user)):
    """Get the authenticated Student's profile."""
    record = await get_repository().get_student(user.id)
    return _profile(user, record)


@router.patch("/me", response_model=UserProfile)
async def update_profile(data: UpdateProfileRequest, user=Depends(get_current_user)):
    """Update the authenticated Student's name or preferences."""
    changes: dict = {}
    if data.name is not None:
        changes["name"] = data.name
    if data.preferences is not None:
        changes["preferences"] = data.preferences

    repository = get_repository()
    if changes:
        await repository.update_student(user.id, changes)

    return _profile(user, await repository.get_student(user.id))


@router.get("/me/language")
async def get_language_preference(user=Depends(get_current_user)):
    """Get the authenticated Student's language preference."""
    record = await get_repository().get_student(user.id)
    return {"language": (record or {}).get("preferred_language") or DEFAULT_LANGUAGE}


@router.patch("/me/language")
async def update_language_preference(
    data: UpdateLanguageRequest, user=Depends(get_current_user)
):
    """Update the authenticated Student's language preference."""
    if data.language not in SUPPORTED_LANGUAGES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported language. Supported: {', '.join(SUPPORTED_LANGUAGES)}",
        )

    await get_repository().update_student(user.id, {"preferred_language": data.language})
    logger.info(f"Updated language preference for student {user.id} to {data.language}")

    return {"language": data.language, "message": "Language preference updated successfully"}
