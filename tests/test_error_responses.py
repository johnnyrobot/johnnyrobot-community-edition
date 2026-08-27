"""
What a failure tells the caller.

An internal failure gets a static message; the exception goes to the log. The
text of an exception routinely carries paths, identifiers, and provider detail
that the browser has no business seeing.

Two further failures have a status of their own, and both are decided in one
place (`api/main.py`) rather than per route:

  - a hostile identifier, which is refused rather than escaped, is a 400 on
    whatever route it arrived on -- never a traceback;
  - a PocketBase outage that happens *after* authentication, inside a route
    body, is a 503 exactly as it already is during authentication -- never a
    401, which would sign the Student out over a blip, and never a flat 500,
    which reads as an ordinary bug (the private persistence boundary).
"""
import pytest

from api.database.pocketbase_client import ProviderUnavailable
from api.database.repository import Repository
from api.services.gemini_service import GeminiService

API = "/api/v1"

SECRET = "internal-detail-that-must-not-escape"


async def test_a_chat_failure_does_not_quote_the_exception(client, alice, monkeypatch):
    from api.routers import chat

    def explode(*args, **kwargs):
        raise RuntimeError(SECRET)

    monkeypatch.setattr(chat, "GeminiService", explode)

    response = await client.post(
        f"{API}/chat/message", headers=alice["headers"], json={"message": "hello", "history": []}
    )

    assert response.status_code == 500
    assert SECRET not in response.text


async def test_a_session_failure_does_not_quote_the_exception(client, alice, monkeypatch):
    from api.routers import sessions

    class Explode:
        def __init__(self, **kwargs):
            raise RuntimeError(SECRET)

    monkeypatch.setattr(sessions.api, "AccessToken", Explode)

    response = await client.post(f"{API}/session/token", headers=alice["headers"])

    assert response.status_code == 500
    assert SECRET not in response.text


async def test_a_memory_failure_does_not_quote_the_exception(client, alice, monkeypatch):
    from api.services import student_memory

    class ExplodingClient:
        def __init__(self, **kwargs):
            pass

        async def get_all(self, **kwargs):
            raise RuntimeError(SECRET)

    monkeypatch.setattr(student_memory, "AsyncMemoryClient", ExplodingClient)
    # Student Memory is optional: with no key configured the seam takes
    # The NoOpMemoryClient branch, never builds the client above, and the route
    # answers 200 -- so this test would assert 500 against a route that cannot
    # fail. `.env.local.example` ships MEM0_API_KEY blank on purpose, so
    # without this line the result depends on the developer's own `.env`.
    # That degradation is the subject of test_student_memory_optional.py; here
    # it is only a precondition, and the failure below is the subject.
    monkeypatch.setattr(student_memory.settings, "mem0_api_key", "a-configured-key")

    response = await client.get(f"{API}/memory/", headers=alice["headers"])

    assert response.status_code == 500
    assert SECRET not in response.text


async def test_the_exception_still_reaches_the_log(client, alice, monkeypatch, caplog):
    from api.routers import chat

    def explode(*args, **kwargs):
        raise RuntimeError(SECRET)

    monkeypatch.setattr(chat, "GeminiService", explode)

    with caplog.at_level("ERROR"):
        await client.post(
            f"{API}/chat/message", headers=alice["headers"], json={"message": "hello", "history": []}
        )

    assert SECRET in caplog.text


# -- a hostile identifier is refused, not crashed ----------------------
#
# `build_filter` refuses to render a value carrying `"` or `\` into a filter
# literal rather than escaping it. That refusal is correct; what was missing is
# any route turning it into an answer. Four `Repository` methods take
# caller-influenced strings, and `DELETE /textbooks/{id}` passed a raw path
# parameter straight into one of them with nothing catching the result.

HOSTILE_IDENTIFIERS = [
    'aaaaaaa"aaaaaaa',
    "aaaaaaa\\aaaaaaa",
    'x" || student = "someone-else',
]


@pytest.mark.parametrize(
    "identifier", HOSTILE_IDENTIFIERS, ids=["quote", "backslash", "or-injection"]
)
async def test_a_hostile_material_identity_is_refused_with_a_clean_status(
    client, alice, identifier
):
    """A crafted Material Selection cannot break out of the owner filter."""
    response = await client.delete(f"{API}/textbooks/{identifier}", headers=alice["headers"])

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid identifier in request"


@pytest.mark.parametrize(
    "identifier", HOSTILE_IDENTIFIERS, ids=["quote", "backslash", "or-injection"]
)
async def test_a_refused_identifier_is_not_quoted_back_to_the_caller(client, alice, identifier):
    """The refusal names the field internally; the caller gets none of it."""
    response = await client.delete(f"{API}/textbooks/{identifier}", headers=alice["headers"])

    assert identifier not in response.text
    assert "Refusing to filter" not in response.text


