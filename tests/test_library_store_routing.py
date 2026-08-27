"""
Both Tutor surfaces aim a search at the Student's own Library store.

`get_search_tool_config` refuses to build a search without a store name
(tests/test_library_stores.py). That refusal only protects anything if the
callers resolve the right store, so this file drives the two surfaces that
search — the chat route and the voice agent's `query_documents` tool — and
reads back the store each one actually asked Gemini to look in.

The interesting case is the Student who has never uploaded. Their Library has
no store, and the honest answer is that they have no Course Materials. The
failure this file exists to prevent is a surface that decides the answer for
such a Student is "search someone else's store", or "make a store and search
that", instead.
"""
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from google.genai import types as genai_types

API = "/api/v1"

ALICE_STORE = "fileSearchStores/alice-lib-4k2j9x1m"
BOB_STORE = "fileSearchStores/bob-lib-8h3n5p7q"


@pytest.fixture
def captured_tools(monkeypatch):
    """Answer chat's Gemini call and hand back the tools it was configured with."""
    from api.routers import chat

    seen = {}

    class _Models:
        def generate_content(self, *, model, contents, config):
            seen["tools"] = list(config.tools or [])
            return SimpleNamespace(
                text="Let's work through it together.",
                candidates=[SimpleNamespace(grounding_metadata=None)],
            )

    monkeypatch.setattr(chat, "Client", lambda **kw: SimpleNamespace(models=_Models()))
    return seen


def _store_names(tools):
    """Every File Search store the request was pointed at."""
    names = []
    for tool in tools:
        search = getattr(tool, "file_search", None)
        if search is not None:
            names.extend(search.file_search_store_names or [])
    return names


# -- The chat surface --------------------------------------------------------


async def test_chat_searches_the_students_own_library_store(client, provider, captured_tools):
    """A Student's chat search names their store and no other."""
    student_id = provider.add_student(
        "alice@example.com", "alice-password", library_store_name=ALICE_STORE
    )
    provider.add_student("bob@example.com", "bob-password", library_store_name=BOB_STORE)
    headers = {"Authorization": f"Bearer {provider.token_for(student_id)}"}

    response = await client.post(
        f"{API}/chat/message", json={"message": "What is a limit?", "history": []}, headers=headers
    )

    assert response.status_code == 200
    assert _store_names(captured_tools["tools"]) == [ALICE_STORE]


async def test_chat_for_a_student_with_no_library_searches_nothing(
    client, provider, captured_tools
):
    """No uploads means no store, which means no search — not a borrowed one.

    The Student still gets a tutor. They just get one that was not handed a
    File Search store belonging to somebody else.
    """
    student_id = provider.add_student("alice@example.com", "alice-password")
    provider.add_student("bob@example.com", "bob-password", library_store_name=BOB_STORE)
    headers = {"Authorization": f"Bearer {provider.token_for(student_id)}"}

    response = await client.post(
        f"{API}/chat/message", json={"message": "What is a limit?", "history": []}, headers=headers
    )

    assert response.status_code == 200
    assert _store_names(captured_tools["tools"]) == []


async def test_chat_never_creates_a_store_on_the_search_path(client, provider, captured_tools):
    """Searching must not provision a Library. Only uploading does that.

    A store created here would be empty by construction, and the code that
    created it would be the code that must never widen a search.
    """
    student_id = provider.add_student("alice@example.com", "alice-password")
    headers = {"Authorization": f"Bearer {provider.token_for(student_id)}"}

    with patch("api.services.gemini_service.genai.Client") as provider_client:
        await client.post(
            f"{API}/chat/message", json={"message": "What is a limit?", "history": []}, headers=headers
        )

    provider_client.return_value.file_search_stores.create.assert_not_called()


# -- The voice surface -------------------------------------------------------


async def test_the_voice_tool_searches_the_students_own_library_store(provider):
    """`query_documents` resolves the caller's store before it searches."""
    import tools as agent_tools
    from api.services.gemini_service import GeminiService

    student_id = provider.add_student(
        "alice@example.com", "alice-password", library_store_name=ALICE_STORE
    )

    seen = {}

    def _query(self, query, user_id, store_name, textbook_id=None):
        seen["store_name"] = store_name
        return "A limit describes what a function approaches."

    with patch.object(agent_tools, "get_user_id", return_value=student_id), patch.object(
        GeminiService, "query_textbook", _query
    ):
        answer = await agent_tools.query_documents("What is a limit?")

    assert seen["store_name"] == ALICE_STORE
    assert "limit" in answer


async def test_the_voice_tool_tells_a_student_with_no_library_so(provider):
    """No Library, no search, and an answer that says why.

    Reporting an empty Library is honest. Searching another Student's store
    to have something to say is the failure the per-Library search boundary exists to prevent.
    """
    import tools as agent_tools
    from api.services.gemini_service import GeminiService

    student_id = provider.add_student("alice@example.com", "alice-password")

    with patch.object(agent_tools, "get_user_id", return_value=student_id), patch.object(
        GeminiService, "query_textbook"
    ) as query:
        answer = await agent_tools.query_documents("What is a limit?")

    query.assert_not_called()
    assert "course material" in answer.lower()
