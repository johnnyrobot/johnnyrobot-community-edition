"""
Canvas as a Material Source.

Disconnecting marks the source disconnected and keeps its record, because
that is the only place a Disconnected Source can exist and the only place
Source Suppression could later live (the source-identity contract). Imported Course Materials are
untouched by disconnection (the disconnected-source preservation rule).
"""
import pytest

from api.routers import canvas as canvas_router
from api.security import crypto
from tests.conftest import TEST_CANVAS_URL

API = "/api/v1"


@pytest.fixture(autouse=True)
def encryption_key(monkeypatch):
    from api.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("CANVAS_TOKEN_KEY", crypto.generate_key())
    monkeypatch.setenv("CANVAS_TOKEN_KEY_VERSION", "1")
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def canvas_api(monkeypatch):
    """A Canvas that accepts one token and refuses the rest.

    `validate_token` answers with a bool, exactly as the real
    `CanvasService.validate_token` does. An earlier version of this stub
    answered the question through `get_courses`, returning `None` for a bad
    token -- a contract the real class never implemented, since its
    `get_courses` turns every failure into `[]`. The test below passed green
    against that stub while the route it covers accepted any token at all.
    A stub that agrees with the real method's *signature* but not its
    *behaviour* proves nothing.

    That is why the stub resolves its instance through `default_canvas_url()`
    rather than carrying a hostname of its own: a stub with a hardcoded
    instance would answer the same whether the real class read configuration
    or ignored it, and the tests below exist precisely to tell those apart.
    """
    from api.services.canvas_service import default_canvas_url

    class StubCanvasService:
        def __init__(self, api_token, user_id, canvas_url=None):
            self.api_token = api_token
            self.user_id = user_id
            self.canvas_url = (canvas_url or default_canvas_url()).rstrip('/')

        async def validate_token(self):
            return self.api_token == "valid-canvas-token"

        async def sync_all_data(self):
            return {"courses": 1, "pages": 0}

    monkeypatch.setattr(canvas_router, "CanvasService", StubCanvasService)
    return StubCanvasService


async def connect(client, headers, token="valid-canvas-token"):
    return await client.post(f"{API}/canvas/token", headers=headers, json={"api_token": token})


async def test_a_valid_token_connects_the_source(client, alice):
    response = await connect(client, alice["headers"])

    assert response.status_code == 200
    assert response.json()["disconnected"] is False


async def test_an_invalid_token_is_refused_before_it_is_stored(client, provider, alice):
    """a bad Canvas credential is invalid input (400), not a failed
    authentication of the caller (401). Left as 401, the authentication
    interceptor would treat this as the Student's own identity token being
    dead and sign them out of the whole application."""
    response = await connect(client, alice["headers"], token="wrong-token")

    assert response.status_code == 400
    assert provider.records("canvas_tokens") == []


async def test_the_real_service_calls_a_credential_check_not_a_course_listing(monkeypatch):
    """The stub above is only worth anything while the real method it stands
    in for behaves the same way:
    The route asked `get_courses()`, which reports every failure as `[]`, so
    an unusable token was accepted and stored while the stub said otherwise.
    """
    from api.services.canvas_service import CanvasService

    asked = []

    async def refuse(self, endpoint, params=None):
        asked.append(endpoint)
        return None

    monkeypatch.setattr(CanvasService, "_make_request", refuse)

    assert await CanvasService("bad-token", "student-id").validate_token() is False
    assert asked == ["/api/v1/users/self"]


async def test_the_real_service_accepts_a_token_canvas_answers_for(monkeypatch):
    """A Student enrolled in nothing still holds a valid credential, which is
    why the check is `/api/v1/users/self` rather than a course listing."""
    from api.services.canvas_service import CanvasService

    async def identify(self, endpoint, params=None):
        return {"id": 4, "name": "A Student"}

    monkeypatch.setattr(CanvasService, "_make_request", identify)

    assert await CanvasService("good-token", "student-id").validate_token() is True


