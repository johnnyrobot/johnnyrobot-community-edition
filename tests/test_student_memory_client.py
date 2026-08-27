"""
Where the Student Memory client comes from, and how often (process-wide memory client construction).

`mem0.AsyncMemoryClient.__init__` validates its API key with a blocking
`requests.get` that carries no timeout, so building one costs a full network
round trip bounded only by the OS. Every call site used to build its own,
inside a request handler, on the event loop -- so a stale key or an unreachable
Mem0 charged every Student that round trip, and stalled every other request
sharing the loop while it ran.

`test_student_memory_optional.py` covers what a Student sees when Mem0 is
missing or broken; this file covers what it *costs*. Three properties, and each
one is a different failure if it goes:

  - **once per process.** The build is cached, success or failure alike, so a
    key that will never work is refused once rather than on every message.
  - **off the event loop.** The build runs in a worker thread, so the loop
    keeps serving everyone else while it waits on the network.
  - **bounded.** A build that hangs is abandoned at `mem0_timeout_seconds` and
    The caller gets the no-op, so nothing waits on an OS-level TCP timeout --
    least of all the API's own startup.
"""
import asyncio
import importlib
import inspect
from types import SimpleNamespace

import google.genai.types as genai_types
import pytest
import requests

import api.main
from api.config import get_settings
from api.services import student_memory
from api.services.student_memory import NoOpMemoryClient

API = "/api/v1"

NO_OP_LOG = "Student Memory is a no-op"

REJECTED = ValueError("Error: Invalid API key")
UNREACHABLE = requests.exceptions.ConnectionError("api.mem0.ai unreachable")


def _recording(built, failure=None, blocking_for=0.0):
    """A stand-in for the real client that records every build.

    `built` is the list of keys handed to a constructor, so a test can say how
    many builds happened and with what -- the whole subject of this file.
    `blocking_for` reproduces the one thing that makes the real constructor
    expensive: it blocks the calling thread, exactly as `requests.get` does.
    """

    class _Recorder:
        def __init__(self, *args, **kwargs):
            built.append(kwargs.get("api_key"))
            if blocking_for:
                import time

                time.sleep(blocking_for)
            if failure is not None:
                raise failure

        async def add(self, *args, **kwargs):
            return {"results": []}

        async def search(self, *args, **kwargs):
            return []

        async def get_all(self, *args, **kwargs):
            return []

        async def delete(self, *args, **kwargs):
            return {"message": "deleted"}

        async def delete_all(self, *args, **kwargs):
            return {"message": "deleted"}

    return _Recorder


def _mem0_configured(monkeypatch, built, **kwargs):
    """Point the seam at a recording client with a key configured."""
    monkeypatch.setattr(student_memory, "AsyncMemoryClient", _recording(built, **kwargs))
    monkeypatch.setattr(student_memory.settings, "mem0_api_key", "a-configured-key")


# -- built once --------------------------------------------------------------


async def test_a_second_caller_gets_the_client_the_first_one_built(monkeypatch):
    """One process, one client. The second ask costs nothing."""
    built = []
    _mem0_configured(monkeypatch, built)

    first = await student_memory.get_memory_client()
    second = await student_memory.get_memory_client()

    assert first is second
    assert built == ["a-configured-key"]


async def test_a_rejected_key_is_refused_once_rather_than_on_every_ask(monkeypatch, caplog):
    """The expensive half of the failure is the retry, not the rejection.

    A key that Mem0 rejects will be rejected again, and each rejection costs a
    round trip. Caching the refusal is what turns "every message pays" into
    "the deployment paid once".
    """
    built = []
    _mem0_configured(monkeypatch, built, failure=REJECTED)

    with caplog.at_level("WARNING"):
        first = await student_memory.get_memory_client()
        second = await student_memory.get_memory_client()

    assert isinstance(first, NoOpMemoryClient)
    assert first is second
    assert built == ["a-configured-key"]
    assert NO_OP_LOG in caplog.text


async def test_an_unreachable_mem0_is_reached_for_once(monkeypatch):
    """mem0 converts only `HTTPError`, so a lost connection escapes as-is.

    It is also the failure that blocks longest, which makes it the one that
    most needs to happen at most once.
    """
    built = []
    _mem0_configured(monkeypatch, built, failure=UNREACHABLE)

    first = await student_memory.get_memory_client()
    second = await student_memory.get_memory_client()

    assert isinstance(first, NoOpMemoryClient)
    assert first is second
    assert built == ["a-configured-key"]


