"""
The PocketBase HTTP boundary.

A caller's token is only ever presented by the unprivileged verifier, and an
unreachable provider is distinguishable from a rejected credential. Confusing
The two signs every Student out over a blip.
"""
import httpx
import pytest

from api.database.pocketbase_client import (
    PocketBaseClient,
    ProviderRejected,
    ProviderUnavailable,
)


def build_client(handler):
    return PocketBaseClient(
        base_url="http://pocketbase:8090",
        superuser_email="operator@example.test",
        superuser_password="operator-password",
        transport=httpx.MockTransport(handler),
    )


async def test_a_valid_token_returns_the_student_record():
    def handler(request):
        assert request.headers["authorization"] == "student-token"
        return httpx.Response(
            200,
            json={"token": "refreshed", "record": {"id": "aaaaaaaaaaaaaaa", "email": "s@example.test"}},
        )

    record = await build_client(handler).verify_student_token("student-token")

    assert record["id"] == "aaaaaaaaaaaaaaa"


@pytest.mark.parametrize("status_code", [400, 401, 403, 404], ids=["bad-request", "unauthorized", "forbidden", "not-found"])
async def test_a_definitive_rejection_is_reported_as_rejected(status_code):
    def handler(request):
        return httpx.Response(status_code, json={"message": "Failed to authenticate."})

    with pytest.raises(ProviderRejected):
        await build_client(handler).verify_student_token("expired-token")


@pytest.mark.parametrize("status_code", [500, 502, 503, 504], ids=["error", "bad-gateway", "unavailable", "timeout-gateway"])
async def test_a_provider_error_is_reported_as_unavailable(status_code):
    def handler(request):
        return httpx.Response(status_code, text="upstream failure")

    with pytest.raises(ProviderUnavailable):
        await build_client(handler).verify_student_token("valid-token")


@pytest.mark.parametrize(
    "failure",
    [httpx.ConnectTimeout("timed out"), httpx.ConnectError("refused"), httpx.ReadTimeout("read timed out")],
    ids=["connect-timeout", "refused", "read-timeout"],
)
async def test_a_transport_failure_is_reported_as_unavailable(failure):
    def handler(request):
        raise failure

    with pytest.raises(ProviderUnavailable):
        await build_client(handler).verify_student_token("valid-token")


async def test_the_verifier_never_carries_the_superuser_credential():
    """One shared, mutated client would leak a Student's token across requests."""
    seen = []

    def handler(request):
        seen.append((request.url.path, request.headers.get("authorization")))
        if request.url.path.endswith("/_superusers/auth-with-password"):
            return httpx.Response(200, json={"token": "superuser-token", "record": {"id": "op"}})
        return httpx.Response(200, json={"token": "t", "record": {"id": "aaaaaaaaaaaaaaa"}})

    client = build_client(handler)
    await client.request("GET", "/api/collections/course_materials/records")
    await client.verify_student_token("student-token")

    data_auth = [auth for path, auth in seen if path.endswith("/course_materials/records")]
    verify_auth = [auth for path, auth in seen if path.endswith("/auth-refresh")]
    assert data_auth == ["superuser-token"]
    assert verify_auth == ["student-token"]


async def test_an_expired_superuser_token_is_renewed_once():
    calls = {"auth": 0, "data": 0}

    def handler(request):
        if request.url.path.endswith("/_superusers/auth-with-password"):
            calls["auth"] += 1
            return httpx.Response(200, json={"token": f"superuser-{calls['auth']}", "record": {"id": "op"}})
        calls["data"] += 1
        if calls["data"] == 1:
            return httpx.Response(401, json={"message": "The request requires valid record authorization token."})
        return httpx.Response(200, json={"items": []})

    response = await build_client(handler).request("GET", "/api/collections/course_materials/records")

    assert response.status_code == 200
    assert calls["auth"] == 2
