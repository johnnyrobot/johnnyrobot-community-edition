"""
Course Material upload staging.

An uploaded file is written to a staging directory before it is handed to
Gemini. The client controls the filename, so staging must not let that name
decide where the bytes land.
"""
import io
import os

import pytest
from fastapi import UploadFile

from api.routers import textbooks


@pytest.fixture
def staged_path(monkeypatch, tmp_path):
    """Run the upload against a stub service and capture the staged path."""
    recorded = {}

    class StubGeminiService:
        async def upload_textbook(self, file_path, title, user_id):
            recorded["path"] = os.path.realpath(file_path)
            return "stub-material-id"

    monkeypatch.setattr(textbooks, "GeminiService", StubGeminiService)
    monkeypatch.chdir(tmp_path)
    return recorded


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "hostile_filename",
    [
        "../../../../tmp/pwned.txt",
        "../.bashrc.md",
        "/etc/cron.d/pwned.txt",
        "..\\..\\windows.txt",
    ],
    ids=["deep-traversal", "parent-escape", "absolute-path", "backslash"],
)
async def test_upload_stays_inside_the_staging_directory(
    staged_path, tmp_path, hostile_filename
):
    upload = UploadFile(
        filename=hostile_filename,
        file=io.BytesIO(b"course material bytes"),
    )

    await textbooks.upload_textbook(file=upload, title="Notes", user_id="alice")

    staging_dir = os.path.realpath(tmp_path / "temp_uploads")
    assert staged_path["path"].startswith(staging_dir + os.sep)
