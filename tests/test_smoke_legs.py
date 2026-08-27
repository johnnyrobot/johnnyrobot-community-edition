"""
What each leg concludes, and from what.

Every leg here runs against `tests/fake_page.py`, never a browser. The thing
worth testing is the decision -- "the pill said the tutor never joined, so the
agent is down" -- and that decision is wrong in exactly the ways a human
skimming a green table would not notice.
"""
import pytest

from evals.config import SmokeConfig
from evals.smoke.legs import auth_leg
from evals.smoke.results import Verdict
from tests.fake_page import FakePage

CONFIG = SmokeConfig(
    base_url="http://localhost",
    student_email="smoke@example.com",
    student_password="smoke-password",
)


async def test_landing_on_the_dashboard_passes():
    page = FakePage(url="/login")
    page.current_url = "/login"

    async def land(fragment, timeout):
        return fragment == "/dashboard"

    page.wait_for_url = land

    assert (await auth_leg(page, CONFIG)).verdict is Verdict.PASS


async def test_staying_on_login_fails():
    """Credentials rejected, or the API never answered. Either way, not signed in."""
    page = FakePage(url="/login")

    async def never_lands(fragment, timeout):
        return False

    page.wait_for_url = never_lands

    assert (await auth_leg(page, CONFIG)).verdict is Verdict.FAIL


async def test_a_failed_sign_in_says_what_it_saw():
    page = FakePage(url="/login")

    async def never_lands(fragment, timeout):
        return False

    page.wait_for_url = never_lands

    assert (await auth_leg(page, CONFIG)).detail


async def test_a_failed_sign_in_states_only_what_happened():
    """The old wording -- "signed in but never reached /dashboard" -- asserts
    a sign-in that was never observed, then denies it in the same breath. Say
    only what this leg actually did (submitted the form) and actually saw
    (/dashboard never arrived); the rest of the sentence's reasoning stays."""
    page = FakePage(url="/login")

    async def never_lands(fragment, timeout):
        return False

    page.wait_for_url = never_lands

    result = await auth_leg(page, CONFIG)

    assert result.detail.startswith(
        "submitted the sign-in form but never reached /dashboard"
    )
    assert "the credentials were refused, or the API never answered" in result.detail


async def test_the_credentials_are_actually_typed():
    """A leg that passes without submitting anything would pass on a broken form."""
    page = FakePage(url="/login")

    async def land(fragment, timeout):
        return True

    page.wait_for_url = land

    await auth_leg(page, CONFIG)

    assert ("fill", 'input[type="email"]', "smoke@example.com") in page.actions
    assert ("fill", 'input[type="password"]', "smoke-password") in page.actions
    assert ("click", 'button[type="submit"]') in page.actions


async def test_the_leg_is_named_auth():
    page = FakePage(url="/login")

    async def land(fragment, timeout):
        return True

    page.wait_for_url = land

    assert (await auth_leg(page, CONFIG)).name == "auth"


from evals.smoke.legs import (
    DOCUMENT_SUBMIT,
    DOCUMENT_TITLE,
    FIXTURE_ROW,
    FIXTURE_TITLE,
    ROW_IMPORT_FAILED,
    ROW_PROCESSING,
    documents_leg,
)

UPLOAD = '[data-testid="document-upload-input"]'


def uploaded_page(**kwargs):
    """A page where the fixture row appears once this leg uploads it.

    `appears`, not `present`: `present` is `FakePage`'s "true right now" state,
    which `count()` reads too, and `documents_leg` calls `count(FIXTURE_ROW)`
    *before* uploading anything, to decide whether a previous run's row was
    left behind stale. Seeding `present` here would make every one of these
    tests look like it started with a stale row already there, tripping the
    leftover-row check the leg runs first. `appears` only answers `wait_for`
    (see `FakePage.wait_for`), which is exactly the point in the leg's
    timeline -- after upload -- where these tests mean for the row to exist.
    """
    return FakePage(appears={FIXTURE_ROW}, **kwargs)