async def test_the_stored_token_is_ciphertext(client, provider, alice):
    await connect(client, alice["headers"])

    record = provider.records("canvas_tokens")[0]
    assert "valid-canvas-token" not in str(record)
    assert record["api_token_ciphertext"]
    assert record["key_version"] == 1


async def test_the_token_is_never_returned_to_the_browser(client, alice):
    connected = await connect(client, alice["headers"])
    fetched = await client.get(f"{API}/canvas/token", headers=alice["headers"])

    assert "valid-canvas-token" not in connected.text
    assert "valid-canvas-token" not in fetched.text
    assert "ciphertext" not in fetched.text


async def test_the_stored_token_decrypts_back_to_what_was_sent(client, provider, alice):
    await connect(client, alice["headers"])

    record = provider.records("canvas_tokens")[0]
    assert crypto.decrypt_canvas_token(record["api_token_ciphertext"], record["key_version"]) == "valid-canvas-token"


async def test_disconnecting_keeps_the_source_record(client, provider, alice):
    await connect(client, alice["headers"])

    response = await client.delete(f"{API}/canvas/token", headers=alice["headers"])

    assert response.status_code == 200
    records = provider.records("canvas_tokens")
    assert len(records) == 1 and records[0]["disconnected"] is True


async def test_a_failed_cache_clear_still_disconnects_the_source(client, provider, alice, monkeypatch):
    """The disconnect is what the Student asked for; the cache is disposable.

    Two writes, and only the first carries the contract: once the source is
    marked disconnected and its credential cleared, Canvas has stopped being
    used. The cached Canvas records are a refreshable copy of platform data,
    not Student Library content, and the next connect resyncs them -- so
    failing the whole disconnect because they could not be cleared would tell
    a Student their request was refused when it was already honoured. The
    failure is logged instead, so a stale cache can be found.
    """
    from api.database.pocketbase_client import ProviderUnavailable
    from api.database.repository import Repository, get_repository

    await connect(client, alice["headers"])
    await get_repository().upsert_canvas_record(
        alice["id"], "page", "7", {"title": "Alice page", "content": "", "course_id": "1"}
    )

    async def unavailable(self, *args, **kwargs):
        raise ProviderUnavailable("the cache clear failed on its own")

    monkeypatch.setattr(Repository, "delete_canvas_records", unavailable)

    response = await client.delete(f"{API}/canvas/token", headers=alice["headers"])

    assert response.status_code == 200
    record = provider.records("canvas_tokens")[0]
    assert record["disconnected"] is True
    assert record["api_token_ciphertext"] == ""


async def test_a_failed_cache_clear_is_logged_rather_than_silent(client, alice, monkeypatch, caplog):
    from api.database.pocketbase_client import ProviderUnavailable
    from api.database.repository import Repository

    await connect(client, alice["headers"])

    async def unavailable(self, *args, **kwargs):
        raise ProviderUnavailable("the cache clear failed on its own")

    monkeypatch.setattr(Repository, "delete_canvas_records", unavailable)

    with caplog.at_level("ERROR"):
        await client.delete(f"{API}/canvas/token", headers=alice["headers"])

    assert "were not cleared" in caplog.text


async def test_an_outage_while_connecting_the_source_is_unavailable(client, alice, monkeypatch):
    """A storage outage is 503, not the router's flat 500 (the storage-outage contract)."""
    from api.database.pocketbase_client import ProviderUnavailable
    from api.database.repository import Repository

    async def unavailable(self, *args, **kwargs):
        raise ProviderUnavailable("pocketbase returned 500")

    monkeypatch.setattr(Repository, "upsert_canvas_token", unavailable)

    response = await connect(client, alice["headers"])

    assert response.status_code == 503
    assert response.json()["detail"] == "This service is temporarily unavailable"


