"""
Isolation is the non-negotiable one.

A second Student must never reach another's Concepts, Sections, or edges --
through a direct query, through a Material Selection naming another's
material, or through a REQUIRES traversal that walks out of its Library.

The traversal case is new and is the one a property-bounded implementation
could plausibly get wrong: a multi-hop query that filters the start node but
not the path. the optional graph boundary's Graph Cell -- one database per Library, which the
engine enforces -- is unachievable on Neo4j Community, so this filter is all
there is. Everything here exists because of that.

The roster at the bottom is the same device as
tests/test_cross_student_isolation.py's: a newly added query that forgets the
Library filter fails a test instead of shipping.
"""
import pytest

from api.graph import store
from api.graph.client import set_graph_client
from api.graph.grounding import Grounded
from api.graph.parser import parse_sections

ALICE = "aaaaaaaaaa11111"
BOB = "bbbbbbbbbb22222"
ALICE_MATERIAL = "mmmmmmmmmm11111"
BOB_MATERIAL = "mmmmmmmmmm22222"


class RecordingGraphClient:
    """Records every Cypher string and parameter set the store emits.

    Deliberately not a graph. What these tests check is what the store *asks*
    for -- whether the Library filter is present and whether it binds every hop
    -- and a fake that answered queries would let a query with no filter pass
    because the fake happened to hold one Library's data.
    """

    is_configured = True

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def run(self, cypher: str, **params) -> list[dict]:
        self.calls.append((cypher, params))
        return []

    async def close(self) -> None:
        return None


@pytest.fixture
def graph():
    recorder = RecordingGraphClient()
    set_graph_client(recorder)
    yield recorder
    set_graph_client(None)


def grounded_edge():
    return Grounded(
        kind="requires",
        concept="Rule of Terminal Reversal",
        requires="Damp Tension",
        section_id="s1",
        excerpt="Applying this rule requires Damp Tension",
        char_start=10,
        char_end=50,
    )


# -- Every query names the Library ------------------------------------------


async def test_every_write_carries_the_library_key(graph):
    sections = parse_sections("# One\n\nBody text here.\n", ALICE_MATERIAL)

    await store.write_generation(ALICE, ALICE_MATERIAL, "Notes", 1, sections, [grounded_edge()])

    assert graph.calls
    for cypher, params in graph.calls:
        assert params.get("library_key") == ALICE, f"unscoped write: {cypher[:60]}"


async def test_a_read_carries_the_library_key(graph):
    await store.prerequisites_of(ALICE, "damp-tension")

    for cypher, params in graph.calls:
        assert params.get("library_key") == ALICE


async def test_a_delete_carries_the_library_key(graph):
    await store.delete_material(ALICE, ALICE_MATERIAL)

    for cypher, params in graph.calls:
        assert params.get("library_key") == ALICE


async def test_a_material_identity_cannot_widen_past_its_library(graph):
    """The shape gemini_service.get_search_tool_config has: ANDed, never replaced.

    Naming another Student's material must narrow within the caller's Library
    and find nothing, not reach across.
    """
    await store.count_material_sections(ALICE, BOB_MATERIAL)

    cypher, params = graph.calls[0]
    assert params["library_key"] == ALICE
    assert params["material_id"] == BOB_MATERIAL
    assert "library_key" in cypher


# -- The traversal, which is the case that could go wrong -------------------


async def test_a_traversal_binds_the_library_on_every_hop(graph):
    """Filtering the start node is not enough.

    A REQUIRES chain is variable-length. A query that scopes only where the
    walk begins will happily follow an edge into another Student's Concept and
    read it back as a prerequisite. The path predicate is what stops that.
    """
    await store.prerequisites_of(ALICE, "damp-tension", depth=3)

    cypher = graph.calls[0][0]
    assert "ALL(" in cypher and "nodes(path)" in cypher, (
        "A variable-length traversal must constrain every node on the path, "
        "not only the one it starts from."
    )


async def test_a_traversal_depth_is_never_taken_from_a_caller_unchecked():
    """Depth becomes query text, because Cypher takes no parameter there."""
    with pytest.raises(ValueError):
        await store.prerequisites_of(ALICE, "damp-tension", depth=99)


async def test_a_malformed_library_key_never_reaches_a_query(graph):
    with pytest.raises(ValueError):
        await store.prerequisites_of('" OR 1=1 //', "damp-tension")

    assert graph.calls == []


async def test_a_malformed_material_identity_never_reaches_a_query(graph):
    with pytest.raises(ValueError):
        await store.count_material_sections(ALICE, "not-an-id")

    assert graph.calls == []


# -- Values are parameters, never interpolation -----------------------------


async def test_no_query_interpolates_a_value(graph):
    """Concept names come from a model and contain whatever it produced.

    A display name with a quote in it must be a parameter, not part of the
    query text. Depth is the sole documented exception and is an int.
    """
    sections = parse_sections("# One\n\nBody text here.\n", ALICE_MATERIAL)
    hostile = Grounded(
        kind="requires",
        concept='Rule" OR 1=1 //',
        requires="Damp Tension",
        section_id="s1",
        excerpt="Applying this rule requires Damp Tension",
        char_start=10,
        char_end=50,
    )

    await store.write_generation(ALICE, ALICE_MATERIAL, "Notes", 1, sections, [hostile])

    for cypher, _ in graph.calls:
        assert '" OR 1=1' not in cypher


# -- Degradation ------------------------------------------------------------


async def test_a_graph_that_is_off_reads_as_empty():
    """No graph is indistinguishable from an empty graph, so nothing raises."""
    from api.graph.client import NoOpGraphClient

    set_graph_client(NoOpGraphClient())
    try:
        assert await store.prerequisites_of(ALICE, "damp-tension") == []
    finally:
        set_graph_client(None)


# -- The roster -------------------------------------------------------------


def test_every_query_in_the_module_filters_on_the_library():
    """What makes this a coverage claim rather than a list of the queries I remembered.

    A new query added to store.CYPHER without a library_key clause fails here.
    A query added *outside* CYPHER is caught by the next test.
    """
    unscoped = [name for name, cypher in store.CYPHER.items() if "library_key" not in cypher]

    assert unscoped == [], (
        "These queries do not name library_key. Every node in this graph "
        "carries one and every query filters on it unconditionally -- it is "
        "the only Library boundary Neo4j Community can give us."
    )


def test_every_variable_length_query_constrains_the_whole_path():
    """The traversal-escape guard, as a rule rather than one test's assertion."""
    import re

    variable_length = {
        name: cypher for name, cypher in store.CYPHER.items() if re.search(r"\*\s*\d", cypher)
    }
    unguarded = [
        name
        for name, cypher in variable_length.items()
        if "ALL(" not in cypher or "nodes(path)" not in cypher
    ]

    assert unguarded == [], (
        "A variable-length traversal must bind library_key on every node of "
        "the path. Filtering only the start node lets the walk leave the "
        "Student Library."
    )


def test_no_cypher_lives_outside_the_roster():
    """A query written inline would be invisible to both tests above."""
    import inspect

    source = inspect.getsource(store)
    body = source.split("CYPHER = {", 1)[1].split("\n}\n", 1)[1]

    for keyword in ("MATCH ", "MERGE ", "CREATE ", "DETACH DELETE"):
        assert keyword not in body, (
            f"Found {keyword.strip()!r} outside store.CYPHER. Every query in "
            "this application lives in that dict so the roster tests can see it."
        )
