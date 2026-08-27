"""
Starting a smoke run, and refusing to start one.

A missing browser binary is not a failing deployment. Reported as a table of
voids it would be a result-shaped object produced by a harness that never ran,
which is the one direction this project has repeatedly decided a test must not
lie in.
"""
import pytest

from evals.smoke.browser import BrowserMissing


def test_a_missing_browser_is_its_own_error():
    """Distinct from any leg verdict, so the entry point can refuse by name."""
    assert issubclass(BrowserMissing, RuntimeError)


from evals.config import SmokeConfig
from evals.smoke.results import Verdict
from evals.smoke.runner import run_smoke
from tests.fake_page import FakePage
from evals.smoke.legs import AGENT_AUDIO, CHAT_REPLY, FIXTURE_ROW

CONFIG = SmokeConfig(
    base_url="http://localhost",
    student_email="smoke@example.com",
    student_password="smoke-password",
)


def working_page():
    """A deployment where everything works.

    `FIXTURE_ROW` is in `appears`, not `present`: `present` is what `count()`
    reads, and `documents_leg` counts `FIXTURE_ROW` before it ever uploads
    anything, to detect a stale row left behind by a previous run. Seeding it
    into `present` here would make this "everything works" page look like it
    already has a leftover row sitting there, which is exactly the case
    `tests/test_smoke_legs.py::test_a_stale_row_that_will_not_clear_fails_
    honestly` exists to catch -- not what a healthy run looks like.
    """
    page = FakePage(present={AGENT_AUDIO: 1, CHAT_REPLY: 1}, appears={FIXTURE_ROW},
                    text={CHAT_REPLY: "Hello!"})

    async def lands(fragment, timeout):
        return True

    async def settles(selector, timeout):
        return True

    async def counts(selector, expected, timeout):
        return expected == 0

    page.wait_for_url = lands
    page.wait_for_gone = settles
    page.wait_for_count = counts
    return page


async def test_a_working_deployment_passes_every_leg(tmp_path):
    result = await run_smoke(working_page(), CONFIG, str(tmp_path))

    assert result.passes == 4


async def test_every_leg_is_reported(tmp_path):
    result = await run_smoke(working_page(), CONFIG, str(tmp_path))

    assert [leg.name for leg in result.legs] == ["auth", "documents", "chat", "voice"]


async def test_a_failed_sign_in_voids_the_rest(tmp_path):
    """They did not fail. They never ran, and that is different information."""
    page = working_page()

    async def never_lands(fragment, timeout):
        return False

    page.wait_for_url = never_lands

    result = await run_smoke(page, CONFIG, str(tmp_path))

    assert result.legs[0].verdict is Verdict.FAIL
    assert [leg.verdict for leg in result.legs[1:]] == [Verdict.VOID] * 3


async def test_a_leg_that_raises_is_void_not_failed(tmp_path):
    """A harness that broke has learned nothing about the deployment."""
    page = working_page()

    async def explode(selector, timeout):
        raise RuntimeError("the browser died")

    page.wait_for = explode

    result = await run_smoke(page, CONFIG, str(tmp_path))

    assert any(leg.verdict is Verdict.VOID for leg in result.legs[1:])


async def test_one_broken_leg_does_not_stop_the_others(tmp_path):
    """The entire reason for independent legs."""
    page = working_page()
    page.present[AGENT_AUDIO] = 0

    async def audio_never_arrives(selector, timeout):
        return selector != AGENT_AUDIO

    page.wait_for = audio_never_arrives

    result = await run_smoke(page, CONFIG, str(tmp_path))

    assert result.legs[-1].verdict is Verdict.FAIL
    assert result.legs[2].verdict is Verdict.PASS


async def test_a_failing_leg_gets_a_screenshot(tmp_path):
    page = working_page()
    page.present[AGENT_AUDIO] = 0

    async def audio_never_arrives(selector, timeout):
        return selector != AGENT_AUDIO

    page.wait_for = audio_never_arrives

    result = await run_smoke(page, CONFIG, str(tmp_path))

    assert result.legs[-1].screenshot
    assert ("screenshot", result.legs[-1].screenshot) in page.actions


async def test_a_passing_leg_gets_no_screenshot(tmp_path):
    """Nothing to look at, and nothing to clutter the report."""
    result = await run_smoke(working_page(), CONFIG, str(tmp_path))

    assert not any(leg.screenshot for leg in result.legs)


import evals.__main__ as entry
from evals.smoke.browser import BrowserMissing


def test_the_smoke_flag_parses():
    assert entry._parse_args(["--smoke"]).smoke is True


def test_headed_is_off_by_default():
    assert entry._parse_args(["--smoke"]).headed is False


def test_headed_can_be_asked_for():
    assert entry._parse_args(["--smoke", "--headed"]).headed is True


def test_a_missing_browser_is_refused_by_name(monkeypatch, capsys):
    """Not a table of voids. An operator who never ran `playwright install`
    has learned nothing about their deployment."""

    def no_browser(*args, **kwargs):
        raise BrowserMissing("Run: playwright install chromium")

    monkeypatch.setattr(entry, "load_smoke_config", lambda: CONFIG)
    monkeypatch.setattr(entry, "open_page", no_browser)

    code = entry.main(["--smoke"])

    err = capsys.readouterr().err
    assert code == 2
    assert "playwright install chromium" in err
    # `BrowserMissing` is itself a `RuntimeError`, so a handler swapped
    # ahead of it in `main` would still exit 2 and still quote the install
    # command (interpolated into its own message) -- the two checks above
    # would not notice. This one pins the handler that actually ran: the
    # generic `except RuntimeError` handler's message is distinctively
    # prefixed, and a correctly-ordered `main` must never print it here.
    assert "The eval run could not start:" not in err
