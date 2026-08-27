"""
Student Memory can be kept on the Deployment Operator's own infrastructure.

Same contract as the hosted client, and the same seam: built at most once, off
The event loop, bounded, and never raising. What changes is where the remembered
exchanges live -- a local vector store and graph rather than Mem0's service.

The degradation rules matter more here than in the hosted case, because there
are now two more things that can be down. A deployment whose Qdrant or Neo4j is
unreachable must still answer a Student; it simply does not remember. the optional graph boundary
requires a graph-disabled deployment to stay valid, and this is where that is
enforced for memory.

Gemini is the LLM and embedder on purpose: requirements.txt records an accepted
conflict between mem0ai's `openai<1.110.0` and the `openai>=2` that
livekit-agents pins, and routing mem0 through Gemini leaves it dormant.
"""
import asyncio

import pytest

from api.services import student_memory
from api.services.student_memory import NoOpMemoryClient


@pytest.fixture(autouse=True)
def unbuilt():
    """Each test decides for itself what this process's client is."""
    student_memory.set_memory_client(None)
    yield
    student_memory.set_memory_client(None)


@pytest.fixture
def self_hosted(monkeypatch):
    """A fully configured self-hosted deployment."""
    monkeypatch.setattr(student_memory.settings, "mem0_self_hosted", True)
    monkeypatch.setattr(student_memory.settings, "google_api_key", "a-google-key")
    monkeypatch.setattr(student_memory.settings, "qdrant_host", "qdrant")
    monkeypatch.setattr(student_memory.settings, "qdrant_port", 6333)
    monkeypatch.setattr(student_memory.settings, "neo4j_url", "bolt://neo4j:7687")
    monkeypatch.setattr(student_memory.settings, "neo4j_username", "neo4j")
    monkeypatch.setattr(student_memory.settings, "neo4j_password", "a-neo4j-password")


@pytest.fixture
def built_config(monkeypatch):
    """Capture the config handed to mem0 without building anything real."""
    captured = {}

    class _FakeAsyncMemory:
        # `async`, because the real `AsyncMemory.from_config` is a coroutine
        # function. A synchronous fake here hid a real defect once: the build
        # ran it in a worker thread and returned the un-awaited coroutine as
        # The client, which every call site then failed on with "'coroutine'
        # object has no attribute 'add'". A fake that does not match the shape
        # of the thing it stands in for tests nothing worth knowing.
        @classmethod
        async def from_config(cls, config):
            captured["config"] = config
            return cls()

        async def add(self, *args, **kwargs):
            return {"results": []}

    monkeypatch.setattr(student_memory, "AsyncMemory", _FakeAsyncMemory)
    return captured


async def test_a_self_hosted_deployment_does_not_need_a_mem0_key(self_hosted, built_config, monkeypatch):
    """The whole point: no account with Mem0, no key, still remembers."""
    monkeypatch.setattr(student_memory.settings, "mem0_api_key", "")

    client = await student_memory.get_memory_client()

    assert not isinstance(client, NoOpMemoryClient)


async def test_self_hosted_wins_over_a_hosted_key(self_hosted, built_config, monkeypatch):
    """Explicit configuration beats a leftover key.

    A deployment that has set `MEM0_SELF_HOSTED` has said where its Student
    Memory lives. Silently preferring a stale hosted key would send remembered
    exchanges somewhere the Operator did not choose.
    """
    monkeypatch.setattr(student_memory.settings, "mem0_api_key", "a-leftover-key")

    await student_memory.get_memory_client()

    assert "config" in built_config, "built the hosted client instead of the self-hosted one"


async def test_gemini_is_the_llm_and_embedder(self_hosted, built_config):
    """Never OpenAI, so the recorded mem0ai/livekit-agents conflict stays dormant."""
    await student_memory.get_memory_client()
    config = built_config["config"]

    assert config["llm"]["provider"] == "gemini"
    assert config["embedder"]["provider"] == "gemini"


# -- the two backends must look identical to a call site -------------------


async def test_a_lookup_answers_with_a_list_like_the_hosted_client(self_hosted, monkeypatch):
    """OSS mem0 wraps results in a dict; the hosted client does not.

    Call sites do `[m['memory'] for m in memories]`. Handed the OSS shape they
    iterate the dict's keys and fail with `string indices must be integers` --
    which surfaces only as "Mem0 error (non-fatal)" while the deployment
    quietly stops remembering. Observed exactly that way against the running
    stack.
    """
    remembered = [{"id": "1", "memory": "Prefers worked examples"}]

    class _OssShaped:
        @classmethod
        async def from_config(cls, config):
            return cls()

        async def search(self, *args, **kwargs):
            return {"results": remembered}

        async def get_all(self, *args, **kwargs):
            return {"results": remembered}

    monkeypatch.setattr(student_memory, "AsyncMemory", _OssShaped)

    client = await student_memory.get_memory_client()

    assert await client.search("anything", user_id="s") == remembered
    assert await client.get_all(user_id="s") == remembered


async def test_an_empty_self_hosted_memory_looks_like_an_empty_hosted_one(self_hosted, monkeypatch):
    """Nothing remembered yet is an empty list, not an empty dict."""

    class _Empty:
        @classmethod
        async def from_config(cls, config):
            return cls()

        async def search(self, *args, **kwargs):
            return {"results": []}

        async def get_all(self, *args, **kwargs):
            return {}

    monkeypatch.setattr(student_memory, "AsyncMemory", _Empty)

    client = await student_memory.get_memory_client()

    assert await client.search("anything", user_id="s") == []
    assert await client.get_all(user_id="s") == []


