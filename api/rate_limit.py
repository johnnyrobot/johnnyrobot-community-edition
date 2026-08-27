"""
Login throttling, keyed on the real client address.

PocketBase's built-in per-address limiter is useless in this arrangement:
FastAPI is the sole front door, so every login reaches PocketBase from one
container address and the limiter would either throttle all Students together
or none (the private persistence boundary). Brute-force protection therefore belongs here.

The counter is in-process and resets on restart. That is proportionate for a
single-instance demo deployment; a multi-instance deployment would need shared
state, and this docstring is where to notice that.
"""
import logging
import time
from collections import OrderedDict

from fastapi import Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from api.config import get_settings

logger = logging.getLogger(__name__)

LOGIN_PATH_SUFFIX = "/auth/login"

# An upper bound on how many distinct addresses this process tracks failures
# for at once. Independent of the X-Forwarded-For trust fix in
# `client_address` below (defense in depth, the rate-limit storage bound): any other future path that
# hands this middleware many distinct trusted addresses -- real IPv6 churn at
# scale, say -- must still not grow this dict forever. The oldest-tracked
# address is evicted first once the cap is hit.
_MAX_TRACKED_ADDRESSES = 10_000

# address -> list of failure timestamps within the window.
#
# A plain (insertion-ordered) dict, not `defaultdict(list)`: a defaultdict
# inserts a permanent entry for a key on *read alone*, via `__missing__` -- so
# an attacker who fully controls the address string could grow this dict
# simply by causing lookups, never mind ever failing a login. Every
# place below that inspects an address's history reads with
# `.get(address, [])`, and an address with no *current* failures is removed
# from the dict entirely rather than left holding an empty list.
_failures: "OrderedDict[str, list[float]]" = OrderedDict()


def reset_for_tests() -> None:
    """Clear the counters. Used by tests only."""
    _failures.clear()


def client_address(request: Request) -> str:
    """The address to throttle on.

    Caddy is the only thing in front of FastAPI (the private persistence boundary), and its default
    `reverse_proxy` behavior APPENDS its own observed remote address to
    whatever X-Forwarded-For a request already carries -- it does not strip a
    client-supplied one (`Caddyfile` has no `header_up` /
    `trusted_proxies` override to change that). The trustworthy hop is
    therefore the LAST entry in the chain, the one Caddy itself appended --
    not the first: a caller can send any value it likes ahead of Caddy's own,
    and reading the first entry would let that caller pick its own throttle
    key and rotate it on every request. Keying on the raw socket
    address would instead key every request to Caddy's own container address
    and throttle all Students as one.
    """
    settings = get_settings()
    if settings.trust_forwarded_for:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def _record_failure(address: str, now: float) -> None:
    """Append a failure timestamp for `address`, bounding total tracked
    addresses. Eviction is oldest-tracked-first -- a coarse but
    sufficient policy for a defense-in-depth cap; the primary defense is
    `client_address` reading the correct hop in the first place.
    """
    if address not in _failures and len(_failures) >= _MAX_TRACKED_ADDRESSES:
        _failures.popitem(last=False)
    _failures.setdefault(address, []).append(now)


class LoginRateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not request.url.path.endswith(LOGIN_PATH_SUFFIX):
            return await call_next(request)

        settings = get_settings()
        address = client_address(request)
        now = time.monotonic()
        window = settings.login_rate_limit_window_seconds

        # `.get` never inserts -- unlike `defaultdict(list)`, checking an
        # address that has never failed leaves no trace in `_failures`.
        recent = [t for t in _failures.get(address, []) if now - t < window]
        if recent:
            _failures[address] = recent
        else:
            _failures.pop(address, None)

        if len(recent) >= settings.login_rate_limit_attempts:
            logger.warning(f"Throttling login attempts from {address}")
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"detail": "Too many sign-in attempts. Try again later."},
            )

        response = await call_next(request)

        if response.status_code == status.HTTP_401_UNAUTHORIZED:
            _record_failure(address, now)
        elif response.status_code == status.HTTP_200_OK:
            _failures.pop(address, None)

        return response
