"""
Caller verification.

The distinction this file protects: a definitively bad credential is 401, and
an unreachable provider is 503. Reporting an outage as 401 clears valid browser
auth state and signs every Student out over a blip.
"""
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from api import dependencies
from api.database.pocketbase_client import PocketBaseClient
from tests.fake_pocketbase import FakePocketBase


@pytest.fixture
def provider():
    fake = FakePocketBase()
    dependencies.set_provider_client(
        PocketBaseClient(
            base_url="http://pocketbase:8090",
            superuser_email="operator@example.test",
            superuser_password="operator-password",
            transport=fake.transport,
        )
    )
    yield fake
    dependencies.set_provider_client(None)


def bearer(token):
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


async def test_a_valid_token_identifies_the_student(provider):
    student_id = provider.add_student("alice@example.test", "pw")

    user = await dependencies.get_current_user(bearer(provider.token_for(student_id)))

    assert (user.id, user.email) == (student_id, "alice@example.test")


async def test_an_expired_token_is_rejected_as_unauthorized(provider):
    student_id = provider.add_student("alice@example.test", "pw")
    token = provider.token_for(student_id)
    provider.expire(token)

    with pytest.raises(HTTPException) as raised:
        await dependencies.get_current_user(bearer(token))

    assert raised.value.status_code == 401


async def test_a_malformed_token_is_rejected_as_unauthorized(provider):
    with pytest.raises(HTTPException) as raised:
        await dependencies.get_current_user(bearer("not-a-token"))

    assert raised.value.status_code == 401


async def test_a_superuser_token_does_not_authenticate_a_student(provider):
    with pytest.raises(HTTPException) as raised:
        await dependencies.get_current_user(bearer(provider.superuser_token_for_test()))

    assert raised.value.status_code == 401


@pytest.mark.parametrize("mode", ["timeout", "refused", "server-error"], ids=["timeout", "refused", "server-error"])
async def test_an_outage_is_reported_as_unavailable_not_unauthorized(provider, mode):
    student_id = provider.add_student("alice@example.test", "pw")
    token = provider.token_for(student_id)
    provider.fail_with(mode)

    with pytest.raises(HTTPException) as raised:
        await dependencies.get_current_user(bearer(token))

    assert raised.value.status_code == 503


async def test_an_unconfigured_provider_is_unavailable_not_unauthorized():
    dependencies.set_provider_client(None)

    with pytest.raises(HTTPException) as raised:
        await dependencies.get_current_user(bearer("any-token"))

    assert raised.value.status_code == 503


async def test_no_verification_result_is_cached(provider):
    """Any cache is a revocation-delay window; the private persistence boundary declines one."""
    student_id = provider.add_student("alice@example.test", "pw")
    token = provider.token_for(student_id)
    await dependencies.get_current_user(bearer(token))

    provider.expire(token)

    with pytest.raises(HTTPException) as raised:
        await dependencies.get_current_user(bearer(token))

    assert raised.value.status_code == 401
