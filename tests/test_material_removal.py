"""
Material Removal.

Removal immediately and permanently excludes a Course Material from listing
and search (the immediate-removal contract). Deleting only the record leaves the file retrievable in
The File Search store, so removal is not complete until the provider file is
gone — and a provider failure is never reported as success.

This is Material Removal, not the independently verified Material Purge the
domain model defines.
"""
import pytest

from api.database.repository import get_repository
from api.services.gemini_service import GeminiService
from tests.test_material_lifecycle import provider_files  # noqa: F401

API = "/api/v1"


async def upload(student_id, tmp_path, title="Notes"):
    path = tmp_path / "notes.md"
    path.write_text("course material bytes")
    return await GeminiService().upload_textbook(str(path), title, student_id)


async def test_removal_takes_the_record_and_the_provider_file(provider, provider_files, tmp_path, alice):
    material_id = await upload(alice["id"], tmp_path)

    await GeminiService().delete_textbook(material_id, alice["id"])

    assert provider.records("course_materials") == []
    assert provider_files["deleted"] == [f"cm-{material_id}"]


async def test_removal_takes_the_document_out_of_the_library_store(
    provider, provider_files, tmp_path, alice
):
    """Deleting the uploaded file is not enough; the document outlives it.

    Measured against live Gemini while deciding per-Library store isolation: after
    `files.delete`, a query against the store still returned the material's
    content. Only deleting the imported Document removed it. Removal that
    stops at the file reports success while the Course Material is still
    searchable — the exact opposite of what the immediate-removal contract requires.
    """
    material_id = await upload(alice["id"], tmp_path)
    document = provider.records("course_materials")[0]["provider_document_name"]
    assert document

    await GeminiService().delete_textbook(material_id, alice["id"])

    assert provider_files["documents_deleted"] == [document]


async def test_a_material_records_the_document_that_makes_it_searchable(
    provider, provider_files, tmp_path, alice
):
    """The document name is recorded at import, because removal needs it later.

    It is returned once, by the import operation. Not recording it means a
    later removal has to search the store for the right document, or cannot
    find it at all.
    """
    await upload(alice["id"], tmp_path)

    record = provider.records("course_materials")[0]
    assert record["provider_document_name"].startswith("fileSearchStores/")
    assert "/documents/" in record["provider_document_name"]


async def test_a_failed_document_delete_is_never_reported_as_success(
    provider, provider_files, tmp_path, alice
):
    """A material left searchable must not be reported as removed."""
    material_id = await upload(alice["id"], tmp_path)
    provider_files["fail_document_delete"] = True

    with pytest.raises(RuntimeError):
        await GeminiService().delete_textbook(material_id, alice["id"])

    assert provider.records("course_materials")[0]["status"] == "failed"


async def test_a_failed_provider_delete_is_never_reported_as_success(provider, provider_files, tmp_path, alice):
    material_id = await upload(alice["id"], tmp_path)
    provider_files["fail_delete"] = True

    with pytest.raises(RuntimeError):
        await GeminiService().delete_textbook(material_id, alice["id"])


async def test_a_failed_provider_delete_leaves_a_visible_failed_state(provider, provider_files, tmp_path, alice):
    """The Student must be able to see that removal did not finish."""
    material_id = await upload(alice["id"], tmp_path)
    provider_files["fail_delete"] = True

    with pytest.raises(RuntimeError):
        await GeminiService().delete_textbook(material_id, alice["id"])

    record = provider.records("course_materials")[0]
    assert record["status"] == "failed"


async def test_removing_another_students_material_does_nothing(provider, provider_files, tmp_path, alice, bob):
    material_id = await upload(bob["id"], tmp_path)

    await GeminiService().delete_textbook(material_id, alice["id"])

    assert len(provider.records("course_materials")) == 1
    assert provider_files["deleted"] == []


async def test_the_route_lists_only_the_callers_materials(client, provider, provider_files, tmp_path, alice, bob):
    await upload(alice["id"], tmp_path, "Alice notes")
    await upload(bob["id"], tmp_path, "Bob notes")

    response = await client.get(f"{API}/textbooks/", headers=alice["headers"])

    assert [m["title"] for m in response.json()["textbooks"]] == ["Alice notes"]


async def test_the_route_refuses_to_delete_another_students_material(client, provider, provider_files, tmp_path, alice, bob):
    material_id = await upload(bob["id"], tmp_path)

    response = await client.delete(f"{API}/textbooks/{material_id}", headers=alice["headers"])

    assert response.status_code == 404
    assert len(provider.records("course_materials")) == 1


async def test_the_route_reports_a_failed_provider_delete_without_quoting_it(
    client, provider, provider_files, tmp_path, alice
):
    """The Student is told removal did not finish; the reason stays in the log.

    The provider failure text names the provider file and the underlying
    error. None of that belongs in a browser, and neither does an unhandled
    traceback — which is what this route produced before, with no response of
    its own at all.
    """
    material_id = await upload(alice["id"], tmp_path)
    provider_files["fail_delete"] = True

    response = await client.delete(f"{API}/textbooks/{material_id}", headers=alice["headers"])

    assert response.status_code == 500
    assert response.json()["detail"] == "Could not remove the Course Material"
    assert "provider delete failed" not in response.text
    assert f"cm-{material_id}" not in response.text


async def test_a_route_level_removal_failure_leaves_a_visible_failed_state(
    client, provider, provider_files, tmp_path, alice
):
    """Reporting the failure is not enough: the material must look failed too."""
    material_id = await upload(alice["id"], tmp_path)
    provider_files["fail_delete"] = True

    await client.delete(f"{API}/textbooks/{material_id}", headers=alice["headers"])

    records = provider.records("course_materials")
    assert len(records) == 1 and records[0]["status"] == "failed"


async def test_the_route_rejects_a_malformed_identity(client, provider, provider_files, alice):
    response = await client.delete(f"{API}/textbooks/not-an-id", headers=alice["headers"])

    assert response.status_code == 404


# Not in the brief's literal test list, added because a Processing or Failed
# material may never have reached the provider at all -- the removal contract
# this feature is explicit that removal must work for a material in any
# Material Status, and that a Failed material "may or may not have a provider
# copy." This exercises the "may not" branch directly.
async def test_removing_a_material_with_no_provider_copy_succeeds(provider, provider_files, alice):
    """A still-Processing (or Failed-before-indexing) material has no provider file yet."""
    material = await get_repository().create_material(
        alice["id"], {"title": "Still processing", "status": "processing"}
    )

    await GeminiService().delete_textbook(material["id"], alice["id"])

    assert provider.records("course_materials") == []
    assert provider_files["deleted"] == []
