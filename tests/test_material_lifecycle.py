"""
A Course Material's life: created, indexed, listed, removed.

The identity is assigned by PocketBase before the file reaches the provider,
because that identity is written into the provider's metadata and is what the
owner filter narrows on. Material Status keeps a half-finished import visible
to its Student without being searchable.
"""
import pytest

from api.services.gemini_service import GeminiService


@pytest.fixture
def provider_files(monkeypatch):
    """A Gemini stand-in that records what it was asked to do."""
    recorded = {
        "uploaded": [],
        "imported": [],
        "deleted": [],
        "documents_deleted": [],
        "stores_created": [],
        "fail_delete": False,
        "fail_upload": False,
        "fail_document_delete": False,
    }

    class StubFiles:
        def upload(self, file=None, config=None):
            if recorded["fail_upload"]:
                raise RuntimeError("provider upload failed")
            recorded["uploaded"].append(config)
            return type("F", (), {"name": config["name"], "uri": f"uri://{config['name']}"})()

        def delete(self, name=None):
            if recorded["fail_delete"]:
                raise RuntimeError("provider delete failed")
            recorded["deleted"].append(name)

    class StubDocuments:
        """Documents outlive the files they were imported from.

        Measured against live Gemini (per-Library store isolation): deleting the uploaded file
        leaves the imported document in the store, and the content stays
        searchable through it. A stub where `files.delete` silently removed
        The document too would let a broken removal pass.
        """

        def delete(self, name=None, config=None):
            if recorded["fail_document_delete"]:
                raise RuntimeError("provider document delete failed")
            recorded["documents_deleted"].append(name)

    class StubStores:
        documents = StubDocuments()

        def create(self, config=None):
            recorded["stores_created"].append(config)
            name = f"fileSearchStores/lib-{len(recorded['stores_created'])}"
            return type("S", (), {"name": name})()

        def import_file(self, file_search_store_name=None, file_name=None, config=None):
            recorded["imported"].append(config)
            document = f"doc-{file_name}"
            return type(
                "Op",
                (),
                {
                    "done": True,
                    "response": type(
                        "R", (), {"document_name": document, "parent": file_search_store_name}
                    )(),
                },
            )()

    class StubClient:
        files = StubFiles()
        file_search_stores = StubStores()

    monkeypatch.setattr(GeminiService, "__init__", lambda self: None)
    original_new = GeminiService.__new__

    def build(cls, *args, **kwargs):
        instance = original_new(cls)
        instance.client = StubClient()
        instance.collection_name = "course_materials"
        return instance

    monkeypatch.setattr(GeminiService, "__new__", build)
    return recorded


async def test_an_upload_takes_a_pocketbase_identity(provider, provider_files, tmp_path):
    student_id = provider.add_student("alice@example.com", "pw")
    path = tmp_path / "notes.md"
    path.write_text("course material bytes")

    material_id = await GeminiService().upload_textbook(str(path), "Notes", student_id)

    assert len(material_id) == 15 and material_id.isalnum()


async def test_the_provider_metadata_carries_that_identity_and_owner(provider, provider_files, tmp_path):
    student_id = provider.add_student("alice@example.com", "pw")
    path = tmp_path / "notes.md"
    path.write_text("course material bytes")

    material_id = await GeminiService().upload_textbook(str(path), "Notes", student_id)

    metadata = {entry["key"]: entry["string_value"] for entry in provider_files["imported"][0]["custom_metadata"]}
    assert metadata["textbook_id"] == material_id
    assert metadata["uploaded_by"] == student_id


async def test_a_completed_upload_is_ready(provider, provider_files, tmp_path):
    student_id = provider.add_student("alice@example.com", "pw")
    path = tmp_path / "notes.md"
    path.write_text("course material bytes")

    await GeminiService().upload_textbook(str(path), "Notes", student_id)

    assert provider.records("course_materials")[0]["status"] == "ready"


async def test_a_failed_import_leaves_the_material_visible_and_failed(provider, provider_files, tmp_path):
    """A Failed material stays visible to its Student but is not searchable."""
    student_id = provider.add_student("alice@example.com", "pw")
    path = tmp_path / "notes.md"
    path.write_text("course material bytes")
    provider_files["fail_upload"] = True

    with pytest.raises(RuntimeError):
        await GeminiService().upload_textbook(str(path), "Notes", student_id)

    records = provider.records("course_materials")
    assert len(records) == 1 and records[0]["status"] == "failed"


async def test_listing_returns_only_the_owning_students_materials(provider, provider_files, tmp_path):
    alice_id = provider.add_student("alice@example.com", "pw")
    bob_id = provider.add_student("bob@example.com", "pw")
    path = tmp_path / "notes.md"
    path.write_text("course material bytes")
    service = GeminiService()
    await service.upload_textbook(str(path), "Alice notes", alice_id)
    await service.upload_textbook(str(path), "Bob notes", bob_id)

    materials = await service.list_textbooks(alice_id)

    assert [m["title"] for m in materials] == ["Alice notes"]


async def test_listing_without_an_owner_is_refused(provider, provider_files):
    """A Student Library is never global."""
    with pytest.raises(ValueError):
        await GeminiService().list_textbooks("")
