"""
Record storage against PocketBase.

The store carries no ownership rules of its own — that is the repository's
job — but it must report a unique-index rejection distinctly, because that is
The signal that turns a Canvas re-import into a Material Update.
"""
import logging
import pytest
import httpx

from api.database.pocketbase_client import PocketBaseClient
from api.database.store import DuplicateRecord
from api.database.pocketbase_store import PocketBaseStore
from tests.fake_pocketbase import FakePocketBase


@pytest.fixture
def provider():
    return FakePocketBase()


@pytest.fixture
def store(provider):
    return PocketBaseStore(
        PocketBaseClient(
            base_url="http://pocketbase:8090",
            superuser_email="operator@example.test",
            superuser_password="operator-password",
            transport=provider.transport,
        )
    )


async def test_create_returns_the_assigned_identity(store):
    record = await store.create("course_materials", {"student": "s1", "title": "Notes"})

    assert record["id"]
    assert record["title"] == "Notes"


async def test_a_created_record_can_be_read_back(store):
    created = await store.create("course_materials", {"student": "s1", "title": "Notes"})

    assert (await store.get("course_materials", created["id"]))["title"] == "Notes"


async def test_reading_an_absent_record_returns_none(store):
    assert await store.get("course_materials", "aaaaaaaaaaaaaaa") is None


async def test_update_changes_only_the_given_keys(store):
    created = await store.create("course_materials", {"student": "s1", "title": "Notes", "status": "processing"})

    await store.update("course_materials", created["id"], {"status": "ready"})

    record = await store.get("course_materials", created["id"])
    assert (record["status"], record["title"]) == ("ready", "Notes")


async def test_deleting_an_absent_record_is_not_an_error(store):
    await store.delete("course_materials", "aaaaaaaaaaaaaaa")


async def test_query_returns_only_records_matching_every_field(store):
    await store.create("canvas_data", {"student": "s1", "data_type": "page", "title": "a"})
    await store.create("canvas_data", {"student": "s1", "data_type": "assignment", "title": "b"})
    await store.create("canvas_data", {"student": "s2", "data_type": "page", "title": "c"})

    items = await store.query("canvas_data", {"student": "s1", "data_type": "page"})

    assert [item["title"] for item in items] == ["a"]


async def test_query_with_no_filter_returns_everything(store):
    await store.create("users", {"id": "s1"})

    assert len(await store.query("users", limit=1)) == 1


async def test_delete_where_removes_every_match_and_counts_them(store):
    for data_type in ("page", "assignment", "page"):
        await store.create("canvas_data", {"student": "s1", "data_type": data_type})
    await store.create("canvas_data", {"student": "s2", "data_type": "page"})

    removed = await store.delete_where("canvas_data", {"student": "s1"})

    assert removed == 3
    assert len(await store.query("canvas_data", {})) == 1


async def test_a_unique_index_rejection_is_reported_as_a_duplicate(store):
    body = {"student": "s1", "title": "Week 1", "source_identity": "canvas:host:page:7"}
    await store.create("course_materials", body)

    with pytest.raises(DuplicateRecord):
        await store.create("course_materials", body)


# Tests for set() must fail loud on merge=False


async def test_set_with_merge_true_leaves_absent_keys_alone(store):
    """set() with merge=True should only update given keys, leaving others untouched."""
    created = await store.create("course_materials", {"student": "s1", "title": "Notes", "status": "processing"})

    await store.set("course_materials", created["id"], {"status": "ready"}, merge=True)

    record = await store.get("course_materials", created["id"])
    assert (record["status"], record["title"]) == ("ready", "Notes")


async def test_set_with_merge_false_raises_not_implemented(store):
    """set() with merge=False should raise NotImplementedError since PocketBase can only merge."""
    created = await store.create("course_materials", {"student": "s1", "title": "Notes"})

    with pytest.raises(NotImplementedError):
        await store.set("course_materials", created["id"], {"title": "Changed"}, merge=False)


async def test_set_creates_on_404_when_merge_true(store):
    """set() with merge=True should create the record if it doesn't exist (create-on-404 fallback)."""
    await store.set("course_materials", "new-id", {"student": "s1", "title": "New"}, merge=True)

    record = await store.get("course_materials", "new-id")
    assert record["title"] == "New"
    assert record["id"] == "new-id"


# Tests for paginate delete_where, warn on truncation in query


async def test_delete_where_removes_multiple_pages(store, monkeypatch):
    """delete_where should paginate and remove all matches even beyond page size."""
    import api.database.pocketbase_store as pb_store

    # Temporarily reduce page size so 10 records span multiple pages
    monkeypatch.setattr(pb_store, "_PAGE_SIZE", 3)

    # Create records spanning multiple pages (10 records with page size 3)
    # This forces delete_where to loop: 3 + 3 + 3 + 1 = 4 iterations
    count = 10
    for i in range(count):
        await store.create("canvas_data", {"student": "s1", "data_type": f"type_{i}"})

    removed = await store.delete_where("canvas_data", {"student": "s1"})

    # Assert every record was removed and the count is correct
    assert removed == count, f"Expected to remove {count} records, but removed {removed}"
    remaining = await store.query("canvas_data", {"student": "s1"})
    assert len(remaining) == 0, f"Expected no remaining records, but found {len(remaining)}"


