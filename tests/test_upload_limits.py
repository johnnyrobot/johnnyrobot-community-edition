"""
Upload limits.

Uploads accept PDF, TXT, and Markdown up to 100MB. The limit is enforced while
streaming rather than by trusting a declared length, because a declared length
is the client's word.
"""
import pytest

from api.database.repository import get_repository
from api.routers import textbooks

API = "/api/v1"


@pytest.fixture(autouse=True)
def gemini(monkeypatch):
    """Accept anything that gets past the boundary, so failures are the boundary's."""
    class StubGeminiService:
        async def upload_textbook(self, file_path, title, user_id, **kwargs):
            return "aaaaaaaaaaaaaaa"

    monkeypatch.setattr(textbooks, "GeminiService", StubGeminiService)


async def upload(client, headers, filename, content, content_type="text/markdown"):
    return await client.post(
        f"{API}/textbooks/upload",
        headers=headers,
        files={"file": (filename, content, content_type)},
        data={"title": "Notes"},
    )


@pytest.mark.parametrize("filename", ["notes.pdf", "notes.txt", "notes.md"], ids=["pdf", "txt", "md"])
async def test_a_permitted_type_is_accepted(client, alice, filename):
    response = await upload(client, alice["headers"], filename, b"course material bytes")

    assert response.status_code == 200


@pytest.mark.parametrize(
    "filename",
    ["notes.docx", "notes.csv", "notes.html", "notes.json", "notes.exe", "notes"],
    ids=["docx", "csv", "html", "json", "executable", "no-extension"],
)
async def test_a_type_outside_the_claim_is_rejected(client, alice, filename):
    response = await upload(client, alice["headers"], filename, b"course material bytes")

    assert response.status_code == 415
    assert "PDF" in response.json()["detail"]


async def test_a_file_over_the_limit_is_rejected(client, alice):
    oversized = b"x" * (textbooks.MAX_UPLOAD_BYTES + 1)

    response = await upload(client, alice["headers"], "notes.md", oversized)

    assert response.status_code == 413
    assert "100" in response.json()["detail"]


async def test_a_file_at_the_limit_is_accepted(client, alice):
    at_limit = b"x" * textbooks.MAX_UPLOAD_BYTES

    response = await upload(client, alice["headers"], "notes.md", at_limit)

    assert response.status_code == 200


async def test_an_oversized_upload_leaves_nothing_staged(client, alice, tmp_path, monkeypatch):
    """Rejection must not leave a 100MB partial file behind."""
    monkeypatch.chdir(tmp_path)
    oversized = b"x" * (textbooks.MAX_UPLOAD_BYTES + 1)

    await upload(client, alice["headers"], "notes.md", oversized)

    staging = tmp_path / "temp_uploads"
    assert not staging.exists() or list(staging.iterdir()) == []


async def test_an_oversized_upload_creates_no_course_material(client, alice):
    """The limit check runs before the upload workflow's processing record is created.

    A rejected upload must leave no Course Material behind for the Student to
    find in a broken 'processing' state later.
    """
    oversized = b"x" * (textbooks.MAX_UPLOAD_BYTES + 1)

    response = await upload(client, alice["headers"], "notes.md", oversized)

    assert response.status_code == 413
    assert await get_repository().list_materials(alice["id"]) == []


async def test_a_disallowed_type_creates_no_course_material(client, alice):
    """The type check also runs before the processing record is created."""
    response = await upload(client, alice["headers"], "notes.docx", b"course material bytes")

    assert response.status_code == 415
    assert await get_repository().list_materials(alice["id"]) == []
