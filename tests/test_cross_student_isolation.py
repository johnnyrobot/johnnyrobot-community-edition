"""
One negative test per route.

Every test here authenticates as one Student and names another Student's
record. Nothing may leak, and nothing may change. The two tables at the bottom
are the point: every route the application dispatches is either mapped to the
test above that covers it, or listed as structurally unable to name another
Student's record, with the reason recorded. A new route that is in neither
fails test_every_protected_route_is_covered, and a mapped route whose test does
not exist fails test_every_covered_route_names_a_real_test -- which is how an
endpoint that forgot its owner filter gets caught.
"""
import inspect

import pytest

from api.database.repository import get_repository
from api.routers import canvas as canvas_router
from api.services.gemini_service import GeminiService
from tests.test_material_lifecycle import provider_files  # noqa: F401
from tests.test_tutor_sessions import livekit  # noqa: F401
from tests.test_canvas_source import canvas_api, encryption_key  # noqa: F401

API = "/api/v1"


@pytest.fixture
async def bobs_material(provider, provider_files, bob, tmp_path):
    path = tmp_path / "notes.md"
    path.write_text("bob's course material")
    material_id = await GeminiService().upload_textbook(str(path), "Bob notes", bob["id"])
    return material_id


@pytest.fixture
async def bobs_canvas(client, bob):
    await client.post(
        f"{API}/canvas/token", headers=bob["headers"], json={"api_token": "valid-canvas-token"}
    )
    await get_repository().upsert_canvas_record(
        bob["id"], "page", "7", {"title": "Bob page", "content": "x", "course_id": "1", "course_name": "Bio"}
    )


@pytest.fixture
async def bobs_session(client, bob):
    response = await client.post(f"{API}/session/token", headers=bob["headers"])
    return response.json()["room_name"]


@pytest.fixture
def student_memories(monkeypatch):
    """A Mem0 stand-in that keeps each Student's memories apart.

    conftest's autouse stub answers every `get_all` with an empty list, so a
    memory isolation test written against it would pass whether or not the
    route scoped the read -- alice sees nothing either way. This stub returns
    what was remembered for the `user_id` it is asked about, which gives those
    tests a mutation to fail against: drop the owner scoping and alice sees
    bob's memory.

    `delete` deliberately honours no ownership at all, because Mem0 does not
    either: it deletes by memory id alone. The 404 in
    test_deleting_another_students_memory_is_not_found therefore has to come
    from the route's own check, and removing that check destroys bob's memory.
    """
    remembered: dict[str, list[dict]] = {}

    class _PerStudentMemoryClient:
        def __init__(self, *args, **kwargs):
            pass

        async def get_all(self, user_id=None, **kwargs):
            return list(remembered.get(user_id, []))

        async def delete(self, memory_id=None, **kwargs):
            for memories in remembered.values():
                memories[:] = [m for m in memories if m.get("id") != memory_id]
            return {"message": "deleted"}

    from api.services import student_memory

    monkeypatch.setattr(student_memory, "AsyncMemoryClient", _PerStudentMemoryClient)
    # Student Memory is optional; with no key the seam short-circuits to
    # The no-op and never reaches the stub above.
    monkeypatch.setattr(student_memory.settings, "mem0_api_key", "a-configured-key")
    return remembered


@pytest.fixture
def bobs_memory(student_memories, bob):
    student_memories[bob["id"]] = [{"id": "bob-memory-1", "memory": "Bob is studying photosynthesis"}]
    return "bob-memory-1"


# -- Course Materials ------------------------------------------------------


async def test_listing_never_returns_another_students_material(client, alice, bobs_material):
    response = await client.get(f"{API}/textbooks/", headers=alice["headers"])

    assert response.json()["textbooks"] == []


async def test_deleting_another_students_material_is_not_found(client, provider, alice, bobs_material):
    response = await client.delete(f"{API}/textbooks/{bobs_material}", headers=alice["headers"])

    assert response.status_code == 404
    assert len(provider.records("course_materials")) == 1


