"""
How persistence gets installed, and when.

Two processes reach storage. The API installs the PocketBase client and store
as its ASGI lifespan opens, and releases the client as that lifespan closes.
The voice agent installs the same store when a Tutor Session starts -- not when
its module is imported, which would make the bare act of importing it (a test
collecting, a tool reading it) reach for the Deployment Operator's credentials
and take over a process-wide global that nobody asked it to touch.

Nothing here stubs the wiring it is checking: the real lifespan runs, and the
real client is built from settings. Only the settings are stood in for, so the
suite never depends on what a particular developer has in `.env`. See lifespan-based startup wiring
and the private persistence boundary.
"""
import ast
import inspect
import os
import subprocess
import sys
import textwrap
from unittest.mock import patch

import pytest

import api.main
from api.config import get_settings
from api.database.pocketbase_store import PocketBaseStore
from api.database.store import NotConfiguredStore, get_store, set_store
from api.dependencies import (
    IdentityProviderNotConfigured,
    get_provider_client,
    set_provider_client,
)
from api.main import app

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _settings_with(**overrides):
    """Real settings, with the few fields a test cares about pinned."""
    return get_settings().model_copy(update=overrides)


def _connections_are_closed(client):
    return client._verifier.is_closed and client._data.is_closed


@pytest.fixture
async def wiring():
    """Hand the process back its unconfigured globals, whatever the test did.

    The store and the provider client are process-wide by design -- one
    deployment, one PocketBase. A test that installs a real one has to put it
    back, and close it, or the next test inherits a client pointed at a URL it
    never chose.
    """
    yield
    store = get_store()
    if isinstance(store, PocketBaseStore):
        await store._client.aclose()
    try:
        await get_provider_client().aclose()
    except IdentityProviderNotConfigured:
        pass
    set_store(NotConfiguredStore())
    set_provider_client(None)


# -- the API -----------------------------------------------------------------


async def test_the_lifespan_installs_the_pocketbase_store(wiring):
    """Opening the lifespan is what configures persistence."""
    settings = _settings_with(
        pocketbase_url="http://pocketbase.test:8090",
        pocketbase_superuser_password="operator-password",
    )

    with patch.object(api.main, "settings", settings):
        async with app.router.lifespan_context(app):
            store = get_store()
            client = get_provider_client()

            assert isinstance(store, PocketBaseStore)
            assert client._base_url == "http://pocketbase.test:8090"
            # One client, so the token verifier and the record reads share a
            # connection pool and a superuser token rather than racing two.
            assert store._client is client


async def test_a_lifespan_without_a_superuser_password_leaves_persistence_unconfigured(wiring):
    """No credential, no store -- and no store that pretends to persist.

    A deployment missing this credential can neither authenticate nor persist.
    `NotConfiguredStore` fails loudly on first use, which is the honest answer;
    installing a client that cannot authenticate would fail later and vaguer.
    """
    settings = _settings_with(pocketbase_superuser_password="")

    with patch.object(api.main, "settings", settings):
        async with app.router.lifespan_context(app):
            assert isinstance(get_store(), NotConfiguredStore)
            with pytest.raises(IdentityProviderNotConfigured):
                get_provider_client()


async def test_closing_the_lifespan_releases_the_provider_client(wiring):
    """Shutdown closes the connections and clears the global."""
    settings = _settings_with(pocketbase_superuser_password="operator-password")

    with patch.object(api.main, "settings", settings):
        async with app.router.lifespan_context(app):
            client = get_provider_client()
            assert not _connections_are_closed(client)

    assert _connections_are_closed(client)
    with pytest.raises(IdentityProviderNotConfigured):
        get_provider_client()


def test_the_application_registers_no_deprecated_event_handlers():
    """`on_event` is deprecated and slated for removal, and it is load-bearing.

    It carries the store install and the client close, so the day FastAPI drops
    it the API fails at import rather than degrading. The lifespan above is the
    replacement; these two lists staying empty is what keeps it the only path.
    """
    assert app.router.on_startup == []
    assert app.router.on_shutdown == []


# -- the voice agent ---------------------------------------------------------


def test_importing_the_voice_agent_installs_no_store():
    """Importing a module must not take over the process's storage.

    Run in a subprocess because this process imported `agent` long ago -- the
    suite's Mem0 stub patches a name inside it -- and an import only has its
    side effects once. The credential is passed explicitly so the check cannot
    pass for the boring reason that this machine has none configured.
    """
    probe = textwrap.dedent(
        """
        import agent
        from api.database.store import get_store
        print(type(get_store()).__name__)
        """
    )
    environment = {**os.environ, "POCKETBASE_SUPERUSER_PASSWORD": "operator-password"}

    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip() == "NotConfiguredStore", result.stderr


def test_the_voice_agent_installs_the_store_when_asked(wiring):
    import agent

    settings = _settings_with(
        pocketbase_url="http://pocketbase.test:8090",
        pocketbase_superuser_password="operator-password",
    )

    with patch.object(agent, "settings", settings):
        agent.install_store()

    store = get_store()
    assert isinstance(store, PocketBaseStore)
    assert store._client._base_url == "http://pocketbase.test:8090"


def test_the_voice_agent_leaves_a_store_that_is_already_installed(provider):
    """The suite's fake -- or any store an embedding process chose -- wins.

    The agent installs a default for the case where nothing else has; it does
    not overwrite a store somebody else installed. That is what makes the call
    safe at the top of the entrypoint, where the alternative is a second client
    pointed at whatever `.env` happens to say.
    """
    import agent

    settings = _settings_with(pocketbase_superuser_password="operator-password")
    installed = get_store()

    with patch.object(agent, "settings", settings):
        agent.install_store()

    assert get_store() is installed


def test_the_voice_agent_installs_the_store_as_a_job_starts():
    """The entrypoint is the seam that replaces the import-time side effect.

    `agent.entrypoint` needs a live LiveKit `JobContext` and a connected
    participant, so it is read rather than run -- the same compromise
    `test_student_memory_optional` makes about the same function.
    """
    import agent

    tree = ast.parse(textwrap.dedent(inspect.getsource(agent.entrypoint)))
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert "install_store" in called
