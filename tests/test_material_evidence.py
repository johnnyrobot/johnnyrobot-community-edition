"""
A grounded answer arrives with the evidence that grounds it: a relevant
excerpt from a Ready Course Material together with its material identity and
source location.

Retrieval already worked before this file existed -- the chat route asked File
Search, got chunks back, and then read only `response.text`, dropping the
excerpts and their sources on the floor. The Student received a bare assertion
indistinguishable from a confident general answer, which is the exact failure
The concept exists to prevent.

The glossary's last sentence is the reason evidence is its own field rather
than text spliced into the answer.
"""
from types import SimpleNamespace

import pytest
from google.genai import types as genai_types

API = "/api/v1"


class _StubGeminiService:
    async def resolve_library_store(self, user_id):
        """Every Student Library is its own store since per-Library store isolation."""
        return f"fileSearchStores/{user_id}-lib"

    def get_search_tool_config(self, user_id, store_name, textbook_id=None):
        return genai_types.Tool()


def _chunk(*, text, material_id=None, title=None, page=None, document_name=None, stored_title=None):
    """Build a grounding chunk shaped the way File Search returns one."""
    custom = []
    if material_id is not None:
        custom.append(genai_types.GroundingChunkCustomMetadata(key="textbook_id", string_value=material_id))
    if stored_title is not None:
        custom.append(genai_types.GroundingChunkCustomMetadata(key="title", string_value=stored_title))
    custom = custom or None
    return genai_types.GroundingChunk(
        retrieved_context=genai_types.GroundingChunkRetrievedContext(
            text=text,
            title=title,
            page_number=page,
            document_name=document_name,
            custom_metadata=custom,
        )
    )


@pytest.fixture
def answers_with(monkeypatch):
    """Let a test choose the grounding chunks Gemini answers with."""
    from api.routers import chat

    def _install(chunks, answer="The coherence number is 17.4."):
        candidate = SimpleNamespace(
            grounding_metadata=genai_types.GroundingMetadata(grounding_chunks=chunks)
            if chunks is not None
            else None
        )

        class _Client:
            def __init__(self, *args, **kwargs):
                self.models = self

            def generate_content(self, **kwargs):
                return SimpleNamespace(text=answer, candidates=[candidate])

        monkeypatch.setattr(chat.settings, "google_api_key", "stub-google-key")
        monkeypatch.setattr(chat, "GeminiService", _StubGeminiService)
        monkeypatch.setattr(chat, "Client", _Client)

    return _install


async def _ask(client, alice):
    return await client.post(
        f"{API}/chat/message",
        headers=alice["headers"],
        json={"message": "what is the coherence number?", "history": []},
    )


async def test_a_grounded_answer_carries_its_excerpt_and_source(client, alice, answers_with):
    """All three parts the glossary names travel with the answer."""
    answers_with(
        [
            _chunk(
                text="The coherence number K is exactly 17.4.",
                material_id="gx3p8ndxtqzrix4",
                title="Quantum Basketry Chapter 3",
                page=12,
            )
        ]
    )

    response = await _ask(client, alice)

    assert response.status_code == 200
    evidence = response.json()["evidence"]
    assert len(evidence) == 1
    assert evidence[0]["excerpt"] == "The coherence number K is exactly 17.4."
    assert evidence[0]["material_id"] == "gx3p8ndxtqzrix4"
    assert evidence[0]["title"] == "Quantum Basketry Chapter 3"
    assert evidence[0]["location"] == "page 12"


async def test_evidence_supports_the_answer_without_replacing_it(client, alice, answers_with):
    """The response text is untouched; evidence sits beside it."""
    answers_with([_chunk(text="K is 17.4.", material_id="m1")], answer="K is 17.4, measured not derived.")

    body = (await _ask(client, alice)).json()

    assert body["response"] == "K is 17.4, measured not derived."
    assert body["evidence"][0]["excerpt"] == "K is 17.4."


async def test_an_ungrounded_answer_carries_no_evidence(client, alice, answers_with):
    """Absence of evidence is reported as absence, never invented.

    This is the case that makes evidence worth reading: if an answer with no
    retrieved chunks still arrived with evidence attached, the field would
    carry no information about whether the tutor was grounded.
    """
    answers_with([])

    body = (await _ask(client, alice)).json()

    assert body["evidence"] == []


async def test_a_response_without_grounding_metadata_still_answers(client, alice, answers_with):
    """No grounding metadata at all is a normal answer, not an error.

    Gemini omits the field entirely when no tool retrieved anything, so
    reaching into it unguarded would turn every ungrounded turn into a 500.
    """
    answers_with(None)

    response = await _ask(client, alice)

    assert response.status_code == 200
    assert response.json()["evidence"] == []


async def test_every_retrieved_chunk_becomes_evidence(client, alice, answers_with):
    """A multi-chunk answer reports each source, not just the first."""
    answers_with(
        [
            _chunk(text="Rule of Odd Radials.", material_id="m1", page=3),
            _chunk(text="Rule of Damp Tension.", material_id="m1", page=4),
            _chunk(text="Rule of Terminal Reversal.", material_id="m1", page=5),
        ]
    )

    evidence = (await _ask(client, alice)).json()["evidence"]

    assert [e["location"] for e in evidence] == ["page 3", "page 4", "page 5"]


async def test_material_identity_falls_back_to_the_provider_file_name(client, alice, answers_with):
    """A chunk without custom metadata still names its material.

    The provider file name is `cm-<material id>` by construction, so the
    identity is recoverable even when the metadata does not come back.
    """
    answers_with([_chunk(text="K is 17.4.", document_name="files/cm-gx3p8ndxtqzrix4")])

    evidence = (await _ask(client, alice)).json()["evidence"]

    assert evidence[0]["material_id"] == "gx3p8ndxtqzrix4"


async def test_evidence_names_the_material_the_way_the_student_titled_it(client, alice, answers_with):
    """The Student's own title, not the provider's file name.

    Observed against real Gemini: `retrieved_context.title` comes back as the
    provider file name (`cm-<id>`), while the title the Student typed is the
    `title` custom metadata attached at import. Showing the former would cite
    a Course Material by an identifier the Student has never seen, which
    defeats the point of naming the source at all.
    """
    answers_with(
        [
            _chunk(
                text="K is 17.4.",
                material_id="y1y3xnd81e4r6lm",
                title="cm-y1y3xnd81e4r6lm",
                stored_title="Quantum Basketry Chapter 3",
            )
        ]
    )

    evidence = (await _ask(client, alice)).json()["evidence"]

    assert evidence[0]["title"] == "Quantum Basketry Chapter 3"


async def test_a_chunk_with_no_text_is_not_reported_as_evidence(client, alice, answers_with):
    """Evidence is an excerpt. A chunk carrying none is not evidence."""
    answers_with([_chunk(text=None, material_id="m1"), _chunk(text="Real excerpt.", material_id="m1")])

    evidence = (await _ask(client, alice)).json()["evidence"]

    assert len(evidence) == 1
    assert evidence[0]["excerpt"] == "Real excerpt."