async def test_a_sync_leaves_the_stored_credential_untouched(client, provider, alice, monkeypatch):
    """a sync writes `last_sync` and disturbs nothing else.

    `upsert_canvas_token`'s update branch is a partial merge. Were it ever a
    full replace, a Student's Canvas connection would be destroyed on their
    next sync — ciphertext, key version and the disconnected flag all dropped
    — and they would be asked to reconnect a source they never disconnected.

    The real `CanvasService.sync_all_data` runs here; only its Canvas HTTP
    calls are stubbed out, so the repository write under test is the real one
    on the real path, not a stand-in for it.
    """
    from api.services import canvas_service as canvas_service_module

    await connect(client, alice["headers"])
    before = dict(provider.records("canvas_tokens")[0])
    assert before["api_token_ciphertext"] and before["key_version"] == 1

    async def no_canvas_data(self, endpoint, params=None):
        return None

    monkeypatch.setattr(canvas_service_module.CanvasService, "_make_request", no_canvas_data)

    response = await client.post(f"{API}/canvas/sync", headers=alice["headers"])

    assert response.status_code == 200
    after = provider.records("canvas_tokens")[0]
    assert after["api_token_ciphertext"] == before["api_token_ciphertext"]
    assert after["key_version"] == before["key_version"]
    assert after["disconnected"] is False
    assert after["canvas_url"] == before["canvas_url"]
    assert after["last_sync"]


async def test_disconnecting_clears_the_ciphertext(client, provider, alice):
    await connect(client, alice["headers"])

    await client.delete(f"{API}/canvas/token", headers=alice["headers"])

    assert provider.records("canvas_tokens")[0]["api_token_ciphertext"] == ""


async def test_a_disconnected_source_stops_producing_updates(client, alice):
    await connect(client, alice["headers"])
    await client.delete(f"{API}/canvas/token", headers=alice["headers"])

    response = await client.post(f"{API}/canvas/sync", headers=alice["headers"])

    assert response.status_code == 404


async def test_imported_materials_survive_disconnection(client, provider, alice):
    """The disconnected-source preservation rule: imported Course Materials remain private snapshots."""
    from api.database.repository import get_repository

    await connect(client, alice["headers"])
    await get_repository().create_material(
        alice["id"],
        {"title": "[Canvas] Biology 101 - Week 1", "status": "ready",
         "source_identity": "canvas:canvas.example.edu:page:7", "material_source": "canvas"},
    )

    await client.delete(f"{API}/canvas/token", headers=alice["headers"])

    listed = await client.get(f"{API}/textbooks/", headers=alice["headers"])
    assert [m["title"] for m in listed.json()["textbooks"]] == ["[Canvas] Biology 101 - Week 1"]


async def test_reconnecting_clears_the_disconnected_flag(client, provider, alice):
    await connect(client, alice["headers"])
    await client.delete(f"{API}/canvas/token", headers=alice["headers"])

    response = await connect(client, alice["headers"])

    assert response.json()["disconnected"] is False
    assert provider.records("canvas_tokens")[0]["disconnected"] is False


async def test_reconnecting_does_not_create_a_second_source(client, provider, alice):
    await connect(client, alice["headers"])
    await client.delete(f"{API}/canvas/token", headers=alice["headers"])
    await connect(client, alice["headers"])

    assert len(provider.records("canvas_tokens")) == 1


async def test_another_student_cannot_read_the_source(client, alice, bob):
    await connect(client, alice["headers"])

    response = await client.get(f"{API}/canvas/token", headers=bob["headers"])

    assert response.status_code == 404


async def test_another_student_cannot_disconnect_the_source(client, provider, alice, bob):
    await connect(client, alice["headers"])

    await client.delete(f"{API}/canvas/token", headers=bob["headers"])

    assert provider.records("canvas_tokens")[0]["disconnected"] is False


async def test_cached_canvas_records_are_scoped_to_their_owner(client, provider, alice, bob):
    from api.database.repository import get_repository

    await get_repository().upsert_canvas_record(
        alice["id"], "page", "7", {"title": "Alice page", "content": "", "course_id": "1"}
    )

    response = await client.get(f"{API}/canvas/data", headers=bob["headers"])

    assert response.json()["total"] == 0


