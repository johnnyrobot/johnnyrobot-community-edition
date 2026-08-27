"""
Student Memory is optional.

`mem0.AsyncMemoryClient.__init__` validates its API key over the network, and
what it answers decides what a Student gets. the optional-memory contract names two failures that must
not reach a Student, and the constructor raises on both:

  - **no key at all** -- `ValueError: Mem0 API Key not provided`, raised before
    any request goes out (`mem0/client/main.py:949`);
  - **a key that is wrong or expired** -- the constructor pings Mem0 and turns
    The HTTP error into `ValueError: Error: Invalid API key`
    (`mem0/client/main.py:1009`). A community-college lab that loses its
    connection lands here too, except that `requests` raises `ConnectionError`,
    which mem0 does *not* convert -- so it escapes `__init__` as-is.

All three end at the same place: chat and voice still work, they simply do not
remember across sessions.

Since process-wide memory client construction that construction happens in one place -- `student_memory`,
which every site asks for the process's one client -- so these tests reach the
guard by patching the seam. That the sites really do go through it, and build
nothing of their own, is the subject of `test_student_memory_client.py`.

**How these tests prove the no-op path rather than merely a working route.**
The suite's autouse stub (see "the process-wide memory stub" in `tests/conftest.py`) replaces
`AsyncMemoryClient` with an in-process double that already behaves like a
no-op, so "the route returned 200" would pass even if the guard did nothing.
Each test below installs `_trap(...)` instead: a client that records the key it
was handed and then fails exactly as mem0 does. That makes both halves of the
guard observable and gives every test a mutation to fail against:

  - with **no key**, the recorded attempts must be empty -- reaching an answer
    without ever constructing the real client is the whole proof, and the
    fast-path also keeps a knowingly-off deployment off the network entirely;
  - with a **rejected or unreachable key**, the attempt must be recorded *and*
    The route must still answer. Recording the attempt is what stops this test
    passing vacuously through the no-key branch; still answering is the
    regression this closes -- against a guard that only checks whether the key
    is non-empty, these tests get a 500.
"""
from types import SimpleNamespace

import google.genai.types as genai_types
import requests

from api.services import student_memory
from api.services.student_memory import NoOpMemoryClient

API = "/api/v1"

NO_OP_LOG = "Student Memory is a no-op"

REJECTED = ValueError("Error: Invalid API key")
UNREACHABLE = requests.exceptions.ConnectionError("api.mem0.ai unreachable")


def _trap(attempts, failure=REJECTED):
    """A stand-in for the real client that records its key, then fails as mem0 does.

    Construction is the failure, so a route that answers has either skipped
    construction entirely (no key) or survived it (bad key) -- and `attempts`
    says which.
    """

    class _Trap:
        def __init__(self, *args, **kwargs):
            attempts.append(kwargs.get("api_key"))
            raise failure

    return _Trap


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


ANSWER = "What do you already know about it?"


def _chat_without_gemini(monkeypatch, chat):
    """Keep the chat route off the network, leaving Student Memory the subject."""
    monkeypatch.setattr(chat.settings, "google_api_key", "stub-google-key")
    monkeypatch.setattr(chat, "GeminiService", _StubGeminiService)
    monkeypatch.setattr(chat, "Client", _StubGenAIClient)


async def _send_a_chat(client, alice):
    return await client.post(
        f"{API}/chat/message",
        headers=alice["headers"],
        json={"message": "why does this integral diverge?", "history": []},
    )


# -- the no-op client itself -------------------------------------------------


async def test_the_no_op_answers_as_an_empty_student_memory():
    """A caller cannot tell "nothing remembered yet" from "remembering is off"."""
    no_op = NoOpMemoryClient()

    assert await no_op.add([{"role": "user", "content": "hi"}], user_id="s") == {"results": []}
    assert await no_op.search("hi", user_id="s") == []
    assert await no_op.get_all(user_id="s") == []
    assert await no_op.delete(memory_id="m") == {"message": "deleted"}
    assert await no_op.delete_all(user_id="s") == {"message": "deleted"}


