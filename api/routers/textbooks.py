"""
Textbook management routes.
"""
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form, status
from api.dependencies import get_current_user_id
from api.services.gemini_service import GeminiService
from api.database.pocketbase_client import ProviderUnavailable
from api.database.store import UnfilterableValue
from api.database.repository import get_repository
from api.config import get_settings
import os
import uuid
import logging
from pathlib import PurePath

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/textbooks", tags=["textbooks"])
settings = get_settings()

# The wider set this file once accepted (.docx, .csv, .html, .json) admitted
# types nothing downstream was written for.
ALLOWED_SUFFIXES = {".pdf", ".txt", ".md"}
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
_COPY_CHUNK = 1024 * 1024


def _staging_name(filename: str) -> str:
    """Build a staging filename the client cannot influence.

    The uploaded filename arrives in the Content-Disposition header and is not
    sanitised by Starlette, so it never reaches the path: joining it directly
    lets '../' climb out of the staging directory and an absolute name replace
    it outright. Only an allowlisted extension survives; the stem is generated.
    """
    suffix = PurePath(filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        suffix = ""
    return f"{uuid.uuid4().hex}{suffix}"


def _checked_suffix(filename: str) -> str:
    """Reject a Course Material whose type is outside the claim."""
    suffix = PurePath(filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Course Materials must be PDF, TXT, or MD files",
        )
    return suffix


def _stream_to_staging(upload: UploadFile, file_path: str) -> None:
    """Copy the upload, stopping the moment it exceeds the limit.

    The declared content length is the client's word. Counting the bytes that
    actually arrive is the only limit that holds, and stopping mid-stream is
    what keeps a rejected 100MB upload from being written out in full first.
    """
    written = 0
    with open(file_path, "wb") as buffer:
        while chunk := upload.file.read(_COPY_CHUNK):
            written += len(chunk)
            if written > MAX_UPLOAD_BYTES:
                # status.HTTP_413_REQUEST_ENTITY_TOO_LARGE is deprecated in the
                # installed Starlette (1.6.0); HTTP_413_CONTENT_TOO_LARGE is the
                # same 413 status under the current name.
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail="Course Materials must be 100MB or smaller",
                )
            buffer.write(chunk)


@router.post("/upload")
async def upload_textbook(
    file: UploadFile = File(...),
    title: str = Form(...),
    user_id: str = Depends(get_current_user_id)
):
    """Add a Course Material to the authenticated Student's Library."""
    _checked_suffix(file.filename)

    # Stage the upload under a generated name; see _staging_name.
    temp_dir = "temp_uploads"
    os.makedirs(temp_dir, exist_ok=True)
    file_path = os.path.join(temp_dir, _staging_name(file.filename))

    try:
        _stream_to_staging(file, file_path)

        service = GeminiService()
        textbook_id = await service.upload_textbook(file_path, title, user_id)
    except (HTTPException, ProviderUnavailable, UnfilterableValue):
        # Each already has a correct answer decided elsewhere; the catch-all
        # below would flatten a storage outage into a 500. See the same note
        # in api/routers/canvas.py.
        raise
    except Exception as e:
        logger.error(f"Upload error: {e}")
        raise HTTPException(status_code=500, detail="Could not add the Course Material")
    finally:
        # Remove the staged copy even when the import fails or is rejected.
        if os.path.exists(file_path):
            os.remove(file_path)

    return {"id": textbook_id, "message": "Course Material added"}

@router.get("/")
async def list_textbooks(user_id: str = Depends(get_current_user_id)):
    """List the authenticated Student's Course Materials."""
    service = GeminiService()
    textbooks = await service.list_textbooks(user_id)
    return {"textbooks": textbooks}


@router.delete("/{textbook_id}")
async def delete_textbook(
    textbook_id: str,
    user_id: str = Depends(get_current_user_id),
):
    """Remove one of the authenticated Student's Course Materials.

    Ownership is not checked here. The repository scopes the lookup to the
    caller, so another Student's material is simply not found.
    """
    service = GeminiService()
    if await get_repository().get_material(user_id, textbook_id) is None:
        raise HTTPException(status_code=404, detail="Course Material not found")

    try:
        await service.delete_textbook(textbook_id, user_id)
    except (HTTPException, ProviderUnavailable, UnfilterableValue):
        raise
    except Exception as e:
        # Removal is never reported as success (the immediate-removal contract), but the reason it
        # failed names the provider file, so it goes to the log. The Student
        # is told plainly that the material is still there -- the material is
        # left Failed by the service, so they can see it too.
        logger.error(f"Course Material {textbook_id} was not removed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not remove the Course Material",
        )

    return {"message": "Course Material removed"}