# -- GET /canvas/data must not 500 for the Student who owns the data ----
#
# CanvasDataResponse requires created_at/updated_at (datetime), and due_date
# is Optional[datetime]. upsert_canvas_record writes none of those directly:
# PocketBase supplies `created`/`updated` (no `_at`) on every record, and
# canvas_data's `due_date` is a text field that is '' when absent (the schema).
#
# The fake now stamps `created`/`updated` on create in PocketBase's real
# wire format ("YYYY-MM-DD HH:MM:SS.mmmZ", space separator) rather than the
# test injecting them, so this exercises the actual mapping against a value
# shaped like the one a real PocketBase would send -- not a convenient stand-in.
async def test_the_owning_student_gets_a_populated_canvas_data_response(client, provider, alice):
    from datetime import datetime as _dt

    from api.database.repository import get_repository

    await get_repository().upsert_canvas_record(
        alice["id"], "page", "7",
        {
            "title": "Cell Biology",
            "content": "Chapter 1 content",
            "course_id": "1",
            "course_name": "Biology 101",
            "due_date": "",
            "metadata": {"foo": "bar"},
        },
    )
    raw = provider.records("canvas_data")[0]

    response = await client.get(f"{API}/canvas/data", headers=alice["headers"])

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["title"] == "Cell Biology"
    assert item["due_date"] is None

    def parse(raw_value: str) -> _dt:
        return _dt.fromisoformat(raw_value.replace("Z", "+00:00").replace(" ", "T", 1))

    assert parse(item["created_at"]) == parse(raw["created"])
    assert parse(item["updated_at"]) == parse(raw["updated"])


# -- sync reporting: a sync reports what it wrote, not what Canvas listed ----------
#
# The counts handed back to the Student were the *Canvas* list lengths --
# `counts['pages'] += len(pages)` -- while `_save_canvas_data` ended in a
# catch-all that swallowed every write failure. A store that refused half the
# records still reported all of them as synced, which is the same untruth
# `delete_textbook` goes to real trouble to avoid when it stamps `failed`
# rather than claim a removal it did not complete (the immediate-removal contract).
#
# The two ways a write fails are not the same failure, and the sync answers
# each on its own terms (the persistence seam contract):
#
#   UnfilterableValue   this one record cannot be written; every other record
#                       is fine. Skip it, count it under `skipped`, carry on.
#   ProviderUnavailable the store is down, so every remaining write is doomed.
#                       Abort at the first one and let the 503 handler answer.


def _canvas_returning(*, courses=(), assignments=(), pages=(), page_bodies=None):
    """A Canvas that answers the sync's endpoints from fixed data.

    Only the HTTP boundary is replaced. The real `sync_all_data`,
    `_save_canvas_data`, repository and store all run, so what these tests
    observe is the actual write path rather than a stand-in for it.
    """
    async def _make_request(self, endpoint, params=None):
        if endpoint == "/api/v1/courses":
            return list(courses)
        if endpoint.endswith("/assignments"):
            return list(assignments)
        if endpoint.endswith("/pages"):
            return list(pages)
        if "/pages/" in endpoint:
            return (page_bodies or {}).get(endpoint.rsplit("/pages/", 1)[1])
        return []

    return _make_request


def _serve_canvas(monkeypatch, **data):
    from api.services import canvas_service as canvas_service_module

    monkeypatch.setattr(
        canvas_service_module.CanvasService, "_make_request", _canvas_returning(**data)
    )


async def test_a_record_the_store_refuses_is_skipped_not_counted(
    client, provider, alice, monkeypatch
):
    """One unwritable record costs the Student that record and nothing else.

    `upsert_canvas_record` filters on `canvas_id`, and a page's `canvas_id` is
    a Canvas-supplied URL slug. `_render_filter` refuses a value carrying a
    quote rather than escaping it, so a slug like this raises
    `UnfilterableValue` for this one page while the other two write cleanly --
    no monkeypatching needed to provoke it, because the real store genuinely
    behaves this way.

    Before this test, `counts['pages']` was 3 against 2 stored records.
    """
    await connect(client, alice["headers"])
    _serve_canvas(
        monkeypatch,
        courses=[{"id": 1, "name": "Biology 101"}],
        pages=[
            {"url": "week-1"},
            {"url": 'week-2"quiz'},
            {"url": "week-3"},
        ],
        page_bodies={
            "week-1": {"title": "Week 1", "body": "short"},
            'week-2"quiz': {"title": "Week 2", "body": "short"},
            "week-3": {"title": "Week 3", "body": "short"},
        },
    )

    response = await client.post(f"{API}/canvas/sync", headers=alice["headers"])

    assert response.status_code == 200
    counts = response.json()["counts"]
    stored = provider.records("canvas_data")
    assert counts["pages"] == len(stored) == 2
    assert counts["skipped"] == 1
    assert sorted(record["title"] for record in stored) == ["Week 1", "Week 3"]


