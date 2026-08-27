"""
Authentication routes.

FastAPI is the sole front door (the private persistence boundary): the browser posts credentials here,
never to PocketBase. The token comes back in the response body and is held in
browser storage as a bearer credential rather than an HttpOnly cookie; that
trade is recorded in the private persistence boundary, mitigated by a short token lifetime and a
restrictive content-security-policy.

There is no signup route. A Deployment Operator provisions every Student
(the reset-only demo profile) and PocketBase keeps the users create rule locked.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, status

from api.database.pocketbase_client import ProviderRejected, ProviderUnavailable
from api.dependencies import AuthUser, IdentityProviderNotConfigured, get_current_user, get_provider_client
from api.models.user import AuthResponse, LoginRequest, SessionResponse, UserProfile
from datetime import datetime

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# One message for every failed login. Distinguishing "no such email" from
# "wrong password" enumerates provisioned Students.
_LOGIN_FAILED = "Incorrect email or password"


@router.post("/login", response_model=AuthResponse)
async def login(request: LoginRequest):
    """Exchange a Student's credentials for a bearer token."""
    try:
        result = await get_provider_client().authenticate_student(
            request.email, request.password
        )
    except ProviderRejected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_LOGIN_FAILED)
    except (ProviderUnavailable, IdentityProviderNotConfigured) as e:
        logger.error(f"Login unavailable: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is temporarily unavailable",
        )

    record = result["record"]
    return AuthResponse(
        user=UserProfile(
            id=record["id"],
            email=record.get("email", ""),
            name=record.get("name"),
            preferences=record.get("preferences") or {},
            created_at=_created_at(record),
        ),
        session=SessionResponse(
            access_token=result["token"],
            refresh_token="",
            expires_at="",
            token_type="bearer",
        ),
    )


@router.post("/logout")
async def logout():
    """Acknowledge a sign-out.

    The token is discarded by the client. This endpoint deliberately requires
    no valid credential: signing out of a shared lab machine must never be
    blocked by a token that has already expired.
    """
    return {"message": "Signed out"}


@router.get("/me", response_model=UserProfile)
async def read_current_student(user: AuthUser = Depends(get_current_user)):
    """Return the authenticated Student's identity."""
    # preferences and created_at are known-inaccurate placeholders on a
    # now-live path: the frontend's session-restore calls this route. Wiring
    # The real repository values is tracked separately.
    return UserProfile(
        id=user.id,
        email=user.email or "",
        name=user.name,
        preferences={},
        created_at=datetime.now(),
    )


def _created_at(record: dict) -> datetime:
    raw = record.get("created")
    if isinstance(raw, str) and raw:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00").replace(" ", "T", 1))
        except ValueError:
            pass
    return datetime.now()
