"""
The same expectations, against a real PocketBase.

tests/fake_pocketbase.py can be confidently wrong about filter syntax or the
shape of an auth-refresh rejection, and that class of error passes green in
every other test and fails in the demo. This file is the guard. It is opt-in so
The default run needs no Docker.

    docker compose -f docker-compose.prod.yml up -d pocketbase
    pytest -m live_pocketbase tests/test_pocketbase_contract.py
"""
import os

import pytest

from api.database.pocketbase_client import (
    PocketBaseClient,
    ProviderRejected,
)
from api.database.pocketbase_store import PocketBaseStore
from api.database.store import DuplicateRecord

pytestmark = pytest.mark.live_pocketbase

PB_URL = os.environ.get("POCKETBASE_URL", "http://127.0.0.1:8090")
PB_EMAIL = os.environ.get("POCKETBASE_SUPERUSER_EMAIL", "")
PB_PASSWORD = os.environ.get("POCKETBASE_SUPERUSER_PASSWORD", "")


@pytest.fixture
async def client():
    import httpx

    if not PB_PASSWORD:
        pytest.skip("POCKETBASE_SUPERUSER_PASSWORD is unset")
    try:
        async with httpx.AsyncClient(timeout=2.0) as probe:
            await probe.get(f"{PB_URL}/api/health")
    except httpx.HTTPError:
        pytest.skip(f"No PocketBase reachable at {PB_URL}")

    built = PocketBaseClient(
        base_url=PB_URL,
        superuser_email=PB_EMAIL,
        superuser_password=PB_PASSWORD,
    )
    yield built
    await built.aclose()


@pytest.fixture
async def student(client):
    """A throwaway Student, removed afterwards."""
    email = "contract-test@example.invalid"
    created = await client.request(
        "POST",
        "/api/collections/users/records",
        json={"email": email, "password": "contract-test-password",
              "passwordConfirm": "contract-test-password", "name": "Contract",
              "verified": True},
    )
    record = created.json()
    yield {"id": record["id"], "email": email, "password": "contract-test-password"}
    await client.request("DELETE", f"/api/collections/users/records/{record['id']}")


@pytest.fixture
async def other_student(client):
    """A second throwaway Student, for the cross-Student properties."""
    email = "contract-test-two@example.invalid"
    created = await client.request(
        "POST",
        "/api/collections/users/records",
        json={"email": email, "password": "contract-test-password",
              "passwordConfirm": "contract-test-password", "name": "Contract Two",
              "verified": True},
    )
    record = created.json()
    yield {"id": record["id"], "email": email, "password": "contract-test-password"}
    await client.request("DELETE", f"/api/collections/users/records/{record['id']}")


async def test_password_authentication_returns_a_token_and_record(client, student):
    result = await client.authenticate_student(student["email"], student["password"])

    assert result["token"] and result["record"]["id"] == student["id"]


async def test_a_wrong_password_is_a_definitive_rejection(client, student):
    """Confirms the real rejection maps to 401 and not to 503."""
    with pytest.raises(ProviderRejected):
        await client.authenticate_student(student["email"], "wrong-password")


async def test_a_returned_token_verifies(client, student):
    result = await client.authenticate_student(student["email"], student["password"])

    record = await client.verify_student_token(result["token"])

    assert record["id"] == student["id"]


async def test_a_garbage_token_is_a_definitive_rejection(client, student):
    with pytest.raises(ProviderRejected):
        await client.verify_student_token("not-a-real-token")


async def test_business_collections_reject_an_unauthenticated_read(client, student):
    """Every business collection is superuser-only."""
    import httpx

    async with httpx.AsyncClient(base_url=PB_URL, timeout=5.0) as anonymous:
        response = await anonymous.get("/api/collections/course_materials/records")

    assert response.status_code in (400, 403, 404)


async def test_the_users_create_rule_is_locked(client, student):
    import httpx

    async with httpx.AsyncClient(base_url=PB_URL, timeout=5.0) as anonymous:
        response = await anonymous.post(
            "/api/collections/users/records",
            json={"email": "walkup@example.invalid", "password": "password12345",
                  "passwordConfirm": "password12345"},
        )

    assert response.status_code in (400, 403)


async def test_the_filter_syntax_the_store_emits_is_understood(client, student):
    """The clause shape build_filter produces must work against the real thing."""
    store = PocketBaseStore(client)
    await store.create(
        "course_materials",
        {"student": student["id"], "title": "Contract A", "source_identity": "", "status": "ready"},
    )
    await store.create(
        "course_materials",
        {"student": student["id"], "title": "Contract B", "source_identity": "", "status": "processing"},
    )

    matched = await store.query(
        "course_materials", {"student": student["id"], "status": "ready"}
    )

    assert [m["title"] for m in matched] == ["Contract A"]


async def test_the_source_identity_index_is_unique_and_partial(client, student):
    store = PocketBaseStore(client)
    body = {"student": student["id"], "title": "Week 1",
            "source_identity": "canvas:contract.test:page:7", "status": "ready"}

    await store.create("course_materials", body)
    with pytest.raises(DuplicateRecord):
        await store.create("course_materials", body)

    # Two direct uploads, both without a Source Identity, must both survive.
    plain = {"student": student["id"], "title": "Notes", "source_identity": "", "status": "ready"}
    await store.create("course_materials", plain)
    await store.create("course_materials", plain)


async def test_two_students_may_import_the_same_source(client, student, other_student):
    """The index is scoped to (student, source_identity), not to
    source_identity alone. Scoped wrongly, the first Student to import a
    Canvas page would silently block every other Student from importing the
    same page -- cross-Student interference that the same-Student duplicate
    test above cannot see.
    """
    store = PocketBaseStore(client)
    shared = {"title": "Week 1", "source_identity": "canvas:contract.test:page:11",
              "status": "ready"}

    first = await store.create("course_materials", {**shared, "student": student["id"]})
    second = await store.create(
        "course_materials", {**shared, "student": other_student["id"]}
    )

    assert first["id"] != second["id"]
