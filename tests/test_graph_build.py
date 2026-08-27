"""
The build, and the manifest that makes it accountable.

Two properties matter most here and neither is about the graph being good.

The manifest is content-free by construction (the graph-build determinism contract lists "identities,
epochs, counts, warnings, resource handles, and exact stage roots" -- no
content), because it is written on every build of every material and a
manifest holding excerpts would be a second place a Course Material could leak
from.

And a failed build never changes Material Status. the graph-build determinism contract confines a graph
failure to "the Optional/Shadow graph branch, never weakening the authorized
Gemini baseline". For the live demo: nothing here may stop the tutor answering.
"""
import pytest

from api.database.repository import get_repository
from api.graph import build as graph_build
from api.graph import extraction
from api.graph.client import NoOpGraphClient, set_graph_client

FIXTURE = "tests/fixtures/quantum_basketry.md"

# The \\n escapes are where the fixture wraps. json.loads turns them into real
# newlines, and grounding then finds the quote verbatim. Move a break and this
# stops being a grounded edge, which is the whole mechanism working.
DEPENDENCY_ANSWER = """
[
  {"kind": "requires", "concept": "Rule of Terminal Reversal",
   "requires": "Damp Tension",
   "excerpt": "Applying this rule requires Damp Tension,\\nbecause a reversal that is not tension-corrected unwinds the neighbouring\\nstrand."}
]
"""


class CollectingGraphClient:
    is_configured = True

    def __init__(self):
        self.calls = []

    async def run(self, cypher, **params):
        self.calls.append((cypher, params))
        return []

    async def close(self):
        return None


@pytest.fixture
def graph():
    client = CollectingGraphClient()
    set_graph_client(client)
    yield client
    set_graph_client(None)


@pytest.fixture
def fixture_copy(tmp_path):
    from pathlib import Path

    source = Path(FIXTURE).read_text()
    path = tmp_path / "quantum_basketry.md"
    path.write_text(source)
    return str(path)


@pytest.fixture
def quiet_model(monkeypatch):
    """A model that finds nothing. The default for tests not about extraction."""
    monkeypatch.setattr(extraction, "_generate", lambda prompt, model: "[]")


async def test_a_build_records_a_manifest(provider, alice, graph, fixture_copy, quiet_model):
    material = await get_repository().create_material(alice["id"], {"title": "QB", "status": "ready"})

    await graph_build.build_material_graph(alice["id"], material["id"], "QB", fixture_copy)

    assert len(provider.records("graph_build_manifests")) == 1


async def test_the_manifest_carries_no_content(provider, alice, graph, fixture_copy, monkeypatch):
    """The property that keeps this from being a second leak surface."""
    monkeypatch.setattr(extraction, "_generate", lambda prompt, model: DEPENDENCY_ANSWER)
    material = await get_repository().create_material(alice["id"], {"title": "QB", "status": "ready"})

    await graph_build.build_material_graph(alice["id"], material["id"], "QB", fixture_copy)

    recorded = str(provider.records("graph_build_manifests")[0])
    for phrase in ["Damp Tension", "coherence", "0.7734", "strand", "Terminal Reversal", "QB"]:
        assert phrase not in recorded, f"the manifest leaked {phrase!r}"


async def test_a_grounded_edge_reaches_the_graph(provider, alice, graph, fixture_copy, monkeypatch):
    monkeypatch.setattr(extraction, "_generate", lambda prompt, model: DEPENDENCY_ANSWER)
    material = await get_repository().create_material(alice["id"], {"title": "QB", "status": "ready"})

    manifest = await graph_build.build_material_graph(alice["id"], material["id"], "QB", fixture_copy)

    assert manifest.edges_accepted >= 1
    assert any("REQUIRES" in cypher for cypher, _ in graph.calls)


async def test_an_ungroundable_candidate_is_counted_and_dropped(
    provider, alice, graph, fixture_copy, monkeypatch
):
    """Rejected, never repaired. A repaired candidate is an invented one."""
    invented = """
    [{"kind": "requires", "concept": "Coherence Number", "requires": "Lattice Parity",
      "excerpt": "The coherence number is derived from the parity of the weave lattice."}]
    """
    monkeypatch.setattr(extraction, "_generate", lambda prompt, model: invented)
    material = await get_repository().create_material(alice["id"], {"title": "QB", "status": "ready"})

    manifest = await graph_build.build_material_graph(alice["id"], material["id"], "QB", fixture_copy)

    assert manifest.edges_accepted == 0
    assert manifest.candidates_rejected > 0


async def test_extraction_finding_nothing_is_a_successful_build(
    provider, alice, graph, fixture_copy, quiet_model
):
    """"No dependencies in this material" must stay distinguishable from "extraction is broken"."""
    material = await get_repository().create_material(alice["id"], {"title": "QB", "status": "ready"})

    manifest = await graph_build.build_material_graph(alice["id"], material["id"], "QB", fixture_copy)

    assert manifest.outcome == "built"
    assert manifest.edges_accepted == 0


