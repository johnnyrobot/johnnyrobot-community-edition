"""
The browser, behind a protocol narrow enough to fake.

Legs talk to `Page`, not to Playwright. That is what lets every leg be tested
offline against `tests/fake_page.py` -- a leg that called Playwright directly
could only be tested by launching a browser, and a harness whose own logic is
untested reports the absence of bugs.

The protocol is deliberately small and string-selector based. Playwright's
selector engine already understands `[data-testid="x"]`, `text="y"` and
`:has-text("z")`, so scoping needs no extra methods, and a fake needs to
understand only strings.

Query and wait methods (`count`, `text`, `wait_for`, `wait_for_gone`,
`wait_for_count`, `wait_for_any`, `wait_for_url`) never raise; they encode
absence as a return value (0, "", False) so a leg can express "the tutor
never joined" as a verdict, not an exception. Action methods (`goto`, `fill`,
`click`, `upload`, `screenshot`) raise when their target is absent—the runner
catches these and records them as VOID legs, naming the exception.
"""
import contextlib
from typing import AsyncIterator, Protocol


class BrowserMissing(RuntimeError):
    """Chromium is not installed. Not a failing deployment; nothing ran."""


class Page(Protocol):
    async def goto(self, path: str) -> None:
        """Open a path relative to the deployment's base URL."""
        ...

    async def fill(self, selector: str, value: str) -> None: ...

    async def click(self, selector: str) -> None: ...

    async def upload(self, selector: str, file_path: str) -> None: ...

    async def count(self, selector: str) -> int:
        """How many elements match right now. Never waits."""
        ...

    async def text(self, selector: str) -> str:
        """The first match's text, or "" when nothing matches."""
        ...

    async def wait_for(self, selector: str, timeout: float) -> bool:
        """True if it appeared within the timeout. Never raises."""
        ...

    async def wait_for_gone(self, selector: str, timeout: float) -> bool: ...

    async def wait_for_count(self, selector: str, expected: int, timeout: float) -> bool: ...

    async def wait_for_any(self, selectors: list[str], timeout: float) -> str:
        """The first of these to appear, or "" if none does. Never raises."""
        ...

    async def wait_for_url(self, fragment: str, timeout: float) -> bool: ...

    async def screenshot(self, file_path: str) -> None: ...


