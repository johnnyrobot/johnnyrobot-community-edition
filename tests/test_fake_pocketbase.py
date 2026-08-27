"""
Contracts the fake PocketBase must honour.

The fake stands in for the provider in every route test, so a wrong assumption
here passes green everywhere. tests/test_pocketbase_contract.py runs the same
expectations against a real PocketBase when one is reachable.
"""
import httpx
import pytest

from api.database.pocketbase_client import (
    PocketBaseClient,
    ProviderRejected,
    ProviderUnavailable,
)
from tests.fake_pocketbase import FakePocketBase


@pytest.fixture
def provider():
    return FakePocketBase()


@pytest.fixture
def client(provider):
    return PocketBaseClient(
        base_url="http://pocketbase:8090",
        superuser_email="operator@example.test",
        superuser_password="operator-password",
        transport=provider.transport,
    )


async def test_a_provisioned_student_can_authenticate(provider, client):
    student_id = provider.add_student("alice@example.test", "correct-password")

    result = await client.authenticate_student("alice@example.test", "correct-password")

    assert result["record"]["id"] == student_id
    assert result["token"]


async def test_a_wrong_password_is_definitively_rejected(provider, client):
    provider.add_student("alice@example.test", "correct-password")

    with pytest.raises(ProviderRejected):
        await client.authenticate_student("alice@example.test", "wrong-password")


async def test_an_expired_token_is_definitively_rejected(provider, client):
    student_id = provider.add_student("alice@example.test", "pw")
    token = provider.token_for(student_id)
    provider.expire(token)

    with pytest.raises(ProviderRejected):
        await client.verify_student_token(token)


async def test_a_superuser_token_does_not_authenticate_a_student(provider, client):
    """A superuser credential is not a Student credential."""
    with pytest.raises(ProviderRejected):
        await client.verify_student_token(provider.superuser_token_for_test())


@pytest.mark.parametrize("mode", ["timeout", "refused", "server-error"], ids=["timeout", "refused", "server-error"])
async def test_an_injected_outage_is_reported_as_unavailable(provider, client, mode):
    student_id = provider.add_student("alice@example.test", "pw")
    token = provider.token_for(student_id)
    provider.fail_with(mode)

    with pytest.raises(ProviderUnavailable):
        await client.verify_student_token(token)


async def test_the_partial_unique_index_rejects_a_duplicate_source_identity(provider, client):
    """Re-importing the same source must collide; that is what forces an update."""
    student_id = provider.add_student("alice@example.test", "pw")
    body = {"student": student_id, "title": "Week 1", "source_identity": "canvas:host:page:7"}

    first = await client.request("POST", "/api/collections/course_materials/records", json=body)
    second = await client.request("POST", "/api/collections/course_materials/records", json=body)

    assert first.status_code == 200
    assert second.status_code == 400


async def test_direct_uploads_never_collide_on_the_index(provider, client):
    """Materials with no Source Identity are outside the index entirely."""
    student_id = provider.add_student("alice@example.test", "pw")
    body = {"student": student_id, "title": "Notes", "source_identity": ""}

    first = await client.request("POST", "/api/collections/course_materials/records", json=body)
    second = await client.request("POST", "/api/collections/course_materials/records", json=body)

    assert (first.status_code, second.status_code) == (200, 200)


async def test_a_filtered_list_returns_only_matching_records(provider, client):
    alice = provider.add_student("alice@example.test", "pw")
    bob = provider.add_student("bob@example.test", "pw")
    for owner in (alice, alice, bob):
        await client.request(
            "POST", "/api/collections/course_materials/records",
            json={"student": owner, "title": "t", "source_identity": ""},
        )

    response = await client.request(
        "GET", "/api/collections/course_materials/records",
        params={"filter": f'student = "{alice}"', "perPage": 200},
    )

    items = response.json()["items"]
    assert len(items) == 2
    assert {item["student"] for item in items} == {alice}


async def test_records_are_assigned_pocketbase_shaped_ids(provider, client):
    student_id = provider.add_student("alice@example.test", "pw")

    response = await client.request(
        "POST", "/api/collections/course_materials/records",
        json={"student": student_id, "title": "t", "source_identity": ""},
    )

    record_id = response.json()["id"]
    assert len(record_id) == 15 and record_id.isalnum()