async def test_a_clean_upload_and_removal_passes():
    page = uploaded_page(goes=(FIXTURE_ROW, 'text="Processing"'))

    async def gone(selector, expected, timeout):
        return expected == 0

    page.wait_for_count = gone

    assert (await documents_leg(page, "/tmp/f.txt")).verdict is Verdict.PASS


async def test_a_row_that_never_appears_fails():
    page = FakePage(present={})

    async def never(selector, timeout):
        return False

    page.wait_for = never

    result = await documents_leg(page, "/tmp/f.txt")

    assert result.verdict is Verdict.FAIL
    assert "never appeared" in result.detail


async def test_a_failed_import_fails_and_says_so():
    """'Failed — not searchable' means the tutor will not find this document.

    Seeded on `ROW_IMPORT_FAILED` (scoped to `FIXTURE_ROW`), not the bare
    page-wide label -- see `test_an_unrelated_failed_row_elsewhere_does_not_
    misdiagnose_this_leg` for why the leg must not read the unscoped one."""
    page = uploaded_page()
    page.present[ROW_IMPORT_FAILED] = 1

    result = await documents_leg(page, "/tmp/f.txt")

    assert result.verdict is Verdict.FAIL
    assert "not searchable" in result.detail


async def test_a_failed_import_that_shows_up_after_processing_clears_fails():
    """The design's single most important FAIL to catch: a Course Material is
    created `processing` and only later flips to `failed`. The test above
    seeds the failed badge from the very start, so it is satisfied by the fast
    branch that runs before the Processing wait -- it can never exercise the
    case this test drives, where Processing clears and only THEN does the row
    read Failed. The old code trusted `wait_for_gone(PROCESSING, ...)`
    returning True to mean "ready", removed the row, and reported PASS.

    `wait_for_gone` here plays both parts at once, the way a live import
    actually would: it reports Processing as gone, and in that same instant
    The row flips to Failed -- so a leg that only checked IMPORT_FAILED before
    The wait, never after, would sail straight through to PASS.
    """
    page = uploaded_page()

    async def clears_into_failure(selector, timeout):
        page.present[ROW_IMPORT_FAILED] = 1
        return True

    page.wait_for_gone = clears_into_failure

    result = await documents_leg(page, "/tmp/f.txt")

    assert result.verdict is Verdict.FAIL
    assert "not searchable" in result.detail


async def test_an_unrelated_processing_row_elsewhere_does_not_block_this_leg():
    """`PROCESSING` used to be a page-wide selector. A second Course Material
    in the Library stuck Processing for reasons that have nothing to do with
    this run's upload would make `wait_for_gone` wait out the full
    IMPORT_TIMEOUT and then FAIL -- about someone else's material. Scoped to
    `FIXTURE_ROW` via `ROW_PROCESSING`, an unrelated row's own badge must be
    invisible to this leg."""
    page = uploaded_page()
    page.present['text="Processing"'] = 1  # someone else's material, unscoped

    async def gone(selector, expected, timeout):
        return expected == 0

    page.wait_for_count = gone

    assert (await documents_leg(page, "/tmp/f.txt")).verdict is Verdict.PASS


async def test_an_unrelated_failed_row_elsewhere_does_not_misdiagnose_this_leg():
    """`IMPORT_FAILED` used to be page-wide too. A second material that
    already failed for an unrelated reason would make this leg confidently
    report OUR upload as 'Failed — not searchable' -- a confidently wrong
    diagnosis. Scoped via `ROW_IMPORT_FAILED`, an unrelated row's own Failed
    badge must be invisible to this leg."""
    page = uploaded_page()
    page.present['text="Failed — not searchable"'] = 1  # someone else's material

    async def gone(selector, expected, timeout):
        return expected == 0

    page.wait_for_count = gone

    assert (await documents_leg(page, "/tmp/f.txt")).verdict is Verdict.PASS


async def test_an_import_stuck_in_processing_fails():
    page = uploaded_page()
    page.present['text="Processing"'] = 1

    async def never_settles(selector, timeout):
        return False

    page.wait_for_gone = never_settles

    result = await documents_leg(page, "/tmp/f.txt")

    assert result.verdict is Verdict.FAIL
    assert "Processing" in result.detail


