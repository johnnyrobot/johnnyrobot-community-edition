"""
The deployment says what it can do, and the interface says only that.

Three separate defects had one cause: no user-facing claim was derived from
what this deployment actually supports, so each was written by hand and each
drifted somewhere different.

  - The Documents page advertised DOCX in one panel and PDF/TXT/MD forty lines
    above it. The server rejects DOCX with 415, so a Student following the
    on-screen guidance got an error and no hint that the guidance was wrong.
  - The dashboard promised "Remembers You -- picks up where you left off"
    unconditionally, while a deployment with no Mem0 key logs
    "Student Memory is a no-op" at startup and remembers nothing.
  - The same dashboard promised the tutor "won't write essays" while the text
    path had no policy at all (fixed separately, in #1).

See issues #3 and #6. A claim rendered from this endpoint cannot drift from the
behaviour it describes, because there is only one of it.
"""
import pytest

from api.routers.textbooks import ALLOWED_SUFFIXES, MAX_UPLOAD_BYTES

API = "/api/v1"


async def test_capabilities_requires_a_signed_in_student(client):
    """The shape of a deployment is not public."""
    response = await client.get(f"{API}/capabilities")

    assert response.status_code == 401


async def test_upload_formats_come_from_the_servers_allow_list(client, alice):
    """The list the Student reads is the list the server enforces.

    Asserting equality with `ALLOWED_SUFFIXES` rather than a literal is the
    point: a future change to what the server accepts moves this claim with it,
    which is exactly what did not happen when `.docx` was removed.
    """
    body = (await client.get(f"{API}/capabilities", headers=alice["headers"])).json()

    assert set(body["upload_formats"]) == ALLOWED_SUFFIXES


async def test_docx_is_not_advertised(client, alice):
    """The specific claim that sent Students into a 415."""
    body = (await client.get(f"{API}/capabilities", headers=alice["headers"])).json()

    assert ".docx" not in body["upload_formats"]


async def test_the_upload_limit_is_reported(client, alice):
    """A size shown on screen should be the size actually enforced."""
    body = (await client.get(f"{API}/capabilities", headers=alice["headers"])).json()

    assert body["max_upload_bytes"] == MAX_UPLOAD_BYTES


async def test_student_memory_is_reported_off_when_it_is_a_no_op(client, alice, monkeypatch):
    """The claim tracks the running client, not the presence of a key.

    A key that is absent, wrong, expired, or unreachable all end at the same
    no-op, so asking the client what it is answers for every one of them.
    """
    from api.services import student_memory
    from api.routers import capabilities

    monkeypatch.setattr(
        capabilities, "get_memory_client", _returning(student_memory.NoOpMemoryClient())
    )

    body = (await client.get(f"{API}/capabilities", headers=alice["headers"])).json()

    assert body["student_memory"] is False


async def test_student_memory_is_reported_on_when_a_real_client_is_built(client, alice, monkeypatch):
    from api.routers import capabilities

    class _RealEnough:
        pass

    monkeypatch.setattr(capabilities, "get_memory_client", _returning(_RealEnough()))

    body = (await client.get(f"{API}/capabilities", headers=alice["headers"])).json()

    assert body["student_memory"] is True


async def test_canvas_is_reported_off_when_no_instance_is_configured(client, alice, monkeypatch):
    """An unset CANVAS_BASE_URL already answers 503; the interface should agree."""
    from api.routers import capabilities

    monkeypatch.setattr(capabilities.settings, "canvas_base_url", "")

    body = (await client.get(f"{API}/capabilities", headers=alice["headers"])).json()

    assert body["canvas"] is False


async def test_voice_is_reported_off_when_livekit_is_unconfigured(client, alice, monkeypatch):
    """A deployment that cannot start a Tutor Session should not offer one.

    Encountered live: with `LIVEKIT_URL` still on the `.env.example`
    placeholder, the agent never registered and the browser showed a green
    connected session anyway (#4). Reporting the capability honestly is the
    half of that the server can answer for.
    """
    from api.routers import capabilities

    monkeypatch.setattr(capabilities.settings, "livekit_api_key", "")

    body = (await client.get(f"{API}/capabilities", headers=alice["headers"])).json()

    assert body["voice"] is False


async def test_a_placeholder_livekit_url_is_not_a_configured_deployment(client, alice, monkeypatch):
    """The example value is the one that actually shipped in a working .env.

    `wss://your-project.livekit.cloud` is what `.env.example` carries, and a
    non-empty placeholder passes every emptiness check while connecting to
    nothing.
    """
    from api.routers import capabilities

    monkeypatch.setattr(capabilities.settings, "livekit_url", "wss://your-project.livekit.cloud")

    body = (await client.get(f"{API}/capabilities", headers=alice["headers"])).json()

    assert body["voice"] is False


def _returning(value):
    async def _get():
        return value

    return _get