async def test_no_key_never_reaches_for_the_real_client(monkeypatch, caplog):
    """A knowingly-off deployment stays off the network entirely."""
    built = []
    _mem0_configured(monkeypatch, built)
    monkeypatch.setattr(student_memory.settings, "mem0_api_key", "")

    with caplog.at_level("INFO"):
        client = await student_memory.get_memory_client()

    assert isinstance(client, NoOpMemoryClient)
    assert built == []
    assert NO_OP_LOG in caplog.text


async def test_the_client_can_be_handed_back_for_the_next_build(monkeypatch):
    """Clearing the seam is what lets a process rebuild -- and what tests use.

    Without it the cache would outlive the settings it was built from, and one
    test's Mem0 would leak into the next.
    """
    built = []
    _mem0_configured(monkeypatch, built)

    await student_memory.get_memory_client()
    student_memory.set_memory_client(None)
    await student_memory.get_memory_client()

    assert built == ["a-configured-key", "a-configured-key"]


# -- off the event loop ------------------------------------------------------


async def test_building_the_client_leaves_the_event_loop_free(monkeypatch):
    """The validation blocks a thread, not the process.

    This is the availability half of process-wide memory client construction: `requests.get` inside the
    constructor holds the loop for its whole round trip, so one Student's
    stalled Mem0 lookup stalls every other Student's request behind it.
    """
    built = []
    _mem0_configured(monkeypatch, built, blocking_for=0.2)

    ticks = 0

    async def heartbeat():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    beating = asyncio.create_task(heartbeat())
    await student_memory.get_memory_client()
    beating.cancel()

    assert ticks >= 5, "the event loop stood still while the client was built"


async def test_a_build_that_hangs_is_abandoned_at_the_timeout(monkeypatch):
    """Nothing waits on an OS-level TCP timeout, startup least of all.

    The real constructor passes no timeout to `requests.get`, so an
    unreachable Mem0 that drops packets rather than refusing them blocks until
    The kernel gives up -- minutes. The seam stops waiting long before that and
    answers with the no-op.
    """
    built = []
    _mem0_configured(monkeypatch, built, blocking_for=0.5)
    monkeypatch.setattr(student_memory.settings, "mem0_timeout_seconds", 0.05)

    client = await asyncio.wait_for(student_memory.get_memory_client(), timeout=2)

    assert isinstance(client, NoOpMemoryClient)
    assert built == ["a-configured-key"]


async def test_a_build_abandoned_at_the_timeout_is_not_retried(monkeypatch):
    """A Mem0 that hangs must not hang the next caller too."""
    built = []
    _mem0_configured(monkeypatch, built, blocking_for=0.5)
    monkeypatch.setattr(student_memory.settings, "mem0_timeout_seconds", 0.05)

    first = await asyncio.wait_for(student_memory.get_memory_client(), timeout=2)
    second = await asyncio.wait_for(student_memory.get_memory_client(), timeout=2)

    assert isinstance(second, NoOpMemoryClient)
    assert first is second
    assert built == ["a-configured-key"]


# -- a build that works ------------------------------------------------------


async def test_a_working_key_yields_the_real_client(monkeypatch):
    """The degradation must not swallow the case Mem0 is meant for."""
    built = []
    _mem0_configured(monkeypatch, built)

    client = await student_memory.get_memory_client()

    assert not isinstance(client, NoOpMemoryClient)
    assert built == ["a-configured-key"]


async def test_a_disabled_timeout_still_builds_the_client(monkeypatch):
    """An operator who sets the bound to 0 gets an unbounded wait, not a broken one."""
    built = []
    _mem0_configured(monkeypatch, built)
    monkeypatch.setattr(student_memory.settings, "mem0_timeout_seconds", 0.0)

    client = await student_memory.get_memory_client()

    assert not isinstance(client, NoOpMemoryClient)
    assert built == ["a-configured-key"]


# -- every site shares the one client ----------------------------------------
#
# The properties above are only worth having if nothing goes around them, so
# each site is checked for the build it must no longer do. Reading the source
# for the absence is what makes that hold for the site with no HTTP surface --
# The voice agent -- and it fails loudly on the obvious regression, a site that
# quietly reintroduces its own `AsyncMemoryClient(...)`.


class _StubGeminiService:
    """Answers with an empty File Search tool so the chat route can proceed."""

    async def resolve_library_store(self, user_id):
        """Every Student Library is its own store since per-Library store isolation."""
        return f"fileSearchStores/{user_id}-lib"

    def get_search_tool_config(self, user_id, store_name, textbook_id=None):
        return genai_types.Tool()