async def test_a_stale_row_that_will_not_clear_fails_honestly():
    """A previous run crashed mid-leg and left its own fixture row behind.
    The bounded cleanup loop `break`s the moment `wait_for_count` reports the
    removal did not stick, leaving the stale row in place. The OLD code fell
    straight through into `await page.wait_for(FIXTURE_ROW, ...)`, which then
    found that STALE row and reported "it appeared" -- so a leg that could not
    even establish a clean starting point went on to claim it was measuring
    its own upload. `count(FIXTURE_ROW)` must be checked again right after the
    loop, and this run must not be trusted if it is still non-zero."""
    page = FakePage(present={FIXTURE_ROW: 1})

    async def never_clears(selector, expected, timeout):
        return False

    page.wait_for_count = never_clears

    result = await documents_leg(page, "/tmp/f.txt")

    assert result.verdict is Verdict.FAIL
    assert "leftover" in result.detail.lower()


async def test_a_row_that_will_not_delete_fails():
    page = uploaded_page(goes=('text="Processing"',))

    async def still_there(selector, expected, timeout):
        return False

    page.wait_for_count = still_there

    result = await documents_leg(page, "/tmp/f.txt")

    assert result.verdict is Verdict.FAIL
    assert "remove" in result.detail.lower()


async def test_the_fixture_is_actually_uploaded():
    page = uploaded_page(goes=(FIXTURE_ROW, 'text="Processing"'))

    async def gone(selector, expected, timeout):
        return expected == 0

    page.wait_for_count = gone

    await documents_leg(page, "/tmp/f.txt")

    assert ("upload", UPLOAD, "/tmp/f.txt") in page.actions


async def test_the_title_is_filled_before_upload():
    """The upload is a two-step form: a
    required title field gates `handleUpload`, so a leg that sets the file but
    never fills the title leaves the form invalid and nothing is ever
    submitted. `FIXTURE_TITLE` is the exact string `FIXTURE_ROW` scopes on."""
    page = uploaded_page(goes=(FIXTURE_ROW, 'text="Processing"'))

    async def gone(selector, expected, timeout):
        return expected == 0

    page.wait_for_count = gone

    await documents_leg(page, "/tmp/f.txt")

    assert ("fill", DOCUMENT_TITLE, FIXTURE_TITLE) in page.actions


async def test_the_form_is_actually_submitted():
    """Selecting a file and typing a title does not upload anything --
    `DocumentsPage.tsx`'s `handleUpload` only fires from the form's `onSubmit`,
    so a leg that never clicks the submit button would wait on a row that
    never appears."""
    page = uploaded_page(goes=(FIXTURE_ROW, 'text="Processing"'))

    async def gone(selector, expected, timeout):
        return expected == 0

    page.wait_for_count = gone

    await documents_leg(page, "/tmp/f.txt")

    assert ("click", DOCUMENT_SUBMIT) in page.actions


from evals.smoke.browser import PlaywrightPage


