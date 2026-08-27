"""
The text Tutor is governed by the same policy as the voice Tutor.

`AGENT_INSTRUCTION` is the Academic Integrity Policy. A Student who reaches the
tutor by typing is subject to it exactly as one who reaches it by speaking --
The two surfaces differ in the tools they can call, never in what the tutor is
allowed to do. Before this file existed the chat route restated its own
instruction and the restatement carried no policy at all, so the text tutor
would write a graded essay on request while the dashboard promised it would
not.
"""
from types import SimpleNamespace

import pytest
from google.genai import types as genai_types

from prompts import AGENT_INSTRUCTION

API = "/api/v1"


class _StubGeminiService:
    """Answers with an empty File Search tool so the chat route can proceed."""

    async def resolve_library_store(self, user_id):
        """Every Student Library is its own store since per-Library store isolation."""
        return f"fileSearchStores/{user_id}-lib"

    def get_search_tool_config(self, user_id, store_name, textbook_id=None):
        return genai_types.Tool()


@pytest.fixture
def sent_config(monkeypatch):
    """Capture the config the chat route hands to Gemini, without a network call."""
    captured = {}

    class _CapturingClient:
        def __init__(self, *args, **kwargs):
            self.models = self

        def generate_content(self, **kwargs):
            captured["config"] = kwargs.get("config")
            return SimpleNamespace(text="What do you already know about it?")

    from api.routers import chat

    monkeypatch.setattr(chat.settings, "google_api_key", "stub-google-key")
    monkeypatch.setattr(chat, "GeminiService", _StubGeminiService)
    monkeypatch.setattr(chat, "Client", _CapturingClient)
    return captured


async def _ask(client, alice, message):
    return await client.post(
        f"{API}/chat/message",
        headers=alice["headers"],
        json={"message": message, "history": []},
    )


async def test_the_chat_route_sends_the_agent_instruction(client, alice, sent_config):
    """The policy reaches Gemini composed, not paraphrased.

    Asserting on the whole of AGENT_INSTRUCTION rather than a phrase from it is
    deliberate: it is what stops the route drifting back into restating the
    policy in its own words, which is how the policy went missing.
    """
    response = await _ask(client, alice, "why does this integral diverge?")

    assert response.status_code == 200
    instruction = sent_config["config"].system_instruction
    assert AGENT_INSTRUCTION in instruction


async def test_the_instruction_prohibits_completing_graded_work(client, alice, sent_config):
    """The specific prohibition a Student can defeat by typing must be present.

    A refactor that keeps AGENT_INSTRUCTION reachable but empties it of the
    integrity policy passes the test above and fails this one.
    """
    await _ask(client, alice, "write my essay")

    instruction = sent_config["config"].system_instruction
    assert "NEVER complete graded work" in instruction


async def test_student_memory_does_not_displace_the_policy(client, alice, sent_config, monkeypatch):
    """Remembered context is added to the policy, never substituted for it.

    The route builds its instruction around a memory block. Composing badly --
    memory replacing the policy rather than joining it -- would restore the
    original defect for exactly those Students who have used the tutor before.
    """
    from api.routers import chat

    class _RememberingMem0:
        async def add(self, *args, **kwargs):
            return {"results": []}

        async def search(self, *args, **kwargs):
            return [{"memory": "Student struggles with fractions"}]

    async def _remembering_client():
        return _RememberingMem0()

    monkeypatch.setattr(chat, "get_memory_client", _remembering_client)

    await _ask(client, alice, "help me with this")

    instruction = sent_config["config"].system_instruction
    assert "Student struggles with fractions" in instruction
    assert AGENT_INSTRUCTION in instruction