async def test_a_chat_never_selects_another_students_material(client, alice, bobs_material, monkeypatch):
    """A Material Selection can narrow within the caller's Library, never beyond it.

    Since per-Library store isolation the Library is also a store boundary, so this asserts both
    halves: naming Bob's material moves neither the owner clause nor the store
    off Alice.
    """
    from api.routers import chat

    alice_store = f"fileSearchStores/{alice['id']}-lib"
    captured = {}

    class StubGeminiService:
        async def resolve_library_store(self, user_id):
            return alice_store if user_id == alice["id"] else "fileSearchStores/someone-else"

        def get_search_tool_config(self, user_id, store_name, textbook_id=None):
            captured["user_id"] = user_id
            captured["store_name"] = store_name
            captured["textbook_id"] = textbook_id
            raise RuntimeError("stop before Gemini")

    monkeypatch.setattr(chat, "GeminiService", StubGeminiService)

    await client.post(
        f"{API}/chat/message",
        headers=alice["headers"],
        json={"message": "hi", "history": [], "textbook_id": bobs_material},
    )

    assert captured["user_id"] == alice["id"]
    assert captured["store_name"] == alice_store


# -- Canvas ----------------------------------------------------------------


async def test_reading_another_students_canvas_source_is_not_found(client, alice, bobs_canvas):
    response = await client.get(f"{API}/canvas/token", headers=alice["headers"])

    assert response.status_code == 404


async def test_disconnecting_does_not_touch_another_students_source(client, provider, alice, bobs_canvas):
    await client.delete(f"{API}/canvas/token", headers=alice["headers"])

    assert provider.records("canvas_tokens")[0]["disconnected"] is False


async def test_syncing_without_a_source_does_not_use_anothers(client, alice, bobs_canvas):
    response = await client.post(f"{API}/canvas/sync", headers=alice["headers"])

    assert response.status_code == 404


async def test_canvas_data_never_returns_another_students_records(client, alice, bobs_canvas):
    response = await client.get(f"{API}/canvas/data", headers=alice["headers"])

    assert response.json()["total"] == 0


async def test_canvas_courses_never_returns_another_students_courses(client, alice, bobs_canvas):
    response = await client.get(f"{API}/canvas/courses", headers=alice["headers"])

    assert response.json()["courses"] == []


async def test_canvas_stats_never_counts_another_students_records(client, alice, bobs_canvas):
    response = await client.get(f"{API}/canvas/stats", headers=alice["headers"])

    body = response.json()
    assert (body["configured"], body["total_items"]) == (False, 0)


# -- Tutor Sessions --------------------------------------------------------


async def test_session_history_never_returns_another_students_sessions(client, alice, bobs_session):
    response = await client.get(f"{API}/session/history", headers=alice["headers"])

    assert response.json()["count"] == 0


async def test_ending_another_students_session_does_nothing(client, provider, alice, bobs_session):
    await client.post(f"{API}/session/end", headers=alice["headers"], json={"room_name": bobs_session})

    record = next(r for r in provider.records("sessions") if r["room_name"] == bobs_session)
    assert not record.get("end_time")


async def test_a_session_token_always_names_the_caller(client, alice, bob):
    response = await client.post(f"{API}/session/token", headers=alice["headers"])

    assert bob["id"] not in response.json()["room_name"]


# -- Profile ---------------------------------------------------------------


async def test_a_profile_read_returns_the_caller_not_another_student(client, alice, bob):
    response = await client.get(f"{API}/users/me", headers=alice["headers"])

    assert response.json()["id"] == alice["id"]


async def test_a_profile_update_never_changes_another_student(client, provider, alice, bob):
    await client.patch(f"{API}/users/me", headers=alice["headers"], json={"name": "Renamed"})

    bob_record = next(r for r in provider.records("users") if r["id"] == bob["id"])
    assert bob_record["name"] != "Renamed"


async def test_a_language_read_returns_the_callers_preference_not_anothers(client, alice, bob):
    """Bob's read is here so alice's default cannot come from a write that never landed."""
    await client.patch(f"{API}/users/me/language", headers=bob["headers"], json={"language": "vi-VN"})

    as_alice = await client.get(f"{API}/users/me/language", headers=alice["headers"])
    as_bob = await client.get(f"{API}/users/me/language", headers=bob["headers"])

    assert (as_alice.json()["language"], as_bob.json()["language"]) == ("en-US", "vi-VN")


async def test_a_language_update_never_changes_another_student(client, provider, alice, bob):
    await client.patch(f"{API}/users/me/language", headers=alice["headers"], json={"language": "vi-VN"})

    stored = {r["id"]: r.get("preferred_language") for r in provider.records("users")}
    # Alice's half keeps a rejected PATCH from passing as isolation.
    assert (stored[alice["id"]], stored[bob["id"]]) == ("vi-VN", None)


# -- Student Memory --------------------------------------------------------


