"""
Establishing empty memory, and refusing to proceed without it.

Empty memory cannot be assumed against a real Mem0, because both surfaces write
to it during a run: api/routers/chat.py:150 calls mem0.add() on the incoming
message BEFORE mem0.search(), so a Student's second run already contains their
first; and agent.py's shutdown callback writes the whole Tutor Session history
when a voice session ends.

So it is cleared and then verified. A precondition that did not hold voids the
run -- the harness is not entitled to conclude anything about a tutor that was
given memories.
"""
import httpx
import pytest

from evals.config import EvalConfig
from evals.precondition import PreconditionFailed, ensure
from evals.stack import clear_memory, memory_total, sign_in

CONFIG = EvalConfig(
    base_url="http://testserver",
    student_email="evals@example.com",
    student_password="password",
    google_api_key="key",
)


def stack(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://testserver")


def student():
    from evals.drivers.base import Student

    return Student(student_id="aaaaaaaaaa11111", token="a-token")


# -- Signing in -------------------------------------------------------------


async def test_sign_in_returns_the_student_identity_and_token():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "user": {"id": "aaaaaaaaaa11111", "email": "evals@example.com"},
                "session": {
                    "access_token": "a-token",
                    "refresh_token": "r",
                    "expires_at": 0,
                    "token_type": "bearer",
                },
            },
        )

    async with stack(handler) as client:
        signed_in = await sign_in(client, CONFIG)

    assert (signed_in.student_id, signed_in.token) == ("aaaaaaaaaa11111", "a-token")


async def test_the_student_carries_a_bearer_header():
    assert student().headers == {"Authorization": "Bearer a-token"}


async def test_rejected_credentials_are_refused_clearly():
    """An operator who mistyped the password should be told that, not see a KeyError."""

    def handler(request):
        return httpx.Response(401, json={"detail": "Invalid credentials"})

    async with stack(handler) as client:
        with pytest.raises(RuntimeError) as refusal:
            await sign_in(client, CONFIG)

    assert "401" in str(refusal.value)


async def test_an_unreachable_stack_is_refused_clearly():
    def handler(request):
        raise httpx.ConnectError("connection refused")

    async with stack(handler) as client:
        with pytest.raises(RuntimeError):
            await sign_in(client, CONFIG)


# -- Memory -----------------------------------------------------------------


async def test_memory_total_reads_the_count():
    def handler(request):
        return httpx.Response(200, json={"memories": [{"id": "1"}], "total": 1})

    async with stack(handler) as client:
        assert await memory_total(client, student()) == 1


async def test_clearing_requires_the_confirm_flag():
    """Without ?confirm=true the route refuses, and memory would silently survive."""
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"message": "cleared", "deleted_count": 3})

    async with stack(handler) as client:
        await clear_memory(client, student())

    assert "confirm=true" in seen["url"]


async def test_clearing_uses_delete():
    seen = {}

    def handler(request):
        seen["method"] = request.method
        return httpx.Response(200, json={"message": "cleared", "deleted_count": 0})

    async with stack(handler) as client:
        await clear_memory(client, student())

    assert seen["method"] == "DELETE"


# -- The precondition -------------------------------------------------------


async def test_empty_memory_clears_then_verifies():
    """Clearing is not enough. The verify is what makes this a precondition."""
    calls = []

    def handler(request):
        calls.append(request.method)
        if request.method == "DELETE":
            return httpx.Response(200, json={"message": "cleared", "deleted_count": 2})
        return httpx.Response(200, json={"memories": [], "total": 0})

    async with stack(handler) as client:
        await ensure("empty_memory", client, student())

    assert calls == ["DELETE", "GET"]


async def test_memory_that_survives_the_clear_fails_the_precondition():
    """The run must void rather than judge a tutor that really did have memories."""

    def handler(request):
        if request.method == "DELETE":
            return httpx.Response(200, json={"message": "cleared", "deleted_count": 0})
        return httpx.Response(200, json={"memories": [{"id": "1"}], "total": 1})

    async with stack(handler) as client:
        with pytest.raises(PreconditionFailed):
            await ensure("empty_memory", client, student())


async def test_a_precondition_failure_says_what_was_left_behind():
    def handler(request):
        if request.method == "DELETE":
            return httpx.Response(200, json={"message": "cleared", "deleted_count": 0})
        return httpx.Response(200, json={"memories": [], "total": 4})

    async with stack(handler) as client:
        with pytest.raises(PreconditionFailed) as refusal:
            await ensure("empty_memory", client, student())

    assert "4" in str(refusal.value)


async def test_the_none_precondition_touches_nothing():
    """A future case that wants a warm memory must not have it cleared."""
    calls = []

    def handler(request):
        calls.append(request.method)
        return httpx.Response(200, json={})

    async with stack(handler) as client:
        await ensure("none", client, student())

    assert calls == []


async def test_an_unknown_precondition_is_refused():
    """A typo in a case must fail loudly, not silently establish nothing."""
    async with stack(lambda r: httpx.Response(200, json={})) as client:
        with pytest.raises(PreconditionFailed):
            await ensure("warm_memory", client, student())
