"""
The smoke table.

Every leg is listed whatever its verdict, because the question this harness
answers is "what works right now", and a report that prints only failures
cannot answer it. Failures then repeat with their detail and their screenshot:
`evals/report.py` already established that a bare count is not reviewable, and
that is even more true of a browser, where the useful evidence is a picture.
"""
from evals.smoke.results import SmokeRun, Verdict


def render(run: SmokeRun, base_url: str) -> str:
    if not run.n:
        return "No legs ran, so nothing was measured.\n"

    lines = [f"smoke   base_url={base_url}   browser=chromium", ""]

    for leg in run.legs:
        lines.append(f"  {leg.name:<12} {leg.verdict.value.upper()}")

    # `SmokeRun.passes/failures/voids` already do this arithmetic; this just
    # stops making an operator count rows by hand under time pressure.
    lines.append(
        f"  {run.n} legs: {run.passes} passed, {run.failures} failed, {run.voids} void"
    )

    lines.append("")

    for leg in run.legs:
        if leg.verdict is Verdict.PASS:
            continue
        lines.append(f"  {leg.name} — {leg.verdict.value}")
        if leg.detail:
            lines.append(f"    {leg.detail}")
        if leg.screenshot:
            lines.append(f"    screenshot: {leg.screenshot}")
        lines.append("")

    lines.append(
        "A void leg never ran, so it says nothing about the deployment. "
        "Only a run whose legs all passed exits zero."
    )
    return "\n".join(lines) + "\n"


def exit_code(run: SmokeRun) -> int:
    """0 only when every leg passed.

    VOID exits non-zero for the reason `evals/report.py` already gives about
    INCONCLUSIVE: not knowing is not passing, and a harness whose auth never
    worked must not report green.
    """
    if not run.n:
        return 1
    return 0 if all(leg.verdict is Verdict.PASS for leg in run.legs) else 1
