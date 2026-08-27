"""
The graph is optional, and it degrades rather than raising. A graph-disabled
deployment stays valid, and a graph failure affects only the optional graph
branch without weakening the Gemini baseline. Nothing in this subsystem may
stop the tutor from answering.

Same seam shape as api/services/student_memory.py, deliberately -- one place
that builds one client per process, off the event loop, bounded, degrading to
a no-op that is indistinguishable from an empty graph.
"""
import pytest

from api.graph import client as graph_client
from api.graph.client import NoOpGraphClient, get_graph_client, set_graph_client
from api.graph.identity import canonical_depth, canonical_library_key, canonical_material_id


@pytest.fixture(autouse=True)
def fresh_seam():
    """Hand the seam back between cases, so one test's client cannot answer the next."""
    set_graph_client(None)
    yield
    set_graph_client(None)


async def test_an_unset_neo4j_url_is_a_valid_deployment(monkeypatch):
    monkeypatch.setattr(graph_client.settings, "neo4j_url", "")

    assert isinstance(await get_graph_client(), NoOpGraphClient)


async def test_an_unreachable_neo4j_degrades_rather_than_raising(monkeypatch):
    monkeypatch.setattr(graph_client.settings, "neo4j_url", "bolt://nowhere:7687")
    monkeypatch.setattr(graph_client.settings, "neo4j_password", "x")

    async def refuse():
        raise OSError("connection refused")

    monkeypatch.setattr(graph_client, "_connect", lambda: refuse())

    assert isinstance(await get_graph_client(), NoOpGraphClient)


async def test_a_hanging_neo4j_is_abandoned_at_the_bound(monkeypatch):
    """A host that drops packets must not hold the event loop for the OS's timeout."""
    import asyncio

    monkeypatch.setattr(graph_client.settings, "neo4j_url", "bolt://nowhere:7687")
    monkeypatch.setattr(graph_client.settings, "neo4j_password", "x")
    monkeypatch.setattr(graph_client.settings, "graph_build_timeout_seconds", 0.05)

    async def hang():
        await asyncio.sleep(30)

    monkeypatch.setattr(graph_client, "_connect", lambda: hang())

    assert isinstance(await get_graph_client(), NoOpGraphClient)


async def test_the_no_op_answers_a_query_with_nothing(monkeypatch):
    """A caller cannot tell "no graph" from "nothing in the graph"."""
    monkeypatch.setattr(graph_client.settings, "neo4j_url", "")

    assert await (await get_graph_client()).run("MATCH (n) RETURN n") == []


async def test_the_client_is_built_once_per_process(monkeypatch):
    monkeypatch.setattr(graph_client.settings, "neo4j_url", "")
    builds = []

    original = graph_client._build_graph_client

    async def counted():
        builds.append(1)
        return await original()

    monkeypatch.setattr(graph_client, "_build_graph_client", counted)

    await get_graph_client()
    await get_graph_client()

    assert len(builds) == 1


async def test_a_configured_client_reports_itself_configured(monkeypatch):
    """Consumers use this signal to degrade cleanly when the graph is absent."""
    monkeypatch.setattr(graph_client.settings, "neo4j_url", "bolt://neo4j:7687")
    monkeypatch.setattr(graph_client.settings, "neo4j_password", "x")

    async def connect_fine():
        return object()

    monkeypatch.setattr(graph_client, "_connect", lambda: connect_fine())

    assert (await get_graph_client()).is_configured is True


async def test_a_url_with_no_password_is_a_no_op(monkeypatch):
    """Neo4j refuses anonymous auth, so this would fail on first use instead."""
    monkeypatch.setattr(graph_client.settings, "neo4j_url", "bolt://neo4j:7687")
    monkeypatch.setattr(graph_client.settings, "neo4j_password", "")

    assert isinstance(await get_graph_client(), NoOpGraphClient)


# -- Identity, which fails closed -------------------------------------------


def test_a_library_key_survives_canonicalisation():
    assert canonical_library_key("abcdefghij12345") == "abcdefghij12345"


@pytest.mark.parametrize(
    "bad",
    ["", None, '"', "a\\b", "abc-def", "  ", "abcdefghij12345 ", "'; MATCH (n) DETACH DELETE n //"],
)
def test_an_unusable_library_key_is_refused(bad):
    """It fails closed rather than being escaped into a Cypher string.

    Same posture as gemini_service._canonical_owner: this clause is the only
    thing separating one Student Library from another.
    """
    with pytest.raises(ValueError):
        canonical_library_key(bad)


@pytest.mark.parametrize("bad", ["", None, "short", "abcdefghij12345\n", "abcdefghij1234!"])
def test_a_malformed_material_identity_is_refused(bad):
    with pytest.raises(ValueError):
        canonical_material_id(bad)


def test_a_traversal_depth_is_bounded():
    """Depth is interpolated into Cypher, because *1..$param is not valid Cypher.

    That makes it the one value in this subsystem that cannot be passed as a
    parameter, so it is validated to an int in range instead.
    """
    assert canonical_depth(3) == 3

    for bad in [0, 6, -1, "3", 2.5, None, "1..99"]:
        with pytest.raises(ValueError):
            canonical_depth(bad)
