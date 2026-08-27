"""
FastAPI dependencies for authentication and shared resources.
"""
import logging
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from api.database.pocketbase_client import (
    PocketBaseClient,
    ProviderRejected,
    ProviderUnavailable,
)

logger = logging.getLogger(__name__)

security = HTTPBearer()

_provider_client: Optional[PocketBaseClient] = None


class AuthUser(BaseModel):
    id: str
    email: Optional[str] = None
    name: Optional[str] = None


class IdentityProviderNotConfigured(RuntimeError):
    """Raised when token verification is attempted with no provider wired up."""


def set_provider_client(client: Optional[PocketBaseClient]) -> None:
    """Install the process-wide provider client. Used at startup, and by tests."""
    global _provider_client
    _provider_client = client


def get_provider_client() -> PocketBaseClient:
    if _provider_client is None:
        raise IdentityProviderNotConfigured("No identity provider is configured")
    return _provider_client


async def verify_token(token: str) -> dict:
    """
    Verify a caller's token against PocketBase.

    The token is presented to the auth-refresh endpoint by the unprivileged
    verifier, which carries no credential of its own. There is no cache: any
    expiry window would be a revocation delay, and the request volume does not
    justify one (the private persistence boundary).
    """
    return await get_provider_client().verify_student_token(token)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> AuthUser:
    """
    Verify the caller's token and return the current Student.

    Raises:
        HTTPException: 401 when PocketBase definitively rejects the token,
            503 when it cannot be reached or is not configured. An unavailable
            provider is never reported as bad credentials, because that would
            clear valid browser auth state.
    """
    try:
        record = await verify_token(credentials.credentials)
    except ProviderRejected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
    except (ProviderUnavailable, IdentityProviderNotConfigured) as e:
        logger.error(f"Authentication unavailable: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is temporarily unavailable",
        )

    return AuthUser(
        id=record["id"],
        email=record.get("email"),
        name=record.get("name"),
    )


async def get_current_user_id(
    user: AuthUser = Depends(get_current_user)
) -> str:
    """Get current Student's identifier from the authenticated user."""
    return user.id
