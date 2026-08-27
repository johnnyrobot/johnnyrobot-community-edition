"""
User-related Pydantic models for request/response validation.
"""
from pydantic import BaseModel, EmailStr, Field, ConfigDict
from typing import Optional, Union
from datetime import datetime


class SignupRequest(BaseModel):
    """User signup request."""
    email: EmailStr
    password: str = Field(..., min_length=8, description="Minimum 8 characters")
    name: str = Field(..., min_length=1, max_length=255)


class LoginRequest(BaseModel):
    """User login request.

    ``email`` is EmailStr, not a plain str: malformed input is rejected with
    a 422 before it reaches the router at all (the email-validation contract). Test fixtures use
    ``@example.com``-style addresses rather than the RFC 2606 reserved
    ``.test`` TLD, which the installed email-validator refuses outright.
    """
    email: EmailStr
    password: str


class RefreshTokenRequest(BaseModel):
    """Token refresh request."""
    refresh_token: str


class UserProfile(BaseModel):
    """User profile response."""
    id: str
    email: str
    name: Optional[str] = None
    preferences: dict = {}
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)


class UpdateProfileRequest(BaseModel):
    """Update user profile request."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    preferences: Optional[dict] = None


class SessionResponse(BaseModel):
    """Authentication session response."""
    access_token: str
    refresh_token: str
    expires_at: Union[int, str]  # Accept both int and string
    token_type: str = "bearer"


class AuthResponse(BaseModel):
    """Complete authentication response."""
    user: UserProfile
    session: SessionResponse
