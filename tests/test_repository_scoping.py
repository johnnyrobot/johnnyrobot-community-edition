"""
Mandatory Student filtering.

Every read and write is scoped to the owning Student by the repository, not by
its callers. A route cannot express an unfiltered read, because there is no
method that omits the owner.
"""
import inspect

import pytest

from api.database.pocketbase_client import PocketBaseClient
from api.database.pocketbase_store import PocketBaseStore
from api.database.repository import Repository
from tests.fake_pocketbase import FakePocketBase

ALICE = "alicealiceali"
BOB = "bobbobbobbobbo"


@pytest.fixture
def provider():
    return FakePocketBase()


@pytest.fixture
def repository(provider):
    return Repository(
        PocketBaseStore(
            PocketBaseClient(
                base_url="http://pocketbase:8090",
                superuser_email="operator@example.test",
                superuser_password="operator-password",
                transport=provider.transport,
            )
        )
    )


def test_every_repository_method_takes_the_student_first():
    """No method can be called without naming an owner."""
    exempt = {"__init__"}
    for name, method in inspect.getmembers(Repository, predicate=inspect.isfunction):
        if name.startswith("_") or name in exempt:
            continue
        parameters = list(inspect.signature(method).parameters)
        assert parameters[:2] == ["self", "student_id"], f"{name} does not lead with student_id"


def test_repository_exposes_no_public_property():
    """A @property is invisible to inspect.isfunction, so it would silently
    escape the student_id-first check above; it must not exist at all."""
    for name in dir(Repository):
        if name.startswith("_"):
            continue
        assert not isinstance(inspect.getattr_static(Repository, name), property), (
            f"{name} is a public property and would bypass the student_id-first check"
        )


async def test_a_material_is_listed_only_for_its_owner(repository):
    await repository.create_material(ALICE, {"title": "Alice notes"})
    await repository.create_material(BOB, {"title": "Bob notes"})

    assert [m["title"] for m in await repository.list_materials(ALICE)] == ["Alice notes"]


async def test_another_students_material_cannot_be_read_by_identity(repository):
    created = await repository.create_material(BOB, {"title": "Bob notes"})

    assert await repository.get_material(ALICE, created["id"]) is None


async def test_another_students_material_cannot_be_updated(repository):
    created = await repository.create_material(BOB, {"title": "Bob notes"})

    assert await repository.update_material(ALICE, created["id"], {"title": "hijacked"}) is False
    assert (await repository.get_material(BOB, created["id"]))["title"] == "Bob notes"


async def test_another_students_material_cannot_be_deleted(repository):
    created = await repository.create_material(BOB, {"title": "Bob notes"})

    assert await repository.delete_material(ALICE, created["id"]) is False
    assert await repository.get_material(BOB, created["id"]) is not None


async def test_the_owner_is_written_onto_every_created_record(repository, provider):
    await repository.create_material(ALICE, {"title": "Alice notes"})

    assert provider.records("course_materials")[0]["student"] == ALICE


async def test_a_caller_cannot_override_the_owner_in_the_payload(repository, provider):
    """A forged 'student' key in request data must not decide ownership."""
    await repository.create_material(ALICE, {"title": "Alice notes", "student": BOB})

    assert provider.records("course_materials")[0]["student"] == ALICE


async def test_source_lookup_is_scoped_to_the_owner(repository):
    await repository.create_material(BOB, {"title": "Bob", "source_identity": "canvas:host:page:7"})

    assert await repository.find_material_by_source(ALICE, "canvas:host:page:7") is None


async def test_canvas_records_are_scoped_to_the_owner(repository):
    await repository.upsert_canvas_record(ALICE, "page", "7", {"title": "Alice page"})
    await repository.upsert_canvas_record(BOB, "page", "7", {"title": "Bob page"})

    assert [r["title"] for r in await repository.list_canvas_records(ALICE)] == ["Alice page"]


async def test_a_canvas_record_re_upsert_updates_rather_than_duplicates(repository):
    await repository.upsert_canvas_record(ALICE, "page", "7", {"title": "First"})
    await repository.upsert_canvas_record(ALICE, "page", "7", {"title": "Second"})

    records = await repository.list_canvas_records(ALICE)
    assert len(records) == 1 and records[0]["title"] == "Second"