class PlaywrightPage:
    """`Page`, implemented against a real Playwright page.

    Timeouts arrive in seconds because that is what the legs read naturally;
    Playwright wants milliseconds.
    """

    def __init__(self, page, base_url: str):
        self._page = page
        self._base_url = base_url.rstrip("/")

    async def goto(self, path: str) -> None:
        await self._page.goto(f"{self._base_url}{path}")

    async def fill(self, selector: str, value: str) -> None:
        await self._page.fill(selector, value)

    async def click(self, selector: str) -> None:
        await self._page.click(selector)

    async def upload(self, selector: str, file_path: str) -> None:
        await self._page.set_input_files(selector, file_path)

    async def count(self, selector: str) -> int:
        # The module docstring promises this never raises, same as the five
        # wait_for* methods below. This one and `text` were the two that did
        # not actually hold to it -- a torn-down page or a malformed selector
        # would have propagated straight through the one guarantee every leg
        # is written against.
        try:
            return await self._page.locator(selector).count()
        except Exception:
            return 0

    async def text(self, selector: str) -> str:
        try:
            found = self._page.locator(selector).first
            if not await self._page.locator(selector).count():
                return ""
            return (await found.text_content()) or ""
        except Exception:
            return ""

    async def wait_for(self, selector: str, timeout: float) -> bool:
        try:
            await self._page.wait_for_selector(selector, timeout=timeout * 1000)
            return True
        except Exception:
            return False

    async def wait_for_gone(self, selector: str, timeout: float) -> bool:
        try:
            await self._page.wait_for_selector(
                selector, state="detached", timeout=timeout * 1000
            )
            return True
        except Exception:
            return False

    async def wait_for_count(self, selector: str, expected: int, timeout: float) -> bool:
        # This used to poll `document.querySelectorAll(selector).length`
        # inside `wait_for_function` -- the *browser's* CSS engine, not
        # Playwright's. `count()` and `text()` above both scope through
        # `self._page.locator(selector)`, which understands the full
        # Playwright selector language: `text=`, `:has-text()`, chained `>>`
        # selectors. `document.querySelectorAll` understands none of that --
        # it is plain CSS, and `:has-text()` is not valid CSS at all.
        # `FIXTURE_ROW` in `legs.py` is
        # `[data-testid="document-row"]:has-text("smoke-material")`: every
        # poll against it threw inside the page, `wait_for_function` timed
        # out waiting on a predicate that could never evaluate, and the bare
        # `except` below turned that into a silent False -- reporting a
        # *successful* removal as "would not remove". A live run confirmed
        # The row really was gone. A `Page` method that understands fewer
        # selectors than its own siblings is exactly this kind of trap.
        # `expect(...).to_have_count()` goes through the same locator
        # `count()` uses, so anything `count()` can scope, this can wait on.
        from playwright.async_api import expect

        try:
            await expect(self._page.locator(selector)).to_have_count(
                expected, timeout=timeout * 1000
            )
            return True
        except Exception:
            return False

    async def wait_for_any(self, selectors: list[str], timeout: float) -> str:
        """First of `selectors` to appear within `timeout`, or "" if none does.

        The voice leg needs "did any of these show up" -- `AGENT_AUDIO` (a
        `[data-testid=...]` attribute selector) or one of `LIVE_LABELS` (all
        `text="..."` selectors) -- not a sequence, so this waits on all of
        them at once rather than polling each in turn.

        The obvious way to do that is joining the selectors with `", "` into
        one string, mirroring the CSS selector list
        `document.querySelectorAll("a, b, c")` performs a union over. That is
        wrong here for the same reason `wait_for_count` used to be wrong:
        Playwright decides which selector *engine* parses a string once, for
        The whole string, from how it starts. `[data-testid="agent-audio"],
        text="Ready"` starts looking like CSS, so the whole thing is parsed as
        CSS -- including the `text="Ready"` half, which is not valid CSS and
        throws a parse error before anything can be waited on at all (verified
        directly against a real page while building this: joining an
        attribute selector with a `text=` selector reliably raises
        `Unexpected token "="`). Caught by the `except` below, that would make
        this method always return "", silently -- the same selector-engine
        mismatch `wait_for_count`'s defect already proved this codebase is
        prone to, just with the failure mode inverted (an immediate parse
        error instead of a doomed poll).

        `Locator.or_()` sidesteps this: each selector is resolved by its own
        engine first, as a `Locator`, and only the *results* are unioned --
        so mixing engines is unremarkable, the same way `count()` and `text()`
        already resolve each selector independently.
        """
        if not selectors:
            return ""

        locators = [self._page.locator(selector) for selector in selectors]
        combined = locators[0]
        for locator in locators[1:]:
            combined = combined.or_(locator)

        try:
            await combined.first.wait_for(timeout=timeout * 1000)
        except Exception:
            return ""

        # Something in the union attached, but `or_()` doesn't say which --
        # ask each selector's own `count()`, the same lookup `count()` and
        # `text()` already use, to find out.
        for selector, locator in zip(selectors, locators):
            if await locator.count():
                return selector

        # The union matched, but no individual selector's own count()
        # confirms it -- should not happen, but every method here keeps the
        # same contract ("never raises, absence is a return value"), so
        # honour it here too rather than assume the loop above is exhaustive.
        return ""

    async def wait_for_url(self, fragment: str, timeout: float) -> bool:
        try:
            await self._page.wait_for_url(f"**{fragment}**", timeout=timeout * 1000)
            return True
        except Exception:
            return False

    async def screenshot(self, file_path: str) -> None:
        await self._page.screenshot(path=file_path, full_page=True)


@contextlib.asynccontextmanager
async def open_page(base_url: str, headed: bool = False) -> AsyncIterator[Page]:
    """One browser, one context, one page, closed however the run ends.

    A missing chromium is refused by name here rather than surfacing as a leg
    failure: an operator who has not run `playwright install` has not learned
    anything about their deployment.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError as absent:
        raise BrowserMissing(
            "Playwright is not installed. Run: pip install -r requirements.txt"
        ) from absent

    async with async_playwright() as driver:
        try:
            # `VoiceCallPage.tsx` renders `<LiveKitRoom audio={true}>`, so the
            # instant the voice leg opens `/session` the browser tries to
            # publish a real microphone track. Headless chromium has no audio
            # device and no one to click an OS permission prompt, so a bare
            # launch fails `getUserMedia` and LiveKit tears the room down
            # before the pill ever reaches a state this harness can read --
            # which is exactly the live failure this fixes ("Client initiated
            # disconnect"). `--use-fake-device-for-media-stream` gives
            # chromium a synthetic mic to publish, and
            # `--use-fake-ui-for-media-stream` answers the permission prompt
            # automatically instead of hanging headless forever waiting for a
            # click that can never come.
            #
            # This does NOT make voice_leg an audio round-trip test. Nothing
            # here proves sound goes in or a real voice comes out -- it only
            # gets the session past the microphone-publish step so the page
            # can reach `Ready`/`Listening…`/etc., which is the only thing
            # `voice_leg` was ever built to read.
            browser = await driver.chromium.launch(
                headless=not headed,
                args=[
                    "--use-fake-device-for-media-stream",
                    "--use-fake-ui-for-media-stream",
                ],
            )
        except Exception as unlaunchable:
            raise BrowserMissing(
                "Chromium could not start, which usually means it was never "
                "downloaded. Run: playwright install chromium"
            ) from unlaunchable

        try:
            # Belt-and-suspenders alongside the fake-UI launch arg above: this
            # grants the permission at the browser-context level so nothing
            # about a future flag change or a different consent path can
            # reintroduce the block-on-getUserMedia failure this leg exists
            # to avoid.
            context = await browser.new_context(permissions=["microphone"])
            yield PlaywrightPage(await context.new_page(), base_url)
        finally:
            await browser.close()
