"""
Canvas LMS API routes.
Handles Canvas token management and data synchronization.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import Dict, Any, Optional, List
from datetime import datetime
from api.dependencies import get_current_user_id
from api.database.pocketbase_client import ProviderUnavailable
from api.database.store import UnfilterableValue
from api.database.repository import get_repository
from api.security.crypto import EncryptionNotConfigured, encrypt_canvas_token
from api.services.canvas_service import (
    CanvasNotConfigured,
    CanvasService,
    default_canvas_url,
    get_canvas_service,
)
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/canvas", tags=["canvas"])

# Failures this router must not flatten into its own 500.
#
# Every handler below ends in a catch-all that reports "something went wrong"
# as a 500. That is right for an ordinary bug and wrong for these three, each
# of which already has a correct answer decided elsewhere: an `HTTPException`
# The handler raised deliberately, a genuine PocketBase outage (503, from the
# handler registered in `api/main.py` -- never 401, which would sign the
# Student out over a blip), and a caller-supplied identifier that cannot be
# rendered into an owner filter (400, same place). Re-raising rather than
# rebuilding the response here keeps one message and one log line per failure.
_DECIDED_ELSEWHERE = (HTTPException, ProviderUnavailable, UnfilterableValue)


class CanvasTokenRequest(BaseModel):
    """Canvas token configuration request."""
    api_token: str
    canvas_url: Optional[str] = None  # Optional; omitted, the configured CANVAS_BASE_URL is used


class CanvasTokenResponse(BaseModel):
    """Canvas token response."""
    id: str
    canvas_url: str
    last_sync: Optional[datetime] = None
    created_at: datetime
    disconnected: bool = False


class CanvasSyncResponse(BaseModel):
    """Canvas sync response."""
    success: bool
    message: str
    counts: Dict[str, int]


class CanvasDataResponse(BaseModel):
    """Canvas data item response."""
    id: str
    data_type: str
    canvas_id: str
    course_id: Optional[str]
    course_name: Optional[str]
    title: str
    content: str
    due_date: Optional[datetime]
    metadata: Dict[str, Any]
    created_at: datetime
    updated_at: datetime


class CanvasDataListResponse(BaseModel):
    """List of Canvas data."""
    items: List[CanvasDataResponse]
    total: int
    by_type: Dict[str, int]


@router.post("/token", response_model=CanvasTokenResponse)
async def save_canvas_token(
    request: CanvasTokenRequest,
    user_id: str = Depends(get_current_user_id)
):
    """Connect or reconnect Canvas as a Material Source.

    The token is validated against Canvas before it is stored, so a typo is
    caught while the Student is still looking at the field. It is stored as
    ciphertext under a key custodied outside PocketBase (the reset-only demo profile).
    """
    try:
        canvas_service = CanvasService(
            api_token=request.api_token,
            user_id=user_id,
            canvas_url=request.canvas_url,
        )
    except CanvasNotConfigured as e:
        # The Operator has not said which Canvas this deployment syncs from,
        # so there is nothing to validate the token against. 503 rather than
        # 400: the Student's request is fine and there is nothing they can do
        # about it, which is how an absent CANVAS_TOKEN_KEY already answers.
        logger.error(f"Cannot connect a Canvas source: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Canvas connection is temporarily unavailable",
        )

    if not await canvas_service.validate_token():
        # a bad Canvas credential is invalid input, not a failed
        # authentication of the caller. 401 is reserved for the Student's own
        # identity credential -- the authentication interceptor treats any
        # 401 as "identity token is dead" and signs the Student out of the
        # whole application, which a mistyped Canvas token must never do.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Canvas API token or URL",
        )

    try:
        ciphertext, key_version = encrypt_canvas_token(request.api_token)
    except EncryptionNotConfigured as e:
        logger.error(f"Cannot store a Canvas token: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Canvas connection is temporarily unavailable",
        )

    try:
        record = await get_repository().upsert_canvas_token(
            user_id,
            {
                # The instance the token was just validated against, not a
                # second derivation of it. Recomputing the default here meant
                # two code paths had to agree about resolution and trailing
                # slashes; storing what the service resolved cannot diverge.
                "canvas_url": canvas_service.canvas_url,
                "api_token_ciphertext": ciphertext,
                "key_version": key_version,
                "disconnected": False,
            },
        )
    except _DECIDED_ELSEWHERE:
        raise
    except Exception as e:
        logger.error(f"Error connecting the Canvas source: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to connect Canvas",
        )

    return _token_response(record)


def _token_response(record: dict) -> CanvasTokenResponse:
    """Render a source record without ever naming the credential."""
    last_sync = None
    if record.get("last_sync"):
        last_sync = datetime.fromisoformat(record["last_sync"])
    created_at = datetime.now()
    if record.get("created"):
        try:
            created_at = datetime.fromisoformat(
                record["created"].replace("Z", "+00:00").replace(" ", "T", 1)
            )
        except ValueError:
            pass
    return CanvasTokenResponse(
        id=record["id"],
        # The record's own instance, never the deployment's current default:
        # this reports which Canvas a Student is connected to, and a record
        # missing that is malformed rather than an invitation to guess. Every
        # record `save_canvas_token` writes carries one.
        canvas_url=record.get("canvas_url", ""),
        last_sync=last_sync,
        created_at=created_at,
        disconnected=bool(record.get("disconnected")),
    )


@router.get("/token", response_model=CanvasTokenResponse)
async def get_canvas_token(user_id: str = Depends(get_current_user_id)):
    """Describe the Canvas source, never revealing the credential."""
    record = await get_repository().get_canvas_token(user_id)

    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Canvas is not connected",
        )

    return _token_response(record)


@router.delete("/token")
async def delete_canvas_token(user_id: str = Depends(get_current_user_id)):
    """Disconnect Canvas.

    The source record is marked disconnected rather than destroyed, which is
    what gives Disconnected Source somewhere to exist and Source Suppression
    somewhere to live later (the source-identity contract). The cached Canvas records go, because
    they are a refreshable cache of platform data rather than Student Library
    content. Imported Course Materials are untouched (the disconnected-source preservation rule).

    The two writes are ordered so the partial-failure window has a defined
    outcome. Marking the source disconnected is the endpoint's actual
    contract -- the Student asked to stop using Canvas, and once the
    credential is gone and the source is marked, that has happened. If it
    fails, nothing was honoured and the caller gets the failure (503 for an
    outage, via the handler in `api/main.py`). Clearing the cache afterwards
    is best-effort: it is exactly the disposable data described above, the
    next connect resyncs it, and failing the whole disconnect over it would
    tell a Student their request was refused when it was not. A failure there
    is logged so a stale cache can be found rather than silently accumulating.
    """
    repository = get_repository()
    await repository.mark_canvas_source_disconnected(user_id)

    try:
        removed = await repository.delete_canvas_records(user_id)
        logger.info(
            f"Disconnected Canvas for student {user_id}; dropped {removed} cached records"
        )
    except Exception as e:
        logger.error(
            f"Disconnected Canvas for student {user_id}, but its cached records "
            f"were not cleared: {e}"
        )

    return {"message": "Canvas disconnected"}


@router.post("/sync", response_model=CanvasSyncResponse)
async def sync_canvas_data(user_id: str = Depends(get_current_user_id)):
    """
    Sync Canvas data (assignments, discussions, pages, etc.) from Canvas LMS.
    
    This will fetch all course data and make it available for RAG queries.
    """
    try:
        canvas_service = await get_canvas_service(user_id)
        
        if not canvas_service:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Canvas token not configured. Please add your Canvas API token first."
            )
        
        # Sync all data
        counts = await canvas_service.sync_all_data()
        
        # Trigger embedding generation for new Canvas data
        # Skipping RAG service call for now as we are rebuilding RAG stack
        # from api.services.rag_service import get_rag_service
        # rag_service = get_rag_service()
        # await rag_service.process_canvas_data(user_id)
        
        return CanvasSyncResponse(
            success=True,
            message="Canvas data synced successfully",
            counts=counts
        )
        
    except _DECIDED_ELSEWHERE:
        raise
    except Exception as e:
        logger.error(f"Error syncing Canvas data: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to sync Canvas data"
        )


def _canvas_data_response(record: dict) -> CanvasDataResponse:
    """Map a cached Canvas record onto the response model.

    PocketBase supplies `created`/`updated` (no `_at`), and `due_date` is
    a text field that is '' when absent (the schema). Passing a raw record
    straight into the response model fails validation on every non-empty
    response; this is the mapping that avoids that.
    """
    due_date = record.get("due_date") or None
    if due_date:
        try:
            due_date = datetime.fromisoformat(due_date.replace("Z", "+00:00").replace(" ", "T", 1))
        except ValueError:
            due_date = None

    return CanvasDataResponse(
        id=record["id"],
        data_type=record.get("data_type", ""),
        canvas_id=record.get("canvas_id", ""),
        course_id=record.get("course_id"),
        course_name=record.get("course_name"),
        title=record.get("title", ""),
        content=record.get("content", ""),
        due_date=due_date,
        metadata=record.get("metadata") or {},
        created_at=_record_timestamp(record, "created"),
        updated_at=_record_timestamp(record, "updated"),
    )


def _record_timestamp(record: dict, field: str) -> datetime:
    raw = record.get(field)
    if isinstance(raw, str) and raw:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00").replace(" ", "T", 1))
        except ValueError:
            pass
    return datetime.now()


@router.get("/data", response_model=CanvasDataListResponse)
async def get_canvas_data(
    data_type: Optional[str] = None,
    course_id: Optional[str] = None,
    user_id: str = Depends(get_current_user_id)
):
    """
    Get synced Canvas data.

    Optionally filter by data type (calendar, assignment, discussion, announcement, page)
    or course ID.
    """
    try:
        items = await get_repository().list_canvas_records(
            user_id, data_type=data_type, course_id=course_id
        )

        by_type = {}
        for item in items:
            dtype = item.get('data_type', '')
            by_type[dtype] = by_type.get(dtype, 0) + 1

        return CanvasDataListResponse(
            items=[_canvas_data_response(item) for item in items],
            total=len(items),
            by_type=by_type
        )

    except _DECIDED_ELSEWHERE:
        raise
    except Exception as e:
        logger.error(f"Error getting Canvas data: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get Canvas data"
        )


@router.get("/courses")
async def get_canvas_courses(user_id: str = Depends(get_current_user_id)):
    """Get list of Canvas courses."""
    try:
        # Distinct courses are derived from this Student's cached Canvas records.
        items = await get_repository().list_canvas_records(user_id)

        courses = {}
        for item in items:
            if item.get('course_id') and item.get('course_name'):
                courses[item['course_id']] = item['course_name']

        return {
            "courses": [
                {"course_id": cid, "course_name": name}
                for cid, name in courses.items()
            ]
        }

    except _DECIDED_ELSEWHERE:
        raise
    except Exception as e:
        logger.error(f"Error getting Canvas courses: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get Canvas courses"
        )


@router.get("/stats")
async def get_canvas_stats(user_id: str = Depends(get_current_user_id)):
    """Get Canvas data statistics."""
    try:
        repository = get_repository()
        token_record = await repository.get_canvas_token(user_id)

        if not token_record or token_record.get("disconnected"):
            # Name the instance a new connection *would* use, so the connect
            # form can say where it is about to send a credential without
            # carrying a hostname of its own. None when the deployment has
            # not configured Canvas -- which is reported, not raised: stats
            # are fetched on page load whether or not Canvas is in use.
            try:
                offered = default_canvas_url()
            except CanvasNotConfigured:
                offered = None
            return {
                "configured": False,
                "canvas_url": offered,
                "last_sync": None,
                "total_items": 0,
                "by_type": {},
            }

        items = await repository.list_canvas_records(user_id)

        count = 0
        by_type = {}
        for item in items:
            count += 1
            dtype = item['data_type']
            by_type[dtype] = by_type.get(dtype, 0) + 1

        return {
            "configured": True,
            "canvas_url": token_record.get("canvas_url"),
            "last_sync": token_record.get("last_sync"),
            "total_items": count,
            "by_type": by_type
        }

    except _DECIDED_ELSEWHERE:
        raise
    except Exception as e:
        logger.error(f"Error getting Canvas stats: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get Canvas statistics",
        )