async def test_a_storage_outage_mid_sync_is_never_reported_as_success(
    client, provider, alice, monkeypatch
):
    """The headline case, isolated from the write that used to mask it.

    Only the record writes fail here; the trailing `last_sync` write succeeds.
    That matters: `sync_all_data`'s outer handler re-raising on *that* write is
    what made a total outage look correct, and it hid the fact that a sync
    which persisted nothing still answered 200 with a full set of counts.
    """
    from api.database.pocketbase_client import ProviderUnavailable
    from api.database.repository import Repository

    async def unavailable(self, *args, **kwargs):
        raise ProviderUnavailable("pocketbase returned 500")

    await connect(client, alice["headers"])
    _serve_canvas(
        monkeypatch,
        courses=[{"id": 1, "name": "Biology 101"}],
        assignments=[{"id": 11, "name": "Lab 1"}, {"id": 12, "name": "Lab 2"}],
    )
    monkeypatch.setattr(Repository, "upsert_canvas_record", unavailable)

    response = await client.post(f"{API}/canvas/sync", headers=alice["headers"])

    assert response.status_code == 503
    assert provider.records("canvas_data") == []


async def test_an_outage_stops_the_sync_at_the_first_doomed_write(
    client, alice, monkeypatch
):
    """Aborting is not just tidier than skipping — it is the difference
    between a 503 and a hung request.

    Every write during an outage costs a full provider timeout. Carrying on
    through a Student's whole course load would leave the request hanging for
    as long as it takes to time out once per record before finally answering.
    The count below is the whole of that argument, so it is asserted rather
    than assumed.
    """
    from api.database.pocketbase_client import ProviderUnavailable
    from api.database.repository import Repository

    attempts = []

    async def unavailable(self, *args, **kwargs):
        attempts.append(args)
        raise ProviderUnavailable("pocketbase returned 500")

    await connect(client, alice["headers"])
    _serve_canvas(
        monkeypatch,
        courses=[{"id": 1, "name": "Biology 101"}],
        assignments=[{"id": n, "name": f"Lab {n}"} for n in range(1, 6)],
    )
    monkeypatch.setattr(Repository, "upsert_canvas_record", unavailable)

    await client.post(f"{API}/canvas/sync", headers=alice["headers"])

    assert len(attempts) == 1


async def test_a_clean_sync_still_counts_every_record_it_wrote(
    client, provider, alice, monkeypatch
):
    """The guard against over-correcting: when nothing fails, the counts are
    The full set, and `skipped` says so."""
    await connect(client, alice["headers"])
    _serve_canvas(
        monkeypatch,
        courses=[{"id": 1, "name": "Biology 101"}],
        assignments=[{"id": 11, "name": "Lab 1"}, {"id": 12, "name": "Lab 2"}],
    )

    response = await client.post(f"{API}/canvas/sync", headers=alice["headers"])

    counts = response.json()["counts"]
    assert counts["assignments"] == len(provider.records("canvas_data")) == 2
    assert counts["skipped"] == 0


# -- The Canvas instance is deployment configuration ------------------------
#
# `CanvasService.CANVAS_BASE_URL` was a constant in the source, so every
# installation of this self-hosted software synced from whichever institution's
# Canvas happened to be committed (the self-hosted configuration boundary). It is now `CANVAS_BASE_URL` in
# The environment, with no default: an Operator who has not said which Canvas
# they use does not get a guess.


