"""
The running deployment's HTTP API, as the harness uses it.

Everything here goes through Caddy, the same front door a browser uses:
`Caddyfile.local` routes `/api/*` to `backend:8000`. That is the point of
choosing this seam -- an auth, proxy, or Mem0-wiring regression is exactly the
class of failure a live demo hits, and an in-process harness would not see it.
"""
import httpx

from evals.config import EvalConfig
from evals.drivers.base import Student

API = "/api/v1"

# The drivers set their own generous timeouts; these calls inherit httpx's 5s
# default without this. Clearing Student Memory is the slow one -- the route
# deletes serially, one Mem0 round trip per memory -- and a ReadTimeout there
# voids every run, so a slow Mem0 would report INCONCLUSIVE rather than
# measuring anything.
STACK_TIMEOUT_SECONDS = 60.0


def _checked(response: httpx.Response, doing: str) -> httpx.Response:
    """Turn a bad status into a message an operator can act on.

    The response body is deliberately not quoted: it may carry a token or a
    credential, and this text goes to a terminal and possibly a CI log.
    """
    if response.status_code >= 400:
        raise RuntimeError(
            f"The deployment refused while {doing}: HTTP {response.status_code}. "
            f"Check EVAL_BASE_URL and that the stack is running."
        )
    return response


async def sign_in(client: httpx.AsyncClient, config: EvalConfig) -> Student:
    """Sign the eval Student in and hold their token for the run.

    One sign-in per invocation, not per run: the token outlives the individual
    runs and re-authenticating twenty times would measure the login rate
    limiter (api/config.py: login_rate_limit_attempts defaults to 10) rather
    than the tutor.
    """
    # Relative, like every other call here: the client is built with
    # `base_url=config.base_url` in evals/__main__.py, so the two would only
    # ever disagree by accident.
    try:
        response = await client.post(
            f"{API}/auth/login",
            json={"email": config.student_email, "password": config.student_password},
            timeout=STACK_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as unreachable:
        raise RuntimeError(
            f"Could not reach the deployment at {config.base_url}: {unreachable}"
        ) from unreachable

    body = _checked(response, "signing the eval Student in").json()
    return Student(student_id=body["user"]["id"], token=body["session"]["access_token"])


async def memory_total(client: httpx.AsyncClient, student: Student) -> int:
    """How many memories this Student currently has."""
    response = await client.get(f"{API}/memory/", headers=student.headers, timeout=STACK_TIMEOUT_SECONDS)
    return int(_checked(response, "reading Student Memory").json()["total"])


async def clear_memory(client: httpx.AsyncClient, student: Student) -> int:
    """Clear this Student's memories and return how many went.

    `confirm=true` is required by the route; without it the clear is refused
    and memory would quietly survive into the next run.
    """
    response = await client.delete(
        f"{API}/memory/?confirm=true", headers=student.headers, timeout=STACK_TIMEOUT_SECONDS
    )
    return int(_checked(response, "clearing Student Memory").json().get("deleted_count") or 0)
