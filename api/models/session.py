"""
Session-related Pydantic models.
"""
from pydantic import BaseModel
from datetime import datetime
from typing import Optional


class LiveKitTokenResponse(BaseModel):
    """LiveKit access token response."""
    token: str
    room_name: str
    url: str


class ConversationSession(BaseModel):
    """Conversation session model."""
    id: str
    user_id: str
    livekit_room_id: str
    start_time: datetime
    end_time: Optional[datetime] = None
    transcript: Optional[dict] = None


class EndSessionRequest(BaseModel):
    """Request to end the caller's most recent open Tutor Session.

    No room_name: a "room" is LiveKit plumbing, not a product concept,
    and the Student never sees or chooses one. The endpoint always closes the
    caller's own most recent open Tutor Session, never one they name.
    """
    transcript: Optional[dict] = None
