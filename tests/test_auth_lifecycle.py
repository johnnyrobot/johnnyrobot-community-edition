"""
Signing in and out.

Accounts are provisioned by a Deployment Operator (the reset-only demo profile): there is no
self-registration route, and adding one would be a conformance change, not a
convenience.
"""
API = "/api/v1"


async def test_a_provisioned_student_can_sign_in(client, alice):
    response = await client.post(
        f"{API}/auth/login", json={"email": alice["email"], "password": alice["password"]}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["user"]["id"] == alice["id"]
    assert body["session"]["access_token"]
    assert body["session"]["token_type"] == "bearer"


async def test_a_wrong_password_is_rejected(client, alice):
    response = await client.post(
        f"{API}/auth/login", json={"email": alice["email"], "password": "wrong-password"}
    )

    assert response.status_code == 401


async def test_an_unknown_email_is_rejected(client, provider):
    response = await client.post(
        f"{API}/auth/login", json={"email": "nobody@example.com", "password": "any"}
    )

    assert response.status_code == 401


async def test_a_malformed_email_is_rejected_at_the_model(client, provider):
    """EmailStr (the email-validation contract) rejects obviously malformed input with a 422,
    before the request ever reaches PocketBase. That is a different thing
    from an authentication failure -- it never reaches the single failed-
    login message at all."""
    response = await client.post(
        f"{API}/auth/login", json={"email": "not-an-email", "password": "any"}
    )

    assert response.status_code == 422


async def test_a_login_failure_does_not_say_which_half_was_wrong(client, alice):
    """Distinguishing them enumerates provisioned Students.

    Both addresses here are well-formed (the email-validation contract moved the fixtures off
    The reserved .test TLD to keep EmailStr's format check meaningful), so
    this exercises the authentication-failure path, not the 422
    model-validation path covered by test_a_malformed_email_is_rejected_at_the_model.
    """
    unknown = await client.post(
        f"{API}/auth/login", json={"email": "nobody@example.com", "password": "any"}
    )
    wrong = await client.post(
        f"{API}/auth/login", json={"email": alice["email"], "password": "wrong-password"}
    )

    assert unknown.status_code == 401
    assert wrong.status_code == 401
    assert unknown.json()["detail"] == wrong.json()["detail"]


async def test_a_signed_in_student_can_read_their_own_identity(client, alice):
    response = await client.get(f"{API}/auth/me", headers=alice["headers"])

    assert response.status_code == 200
    assert response.json()["id"] == alice["id"]


async def test_reading_identity_without_a_token_is_rejected(client, provider):
    # FastAPI's HTTPBearer (installed: 0.141.1) returns 401 for a missing
    # Authorization header -- verified against
    # HTTPBearer.make_not_authenticated_error, which raises
    # HTTPException(status_code=HTTP_401_UNAUTHORIZED). Older FastAPI
    # versions returned 403 here; this app never chose 403 deliberately.
    assert (await client.get(f"{API}/auth/me")).status_code == 401


async def test_logout_succeeds_even_with_an_expired_token(client, provider, alice):
    """Signing out of a lab machine must never be blocked by a stale token."""
    provider.expire(provider.token_for(alice["id"]))

    assert (await client.post(f"{API}/auth/logout", headers=alice["headers"])).status_code == 200


async def test_there_is_no_self_registration_route(client, provider):
    response = await client.post(
        f"{API}/auth/signup",
        json={"email": "new@example.com", "password": "password123", "name": "New"},
    )

    assert response.status_code == 404