async def test_a_canvas_token_is_scoped_to_the_owner(repository):
    await repository.upsert_canvas_token(BOB, {"canvas_url": "https://canvas.test"})

    assert await repository.get_canvas_token(ALICE) is None


async def test_a_forged_owner_cannot_survive_a_canvas_token_update(repository):
    await repository.upsert_canvas_token(ALICE, {"canvas_url": "https://canvas.test"})

    returned = await repository.upsert_canvas_token(
        ALICE, {"canvas_url": "https://canvas.test", "student": BOB}
    )

    assert returned["student"] == ALICE


async def test_another_students_tutor_session_cannot_be_updated(repository):
    await repository.create_tutor_session(BOB, "room-1", {"start_time": "2026-08-17T00:00:00"})

    assert await repository.update_tutor_session(ALICE, "room-1", {"end_time": "now"}) is False


async def test_a_canvas_source_disconnect_leaves_another_students_token_alone(repository):
    await repository.upsert_canvas_token(ALICE, {"api_token_ciphertext": "alice", "key_version": 1})
    await repository.upsert_canvas_token(BOB, {"api_token_ciphertext": "bob", "key_version": 1})

    await repository.mark_canvas_source_disconnected(ALICE)

    bob_token = await repository.get_canvas_token(BOB)
    assert bob_token["api_token_ciphertext"] == "bob"
    assert bob_token.get("disconnected") is not True


async def test_a_canvas_record_purge_removes_only_the_callers_records(repository):
    await repository.upsert_canvas_record(ALICE, "page", "7", {"title": "Alice page"})
    await repository.upsert_canvas_record(BOB, "page", "7", {"title": "Bob page"})

    removed = await repository.delete_canvas_records(ALICE)

    assert removed == 1
    assert [r["title"] for r in await repository.list_canvas_records(BOB)] == ["Bob page"]


async def test_the_owner_is_written_onto_every_tutor_session(repository, provider):
    """A forged 'student' key in the payload must not decide ownership."""
    await repository.create_tutor_session(ALICE, "room-1", {"student": BOB})

    assert provider.records("sessions")[0]["student"] == ALICE


async def test_another_students_tutor_session_cannot_be_read_by_room_name(repository):
    await repository.create_tutor_session(BOB, "room-1", {"start_time": "2026-08-17T00:00:00"})

    assert await repository.get_tutor_session(ALICE, "room-1") is None


async def test_a_tutor_session_is_listed_only_for_its_owner(repository):
    await repository.create_tutor_session(ALICE, "room-1", {"start_time": "2026-08-17T00:00:00"})
    await repository.create_tutor_session(BOB, "room-2", {"start_time": "2026-08-17T00:00:00"})

    listed = await repository.list_tutor_sessions(ALICE)

    assert [s["room_name"] for s in listed] == ["room-1"]


async def test_ending_an_open_session_never_closes_another_students(repository):
    """The caller never names a room, so the owner filter is all that scopes it."""
    await repository.create_tutor_session(
        BOB, "room-1", {"start_time": "2026-08-17T00:00:00", "end_time": ""}
    )

    assert await repository.end_open_tutor_session(ALICE, {"end_time": "now"}) is False
    assert (await repository.get_tutor_session(BOB, "room-1"))["end_time"] == ""


async def test_the_owner_is_written_onto_every_graph_manifest(repository, provider):
    """A manifest holds no content, but whose build it records is still theirs."""
    await repository.create_graph_manifest(ALICE, {"material": "m1", "generation": 1})

    assert provider.records("graph_build_manifests")[0]["student"] == ALICE


async def test_graph_build_history_is_listed_only_for_its_owner(repository):
    """Build history says which of a Student's materials were built, and when."""
    await repository.create_graph_manifest(ALICE, {"material": "m1", "generation": 1})
    await repository.create_graph_manifest(BOB, {"material": "m2", "generation": 1})

    assert [m["material"] for m in await repository.list_graph_manifests(ALICE)] == ["m1"]


# -- the roster -------------------------------------------------------------
#
# test_every_repository_method_takes_the_student_first proves a method
# *accepts* an owner. It cannot prove the method *uses* one: a body that takes
# student_id and never passes it to the store satisfies the signature walk
# exactly as well as a correct one does. The roster below closes that gap the
# way tests/test_cross_student_isolation.py closes it for routes -- every
# public method either names a test that demonstrates the isolation
# behaviourally, or records why it has no owner filter to omit.