async def test_the_no_op_and_the_self_hosted_client_share_an_interface(self_hosted, built_config):
    """Whatever is installed, the call sites reach the same method names."""
    client = await student_memory.get_memory_client()

    for method in ("add", "search", "get_all", "delete", "delete_all"):
        assert hasattr(client, method), f"self-hosted client is missing {method}"
        assert hasattr(NoOpMemoryClient(), method)


async def test_the_embedding_model_is_named_and_current(self_hosted, built_config):
    """mem0's own default is retired, and the failure is silent until first use.

    Left unnamed, the embedder defaults to `models/text-embedding-004`, which
    this API version answers with "404 NOT_FOUND ... is not supported for
    embedContent". The client builds, the deployment looks configured, and
    nothing is ever remembered -- which is exactly how it presented when this
    was first stood up.
    """
    await student_memory.get_memory_client()
    embedder = built_config["config"]["embedder"]["config"]

    assert embedder["model"] != "models/text-embedding-004"
    assert embedder["model"] == student_memory.EMBEDDING_MODEL


async def test_the_embedder_and_the_collection_agree_on_width(self_hosted, built_config):
    """The width Gemini is asked for is the width Qdrant's collection is built at.

    These are two separate keys in two separate sections of mem0's config, and
    The vector store's defaults to OpenAI's 1536 no matter which embedder is
    configured. Left to drift, the collection is created 1536 wide, 768 wide
    vectors are written into it, and Qdrant answers `400 (Bad Request)` -- which
    reaches the log only as "Mem0 error (non-fatal)" and presents as a
    deployment that silently never remembers. Asserting they are equal is the
    point; asserting either one alone would have missed this.
    """
    await student_memory.get_memory_client()
    config = built_config["config"]

    embedder_width = config["embedder"]["config"]["embedding_dims"]
    collection_width = config["vector_store"]["config"]["embedding_model_dims"]

    assert embedder_width == collection_width == student_memory.EMBEDDING_DIMENSIONS


async def test_the_vector_store_and_graph_are_the_configured_ones(self_hosted, built_config):
    await student_memory.get_memory_client()
    config = built_config["config"]

    assert config["vector_store"]["provider"] == "qdrant"
    assert config["vector_store"]["config"]["host"] == "qdrant"
    assert config["graph_store"]["provider"] == "neo4j"
    assert config["graph_store"]["config"]["url"] == "bolt://neo4j:7687"


async def test_no_graph_configured_still_remembers(self_hosted, built_config, monkeypatch):
    """A graph-disabled deployment stays valid (the optional graph boundary).

    Memory falls back to the vector store alone rather than refusing to build,
    so losing the graph costs relationships between memories, not memory.
    """
    monkeypatch.setattr(student_memory.settings, "neo4j_url", "")

    client = await student_memory.get_memory_client()

    assert not isinstance(client, NoOpMemoryClient)
    assert "graph_store" not in built_config["config"]


async def test_a_self_hosted_deployment_with_no_vector_store_is_a_no_op(self_hosted, monkeypatch):
    """Half-configured is reported, not guessed at.

    Without somewhere to put embeddings there is no memory to build, and
    quietly falling back to a hosted key -- or to mem0's own default localhost
    Qdrant -- would send a lab's memory somewhere nobody chose.
    """
    monkeypatch.setattr(student_memory.settings, "qdrant_host", "")

    client = await student_memory.get_memory_client()

    assert isinstance(client, NoOpMemoryClient)


async def test_a_self_hosted_deployment_with_no_google_key_is_a_no_op(self_hosted, monkeypatch):
    """Gemini is the LLM and embedder, so its key is not optional here."""
    monkeypatch.setattr(student_memory.settings, "google_api_key", "")

    client = await student_memory.get_memory_client()

    assert isinstance(client, NoOpMemoryClient)


async def test_an_unreachable_store_degrades_instead_of_raising(self_hosted, monkeypatch):
    """Qdrant or Neo4j being down must not take the tutor down with it.

    This is the demo-critical case: a container that did not come up should
    cost remembering, never answering.
    """
    class _Exploding:
        @classmethod
        async def from_config(cls, config):
            raise ConnectionError("qdrant refused the connection")

    monkeypatch.setattr(student_memory, "AsyncMemory", _Exploding)

    client = await student_memory.get_memory_client()

    assert isinstance(client, NoOpMemoryClient)


async def test_a_hanging_store_is_abandoned(self_hosted, monkeypatch):
    """Bounded like the hosted build, for the same reason.

    A host that drops packets rather than refusing them would otherwise hold
    The build for an OS-level TCP timeout, and every Student behind it.
    """
    monkeypatch.setattr(student_memory.settings, "mem0_timeout_seconds", 0.05)

    class _Hanging:
        @classmethod
        async def from_config(cls, config):
            import time

            # Deliberately a blocking sleep, not `asyncio.sleep`: the real
            # hazard is a synchronous socket connect, and only a blocking call
            # proves the build is running off the event loop.
            time.sleep(5)
            return cls()

    monkeypatch.setattr(student_memory, "AsyncMemory", _Hanging)

    client = await asyncio.wait_for(student_memory.get_memory_client(), timeout=3)

    assert isinstance(client, NoOpMemoryClient)


async def test_the_self_hosted_client_is_built_once(self_hosted, built_config):
    """Same caching guarantee as the hosted path."""
    first = await student_memory.get_memory_client()
    second = await student_memory.get_memory_client()

    assert first is second


async def test_self_hosted_off_still_uses_the_hosted_key(built_config, monkeypatch):
    """Nothing changes for a deployment that has not opted in."""
    monkeypatch.setattr(student_memory.settings, "mem0_self_hosted", False)
    monkeypatch.setattr(student_memory.settings, "mem0_api_key", "")

    client = await student_memory.get_memory_client()

    assert isinstance(client, NoOpMemoryClient)
    assert "config" not in built_config