# -- chat --------------------------------------------------------------------


async def test_a_chat_answers_with_no_mem0_key_configured(client, alice, monkeypatch, caplog):
    """With no key, chat never reaches for the real client -- and still answers."""
    from api.routers import chat

    attempts = []
    _chat_without_gemini(monkeypatch, chat)
    monkeypatch.setattr(student_memory, "AsyncMemoryClient", _trap(attempts))
    monkeypatch.setattr(student_memory.settings, "mem0_api_key", "")

    with caplog.at_level("INFO"):
        response = await _send_a_chat(client, alice)

    assert response.status_code == 200
    assert response.json()["response"] == ANSWER
    assert attempts == []
    assert NO_OP_LOG in caplog.text


async def test_a_chat_answers_when_the_mem0_key_is_rejected(client, alice, monkeypatch, caplog):
    """An expired key degrades the lab instead of breaking it.

    The recorded attempt is what makes this test mean something: the configured
    key really did reach `AsyncMemoryClient`, which really did raise the
    `ValueError` mem0 raises for a bad key -- and the chat answered anyway.
    """
    from api.routers import chat

    attempts = []
    _chat_without_gemini(monkeypatch, chat)
    monkeypatch.setattr(student_memory, "AsyncMemoryClient", _trap(attempts))
    monkeypatch.setattr(student_memory.settings, "mem0_api_key", "expired-key")

    with caplog.at_level("WARNING"):
        response = await _send_a_chat(client, alice)

    assert response.status_code == 200
    assert response.json()["response"] == ANSWER
    assert attempts == ["expired-key"]
    assert NO_OP_LOG in caplog.text


async def test_a_chat_answers_when_mem0_is_unreachable(client, alice, monkeypatch):
    """A lab that loses its connection keeps tutoring.

    mem0 converts only `HTTPError` into `ValueError`, so an unreachable Mem0
    escapes the constructor as `requests.exceptions.ConnectionError`. Catching
    The key error alone would leave this door open.
    """
    from api.routers import chat

    attempts = []
    _chat_without_gemini(monkeypatch, chat)
    monkeypatch.setattr(student_memory, "AsyncMemoryClient", _trap(attempts, failure=UNREACHABLE))
    monkeypatch.setattr(student_memory.settings, "mem0_api_key", "a-configured-key")

    response = await _send_a_chat(client, alice)

    assert response.status_code == 200
    assert attempts == ["a-configured-key"]


# -- the memory routes -------------------------------------------------------


async def test_listing_memories_with_no_mem0_key_is_empty_not_an_error(
    client, alice, monkeypatch, caplog
):
    from api.routers import memory

    attempts = []
    monkeypatch.setattr(student_memory, "AsyncMemoryClient", _trap(attempts))
    monkeypatch.setattr(student_memory.settings, "mem0_api_key", "")

    with caplog.at_level("INFO"):
        response = await client.get(f"{API}/memory/", headers=alice["headers"])

    assert response.status_code == 200
    assert response.json() == {"memories": [], "total": 0}
    assert attempts == []
    assert NO_OP_LOG in caplog.text


async def test_listing_memories_with_a_rejected_mem0_key_is_empty_not_an_error(
    client, alice, monkeypatch, caplog
):
    """The same degradation on the route a Student uses to read their memories."""
    from api.routers import memory

    attempts = []
    monkeypatch.setattr(student_memory, "AsyncMemoryClient", _trap(attempts))
    monkeypatch.setattr(student_memory.settings, "mem0_api_key", "expired-key")

    with caplog.at_level("WARNING"):
        response = await client.get(f"{API}/memory/", headers=alice["headers"])

    assert response.status_code == 200
    assert response.json() == {"memories": [], "total": 0}
    assert attempts == ["expired-key"]
    assert NO_OP_LOG in caplog.text