async def test_a_pdf_is_a_recorded_non_build(provider, alice, graph, tmp_path, quiet_model):
    """No pinned PDF extractor is installed; the graph-build determinism contract makes that a graph-branch skip."""
    path = tmp_path / "book.pdf"
    path.write_bytes(b"%PDF-1.4 not really a pdf")
    material = await get_repository().create_material(alice["id"], {"title": "Book", "status": "ready"})

    manifest = await graph_build.build_material_graph(alice["id"], material["id"], "Book", str(path))

    assert manifest.outcome == "skipped_unsupported_format"


async def test_a_build_never_changes_material_status(
    provider, alice, fixture_copy, monkeypatch, graph
):
    """The rule the whole failure posture rests on."""
    def explode(prompt, model):
        raise RuntimeError("Gemini is down")

    monkeypatch.setattr(extraction, "_generate", explode)
    monkeypatch.setattr(
        graph_build.store, "write_generation",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("neo4j write failed")),
    )
    material = await get_repository().create_material(alice["id"], {"title": "QB", "status": "ready"})

    await graph_build.build_material_graph(alice["id"], material["id"], "QB", fixture_copy)

    assert provider.records("course_materials")[0]["status"] == "ready"


async def test_a_failed_build_never_raises(
    provider, alice, fixture_copy, monkeypatch, graph, quiet_model
):
    """A build runs behind a Ready material. It has nothing it is allowed to break.

    `quiet_model` because this is about the write failing, not about extraction.
    Without it the test reaches live Gemini once per Section, which makes a unit
    test depend on a key being set and on somebody else's uptime.
    """
    monkeypatch.setattr(
        graph_build.store, "write_generation",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("neo4j write failed")),
    )
    material = await get_repository().create_material(alice["id"], {"title": "QB", "status": "ready"})

    manifest = await graph_build.build_material_graph(alice["id"], material["id"], "QB", fixture_copy)

    assert manifest.outcome == "failed"


async def test_a_failure_is_still_recorded(
    provider, alice, fixture_copy, monkeypatch, graph, quiet_model
):
    """"The build is marked failed in its manifest" -- a silent failure is worse."""
    monkeypatch.setattr(
        graph_build.store, "write_generation",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("neo4j write failed")),
    )
    material = await get_repository().create_material(alice["id"], {"title": "QB", "status": "ready"})

    await graph_build.build_material_graph(alice["id"], material["id"], "QB", fixture_copy)

    assert provider.records("graph_build_manifests")[0]["outcome"] == "failed"


async def test_a_deployment_with_no_graph_records_a_skip(provider, alice, fixture_copy, quiet_model):
    """An unset NEO4J_URL is a valid deployment, and the history should say so."""
    set_graph_client(NoOpGraphClient())
    try:
        material = await get_repository().create_material(
            alice["id"], {"title": "QB", "status": "ready"}
        )

        manifest = await graph_build.build_material_graph(
            alice["id"], material["id"], "QB", fixture_copy
        )

        assert manifest.outcome == "skipped_no_graph"
    finally:
        set_graph_client(None)


async def test_a_rebuild_takes_the_next_generation(
    provider, alice, graph, fixture_copy, quiet_model
):
    """Builds are generation-scoped; a rebuild never mutates in place."""
    material = await get_repository().create_material(alice["id"], {"title": "QB", "status": "ready"})

    first = await graph_build.build_material_graph(alice["id"], material["id"], "QB", fixture_copy)
    second = await graph_build.build_material_graph(alice["id"], material["id"], "QB", fixture_copy)

    assert second.generation == first.generation + 1


async def test_the_stage_digests_are_stable_across_rebuilds(
    provider, alice, graph, fixture_copy, quiet_model
):
    """Identical stage digests with differing counts is exactly the Drifted signal."""
    material = await get_repository().create_material(alice["id"], {"title": "QB", "status": "ready"})

    first = await graph_build.build_material_graph(alice["id"], material["id"], "QB", fixture_copy)
    second = await graph_build.build_material_graph(alice["id"], material["id"], "QB", fixture_copy)

    assert (first.source_digest, first.sections_digest) == (
        second.source_digest,
        second.sections_digest,
    )


async def test_build_history_is_owner_scoped(provider, alice, bob, graph, fixture_copy, quiet_model):
    material = await get_repository().create_material(alice["id"], {"title": "QB", "status": "ready"})
    await graph_build.build_material_graph(alice["id"], material["id"], "QB", fixture_copy)

    assert await get_repository().list_graph_manifests(bob["id"]) == []