async def test_a_hostile_query_filter_is_refused_the_same_way(client, alice):
    """The same guarantee on a route that filters on a query parameter."""
    response = await client.get(
        f"{API}/canvas/data",
        headers=alice["headers"],
        params={"data_type": 'page" || student = "x'},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid identifier in request"


async def test_a_wellformed_identifier_still_reaches_its_own_answer(client, alice):
    """The guard must not swallow an ordinary miss: absent is 404, not 400."""
    response = await client.delete(f"{API}/textbooks/aaaaaaaaaaaaaaa", headers=alice["headers"])

    assert response.status_code == 404


# -- a PocketBase outage inside a route body -------------------------------
#
# `PocketBaseClient.request` raises `ProviderUnavailable` for a 5xx or an
# unreachable connection, and can never raise `ProviderRejected` -- that one
# comes only from the two authentication calls. So `ProviderUnavailable` is the
# whole of what a route body has to answer for.
#
# The fake's transport-level `fail_with` cannot express this: it would fail the
# caller's own token verification too, and the request would never reach a route
# body at all. Failing one repository method is what isolates the post-auth case.

OUTAGE_ROUTES = [
    # No try/except of any kind: these reach the handler in api/main.py.
    ("GET", f"{API}/users/me", None, "get_student"),
    ("PATCH", f"{API}/users/me", {"name": "New Name"}, "update_student"),
    ("GET", f"{API}/canvas/token", None, "get_canvas_token"),
    ("DELETE", f"{API}/canvas/token", None, "mark_canvas_source_disconnected"),
    ("GET", f"{API}/session/history", None, "list_tutor_sessions"),
    ("POST", f"{API}/session/end", {}, "end_open_tutor_session"),
    ("DELETE", f"{API}/textbooks/aaaaaaaaaaaaaaa", None, "get_material"),
    # A catch-all already maps everything here to a flat 500; a genuine outage
    # has to be named ahead of it to keep its own status.
    ("POST", f"{API}/canvas/sync", None, "get_canvas_token"),
    ("GET", f"{API}/canvas/data", None, "list_canvas_records"),
    ("GET", f"{API}/canvas/courses", None, "list_canvas_records"),
    ("GET", f"{API}/canvas/stats", None, "get_canvas_token"),
]


@pytest.fixture
def storage_outage(monkeypatch):
    """Fail one `Repository` method, leaving authentication working."""

    def fail(method_name):
        async def unavailable(self, *args, **kwargs):
            raise ProviderUnavailable(SECRET)

        monkeypatch.setattr(Repository, method_name, unavailable)

    return fail


@pytest.mark.parametrize(
    "method,path,body,repository_method",
    OUTAGE_ROUTES,
    ids=[f"{m}-{p}" for m, p, _, _ in OUTAGE_ROUTES],
)
async def test_an_outage_after_authentication_is_still_unavailable(
    client, alice, storage_outage, method, path, body, repository_method
):
    """503 for an outage inside a route body, exactly as during authentication."""
    storage_outage(repository_method)

    response = await client.request(method, path, headers=alice["headers"], json=body)

    assert response.status_code == 503


@pytest.mark.parametrize(
    "method,path,body,repository_method",
    OUTAGE_ROUTES,
    ids=[f"{m}-{p}" for m, p, _, _ in OUTAGE_ROUTES],
)
async def test_an_outage_after_authentication_does_not_quote_the_exception(
    client, alice, storage_outage, method, path, body, repository_method
):
    storage_outage(repository_method)

    response = await client.request(method, path, headers=alice["headers"], json=body)

    assert SECRET not in response.text
    assert response.json()["detail"] == "This service is temporarily unavailable"


async def test_an_outage_after_authentication_reaches_the_log(
    client, alice, storage_outage, caplog
):
    storage_outage("get_student")

    with caplog.at_level("ERROR"):
        await client.get(f"{API}/users/me", headers=alice["headers"])

    assert SECRET in caplog.text


async def test_an_outage_while_adding_a_material_is_unavailable(client, alice, monkeypatch):
    """`POST /textbooks/upload`'s catch-all must not flatten an outage either."""

    async def unavailable(self, *args, **kwargs):
        raise ProviderUnavailable(SECRET)

    monkeypatch.setattr(GeminiService, "upload_textbook", unavailable)

    response = await client.post(
        f"{API}/textbooks/upload",
        headers=alice["headers"],
        files={"file": ("notes.md", b"course material bytes", "text/markdown")},
        data={"title": "Notes"},
    )

    assert response.status_code == 503
    assert SECRET not in response.text
