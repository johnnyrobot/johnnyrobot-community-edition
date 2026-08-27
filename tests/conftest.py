"""
Shared fixtures.

Tests drive the real application over ASGI. Nothing above the PocketBase
adapter is stubbed: routers, dependencies, and the repository all run for real,
and only the socket is replaced by the in-process fake. The things this spec
exists to build — the 401/503 distinction, mandatory owner filtering, the
partial unique index — are precisely what a higher seam would skip.
"""
from unittest.mock import patch

import httpx
import pytest

from api.database.pocketbase_client import PocketBaseClient
from api.database.pocketbase_store import PocketBaseStore
from api.database.store import NotConfiguredStore, set_store
from api.dependencies import set_provider_client
from api.services.student_memory import set_memory_client
from tests.fake_pocketbase import FakePocketBase


@pytest.fixture
def provider():
    fake = FakePocketBase()
    client = PocketBaseClient(
        base_url="http://pocketbase:8090",
        superuser_email="operator@example.com",
        superuser_password="operator-password",
        transport=fake.transport,
    )
    set_provider_client(client)
    set_store(PocketBaseStore(client))
    yield fake
    set_provider_client(None)
    set_store(NotConfiguredStore())


@pytest.fixture
async def client(provider):
    from api.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


@pytest.fixture
def alice(provider):
    student_id = provider.add_student("alice@example.com", "alice-password")
    return {
        "id": student_id,
        "email": "alice@example.com",
        "password": "alice-password",
        "headers": {"Authorization": f"Bearer {provider.token_for(student_id)}"},
    }


@pytest.fixture
def bob(provider):
    student_id = provider.add_student("bob@example.com", "bob-password")
    return {
        "id": student_id,
        "email": "bob@example.com",
        "password": "bob-password",
        "headers": {"Authorization": f"Bearer {provider.token_for(student_id)}"},
    }


# -- keep Mem0 off the network for the whole suite -----------------------
#
# `mem0.AsyncMemoryClient.__init__` validates its API key over the network and
# raises `ValueError: Error: Invalid API key` against the placeholder key in
# `.env`. Nothing in this plan stubs it otherwise, so several later tasks'
# tests would fail for a reason unrelated to what they test.
#
# `AsyncMemoryClient` is imported with `from mem0 import AsyncMemoryClient`,
# which binds the name into the importing module's namespace, so patching
# `mem0.AsyncMemoryClient` itself would not reach it. It is patched where it is
# looked up instead -- which since process-wide memory client construction is one place, not five: every call
# site now asks `api.services.student_memory.get_memory_client()` for the one
# client the process builds, and that module holds the only import.
#
# The stub is a real object, not a bare MagicMock, because every call site
# awaits its methods (`add`, `search`, `get_all`, `delete_all`); a MagicMock's
# return value is not awaitable and would raise TypeError at the await.
class _StubMemoryClient:
    """A Mem0 client stand-in that never leaves the process."""

    def __init__(self, *args, **kwargs):
        pass

    async def add(self, *args, **kwargs):
        return {"results": []}

    async def search(self, *args, **kwargs):
        return []

    async def get_all(self, *args, **kwargs):
        return []

    async def delete(self, *args, **kwargs):
        return {"message": "deleted"}

    async def delete_all(self, *args, **kwargs):
        return {"message": "deleted"}


@pytest.fixture(autouse=True)
def stub_mem0(request, monkeypatch):
    """Patch the `AsyncMemoryClient` lookup with an in-process stub.

    Tests that exercise a Mem0 failure install their own patch on the same
    name. `unittest.mock.patch` restores whatever was active when it exits, so
    a test's own `patch(...)`/`monkeypatch.setattr(...)` nested inside this
    fixture's wins for its duration and this stub is back afterward — the two
    do not fight.

    They must clear the built client as well, which is why this fixture clears
    it on both sides rather than leaving each test to remember.
    """
    # Where Student Memory lives is deployment configuration, and the suite
    # must not inherit the developer's. A laptop with `MEM0_SELF_HOSTED=true`
    # in its `.env` -- which `.env.local.example` now ships -- would otherwise
    # send every one of these cases down the self-hosted branch, and the
    # `AsyncMemoryClient` stub below would never be reached. A test that cares
    # about self-hosting turns it back on for itself. Same reasoning as
    # `canvas_instance` further down.
    from api.services import student_memory as _student_memory

    monkeypatch.setattr(_student_memory.settings, "mem0_self_hosted", False)

    patcher = patch("api.services.student_memory.AsyncMemoryClient", _StubMemoryClient)
    patcher.start()
    # The client is process-wide and built once (process-wide memory client construction), so a case that
    # builds one would answer every case after it -- including cases that patch
    # a different client, or none. Cleared on both sides: before, so a test
    # starts from an unbuilt seam whatever ran first; after, so nothing this
    # test built outlives its patches.
    set_memory_client(None)
    yield
    set_memory_client(None)
    patcher.stop()


@pytest.fixture(autouse=True)
def graph_off_by_default(monkeypatch):
    """No test reaches a real Neo4j unless it installs a client itself.

    A developer's .env may well set NEO4J_URL -- the local stack runs one, and
    `.env.local.example` ships `bolt://neo4j:7687` -- and without this the
    suite's behaviour would depend on whose machine it ran on, which is the
    same class of problem as the Mem0 stub above.

    That Compose hostname does not resolve from the host, so today the seam
    happens to degrade to the no-op and the suite is green by accident of DNS.
    `docker-compose.local.yml` publishes bolt on 127.0.0.1:7687 though, so a
    developer who points .env there to inspect the graph would have every
    upload and removal test writing Sections into the real database instead.

    It clears the seam and blanks the URL rather than installing a no-op, so a
    test that installs its own client is unaffected: this fixture is autouse
    and therefore sets up first, and whatever the test installs afterwards
    stands. Blanking the URL is what makes the *default* build a no-op.
    """
    from api.graph import client as graph_client

    monkeypatch.setattr(graph_client.settings, "neo4j_url", "")
    graph_client.set_graph_client(None)
    yield
    graph_client.set_graph_client(None)


# -- Canvas instance is deployment configuration, not a repo constant --------
#
# `CANVAS_BASE_URL` has no committed default: the hostname a deployment syncs
# from is the Operator's. Hardcoding one institution's Canvas would ship it to
# every installation of this self-hosted software. Unset, the connect route
# refuses rather than guessing.
#
# Every Canvas test needs *some* instance configured, so it is set here once
# rather than in each of the four files that connect a source. A test that
# cares about the unset case deletes it with `monkeypatch.delenv`.
TEST_CANVAS_URL = "https://canvas.test.example"


@pytest.fixture(autouse=True)
def canvas_instance(monkeypatch):
    from api.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("CANVAS_BASE_URL", TEST_CANVAS_URL)
    yield
    get_settings.cache_clear()
