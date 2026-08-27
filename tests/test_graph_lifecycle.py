"""
The graph follows the Course Material lifecycle.

The graph is a stored representation, so the glossary's rules reach it
directly: Material Removal is immediate and includes the graph, Material Update
cuts over atomically, and Material Purge establishes absence rather than
assuming it.

The end-to-end shape here is the graph analogue of
tests/test_material_removal.py, which already checks the provider file is gone
and not merely unlinked.
"""
import pytest

from api.graph import build as graph_build
from api.graph import extraction
from api.graph.client import NoOpGraphClient, set_graph_client
from api.services.gemini_service import GeminiService
from tests.test_material_lifecycle import provider_files  # noqa: F401


class SpyGraphClient:
    is_configured = True

    def __init__(self):
        self.calls = []
        self.fail = False

    async def run(self, cypher, **params):
        if self.fail:
            raise RuntimeError("neo4j unavailable")
        self.calls.append((cypher, params))
        # count_* queries read rows[0]["total"]; answer as an emptied graph.
        if "count(" in cypher:
            return [{"total": 0, "reaped": 0}]
        return []

    async def close(self):
        return None

    def cyphers(self):
        return " ".join(c for c, _ in self.calls)


@pytest.fixture
def graph():
    client = SpyGraphClient()
    set_graph_client(client)
    yield client
    set_graph_client(None)


@pytest.fixture(autouse=True)
def quiet_model(monkeypatch):
    monkeypatch.setattr(extraction, "_generate", lambda prompt, model: "[]")


async def upload(student_id, tmp_path, title="Notes", body="# One\n\nSome body text here.\n"):
    path = tmp_path / "notes.md"
    path.write_text(body)
    return await GeminiService().upload_textbook(str(path), title, student_id)


# -- Build after Ready ------------------------------------------------------


async def test_an_upload_builds_the_graph(provider, provider_files, graph, tmp_path, alice):
    await upload(alice["id"], tmp_path)

    assert provider.records("graph_build_manifests")


async def test_the_material_is_ready_before_the_graph_is_built(
    provider, provider_files, graph, tmp_path, alice
):
    """Building before Ready would let a graph failure block a searchable material."""
    statuses = []

    class WatchingClient(SpyGraphClient):
        async def run(self, cypher, **params):
            statuses.append(provider.records("course_materials")[0]["status"])
            return await super().run(cypher, **params)

    set_graph_client(WatchingClient())
    await upload(alice["id"], tmp_path)

    assert statuses and set(statuses) == {"ready"}


async def test_a_graph_failure_never_fails_the_upload(
    provider, provider_files, graph, tmp_path, alice
):
    graph.fail = True

    material_id = await upload(alice["id"], tmp_path)

    assert provider.records("course_materials")[0]["status"] == "ready"
    assert material_id


async def test_a_deployment_with_no_graph_still_uploads(provider, provider_files, tmp_path, alice):
    set_graph_client(NoOpGraphClient())
    try:
        material_id = await upload(alice["id"], tmp_path)

        assert provider.records("course_materials")[0]["status"] == "ready"
        assert material_id
    finally:
        set_graph_client(None)


async def test_a_material_that_never_reaches_ready_is_never_built(
    provider, provider_files, graph, tmp_path, alice
):
    """Only Ready materials are usable by a Tutor Session, so only Ready ones are built."""
    provider_files["fail_upload"] = True

    with pytest.raises(Exception):
        await upload(alice["id"], tmp_path)

    assert provider.records("graph_build_manifests") == []


# -- Removal ----------------------------------------------------------------


async def test_removal_deletes_the_materials_sections(
    provider, provider_files, graph, tmp_path, alice
):
    material_id = await upload(alice["id"], tmp_path)
    graph.calls.clear()

    await GeminiService().delete_textbook(material_id, alice["id"])

    assert "DETACH DELETE" in graph.cyphers()


async def test_removal_reaps_orphaned_concepts(provider, provider_files, graph, tmp_path, alice):
    """An ungrounded Concept would let a removed material's vocabulary survive."""
    material_id = await upload(alice["id"], tmp_path)
    graph.calls.clear()

    await GeminiService().delete_textbook(material_id, alice["id"])

    assert "DEFINED_IN" in graph.cyphers()