async def test_wait_for_count_understands_has_text_selectors():
    """The bug a live run found: `FIXTURE_ROW` is `[data-testid="document-row"]
    :has-text("smoke-material")`, and `:has-text()` is a Playwright-only
    selector extension -- not valid CSS, so the browser's own
    `document.querySelectorAll` cannot parse it at all. The old
    `wait_for_count` polled exactly that inside `wait_for_function`, every
    poll threw, the bare `except` turned the eventual timeout into a silent
    False, and `documents_leg` reported a *successful* removal as "would not
    remove" -- a live run confirmed the row really was gone.

    `FakePage.wait_for_count` cannot reproduce this: it never parses a
    selector, only looks one up in a dict, which is exactly why this defect
    reached a live deployment without any offline test catching it first.
    Proving the fix means driving `PlaywrightPage` itself against a real
    (headless) page, not the fake.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        pytest.skip("Playwright is not installed")

    async with async_playwright() as driver:
        try:
            browser = await driver.chromium.launch()
        except Exception:
            pytest.skip("Chromium is not installed; run `playwright install chromium`")
            return

        try:
            raw_page = await browser.new_page()
            page = PlaywrightPage(raw_page, base_url="http://example.invalid")

            # No element anywhere matches FIXTURE_ROW -- exactly the DOM state
            # `documents_leg` waits for after clicking remove. The old
            # implementation returned False here regardless of the DOM,
            # because the browser throws trying to parse `:has-text(...)`
            # before it ever gets to count anything.
            await raw_page.set_content(
                '<div data-testid="document-row">unrelated-material</div>'
            )
            assert await page.wait_for_count(FIXTURE_ROW, 0, timeout=1) is True

            # And the converse must still hold, so the fix is not just
            # "always return True": a row that IS there must not be reported
            # as already at the expected count of 0.
            await raw_page.set_content(
                '<div data-testid="document-row">smoke-material</div>'
            )
            assert await page.wait_for_count(FIXTURE_ROW, 0, timeout=0.3) is False
        finally:
            await browser.close()


from evals.smoke.legs import CHAT_PROMPT, chat_leg

ASSISTANT = '[data-testid="chat-message-assistant"]'


async def test_a_rendered_reply_passes():
    page = FakePage(present={ASSISTANT: 1}, text={ASSISTANT: "Hello! What shall we work on?"})

    assert (await chat_leg(page)).verdict is Verdict.PASS


async def test_no_reply_within_the_timeout_fails():
    page = FakePage(present={})

    async def never(selector, timeout):
        return False

    page.wait_for = never

    result = await chat_leg(page)

    assert result.verdict is Verdict.FAIL
    assert "no reply" in result.detail.lower()


async def test_an_empty_reply_bubble_fails():
    """A bubble that rendered with nothing in it is not a working chat."""
    page = FakePage(present={ASSISTANT: 1}, text={ASSISTANT: "   "})

    assert (await chat_leg(page)).verdict is Verdict.FAIL


async def test_the_prompt_is_actually_sent():
    page = FakePage(present={ASSISTANT: 1}, text={ASSISTANT: "Hi"})

    await chat_leg(page)

    assert ("fill", '[data-testid="chat-input"]', CHAT_PROMPT) in page.actions
    assert ("click", '[data-testid="chat-send"]') in page.actions


from evals.smoke.legs import (
    AGENT_AUDIO,
    CONNECTING,
    LIVE_LABELS,
    NO_TUTOR,
    NOT_CONNECTED,
    READY,
    RECONNECTING,
    voice_leg,
)


async def test_wait_for_any_understands_mixed_selector_engines():
    """`AGENT_AUDIO` is a `[data-testid=...]` attribute selector; `LIVE_LABELS`
    are all `text="..."` selectors -- exactly the list `voice_leg` passes to
    `wait_for_any`. A naive `", ".join(selectors)` -- tried first while
    building this fix -- makes Playwright parse the *whole* joined string
    with one selector engine, decided by how the string starts: led by an
    attribute selector, it all parses as CSS, and `text="Ready"` is not valid
    CSS, so `wait_for_selector` on the join throws immediately (verified
    directly: `Unexpected token "="`). Caught by a bare `except`, that would
    make `wait_for_any` always return "" here -- silently, and specifically
    on the one selector list this fix exists to support. `Locator.or_()`
    resolves each selector by its own engine before unioning, so mixing
    engines is unremarkable.

    `FakePage.wait_for_any` cannot catch this, for the same reason
    `FakePage.wait_for_count` could not catch defect 1: it never parses a
    selector, so a selector-engine mismatch is invisible to it by
    construction.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        pytest.skip("Playwright is not installed")

    async with async_playwright() as driver:
        try:
            browser = await driver.chromium.launch()
        except Exception:
            pytest.skip("Chromium is not installed; run `playwright install chromium`")
            return

        try:
            raw_page = await browser.new_page()
            page = PlaywrightPage(raw_page, base_url="http://example.invalid")
            await raw_page.set_content('<div data-testid="agent-audio">audio</div>')

            matched = await page.wait_for_any([AGENT_AUDIO, *LIVE_LABELS], timeout=1)

            assert matched == AGENT_AUDIO
        finally:
            await browser.close()


