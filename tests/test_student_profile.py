"""
A Student's own profile and language preference.

The profile is read from the Student's own record and nowhere else, so there
is no path by which naming another identity returns their data.
"""
API = "/api/v1"


async def test_a_student_reads_their_own_profile(client, alice):
    response = await client.get(f"{API}/users/me", headers=alice["headers"])

    assert response.status_code == 200
    body = response.json()
    assert (body["id"], body["email"]) == (alice["id"], "alice@example.com")


async def test_the_language_preference_defaults_to_english(client, alice):
    response = await client.get(f"{API}/users/me/language", headers=alice["headers"])

    assert response.json()["language"] == "en-US"


async def test_a_language_preference_survives_a_reread(client, alice):
    await client.patch(f"{API}/users/me/language", headers=alice["headers"], json={"language": "es-ES"})

    response = await client.get(f"{API}/users/me/language", headers=alice["headers"])

    assert response.json()["language"] == "es-ES"


async def test_an_unsupported_language_is_refused(client, alice):
    response = await client.patch(
        f"{API}/users/me/language", headers=alice["headers"], json={"language": "xx-XX"}
    )

    assert response.status_code == 400


async def test_one_students_language_does_not_change_anothers(client, alice, bob):
    await client.patch(f"{API}/users/me/language", headers=alice["headers"], json={"language": "vi-VN"})

    response = await client.get(f"{API}/users/me/language", headers=bob["headers"])

    assert response.json()["language"] == "en-US"


async def test_the_agent_reads_the_same_preference_the_api_wrote(client, alice):
    """agent.py loads the preference in-process; both paths must agree."""
    from api.services.user_service import get_user_language_preference

    await client.patch(f"{API}/users/me/language", headers=alice["headers"], json={"language": "ko-KR"})

    assert await get_user_language_preference(alice["id"]) == "ko-KR"


async def test_a_profile_update_changes_only_the_named_fields(client, alice):
    await client.patch(f"{API}/users/me", headers=alice["headers"], json={"name": "Alice A."})

    response = await client.get(f"{API}/users/me", headers=alice["headers"])
    body = response.json()
    assert (body["name"], body["email"]) == ("Alice A.", "alice@example.com")
