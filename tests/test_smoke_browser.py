"""
`PlaywrightPage`'s never-raises contract, proven without a real browser.

The module docstring in `evals/smoke/browser.py` promises `count`, `text`,
`wait_for`, `wait_for_gone`, `wait_for_count`, `wait_for_any` and `wait_for_url`
never raise -- absence is a return value, not an exception, so a leg can
express "the tutor never joined" as a verdict instead of blowing up mid-run.
Five of the seven already wrap `try/except`; this file exists because `count`
and `text` did not.

A real Playwright page is not needed to prove this: `PlaywrightPage` only
ever calls `.locator(...)` on whatever `page` object it was constructed with,
so a fake whose `.locator()` raises stands in for a genuine Playwright
failure (a malformed selector, a torn-down page, a closed context) without
needing chromium installed at all.
"""
from evals.smoke.browser import PlaywrightPage


class ExplodingPage:
    """Stands in for a Playwright `Page` whose `.locator()` call blows up."""

    def locator(self, selector):
        raise RuntimeError("the page is gone")


async def test_count_never_raises_even_when_playwright_does():
    page = PlaywrightPage(ExplodingPage(), base_url="http://example.invalid")

    assert await page.count("anything") == 0


async def test_text_never_raises_even_when_playwright_does():
    page = PlaywrightPage(ExplodingPage(), base_url="http://example.invalid")

    assert await page.text("anything") == ""