async def test_query_warns_when_truncated(store, caplog):
    """query should log a warning when totalItems exceeds items returned."""
    # Create some records that would be truncated if limit is small
    for i in range(3):
        await store.create("users", {"id": f"u{i}"})

    # Query with a small limit to trigger truncation warning
    # Set the logger level to capture WARNING-level messages from pocketbase_store
    caplog.set_level(logging.WARNING, logger="api.database.pocketbase_store")
    items = await store.query("users", limit=2)

    # Assert we got the expected number of items
    assert len(items) == 2
    # Assert the warning was actually logged (check for truncation message)
    assert "truncated" in caplog.text.lower(), (
        f"Expected truncation warning in log, but got: {caplog.text}"
    )


# Tests for create() raises DuplicateRecord only for unique-index rejection


async def test_a_non_unique_index_400_does_not_raise_duplicate_record():
    """create() should only raise DuplicateRecord for validation_not_unique, not other 400 errors."""
    # Use a mock transport that returns a 400 with a non-unique-index error
    def mock_handler(request: httpx.Request) -> httpx.Response:
        # Handle superuser auth
        if "_superusers/auth-with-password" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "token": "test-superuser-token",
                    "record": {"id": "operator-id", "email": "operator@example.test"}
                }
            )
        if request.method == "POST" and "records" in request.url.path:
            # Return a 400 that is NOT a unique-index rejection
            return httpx.Response(
                400,
                json={
                    "message": "Failed to create record.",
                    "data": {
                        "title": {"code": "validation_required"}  # Not validation_not_unique
                    }
                }
            )
        return httpx.Response(500)  # Unexpected request

    transport = httpx.MockTransport(mock_handler)
    client = PocketBaseClient(
        base_url="http://pocketbase:8090",
        superuser_email="operator@example.test",
        superuser_password="operator-password",
        transport=transport,
    )
    store = PocketBaseStore(client)

    # This should raise httpx.HTTPStatusError (from raise_for_status), not DuplicateRecord
    with pytest.raises(httpx.HTTPStatusError):
        await store.create("course_materials", {"student": "s1", "title": ""})  # Missing required title


async def test_create_is_defensive_about_malformed_400_body():
    """create() should not crash if 400 body is malformed or lacks expected structure."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        # Handle superuser auth
        if "_superusers/auth-with-password" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "token": "test-superuser-token",
                    "record": {"id": "operator-id", "email": "operator@example.test"}
                }
            )
        if request.method == "POST" and "records" in request.url.path:
            # Return a 400 with a malformed body (not JSON)
            return httpx.Response(400, text="Internal Server Error")
        return httpx.Response(500)

    transport = httpx.MockTransport(mock_handler)
    client = PocketBaseClient(
        base_url="http://pocketbase:8090",
        superuser_email="operator@example.test",
        superuser_password="operator-password",
        transport=transport,
    )
    store = PocketBaseStore(client)

    # Should raise httpx.HTTPStatusError (from raise_for_status), not crash on JSON parse
    with pytest.raises(httpx.HTTPStatusError):
        await store.create("course_materials", {"student": "s1", "title": "Test"})


async def test_create_handles_400_with_json_array_body():
    """create() should handle 400 response with JSON array body defensively."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        # Handle superuser auth
        if "_superusers/auth-with-password" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "token": "test-superuser-token",
                    "record": {"id": "operator-id", "email": "operator@example.test"}
                }
            )
        if request.method == "POST" and "records" in request.url.path:
            # Return a 400 with a JSON array body (not a dict)
            return httpx.Response(400, json=[1, 2, 3])
        return httpx.Response(500)

    transport = httpx.MockTransport(mock_handler)
    client = PocketBaseClient(
        base_url="http://pocketbase:8090",
        superuser_email="operator@example.test",
        superuser_password="operator-password",
        transport=transport,
    )
    store = PocketBaseStore(client)

    # Should raise httpx.HTTPStatusError, not AttributeError
    with pytest.raises(httpx.HTTPStatusError):
        await store.create("course_materials", {"student": "s1", "title": "Test"})


async def test_create_handles_400_with_json_string_body():
    """create() should handle 400 response with bare JSON string body defensively."""
    def mock_handler(request: httpx.Request) -> httpx.Response:
        # Handle superuser auth
        if "_superusers/auth-with-password" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "token": "test-superuser-token",
                    "record": {"id": "operator-id", "email": "operator@example.test"}
                }
            )
        if request.method == "POST" and "records" in request.url.path:
            # Return a 400 with a bare JSON string body
            return httpx.Response(400, json="error message")
        return httpx.Response(500)

    transport = httpx.MockTransport(mock_handler)
    client = PocketBaseClient(
        base_url="http://pocketbase:8090",
        superuser_email="operator@example.test",
        superuser_password="operator-password",
        transport=transport,
    )
    store = PocketBaseStore(client)

    # Should raise httpx.HTTPStatusError, not AttributeError
    with pytest.raises(httpx.HTTPStatusError):
        await store.create("course_materials", {"student": "s1", "title": "Test"})
