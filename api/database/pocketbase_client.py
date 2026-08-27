"""
The PocketBase HTTP boundary.

This is the only module that knows PocketBase URLs, filter syntax, and token
handling. Everything above it sees `DocumentStore` and the repository.

Two logically separate clients live here, and they must stay separate. The
verifier carries no credential of its own and presents a caller's token on the
single request that needs it; the data client holds the superuser token. A
single shared client whose authorization header is rewritten per request would
leak one Student's token into another concurrent request.
"""
import asyncio
import logging
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


class ProviderRejected(RuntimeError):
    """PocketBase definitively rejected the credential: invalid or expired."""


class ProviderUnavailable(RuntimeError):
    """PocketBase could not be reached, or failed on its own account."""


class PocketBaseClient:
    """Talks to PocketBase over the Compose network."""

    def __init__(
        self,
        base_url: str,
        superuser_email: str,
        superuser_password: str,
        *,
        transport: Optional[httpx.BaseTransport] = None,
        timeout: float = 10.0,
    ):
        self._base_url = base_url.rstrip("/")
        self._superuser_email = superuser_email
        self._superuser_password = superuser_password

        # No default authorization header: a caller's token is passed per
        # request and never stored on the client.
        self._verifier = httpx.AsyncClient(
            base_url=self._base_url, transport=transport, timeout=timeout
        )
        self._data = httpx.AsyncClient(
            base_url=self._base_url, transport=transport, timeout=timeout
        )
        self._superuser_token: Optional[str] = None
        self._auth_lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._verifier.aclose()
        await self._data.aclose()

    # -- identity -----------------------------------------------------------

    async def verify_student_token(self, token: str) -> dict[str, Any]:
        """Present a caller's token to PocketBase and return the Student record."""
        response = await self._send(
            self._verifier,
            "POST",
            "/api/collections/users/auth-refresh",
            headers={"Authorization": token},
        )
        if response.status_code == 200:
            return response.json()["record"]
        raise self._classify(response, "token verification")

    async def authenticate_student(self, email: str, password: str) -> dict[str, Any]:
        """Exchange a Student's password for a token."""
        response = await self._send(
            self._verifier,
            "POST",
            "/api/collections/users/auth-with-password",
            json={"identity": email, "password": password},
        )
        if response.status_code == 200:
            return response.json()
        raise self._classify(response, "password authentication")

    # -- records ------------------------------------------------------------

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Optional[dict[str, Any]] = None,
        params: Optional[dict[str, Any]] = None,
    ) -> httpx.Response:
        """Perform a superuser-authenticated request, renewing the token once."""
        token = await self._superuser_auth()
        response = await self._send(
            self._data, method, path, json=json, params=params,
            headers={"Authorization": token},
        )
        if response.status_code == 401:
            token = await self._superuser_auth(force=True)
            response = await self._send(
                self._data, method, path, json=json, params=params,
                headers={"Authorization": token},
            )
        if response.status_code >= 500:
            raise ProviderUnavailable(
                f"PocketBase returned {response.status_code} for {method} {path}"
            )
        return response

    async def _superuser_auth(self, *, force: bool = False) -> str:
        async with self._auth_lock:
            if self._superuser_token and not force:
                return self._superuser_token
            response = await self._send(
                self._data,
                "POST",
                "/api/collections/_superusers/auth-with-password",
                json={
                    "identity": self._superuser_email,
                    "password": self._superuser_password,
                },
            )
            if response.status_code != 200:
                raise ProviderUnavailable(
                    "PocketBase rejected the superuser credential "
                    f"({response.status_code}); the deployment is misconfigured"
                )
            self._superuser_token = response.json()["token"]
            return self._superuser_token

    # -- plumbing -----------------------------------------------------------

    async def _send(self, client: httpx.AsyncClient, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            return await client.request(method, path, **kwargs)
        except httpx.HTTPError as e:
            raise ProviderUnavailable(f"PocketBase unreachable: {e}") from e

    @staticmethod
    def _classify(response: httpx.Response, what: str) -> RuntimeError:
        if response.status_code >= 500:
            return ProviderUnavailable(
                f"PocketBase failed during {what}: {response.status_code}"
            )
        return ProviderRejected(f"PocketBase rejected {what}: {response.status_code}")