COVERED_METHODS = {
    "create_material": "test_the_owner_is_written_onto_every_created_record",
    "get_material": "test_another_students_material_cannot_be_read_by_identity",
    "list_materials": "test_a_material_is_listed_only_for_its_owner",
    "find_material_by_source": "test_source_lookup_is_scoped_to_the_owner",
    "update_material": "test_another_students_material_cannot_be_updated",
    "delete_material": "test_another_students_material_cannot_be_deleted",
    "get_canvas_token": "test_a_canvas_token_is_scoped_to_the_owner",
    "upsert_canvas_token": "test_a_forged_owner_cannot_survive_a_canvas_token_update",
    "upsert_canvas_record": "test_canvas_records_are_scoped_to_the_owner",
    "list_canvas_records": "test_canvas_records_are_scoped_to_the_owner",
    "mark_canvas_source_disconnected": (
        "test_a_canvas_source_disconnect_leaves_another_students_token_alone"
    ),
    "delete_canvas_records": "test_a_canvas_record_purge_removes_only_the_callers_records",
    "create_tutor_session": "test_the_owner_is_written_onto_every_tutor_session",
    "get_tutor_session": "test_another_students_tutor_session_cannot_be_read_by_room_name",
    "list_tutor_sessions": "test_a_tutor_session_is_listed_only_for_its_owner",
    "update_tutor_session": "test_another_students_tutor_session_cannot_be_updated",
    "end_open_tutor_session": "test_ending_an_open_session_never_closes_another_students",
    "create_graph_manifest": "test_the_owner_is_written_onto_every_graph_manifest",
    "list_graph_manifests": "test_graph_build_history_is_listed_only_for_its_owner",
}

EXEMPT_METHODS = {
    "get_student": (
        "The owner is the record key. `users` is keyed by the Student identity "
        "itself (the PocketBase identity contract), so there is no ownership filter that could be "
        "omitted -- get_student(x) can only ever return x. What remains is "
        "whether the caller passes the authenticated identity, which is a "
        "route concern and is covered by test_cross_student_isolation.py::"
        "test_a_profile_read_returns_the_caller_not_another_student."
    ),
    "update_student": (
        "The owner is the record key, as for get_student above. The route "
        "concern is covered by test_cross_student_isolation.py::"
        "test_a_profile_update_never_changes_another_student."
    ),
}


def _public_repository_methods():
    return {
        name
        for name, _ in inspect.getmembers(Repository, predicate=inspect.isfunction)
        if not name.startswith("_")
    }


def test_every_repository_method_proves_isolation_behaviourally():
    """A new method must earn a test above, or a recorded exemption."""
    uncovered = _public_repository_methods() - set(COVERED_METHODS) - set(EXEMPT_METHODS)

    assert uncovered == set(), (
        "These Repository methods have no test demonstrating that they scope "
        "to the owning Student. The signature walk only proves they accept "
        "student_id. Write one above and map it in COVERED_METHODS, or record "
        "in EXEMPT_METHODS why the method has no owner filter to omit."
    )


def test_every_covered_method_names_a_real_test():
    """What makes the roster a coverage claim rather than a list of names."""
    defined = {
        name
        for name, value in globals().items()
        if name.startswith("test_") and inspect.isfunction(value)
    }

    assert {
        method: name for method, name in COVERED_METHODS.items() if name not in defined
    } == {}, "These methods name a test that is not defined in this module."


def test_the_roster_names_only_methods_that_exist():
    """A renamed or deleted method must not leave a stale claim behind."""
    live = _public_repository_methods()

    assert (set(COVERED_METHODS) | set(EXEMPT_METHODS)) - live == set(), (
        "The roster names methods Repository no longer has. Drop the stale "
        "entries; a roster that outlives its methods overstates coverage."
    )


def test_every_method_exemption_records_why():
    """An exemption is a decision, so it has to say what the decision was."""
    assert set(COVERED_METHODS) & set(EXEMPT_METHODS) == set(), (
        "A method cannot be both tested and exempt. If it has a test, the "
        "exemption is stale; drop it."
    )
    assert [name for name, reason in EXEMPT_METHODS.items() if not reason.strip()] == [], (
        "An exemption with no reason is an untested method in disguise."
    )
