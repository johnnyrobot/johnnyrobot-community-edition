"""
Source Identity.

Canvas synchronisation used to mint a new Course Material every run with no
existence check, so re-importing one source resource produced an unbounded
series of duplicates in both the control plane and the File Search store. The
domain model already said a re-imported Source Identity updates its existing
material; the schema simply never carried the field.
"""
import pytest

from api.services.canvas_memory_service import CanvasMemoryService, source_identity_for
from tests.test_material_lifecycle import provider_files  # noqa: F401

PAGE = {
    "page_id": "7",
    "title": "Week 1: Cells",
    "body": "<p>" + ("Cell biology content. " * 20) + "</p>",
    "updated_at": "2026-08-01T00:00:00Z",
}


def test_a_source_identity_is_stable_for_the_same_resource():
    first = source_identity_for("https://canvas.example.edu", "page", "7")
    second = source_identity_for("https://canvas.example.edu/", "page", "7")

    assert first == second == "canvas:canvas.example.edu:page:7"


def test_different_resources_have_different_identities():
    assert source_identity_for("https://canvas.example.edu", "page", "7") != source_identity_for(
        "https://canvas.example.edu", "page", "8"
    )


async def test_a_first_import_creates_a_course_material(provider, provider_files, alice):
    service = CanvasMemoryService(alice["id"], "https://canvas.example.edu")

    await service.process_canvas_data("page", PAGE, "Biology 101", canvas_id="7")

    records = provider.records("course_materials")
    assert len(records) == 1
    assert records[0]["source_identity"] == "canvas:canvas.example.edu:page:7"
    assert records[0]["material_source"] == "canvas"


async def test_re_importing_the_same_source_does_not_add_a_material(provider, provider_files, alice):
    service = CanvasMemoryService(alice["id"], "https://canvas.example.edu")

    for _ in range(4):
        await service.process_canvas_data("page", PAGE, "Biology 101", canvas_id="7")

    assert len(provider.records("course_materials")) == 1


async def test_re_importing_updates_the_existing_material(provider, provider_files, alice):
    service = CanvasMemoryService(alice["id"], "https://canvas.example.edu")
    await service.process_canvas_data("page", PAGE, "Biology 101", canvas_id="7")

    revised = {**PAGE, "title": "Week 1: Cells (revised)"}
    await service.process_canvas_data("page", revised, "Biology 101", canvas_id="7")

    record = provider.records("course_materials")[0]
    assert "revised" in record["title"]
    assert record["status"] == "ready"


async def test_repeated_updates_with_an_unchanged_title_use_different_provider_names(
    provider, provider_files, alice
):
    """A same-title re-sync must not collide with the file it replaces.

    ``PAGE``'s title never changes across these three calls -- the ordinary
    steady-state re-sync case, not an edge case. The first call creates the
    Course Material; the second and third both take the Material Update path.
    A disambiguator derived only from title length would compute the same
    provider file name for both updates, since the title is identical, which
    means the second update would try to replace the file it just created
    with itself.
    """
    service = CanvasMemoryService(alice["id"], "https://canvas.example.edu")

    for _ in range(3):
        await service.process_canvas_data("page", PAGE, "Biology 101", canvas_id="7")

    names = [config["name"] for config in provider_files["uploaded"]]
    assert len(names) == 3
    assert len(set(names)) == 3


async def test_the_same_source_in_two_libraries_is_two_materials(provider, provider_files, alice, bob):
    """Source Identity is unique within one Student Library, not globally."""
    await CanvasMemoryService(alice["id"], "https://canvas.example.edu").process_canvas_data(
        "page", PAGE, "Biology 101", canvas_id="7"
    )
    await CanvasMemoryService(bob["id"], "https://canvas.example.edu").process_canvas_data(
        "page", PAGE, "Biology 101", canvas_id="7"
    )

    assert len(provider.records("course_materials")) == 2


async def test_a_failed_update_leaves_the_existing_content_unchanged(provider, provider_files, alice):
    """A failed Material Update never costs a working material."""
    service = CanvasMemoryService(alice["id"], "https://canvas.example.edu")
    await service.process_canvas_data("page", PAGE, "Biology 101", canvas_id="7")
    before = provider.records("course_materials")[0]

    provider_files["fail_upload"] = True
    await service.process_canvas_data(
        "page", {**PAGE, "title": "Week 1: Cells (revised)"}, "Biology 101", canvas_id="7"
    )

    after = provider.records("course_materials")[0]
    assert after["title"] == before["title"]
    assert after["provider_file_name"] == before["provider_file_name"]
    assert after["status"] == "ready"


async def test_direct_uploads_never_collide_with_each_other(provider, provider_files, alice, tmp_path):
    """A direct upload carries no Source Identity, so the index ignores it."""
    from api.services.gemini_service import GeminiService

    path = tmp_path / "notes.md"
    path.write_text("course material bytes")
    service = GeminiService()
    await service.upload_textbook(str(path), "Notes", alice["id"])
    await service.upload_textbook(str(path), "Notes", alice["id"])

    assert len(provider.records("course_materials")) == 2