async def test_agent_audio_passes():
    """Connected, a tutor joined, and it is publishing audio."""
    page = FakePage(present={AGENT_AUDIO: 1})

    assert (await voice_leg(page)).verdict is Verdict.PASS


async def test_a_room_that_never_connected_fails():
    page = FakePage(present={NOT_CONNECTED: 1})

    async def no_audio(selector, timeout):
        return False

    page.wait_for = no_audio

    result = await voice_leg(page)

    assert result.verdict is Verdict.FAIL
    assert "connect" in result.detail.lower()


async def test_a_room_with_no_tutor_says_the_agent_is_down():
    """The failure that hit before: a retired model killed the agent at start."""
    page = FakePage(present={NO_TUTOR: 1})

    async def no_audio(selector, timeout):
        return False

    page.wait_for = no_audio

    result = await voice_leg(page)

    assert result.verdict is Verdict.FAIL
    assert "agent" in result.detail.lower()


async def test_a_live_label_passes_with_no_agent_audio():
    """The defect a second live run found: `agent.py` calls `session.start()`
    but never `generate_reply()`, so the tutor never speaks first. A smoke
    run that never speaks to the agent can watch a fully healthy session --
    green "Listening…" pill, live microphone, End Session control -- and
    never see `agent-audio`, because nothing has been said for the agent to
    reply to. That live run reported FAIL against a working deployment.
    `READY` is one of `voiceStatus.ts`'s `live: true` labels, which per that
    file requires the room connected AND an agent present -- proof enough
    that a tutor joined, with no audio element in sight."""
    page = FakePage(present={READY: 1})

    assert (await voice_leg(page)).verdict is Verdict.PASS


async def test_still_connecting_when_the_wait_expires_fails_honestly():
    """Transitional, not terminal -- the wait simply ran out first. Must not
    claim a tutor joined; nothing has been observed that would prove that."""
    page = FakePage(present={CONNECTING: 1})

    async def no_audio(selector, timeout):
        return False

    page.wait_for = no_audio

    result = await voice_leg(page)

    assert result.verdict is Verdict.FAIL
    assert "tutor joined" not in result.detail.lower()
    assert "timeout" in result.detail.lower()


async def test_still_reconnecting_when_the_wait_expires_fails_honestly():
    page = FakePage(present={RECONNECTING: 1})

    async def no_audio(selector, timeout):
        return False

    page.wait_for = no_audio

    result = await voice_leg(page)

    assert result.verdict is Verdict.FAIL
    assert "tutor joined" not in result.detail.lower()
    assert "timeout" in result.detail.lower()


async def test_no_recognised_state_fails_without_claiming_a_tutor_joined():
    """The live failure this branch exists to catch: a stale deployment with
    no `agent-audio` element and none of the known text labels either, because
    The build predated all of them. The old fallback confidently reported "a
    tutor joined but published no audio" here -- a wrong diagnosis that sends
    an operator to debug a healthy agent, which is worse than an honest
    "cannot tell". Nothing was observed, so nothing may be claimed."""
    page = FakePage(present={})

    async def no_audio(selector, timeout):
        return False

    page.wait_for = no_audio

    result = await voice_leg(page)

    assert result.verdict is Verdict.FAIL
    assert "tutor joined" not in result.detail.lower()
    assert "connected" not in result.detail.lower()
    assert "recognis" in result.detail.lower()


async def test_the_failures_are_distinguishable():
    """Different remedies, so they must not collapse into one message.

    `READY` (and the rest of `LIVE_LABELS`) is deliberately not in this list
    any more: it is now a PASS branch (see
    `test_a_live_label_passes_with_no_agent_audio`), not one of the FAIL
    branches this test distinguishes between.
    """
    details = []
    for present in (
        {NOT_CONNECTED: 1},
        {NO_TUTOR: 1},
        {CONNECTING: 1},
        {RECONNECTING: 1},
        {},
    ):
        page = FakePage(present=present)
        details.append((await voice_leg(page)).detail)

    assert len(set(details)) == len(details)
