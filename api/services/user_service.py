"""
User service for managing user preferences including language settings.

These functions are async because every caller awaits them (agent.py and
language_tools.py). They were previously declared sync, so every `await` raised
TypeError into a caller's except block and the language preference silently
never loaded or persisted.
"""
import logging
from api.database.repository import get_repository

logger = logging.getLogger(__name__)

DEFAULT_LANGUAGE = "en-US"


async def get_user_language_preference(user_id: str) -> str:
    """
    Get user's preferred language from storage.

    Args:
        user_id: Student identifier

    Returns:
        Language code (e.g., "en-US", "es-ES"), or "en-US" when unset.
    """
    try:
        record = await get_repository().get_student(user_id)

        if record and record.get("preferred_language"):
            language = record["preferred_language"]
            logger.info(f"Loaded language preference for user {user_id}: {language}")
            return language

        logger.info(
            f"No language preference found for user {user_id}, "
            f"defaulting to {DEFAULT_LANGUAGE}"
        )
        return DEFAULT_LANGUAGE

    except Exception as e:
        logger.warning(f"Could not load language preference for user {user_id}: {e}")
        return DEFAULT_LANGUAGE


async def update_user_language_preference(user_id: str, language_code: str) -> bool:
    """
    Update user's preferred language in storage.

    Args:
        user_id: Student identifier
        language_code: Language code (e.g., "en-US", "es-ES")

    Returns:
        True if successful, False otherwise.
    """
    try:
        await get_repository().update_student(
            user_id, {"preferred_language": language_code}
        )
        logger.info(
            f"Updated language preference for user {user_id} to {language_code}"
        )
        return True

    except Exception as e:
        logger.error(f"Error updating language preference for user {user_id}: {e}")
        return False


async def get_user_profile(user_id: str) -> dict:
    """
    Get complete user profile including language preference.

    Args:
        user_id: Student identifier

    Returns:
        User profile dictionary, empty when absent.
    """
    try:
        return await get_repository().get_student(user_id) or {}

    except Exception as e:
        logger.error(f"Error getting user profile for {user_id}: {e}")
        return {}
