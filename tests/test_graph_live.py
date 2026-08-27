"""
The pipeline against the real Neo4j, once.

Everything else in this suite runs against stand-ins, which is right -- they
are fast and they test the logic. What they cannot tell you is whether the
Cypher parses, whether MERGE does what the store assumes, or whether the
traversal filter actually excludes a second Library in a live database.

Excluded from the default run (see pytest.ini). Run it deliberately, pointing
at wherever bolt is reachable from -- `docker-compose.local.yml` publishes it
on 127.0.0.1, while `.env` names the Compose hostname the containers use:

    NEO4J_URL=bolt://localhost:7687 .venv/bin/python -m pytest \\
        tests/test_graph_live.py -m live_graph -v

The fabricated fixture matters here more than anywhere: a correct answer about
Quantum Basketry cannot come from training data, so a prerequisite chain found
in it was found in the text.
"""
import os
from pathlib import Path

import pytest

from api.graph import client as graph_client
from api.graph import store
from api.graph.client import get_graph_client, set_graph_client
from api.graph.grounding import Grounded
from api.graph.parser import parse_sections

pytestmark = pytest.mark.live_graph

ALICE = "aaaaaaaaaa11111"
BOB = "bbbbbbbbbb22222"
ALICE_MATERIAL = "mmmmmmmmmm11111"
BOB_MATERIAL = "mmmmmmmmmm22222"


@pytest.fixture
async def live_graph(monkeypatch):
    """Opt back in to the real database that conftest turns off for everyone else.

    `graph_off_by_default` is autouse and blanks `neo4j_url` so no ordinary test
    can reach a Neo4j. That is exactly right for them and exactly wrong here, so
    this fixture puts the environment's values back -- deliberately, in the one
    file whose whole purpose is to run against the real thing.
    """
    url = os.environ.get("NEO4J_URL")
    if not url:
        pytest.skip("NEO4J_URL is not set")

    monkeypatch.setattr(graph_client.settings, "neo4j_url", url)
    monkeypatch.setattr(
        graph_client.settings, "neo4j_username", os.environ.get("NEO4J_USERNAME", "neo4j")
    )
    monkeypatch.setattr(
        graph_client.settings, "neo4j_password", os.environ.get("NEO4J_PASSWORD", "")
    )

    set_graph_client(None)
    client = await get_graph_client()
    if not client.is_configured:
        pytest.skip(f"Neo4j is not reachable at {url}")

    yield client

    for key in (ALICE, BOB):
        await client.run(
            "MATCH (n) WHERE n.library_key = $library_key DETACH DELETE n", library_key=key
        )
    set_graph_client(None)


def edges_for(concept, requires, section_id):
    return Grounded(
        kind="requires", concept=concept, requires=requires, section_id=section_id,
        excerpt="a quote long enough to have been grounded", char_start=0, char_end=44,
    )


async def seed(library_key, material_id):
    source = (Path(__file__).parent / "fixtures" / "quantum_basketry.md").read_text()
    sections = parse_sections(source, material_id)
    first = sections[0].section_id
    await store.write_generation(
        library_key, material_id, "Quantum Basketry", 1, sections,
        [
            Grounded(
                kind="defines", concept="Damp Tension", requires="", section_id=first,
                excerpt="a quote long enough to have been grounded", char_start=0, char_end=44,
            ),
            edges_for("Rule of Terminal Reversal", "Damp Tension", first),
            edges_for("Rule of Lattice Parity", "Rule of Terminal Reversal", first),
            edges_for("Coherence Number", "Rule of Lattice Parity", first),
        ],
    )
    return sections


async def test_the_cypher_runs_against_a_real_neo4j(live_graph):
    """The thing no stand-in can tell you."""
    sections = await seed(ALICE, ALICE_MATERIAL)

    assert await store.count_material_sections(ALICE, ALICE_MATERIAL) == len(sections)


async def test_a_prerequisite_chain_is_traversed_transitively(live_graph):
    """The one question vector search cannot answer.

    Coherence Number requires Lattice Parity requires Terminal Reversal
    requires Damp Tension -- three hops, and only the first is a direct edge.
    """
    await seed(ALICE, ALICE_MATERIAL)

    found = await store.prerequisites_of(ALICE, "Coherence Number", depth=3)

    assert {row["concept_key"] for row in found} == {
        "rule of lattice parity",
        "rule of terminal reversal",
        "damp tension",
    }


async def test_a_traversal_never_leaves_its_library(live_graph):
    """The case a property-bounded implementation could plausibly get wrong.

    Both Libraries hold a Concept with the same key, and they are joined by a
    REQUIRES edge written directly. A query that filtered only the start node
    would walk across it.
    """
    await seed(ALICE, ALICE_MATERIAL)
    await seed(BOB, BOB_MATERIAL)

    await live_graph.run(
        """
        MATCH (a:Concept {library_key: $alice, concept_key: 'damp tension'})
        MATCH (b:Concept {library_key: $bob, concept_key: 'coherence number'})
        MERGE (a)-[:REQUIRES {excerpt: 'planted'}]->(b)
        """,
        alice=ALICE,
        bob=BOB,
    )

    found = await store.prerequisites_of(ALICE, "Coherence Number", depth=5)

    assert all(row["concept_key"] != "coherence number" for row in found)
    assert await store.count_material_sections(ALICE, BOB_MATERIAL) == 0


async def test_a_second_library_is_never_visible(live_graph):
    await seed(BOB, BOB_MATERIAL)

    assert await store.prerequisites_of(ALICE, "Coherence Number") == []


async def test_removal_leaves_no_sections_and_no_orphaned_concepts(live_graph):
    """The end-to-end lifecycle assertion, against a real database."""
    await seed(ALICE, ALICE_MATERIAL)

    await store.delete_material(ALICE, ALICE_MATERIAL)

    assert await store.count_material_sections(ALICE, ALICE_MATERIAL) == 0
    assert await store.count_orphaned_concepts(ALICE) == 0


async def test_removing_one_material_leaves_another_library_intact(live_graph):
    await seed(ALICE, ALICE_MATERIAL)
    await seed(BOB, BOB_MATERIAL)

    await store.delete_material(ALICE, ALICE_MATERIAL)

    assert await store.count_material_sections(BOB, BOB_MATERIAL) > 0


async def test_a_rebuild_of_the_same_source_copy_replaces_rather_than_duplicates(live_graph):
    sections = await seed(ALICE, ALICE_MATERIAL)
    await seed(ALICE, ALICE_MATERIAL)
    await store.cut_over(ALICE, ALICE_MATERIAL, 1)

    assert await store.count_material_sections(ALICE, ALICE_MATERIAL) == len(sections)
