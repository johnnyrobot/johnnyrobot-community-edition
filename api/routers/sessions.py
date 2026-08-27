"""
LiveKit session management routes.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from api.models.session import LiveKitTokenResponse, EndSessionRequest
from api.database.repository import get_repository
from api.dependencies import get_current_user, get_current_user_id
from api.config import get_settings
from livekit import api
from livekit.api import LiveKitAPI
from datetime import datetime
import secrets
import time
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/session", tags=["sessions"])


@router.post("/token", response_model=LiveKitTokenResponse)
async def create_session_token(
    user_id: str = Depends(get_current_user_id),
    user = Depends(get_current_user)
):
    """
    Generate LiveKit access token for authenticated user.

    Creates a unique room for the user session and returns token for connection.
    """
    settings = get_settings()
    # Millisecond resolution plus a short random suffix: a bare one-second
    # timestamp lets two session starts by the same Student within the same
    # second collide, and sessions.room_name carries a global unique index
    # -- a collision would silently drop the second Tutor Session
    # record. the PocketBase identity contract requires the Student's record id to stay the
    # room-name component.
    timestamp_ms = int(time.time() * 1000)
    entropy = secrets.token_hex(4)
    room_name = f"user_{user_id}_{timestamp_ms}_{entropy}"

    # Mint the LiveKit credential. Anything that goes wrong in here -- a
    # missing LiveKit key, a Student record with no email to derive a display
    # name from, a signing failure -- is an internal fault, so the exception
    # goes to the log and the caller gets a static message. Letting it escape
    # instead would produce Starlette's plain-text 500, which carries no CORS
    # headers and so reads to the browser as a network error rather than a
    # server error.
    try:
        token = api.AccessToken(
            api_key=settings.livekit_api_key,
            api_secret=settings.livekit_api_secret
        )

        # Set identity and grants
        # Ensure user name is string
        user_name = (user.email or "").split("@")[0]
        if user.name:
            user_name = user.name

        token.with_identity(user_id)
        token.with_name(user_name)
        token.with_grants(api.VideoGrants(
            room_join=True,
            room=room_name,
            can_publish=True,
            can_subscribe=True,
            can_publish_data=True
        ))
        access_token = token.to_jwt()
    except Exception as e:
        logger.error(f"Failed to mint a session token for student {user_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate session token",
        )

    # Record the Tutor Session. A failure here must not cost the Student
    # their session, but it is logged rather than swallowed silently.
    try:
        await get_repository().create_tutor_session(
            user_id, room_name, {"start_time": datetime.now().isoformat()}
        )
        logger.info(f"Created Tutor Session record: {room_name} for student {user_id}")
    except Exception as record_error:
        logger.warning(f"Failed to record Tutor Session {room_name}: {record_error}")

    # Explicitly dispatch the named agent to the room
    try:
        lk_api = LiveKitAPI(
            url=settings.livekit_url,
            api_key=settings.livekit_api_key,
            api_secret=settings.livekit_api_secret
        )

        # Create room and dispatch agent
        await lk_api.room.create_room(
            api.CreateRoomRequest(name=room_name)
        )
        await lk_api.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                room=room_name,
                agent_name="johnnyrobot-community-edition-voice-agent"
            )
        )
        logger.info(f"Dispatched agent to room: {room_name}")
        await lk_api.aclose()
    except Exception as lk_error:
        logger.error(f"LiveKit dispatch error: {str(lk_error)}")

    return LiveKitTokenResponse(
        token=access_token,
        room_name=room_name,
        url=settings.livekit_url
    )


@router.post("/end")
async def end_session(
    data: EndSessionRequest,
    user_id: str = Depends(get_current_user_id)
):
    """
    End the authenticated Student's most recent open Tutor Session.

    A "room" is LiveKit plumbing, not a product concept: the Student never
    names which session to end, so there is no room_name here. A stale open
    session older than the most recent one is left alone.
    """
    update_data = {"end_time": datetime.now().isoformat()}
    if data.transcript:
        update_data["transcript"] = data.transcript

    ended = await get_repository().end_open_tutor_session(user_id, update_data)
    if not ended:
        logger.warning(f"Student {user_id} has no open Tutor Session to end")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No open Tutor Session to end",
        )

    logger.info(f"Ended Tutor Session for student {user_id}")
    return {"message": "Session ended successfully"}


@router.get("/history")
async def get_session_history(
    user_id: str = Depends(get_current_user_id),
    limit: int = 10
):
    """
    Get user's session history.
    """
    sessions = await get_repository().list_tutor_sessions(user_id, limit=limit)

    return {"sessions": sessions, "count": len(sessions)}