async def test_removal_is_synchronous_not_queued(provider, provider_files, graph, tmp_path, alice):
    """Material Removal is immediate, and the graph is a stored representation."""
    material_id = await upload(alice["id"], tmp_path)
    graph.calls.clear()

    await GeminiService().delete_textbook(material_id, alice["id"])

    assert graph.calls, "the graph delete had not run by the time removal returned"


async def test_removal_names_only_the_callers_library(
    provider, provider_files, graph, tmp_path, alice
):
    material_id = await upload(alice["id"], tmp_path)
    graph.calls.clear()

    await GeminiService().delete_textbook(material_id, alice["id"])

    assert all(params.get("library_key") == alice["id"] for _, params in graph.calls)


async def test_a_graph_that_cannot_be_cleared_is_not_reported_as_removed(
    provider, provider_files, graph, tmp_path, alice
):
    """The asymmetry with builds, stated as a test.

    A build that fails costs prerequisites. A removal that fails leaves the
    material's vocabulary in a graph the tutor can still read.
    """
    material_id = await upload(alice["id"], tmp_path)
    graph.fail = True

    with pytest.raises(Exception):
        await GeminiService().delete_textbook(material_id, alice["id"])


async def test_removal_works_with_no_graph_configured(provider, provider_files, tmp_path, alice):
    set_graph_client(NoOpGraphClient())
    try:
        material_id = await upload(alice["id"], tmp_path)

        await GeminiService().delete_textbook(material_id, alice["id"])

        assert provider.records("course_materials") == []
    finally:
        set_graph_client(None)


# -- Purge verification -----------------------------------------------------


async def test_purge_establishes_absence_rather_than_assuming_it(
    provider, provider_files, graph, tmp_path, alice
):
    material_id = await upload(alice["id"], tmp_path)
    await GeminiService().delete_textbook(material_id, alice["id"])
    graph.calls.clear()

    assert await graph_build.verify_material_purged(alice["id"], material_id) is True
    assert "count(" in graph.cyphers(), "purge must read back, not infer"


async def test_purge_verification_fails_when_sections_remain(
    provider, provider_files, tmp_path, alice
):
    class StillPopulated(SpyGraphClient):
        async def run(self, cypher, **params):
            self.calls.append((cypher, params))
            if "count(s)" in cypher:
                return [{"total": 3}]
            return [{"total": 0}]

    set_graph_client(StillPopulated())
    try:
        assert await graph_build.verify_material_purged(alice["id"], "mmmmmmmmmm11111") is False
    finally:
        set_graph_client(None)


# -- Update -----------------------------------------------------------------


async def test_an_update_builds_a_new_generation(provider, provider_files, graph, tmp_path, alice):
    material_id = await upload(alice["id"], tmp_path)

    path = tmp_path / "revised.md"
    path.write_text("# One\n\nRevised body text here.\n")
    await GeminiService().update_material_content(material_id, str(path), "Notes", alice["id"])

    generations = [m["generation"] for m in provider.records("graph_build_manifests")]
    assert sorted(generations) == [1, 2]


async def test_an_update_cuts_over_after_the_new_generation_is_written(
    provider, provider_files, graph, tmp_path, alice
):
    """The previous generation goes after cutover, never before."""
    material_id = await upload(alice["id"], tmp_path)
    graph.calls.clear()

    path = tmp_path / "revised.md"
    path.write_text("# One\n\nRevised body text here.\n")
    await GeminiService().update_material_content(material_id, str(path), "Notes", alice["id"])

    cyphers = [c for c, _ in graph.calls]
    wrote = next(i for i, c in enumerate(cyphers) if "MERGE (s:Section" in c)
    cut = next(i for i, c in enumerate(cyphers) if "s.generation <" in c)
    assert wrote < cut


async def test_a_failed_graph_build_never_fails_an_update(
    provider, provider_files, graph, tmp_path, alice
):
    material_id = await upload(alice["id"], tmp_path)
    graph.fail = True

    path = tmp_path / "revised.md"
    path.write_text("# One\n\nRevised body text here.\n")
    await GeminiService().update_material_content(material_id, str(path), "Notes", alice["id"])

    assert provider.records("course_materials")[0]["status"] == "ready"
