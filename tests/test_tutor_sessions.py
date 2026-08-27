"""
Tutor Session records.

A Tutor Session acts for exactly one Student. Its history is that Student's,
and no other Student can end it or read it. Ending a session is a Student
action on their own most recent open Tutor Session -- there is no room name
in the request, because a "room" is LiveKit plumbing, not a product concept
(the session-ownership contract).
"""
import pytest

API = "/api/v1"


@pytest.fixture(autouse=True)
def livekit(monkeypatch):
    """Keep LiveKit off the network; this file is about ownership, not voice."""
    from api.routers import sessions

    class StubAccessToken:
        def __init__(self, **kwargs):
            pass

        def with_identity(self, identity):
            return self

        def with_name(self, name):
            return self

        def with_grants(self, grants):
            return self

        def to_jwt(self):
            return "stub-livekit-jwt"

    class StubLiveKitAPI:
        def __init__(self, **kwargs):
            self.room = self
            self.agent_dispatch = self

        async def create_room(self, request):
            return None

        async def create_dispatch(self, request):
            return None

        async def aclose(self):
            return None

    monkeypatch.setattr(sessions.api, "AccessToken", StubAccessToken)
    monkeypatch.setattr(sessions, "LiveKitAPI", StubLiveKitAPI)


async def test_a_session_token_names_the_authenticated_student(client, alice):
    response = await client.post(f"{API}/session/token", headers=alice["headers"])

    assert response.status_code == 200
    assert alice["id"] in response.json()["room_name"]


async def test_a_started_session_appears_in_that_students_history(client, alice):
    await client.post(f"{API}/session/token", headers=alice["headers"])

    response = await client.get(f"{API}/session/history", headers=alice["headers"])

    assert response.json()["count"] == 1


async def test_one_students_session_never_appears_in_anothers_history(client, alice, bob):
    await client.post(f"{API}/session/token", headers=alice["headers"])

    response = await client.get(f"{API}/session/history", headers=bob["headers"])

    assert response.json()["count"] == 0


async def test_the_owning_student_can_end_their_session(client, provider, alice):
    room_name = (await client.post(f"{API}/session/token", headers=alice["headers"])).json()["room_name"]

    response = await client.post(f"{API}/session/end", headers=alice["headers"], json={})

    assert response.status_code == 200
    record = next(r for r in provider.records("sessions") if r["room_name"] == room_name)
    assert record["end_time"]


async def test_ending_a_session_never_touches_another_students_open_session(client, provider, alice, bob):
    """A Student's 'end' call only ever closes their own most recent open
    session -- there is no room name to name someone else's by."""
    alice_room = (await client.post(f"{API}/session/token", headers=alice["headers"])).json()["room_name"]
    await client.post(f"{API}/session/token", headers=bob["headers"])

    response = await client.post(f"{API}/session/end", headers=bob["headers"], json={})

    assert response.status_code == 200
    alice_record = next(r for r in provider.records("sessions") if r["room_name"] == alice_room)
    assert not alice_record.get("end_time")


async def test_ending_with_no_open_session_returns_not_found(client, alice):
    """stop reporting success for a no-op."""
    response = await client.post(f"{API}/session/end", headers=alice["headers"], json={})

    assert response.status_code == 404


async def test_a_double_fired_end_call_404s_rather_than_reporting_success(client, alice):
    await client.post(f"{API}/session/token", headers=alice["headers"])

    first = await client.post(f"{API}/session/end", headers=alice["headers"], json={})
    second = await client.post(f"{API}/session/end", headers=alice["headers"], json={})

    assert first.status_code == 200
    assert second.status_code == 404


async def test_ending_closes_only_the_most_recent_open_session(client, provider, alice):
    """Stale open sessions older than the most recent are left alone."""
    first_room = (await client.post(f"{API}/session/token", headers=alice["headers"])).json()["room_name"]
    second_room = (await client.post(f"{API}/session/token", headers=alice["headers"])).json()["room_name"]

    response = await client.post(f"{API}/session/end", headers=alice["headers"], json={})

    assert response.status_code == 200
    records = {r["room_name"]: r for r in provider.records("sessions")}
    assert records[second_room]["end_time"]
    assert not records[first_room].get("end_time")


async def test_the_existing_frontends_room_name_field_is_ignored_not_rejected(client, alice):
    """The frontend sends
    {"room_name": roomName}. EndSessionRequest no longer declares that field;
    pydantic's default extra="ignore" means the extra key is dropped rather
    than producing a 422, so the existing client keeps working unmodified."""
    await client.post(f"{API}/session/token", headers=alice["headers"])

    response = await client.post(
        f"{API}/session/end", headers=alice["headers"], json={"room_name": "a-room-that-does-not-exist"}
    )

    assert response.status_code == 200


async def test_two_tokens_in_quick_succession_get_different_room_names(client, alice):
    """second-resolution timestamps alone can collide; the unique index
    on sessions.room_name would then silently drop the second Tutor Session
    record. Entropy must be enough that back-to-back names differ."""
    first = (await client.post(f"{API}/session/token", headers=alice["headers"])).json()["room_name"]
    second = (await client.post(f"{API}/session/token", headers=alice["headers"])).json()["room_name"]

    assert first != second