async def test_listing_never_returns_another_students_memories(client, alice, bob, bobs_memory):
    """Bob's read proves the memory is there to leak; alice's proves it does not."""
    as_alice = await client.get(f"{API}/memory/", headers=alice["headers"])
    as_bob = await client.get(f"{API}/memory/", headers=bob["headers"])

    assert (as_alice.json()["total"], as_bob.json()["total"]) == (0, 1)


async def test_deleting_another_students_memory_is_not_found(client, alice, bob, bobs_memory):
    """Bob's delete lands second, so its success is what proves alice's did nothing.

    A 404 alone would also be what a Student Memory that is switched off
    answers, and this route deletes by memory id -- an unowned id it accepts
    is one it destroys.
    """
    as_alice = await client.delete(f"{API}/memory/{bobs_memory}", headers=alice["headers"])
    as_bob = await client.delete(f"{API}/memory/{bobs_memory}", headers=bob["headers"])

    assert (as_alice.status_code, as_bob.status_code) == (404, 200)


async def test_clearing_memories_never_clears_another_students(client, alice, bob, bobs_memory):
    """Same shape: bob still has one to clear only because alice's clear spared it."""
    as_alice = await client.delete(f"{API}/memory/?confirm=true", headers=alice["headers"])
    as_bob = await client.delete(f"{API}/memory/?confirm=true", headers=bob["headers"])

    assert (as_alice.json()["deleted_count"], as_bob.json()["deleted_count"]) == (0, 1)


# -- Identity --------------------------------------------------------------


async def test_an_identity_read_returns_the_caller_not_another_student(client, alice, bob):
    """Both reads, so a route answering with one constant identity fails."""
    as_alice = await client.get(f"{API}/auth/me", headers=alice["headers"])
    as_bob = await client.get(f"{API}/auth/me", headers=bob["headers"])

    assert (as_alice.json()["id"], as_bob.json()["id"]) == (alice["id"], bob["id"])


# -- The roster ------------------------------------------------------------

# Every route that reads or writes a Student-owned record, mapped to the test
# above that covers it. Adding a route to the application means adding it here
# and writing that test: a membership list alone would let a route be declared
# covered with nothing behind it, which is exactly what this mapping closes.
#
# What the mapping checks is that the named test exists. That it tests *this*
# route is the reader's job -- keep the name next to the route honest.
COVERED_ROUTES = {
    ("GET", "/api/v1/textbooks/"): "test_listing_never_returns_another_students_material",
    ("DELETE", "/api/v1/textbooks/{textbook_id}"): "test_deleting_another_students_material_is_not_found",
    ("POST", "/api/v1/chat/message"): "test_a_chat_never_selects_another_students_material",
    ("GET", "/api/v1/canvas/token"): "test_reading_another_students_canvas_source_is_not_found",
    ("DELETE", "/api/v1/canvas/token"): "test_disconnecting_does_not_touch_another_students_source",
    ("POST", "/api/v1/canvas/sync"): "test_syncing_without_a_source_does_not_use_anothers",
    ("GET", "/api/v1/canvas/data"): "test_canvas_data_never_returns_another_students_records",
    ("GET", "/api/v1/canvas/courses"): "test_canvas_courses_never_returns_another_students_courses",
    ("GET", "/api/v1/canvas/stats"): "test_canvas_stats_never_counts_another_students_records",
    ("POST", "/api/v1/session/token"): "test_a_session_token_always_names_the_caller",
    ("POST", "/api/v1/session/end"): "test_ending_another_students_session_does_nothing",
    ("GET", "/api/v1/session/history"): "test_session_history_never_returns_another_students_sessions",
    ("GET", "/api/v1/users/me"): "test_a_profile_read_returns_the_caller_not_another_student",
    ("PATCH", "/api/v1/users/me"): "test_a_profile_update_never_changes_another_student",
    ("GET", "/api/v1/users/me/language"): "test_a_language_read_returns_the_callers_preference_not_anothers",
    ("PATCH", "/api/v1/users/me/language"): "test_a_language_update_never_changes_another_student",
    ("GET", "/api/v1/memory/"): "test_listing_never_returns_another_students_memories",
    ("DELETE", "/api/v1/memory/{memory_id}"): "test_deleting_another_students_memory_is_not_found",
    ("DELETE", "/api/v1/memory/"): "test_clearing_memories_never_clears_another_students",
    ("GET", "/api/v1/auth/me"): "test_an_identity_read_returns_the_caller_not_another_student",
}

