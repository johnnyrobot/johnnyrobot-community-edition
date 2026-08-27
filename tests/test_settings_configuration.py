"""
What the Settings block actually configures.

`Settings` carries three decisions that a deployment depends on and that nothing
else in the codebase restates: where the file of variables lives, that a
variable nobody recognises is ignored rather than fatal, and that the name is
matched without regard to case. They were written as a nested `class Config`,
which Pydantic deprecated in V2.0 and removes in V3.0 -- and removal is silent
here, because a config class Pydantic no longer reads is just an unused inner
class. The deployment would lose its `.env` and start refusing the extra keys
already in it, with nothing raised to say why.

So the three are pinned by behaviour rather than by reading the block back, and
a fourth test holds the module to a form Pydantic will keep reading.
"""
import os
import subprocess
import sys

import pytest

from api.config import Settings

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Every field with no default. A `.env` short of these cannot construct at all,
# so each test below writes them whatever else it is checking.
REQUIRED = """\
LIVEKIT_URL=wss://livekit.test
LIVEKIT_API_KEY=key
LIVEKIT_API_SECRET=secret
APP_SECRET_KEY=app-secret
"""


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    """Write a `.env` and make it the one `Settings()` finds.

    The real environment wins over the file, and this machine's may well carry
    The same names, so the fields a test asserts on are cleared first -- the
    suite must not pass or fail on what a developer happens to export.
    """
    monkeypatch.chdir(tmp_path)
    for name in ("POCKETBASE_URL", "pocketbase_url"):
        monkeypatch.delenv(name, raising=False)

    def write(body=""):
        (tmp_path / ".env").write_text(REQUIRED + body)

    return write


def test_settings_come_from_the_env_file(env_file):
    env_file("POCKETBASE_URL=http://pocketbase.from-the-file:8090\n")

    assert Settings().pocketbase_url == "http://pocketbase.from-the-file:8090"


def test_an_unrecognised_variable_does_not_stop_the_process(env_file):
    """A `.env` outlives the code that reads it.

    Variables are added for one service and left behind when it goes; a
    deployment holding one the current build has no field for must still start.
    """
    env_file("A_VARIABLE_NOTHING_READS=1\n")

    assert Settings().pocketbase_url == "http://pocketbase:8090"  # the field default


def test_a_variable_name_is_matched_whatever_its_case(env_file, monkeypatch):
    env_file()
    monkeypatch.setenv("pocketbase_url", "http://pocketbase.lowercase:8090")

    assert Settings().pocketbase_url == "http://pocketbase.lowercase:8090"


def test_importing_the_settings_module_deprecates_nothing():
    """The config has to be in the form Pydantic will still read in V3.

    Run in a subprocess under `-W error::DeprecationWarning`: this process
    imported `api.config` long before the test started, and a warning is
    raised once per site. The subprocess also keeps the strict filter off
    every other test.
    """
    result = subprocess.run(
        [sys.executable, "-W", "error::DeprecationWarning", "-c", "import api.config"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