async def test_clearing_memories_with_no_mem0_key_reports_nothing_to_delete(
    client, alice, monkeypatch
):
    from api.routers import memory

    attempts = []
    monkeypatch.setattr(student_memory, "AsyncMemoryClient", _trap(attempts))
    monkeypatch.setattr(student_memory.settings, "mem0_api_key", "")

    response = await client.delete(f"{API}/memory/?confirm=true", headers=alice["headers"])

    assert response.status_code == 200
    assert response.json()["deleted_count"] == 0
    assert attempts == []


async def test_clearing_memories_with_a_rejected_mem0_key_reports_nothing_to_delete(
    client, alice, monkeypatch
):
    from api.routers import memory

    attempts = []
    monkeypatch.setattr(student_memory, "AsyncMemoryClient", _trap(attempts))
    monkeypatch.setattr(student_memory.settings, "mem0_api_key", "expired-key")

    response = await client.delete(f"{API}/memory/?confirm=true", headers=alice["headers"])

    assert response.status_code == 200
    assert response.json()["deleted_count"] == 0
    assert attempts == ["expired-key"]


async def test_deleting_a_memory_with_no_mem0_key_is_not_found(client, alice, monkeypatch):
    """Nothing was ever remembered, so no memory belongs to this Student."""
    from api.routers import memory

    attempts = []
    monkeypatch.setattr(student_memory, "AsyncMemoryClient", _trap(attempts))
    monkeypatch.setattr(student_memory.settings, "mem0_api_key", "")

    response = await client.delete(f"{API}/memory/some-memory-id", headers=alice["headers"])

    assert response.status_code == 404
    assert attempts == []


async def test_deleting_a_memory_with_a_rejected_mem0_key_is_not_found(client, alice, monkeypatch):
    from api.routers import memory

    attempts = []
    monkeypatch.setattr(student_memory, "AsyncMemoryClient", _trap(attempts))
    monkeypatch.setattr(student_memory.settings, "mem0_api_key", "expired-key")

    response = await client.delete(f"{API}/memory/some-memory-id", headers=alice["headers"])

    assert response.status_code == 404
    assert attempts == ["expired-key"]


# -- the Canvas sync path ----------------------------------------------------


ASSIGNMENT = {"id": 42, "name": "Lab report", "due_at": "2026-09-01T23:59:00Z"}


async def test_a_canvas_sync_completes_with_no_mem0_key(monkeypatch):
    """A sync still imports Course Material; it just remembers nothing personal."""
    from api.services import canvas_memory_service

    attempts = []
    monkeypatch.setattr(student_memory, "AsyncMemoryClient", _trap(attempts))
    monkeypatch.setattr(student_memory.settings, "mem0_api_key", "")

    service = canvas_memory_service.CanvasMemoryService(
        user_id="student-id", canvas_url="https://canvas.example.edu"
    )
    result = await service.process_canvas_data("assignment", ASSIGNMENT, "Biology 101")

    assert result["mem0"] is True
    assert attempts == []
    assert isinstance(await student_memory.get_memory_client(), NoOpMemoryClient)


async def test_a_canvas_sync_completes_when_the_mem0_key_is_rejected(monkeypatch):
    """The recorded attempt is what stops this passing through the no-key branch."""
    from api.services import canvas_memory_service

    attempts = []
    monkeypatch.setattr(student_memory, "AsyncMemoryClient", _trap(attempts))
    monkeypatch.setattr(student_memory.settings, "mem0_api_key", "expired-key")

    service = canvas_memory_service.CanvasMemoryService(
        user_id="student-id", canvas_url="https://canvas.example.edu"
    )
    result = await service.process_canvas_data("assignment", ASSIGNMENT, "Biology 101")

    assert result["mem0"] is True
    assert attempts == ["expired-key"]
    assert isinstance(await student_memory.get_memory_client(), NoOpMemoryClient)
