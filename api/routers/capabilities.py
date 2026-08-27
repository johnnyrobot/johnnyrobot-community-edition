"""
What this deployment can actually do.

Community Edition is operator-configured (the self-hosted configuration boundary), and several capabilities
are optional by design: Student Memory degrades to a no-op without a Mem0 key
, Canvas answers 503 with no `CANVAS_BASE_URL`, and a deployment with no
LiveKit project cannot start a voice Tutor Session at all. Degrading is
correct. Advertising the degraded feature anyway is not, and that is what the
interface did -- the dashboard promised "Remembers You" to Students whose
deployment remembered nothing, and the Documents page offered a file type the
server rejects.

Every user-facing claim about a capability reads from here, so a claim cannot
outlive the thing it describes.
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.config import get_settings
from api.dependencies import get_current_user_id
from api.routers.textbooks import ALLOWED_SUFFIXES, MAX_UPLOAD_BYTES
from api.services.student_memory import NoOpMemoryClient, get_memory_client

router = APIRouter(prefix="/capabilities", tags=["capabilities"])
settings = get_settings()

# Shipped in `.env.example`, and found in a working `.env`. A placeholder is
# non-empty, so it satisfies every emptiness check while connecting to nothing.
PLACEHOLDER_LIVEKIT_URL = "wss://your-project.livekit.cloud"


class Capabilities(BaseModel):
    """The optional parts of a deployment, as they actually are right now."""

    student_memory: bool
    canvas: bool
    voice: bool
    upload_formats: list[str]
    max_upload_bytes: int


def _voice_is_configured() -> bool:
    """Whether a Tutor Session could be started at all.

    Emptiness is not enough: the example URL is a real string that resolves to
    a project nobody owns, which is how an agent came to retry sixteen times
    and exit while the interface showed a connected session.
    """
    url = (settings.livekit_url or "").strip()
    if not url or url == PLACEHOLDER_LIVEKIT_URL:
        return False
    return bool(settings.livekit_api_key and settings.livekit_api_secret)


@router.get("", response_model=Capabilities)
async def read_capabilities(user_id: str = Depends(get_current_user_id)) -> Capabilities:
    """Report the deployment's optional capabilities.

    Student Memory is answered by asking the client what it is rather than by
    checking for a key: absent, wrong, expired, and unreachable keys all end at
    The same no-op, and only the built client knows which happened.
    """
    memory = await get_memory_client()

    return Capabilities(
        student_memory=not isinstance(memory, NoOpMemoryClient),
        canvas=bool((settings.canvas_base_url or "").strip()),
        voice=_voice_is_configured(),
        upload_formats=sorted(ALLOWED_SUFFIXES),
        max_upload_bytes=MAX_UPLOAD_BYTES,
    )
