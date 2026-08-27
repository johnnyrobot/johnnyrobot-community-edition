"""
Login throttling.

PocketBase sees every login arriving from one container address, so its own
per-address limiter would throttle all Students together or none. The limit
belongs here, keyed on the address Caddy forwards.
"""
import pytest

from api import rate_limit

API = "/api/v1"


@pytest.fixture(autouse=True)
def fresh_counters(monkeypatch):
    from api.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("LOGIN_RATE_LIMIT_ATTEMPTS", "3")
    monkeypatch.setenv("LOGIN_RATE_LIMIT_WINDOW_SECONDS", "300")
    rate_limit.reset_for_tests()
    yield
    rate_limit.reset_for_tests()
    get_settings.cache_clear()


async def attempt(client, email, password, address="203.0.113.7"):
    return await client.post(
        f"{API}/auth/login",
        json={"email": email, "password": password},
        headers={"X-Forwarded-For": address},
    )


async def test_repeated_failures_from_one_address_are_throttled(client, alice):
    for _ in range(3):
        await attempt(client, alice["email"], "wrong-password")

    response = await attempt(client, alice["email"], "wrong-password")

    assert response.status_code == 429


async def test_a_throttled_response_still_carries_cors_headers(client, alice):
    """`api/main.py` registers this middleware *before* CORSMiddleware so the
    429 it raises still passes back out through CORS. Swap the two and a
    throttled cross-origin login becomes an opaque network error in the
    browser instead of a readable "too many attempts".
    """
    from api.main import settings

    origin = next(iter(settings.get_cors_origins_list()), None)
    if origin is None:
        pytest.skip("no cross-origin access is configured, so there is no header to carry")

    for _ in range(3):
        await attempt(client, alice["email"], "wrong-password")
    response = await client.post(
        f"{API}/auth/login",
        json={"email": alice["email"], "password": "wrong-password"},
        headers={"X-Forwarded-For": "203.0.113.7", "Origin": origin},
    )

    assert response.status_code == 429
    assert response.headers["access-control-allow-origin"] == origin
    assert "Origin" in response.headers["vary"]


async def test_a_correct_password_still_works_below_the_limit(client, alice):
    await attempt(client, alice["email"], "wrong-password")

    response = await attempt(client, alice["email"], alice["password"])

    assert response.status_code == 200


async def test_throttling_one_address_does_not_throttle_another(client, alice):
    """Keying on the container address would lock out every Student at once."""
    for _ in range(3):
        await attempt(client, alice["email"], "wrong-password", address="203.0.113.7")

    response = await attempt(client, alice["email"], alice["password"], address="198.51.100.4")

    assert response.status_code == 200


async def test_the_last_address_in_the_forwarded_chain_is_used_not_the_first(client, alice):
    """Caddy appends its own observed address as the LAST entry in
    X-Forwarded-For; anything ahead of it is caller-supplied and must not be
    trusted. This corrects a previous version of this test, which
    asserted the opposite -- that the FIRST entry was used -- which was
    exactly the bug the rate-limit storage bound fixes: Caddy's default `reverse_proxy` behavior
    appends its own observed address rather than stripping one the caller
    already sent, so the first entry can be anything an attacker likes.
    """
    for _ in range(3):
        await client.post(
            f"{API}/auth/login",
            json={"email": alice["email"], "password": "wrong-password"},
            headers={"X-Forwarded-For": "203.0.113.7, 10.0.0.2"},
        )

    # Same trusted (last) hop, a different leading entry -- still counts
    # toward the same throttle bucket.
    response = await client.post(
        f"{API}/auth/login",
        json={"email": alice["email"], "password": "wrong-password"},
        headers={"X-Forwarded-For": "198.51.100.55, 10.0.0.2"},
    )

    assert response.status_code == 429


async def test_spoofing_the_leading_forwarded_for_entry_does_not_bypass_the_throttle(client, alice):
    """An attacker controls only the request they send, not Caddy's own
    appended hop, so rotating a fake leading entry must not dodge the
    throttle."""
    spoofed_values = ["attacker-1", "attacker-2", "attacker-3", "attacker-4"]

    for spoofed in spoofed_values[:3]:
        await client.post(
            f"{API}/auth/login",
            json={"email": alice["email"], "password": "wrong-password"},
            headers={"X-Forwarded-For": f"{spoofed}, 203.0.113.7"},
        )

    response = await client.post(
        f"{API}/auth/login",
        json={"email": alice["email"], "password": "wrong-password"},
        headers={"X-Forwarded-For": f"{spoofed_values[3]}, 203.0.113.7"},
    )

    assert response.status_code == 429


async def test_a_successful_login_clears_the_count(client, alice):
    await attempt(client, alice["email"], "wrong-password")
    await attempt(client, alice["email"], "wrong-password")
    await attempt(client, alice["email"], alice["password"])

    for _ in range(3):
        response = await attempt(client, alice["email"], "wrong-password")

    assert response.status_code == 401


async def test_other_routes_are_not_throttled(client, alice):
    for _ in range(6):
        response = await client.get(f"{API}/auth/me", headers=alice["headers"])

    assert response.status_code == 200


async def test_a_never_failing_address_leaves_no_trace(client, alice):
    """A `defaultdict(list)` would insert a permanent empty entry on read
    alone, letting an attacker who fully controls the address string grow
    The tracking dict without ever failing a single login. A plain
    dict must not retain an address that currently has zero recorded
    failures."""
    await attempt(client, alice["email"], alice["password"], address="203.0.113.99")

    assert "203.0.113.99" not in rate_limit._failures


async def test_failures_dict_does_not_grow_without_bound(client, alice, monkeypatch):
    """Defense in depth: independent of the header-trust fix, the
    number of tracked addresses is capped so a flood of distinct trusted
    addresses cannot grow this dict forever."""
    monkeypatch.setattr(rate_limit, "_MAX_TRACKED_ADDRESSES", 5)

    for i in range(20):
        await attempt(client, alice["email"], "wrong-password", address=f"203.0.113.{i}")

    assert len(rate_limit._failures) <= 5