class _StubGenAIClient:
    """Answers a chat turn without leaving the process."""

    def __init__(self, *args, **kwargs):
        self.models = self

    def generate_content(self, **kwargs):
        return SimpleNamespace(text="What do you already know about it?")


def _chat_without_gemini(monkeypatch):
    """Keep the chat route off the network, leaving Student Memory the subject."""
    from api.routers import chat

    monkeypatch.setattr(chat.settings, "google_api_key", "stub-google-key")
    monkeypatch.setattr(chat, "GeminiService", _StubGeminiService)
    monkeypatch.setattr(chat, "Client", _StubGenAIClient)


async def _send_a_chat(client, alice):
    return await client.post(
        f"{API}/chat/message",
        headers=alice["headers"],
        json={"message": "why does this integral diverge?", "history": []},
    )


@pytest.mark.parametrize(
    "module",
    [
        "api.routers.chat",
        "api.routers.memory",
        "api.services.canvas_memory_service",
        "agent",
    ],
)
def test_no_call_site_builds_a_client_of_its_own(module):
    """`student_memory` holds the only import of the real client.

    Every other module asks the seam. A site that builds its own would be back
    to a per-request round trip on the event loop, and none of the guarantees
    above would reach it.
    """
    source = inspect.getsource(importlib.import_module(module))

    assert "AsyncMemoryClient" not in source
    assert "get_memory_client" in source


async def test_a_second_chat_message_does_not_build_a_second_client(client, alice, monkeypatch):
    """The route a Student uses most is the one that paid most (process-wide memory client construction)."""
    built = []
    _mem0_configured(monkeypatch, built)
    _chat_without_gemini(monkeypatch)

    first = await _send_a_chat(client, alice)
    second = await _send_a_chat(client, alice)

    assert [first.status_code, second.status_code] == [200, 200]
    assert built == ["a-configured-key"]


async def test_the_chat_and_memory_routes_share_one_client(client, alice, monkeypatch):
    """One process, one Mem0 -- across routes, not just within one."""
    built = []
    _mem0_configured(monkeypatch, built)
    _chat_without_gemini(monkeypatch)

    await _send_a_chat(client, alice)
    listed = await client.get(f"{API}/memory/", headers=alice["headers"])

    assert listed.status_code == 200
    assert built == ["a-configured-key"]


async def test_a_canvas_sync_shares_the_client_and_builds_none_on_its_own(monkeypatch, alice):
    """Constructing the sync service reaches for nothing; syncing shares."""
    from api.services.canvas_memory_service import CanvasMemoryService

    built = []
    _mem0_configured(monkeypatch, built)
    assignment = {"id": 42, "name": "Lab report", "due_at": "2026-09-01T23:59:00Z"}

    service = CanvasMemoryService(alice["id"], "https://canvas.example.edu")
    assert built == [], "building the service reached for Mem0 before any sync asked"

    first = await service.process_canvas_data("assignment", assignment, "Biology 101")
    second = await service.process_canvas_data("assignment", assignment, "Biology 101")

    assert first["mem0"] is True and second["mem0"] is True
    assert built == ["a-configured-key"]


async def test_the_lifespan_builds_the_client_before_any_student_arrives(monkeypatch):
    """Warmed at startup, so no Student is ever the one who pays the round trip.

    The lifespan is opened with no PocketBase credential so that persistence
    stays out of it -- installing a real store here would outlive the test. See
    `test_startup_wiring.py`, which covers that half.
    """
    built = []
    _mem0_configured(monkeypatch, built)
    without_pocketbase = get_settings().model_copy(
        update={"pocketbase_superuser_password": ""}
    )
    monkeypatch.setattr(api.main, "settings", without_pocketbase)

    async with api.main.app.router.lifespan_context(api.main.app):
        assert built == ["a-configured-key"]


async def test_a_lifespan_with_no_mem0_key_starts_without_reaching_for_one(monkeypatch, caplog):
    """A lab with Student Memory switched off still starts, and says so once."""
    built = []
    _mem0_configured(monkeypatch, built)
    monkeypatch.setattr(student_memory.settings, "mem0_api_key", "")
    without_pocketbase = get_settings().model_copy(
        update={"pocketbase_superuser_password": ""}
    )
    monkeypatch.setattr(api.main, "settings", without_pocketbase)

    with caplog.at_level("INFO"):
        async with api.main.app.router.lifespan_context(api.main.app):
            pass

    assert built == []
    assert caplog.text.count(NO_OP_LOG) == 1