async def test_a_new_connection_uses_the_configured_canvas_instance(
    client, provider, alice, monkeypatch
):
    """The Operator's configured instance is what gets stored on the record."""
    from api.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("CANVAS_BASE_URL", "https://canvas.elsewhere.edu")

    response = await connect(client, alice["headers"])

    assert response.status_code == 200
    assert provider.records("canvas_tokens")[0]["canvas_url"] == "https://canvas.elsewhere.edu"


async def test_connecting_is_refused_when_no_canvas_instance_is_configured(
    client, provider, alice, monkeypatch
):
    """Unset is refused, not guessed.

    503 rather than 500: an unconfigured deployment is the Operator's to fix
    and says nothing about the Student's request, which is exactly how an
    absent `CANVAS_TOKEN_KEY` already answers. Nothing is stored, so a
    Student cannot end up holding a credential against an unknown instance.

    Set empty rather than deleted. `Settings` reads `env_file=".env"`, so
    `delenv` only removes the process variable and pydantic falls straight
    back to whatever the developer's own `.env` holds -- this test passed
    against a real configured instance until that file gained the key, which
    is to say it was passing for the wrong reason. An environment variable
    outranks the file, so an empty one is unset for every developer.
    """
    from api.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("CANVAS_BASE_URL", "")
    assert get_settings().canvas_base_url == "", "premise: no instance configured"

    response = await connect(client, alice["headers"])

    assert response.status_code == 503
    assert provider.records("canvas_tokens") == []


async def test_an_established_connection_ignores_the_configured_default(
    client, provider, alice, monkeypatch
):
    """The record names the instance its token belongs to.

    A Canvas token authenticates against one instance and no other, so
    repointing the deployment default must never silently redirect a Student's
    existing credential at a host it cannot authenticate against.
    """
    from api.config import get_settings
    from api.services.canvas_service import get_canvas_service

    await connect(client, alice["headers"])
    assert provider.records("canvas_tokens")[0]["canvas_url"] == TEST_CANVAS_URL

    get_settings.cache_clear()
    monkeypatch.setenv("CANVAS_BASE_URL", "https://canvas.somewhere-else.edu")
    service = await get_canvas_service(alice["id"])

    assert service.canvas_url == TEST_CANVAS_URL


def test_no_canvas_instance_is_hardcoded_in_the_application():
    """The guarantee, asserted against the source rather than behaviour.

    A default that merely *matches* the configured value in tests would pass
    every case above while still shipping one institution's Canvas to every
    other deployment. Only reading the source can tell those apart -- the same
    reason `test_seam_errors` inspects imports directly.
    """
    import inspect
    import importlib

    for module in ("api.services.canvas_service", "api.routers.canvas", "api.config"):
        source = inspect.getsource(importlib.import_module(module))
        assert "canvas.example.edu" not in source, f"{module} hardcodes a Canvas instance"


async def test_stats_name_the_instance_a_new_connection_would_use(client, alice):
    """The connect form has to name a Canvas before one is connected.

    Without this the frontend has nowhere to learn the deployment's instance,
    so it would have to carry a hostname of its own -- which is precisely what
    moving the instance into the environment exists to stop.
    """
    response = await client.get(f"{API}/canvas/stats", headers=alice["headers"])

    body = response.json()
    assert body["configured"] is False
    assert body["canvas_url"] == TEST_CANVAS_URL


async def test_stats_are_still_served_when_no_instance_is_configured(
    client, alice, monkeypatch
):
    """An unconfigured Canvas is not a broken Documents page.

    Stats are fetched on page load whether or not Canvas is in use, so an
    Operator who never configured Canvas must still get a page -- Canvas
    simply reports itself unavailable.

    Empty rather than deleted, for the reason given above: `delenv` leaves
    pydantic reading the developer's `.env`.
    """
    from api.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("CANVAS_BASE_URL", "")
    assert get_settings().canvas_base_url == "", "premise: no instance configured"

    response = await client.get(f"{API}/canvas/stats", headers=alice["headers"])

    assert response.status_code == 200
    assert response.json()["canvas_url"] is None