# Routes with no surface for a cross-Student test to aim at: the request
# carries no identifier of an existing record, so there is no other Student's
# record to name. Each one still has an ownership property, and the reason says
# where that property is proven, so an entry here is a recorded decision rather
# than a route quietly left untested.
EXEMPT_ROUTES = {
    ("POST", "/api/v1/textbooks/upload"): (
        "Creates a Material from a file and a title; the request names no "
        "existing record, and the owner comes from the token. That a forged "
        "owner in the payload cannot decide ownership is "
        "test_repository_scoping.py::test_a_caller_cannot_override_the_owner_in_the_payload."
    ),
    ("POST", "/api/v1/canvas/token"): (
        "Connects the caller's own Canvas source; the request carries a "
        "credential, never a record identity. That the upsert lands on the "
        "caller's row even against a forged owner is "
        "test_repository_scoping.py::test_a_forged_owner_cannot_survive_a_canvas_token_update."
    ),
    ("POST", "/api/v1/auth/login"): (
        "Unauthenticated by design -- there is no caller yet to isolate from. "
        "Naming another Student's email here is the credential check itself, "
        "which is test_auth_lifecycle.py::test_a_wrong_password_is_rejected."
    ),
    ("POST", "/api/v1/auth/logout"): (
        "Takes no body, requires no credential, and reads or writes no record "
        "at all: api/routers/auth.py::logout returns a constant. Requiring no "
        "credential is deliberate and is "
        "test_auth_lifecycle.py::test_logout_succeeds_even_with_an_expired_token."
    ),
    ("GET", "/api/v1/capabilities"): (
        "Describes the deployment, never a Student: it takes no body and no "
        "identifier, and every field is read from settings or module "
        "constants, so two Students necessarily receive the same answer. It "
        "requires a caller only because the shape of a deployment is not "
        "public, which is "
        "test_deployment_capabilities.py::test_capabilities_requires_a_signed_in_student."
    ),
}


def _iter_live_routes():
    """Yield (full_path, methods) for every dispatchable route.

    See tests/test_provider_outage.py's _iter_route_dependants for why this
    walk is necessary: installed FastAPI (0.141.1) wraps an included
    sub-router in `_IncludedRouter` rather than flattening its routes into
    `app.routes` directly. A naive one-level walk finds zero routes and
    passes this test vacuously.
    """
    from fastapi.routing import APIRoute
    from api.main import app

    for route in app.routes:
        if isinstance(route, APIRoute):
            yield route.path, route.methods
            continue
        if type(route).__name__ == "_IncludedRouter":
            prefix = route.include_context.prefix or ""
            for sub in route.original_router.routes:
                if isinstance(sub, APIRoute):
                    yield prefix + sub.path, sub.methods
                elif type(sub).__name__ == "_IncludedRouter":
                    # One level only. A nested include would be skipped, and
                    # The roster below would look complete while covering
                    # fewer routes than the application dispatches.
                    raise AssertionError(
                        f"A router included under {prefix!r} itself includes another "
                        "router. This walk only descends one level, so those routes "
                        "would never be checked for cross-Student isolation. Make "
                        "the walk recursive."
                    )


def test_every_protected_route_is_covered():
    """A new route must earn a negative test above, or a recorded exemption."""
    live = {
        (method, path)
        for path, methods in _iter_live_routes()
        for method in methods
        if method not in {"HEAD", "OPTIONS"} and path.startswith("/api/v1")
    }

    assert live - set(COVERED_ROUTES) - set(EXEMPT_ROUTES) == set(), (
        "These routes have no cross-Student negative test. Add one above and "
        "map it in COVERED_ROUTES, or record why the route cannot name another "
        "Student's record in EXEMPT_ROUTES."
    )


def test_every_covered_route_names_a_real_test():
    """What makes the roster a coverage claim rather than a list of names.

    A route mapped to a test that does not exist -- a typo, a renamed test, a
    deleted one, or an entry added with no test written -- fails here instead
    of passing green.
    """
    defined = {
        name
        for name, value in globals().items()
        if name.startswith("test_") and inspect.isfunction(value)
    }

    assert {
        route: name for route, name in COVERED_ROUTES.items() if name not in defined
    } == {}, "These routes name a test that is not defined in this module."


def test_every_exemption_records_why():
    """An exemption is a decision, so it has to say what the decision was."""
    assert set(COVERED_ROUTES) & set(EXEMPT_ROUTES) == set(), (
        "A route cannot be both tested and exempt. If it has a test, the "
        "exemption is stale; drop it."
    )
    assert [route for route, reason in EXEMPT_ROUTES.items() if not reason.strip()] == [], (
        "An exemption with no reason is an untested route in disguise."
    )
