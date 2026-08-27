"""
Running the four legs, and deciding what a broken one means.

Two rules do all the work here.

**A leg that raises is VOID, never FAIL.** An exception is the harness
breaking, and a harness that broke has learned nothing about the deployment.
Recording that as a failure would send an operator to debug a tutor that may be
perfectly fine.

**A failed sign-in voids the other three rather than failing them.** They did
not fail; they never ran. Twenty minutes before a demo, "voice is broken" and
"we never got far enough to find out" send you to different places.
"""
import logging
import os

from evals.config import SmokeConfig
from evals.smoke.browser import Page
from evals.smoke.legs import auth_leg, chat_leg, documents_leg, voice_leg
from evals.smoke.results import LegResult, SmokeRun, Verdict

logger = logging.getLogger(__name__)

NOT_REACHED = ("documents", "chat", "voice")


async def _attempt(name: str, leg) -> LegResult:
    """Run one leg, turning any exception into a void rather than a failure."""
    try:
        return await leg
    except Exception as broken:
        logger.warning(f"The {name} leg is void: the harness failed ({broken})")
        return LegResult(
            name=name,
            verdict=Verdict.VOID,
            detail=f"the harness failed: {type(broken).__name__}: {broken}",
        )


async def _with_screenshot(page: Page, result: LegResult, screenshot_dir: str) -> LegResult:
    """Photograph anything that did not pass.

    "voice failed" is far less actionable than a picture of the page, and the
    screenshot is the browser equivalent of `evals/report.py` quoting every
    failing run instead of printing a count.
    """
    if result.verdict is Verdict.PASS:
        return result

    path = os.path.join(screenshot_dir, f"smoke-{result.name}.png")
    try:
        await page.screenshot(path)
    except Exception as no_picture:
        logger.warning(f"Could not screenshot the {result.name} leg ({no_picture})")
        return result

    return LegResult(
        name=result.name,
        verdict=result.verdict,
        detail=result.detail,
        screenshot=path,
    )


async def run_smoke(page: Page, config: SmokeConfig, screenshot_dir: str) -> SmokeRun:
    """Every leg, in order, with only the auth dependency between them."""
    os.makedirs(screenshot_dir, exist_ok=True)

    auth = await _with_screenshot(
        page, await _attempt("auth", auth_leg(page, config)), screenshot_dir
    )

    if auth.verdict is not Verdict.PASS:
        return SmokeRun(
            legs=(
                auth,
                *(
                    LegResult(
                        name=name,
                        verdict=Verdict.VOID,
                        detail="never ran: the smoke Student could not sign in",
                    )
                    for name in NOT_REACHED
                ),
            )
        )

    rest = []
    for name, leg in (
        ("documents", documents_leg(page)),
        ("chat", chat_leg(page)),
        ("voice", voice_leg(page)),
    ):
        rest.append(
            await _with_screenshot(page, await _attempt(name, leg), screenshot_dir)
        )

    return SmokeRun(legs=(auth, *rest))
