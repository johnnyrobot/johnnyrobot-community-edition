"""
What one leg established, and what four of them mean together.

Three verdicts, not two. VOID is a leg that never ran -- auth failed upstream,
The deployment was unreachable, Playwright itself broke. Counting one as a pass
is the failure mode this module exists to prevent, and counting one as a
failure would send an operator to debug a tutor when the thing that broke was
their wifi.
"""
from evals.smoke.results import LegResult, SmokeRun, Verdict

PASS, FAIL, VOID = Verdict.PASS, Verdict.FAIL, Verdict.VOID


def run(*verdicts):
    return SmokeRun(
        legs=tuple(LegResult(name=f"leg-{i}", verdict=v) for i, v in enumerate(verdicts))
    )


def test_counts_add_up():
    tally = run(PASS, PASS, FAIL, VOID)

    assert (tally.n, tally.passes, tally.failures, tally.voids) == (4, 2, 1, 1)


def test_a_void_is_not_a_pass():
    """The property the whole design turns on."""
    assert run(VOID, VOID).passes == 0


def test_a_void_is_not_a_failure_either():
    assert run(VOID, VOID).failures == 0


def test_a_leg_carries_what_was_seen():
    """A verdict with no detail behind it is not reviewable by a human."""
    leg = LegResult(name="voice", verdict=FAIL, detail="the pill read 'Not connected'")

    assert leg.detail


def test_a_leg_is_frozen():
    """A result a later leg can rewrite is not a result."""
    leg = LegResult(name="voice", verdict=PASS)

    try:
        leg.verdict = FAIL
    except Exception:
        return
    raise AssertionError("LegResult must be frozen")


def test_an_empty_run_counts_nothing():
    assert SmokeRun().n == 0
