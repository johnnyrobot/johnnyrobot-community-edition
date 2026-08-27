"""
The harness's own logic, tested the ordinary deterministic way.

Test infrastructure that is itself untested is a liability: a harness that
silently miscounts would report the absence of bugs, which is worse than
reporting nothing. Nothing here touches the network or a model.
"""
from evals.results import MAX_VOID_FRACTION, CaseResult, Outcome, Run


def result(*outcomes, threshold=0):
    return CaseResult(
        case_name="empty-memory-confabulation",
        surface="chat",
        threshold=threshold,
        runs=tuple(Run(outcome=o) for o in outcomes),
    )


CLEAN, FAILED, VOID = Outcome.CLEAN, Outcome.FAILED, Outcome.VOID


def test_all_clean_against_a_zero_threshold_passes():
    assert result(CLEAN, CLEAN, CLEAN, CLEAN).verdict == "PASS"


def test_one_failure_against_a_zero_threshold_fails():
    """#14's threshold is zero: inventing a Student's history is not tolerated at 5%."""
    assert result(CLEAN, CLEAN, CLEAN, FAILED).verdict == "FAIL"


def test_failures_at_the_threshold_pass():
    assert result(CLEAN, CLEAN, FAILED, threshold=1).verdict == "PASS"


def test_failures_past_the_threshold_fail():
    assert result(CLEAN, FAILED, FAILED, threshold=1).verdict == "FAIL"


def test_voids_do_not_count_as_passes():
    """The property this whole design turns on.

    Four void runs establish nothing. Counting them as clean would let a
    harness whose precondition never held report green.
    """
    assert result(VOID, VOID, VOID, VOID).verdict != "PASS"


def test_voids_do_not_count_as_failures_either():
    assert result(VOID, VOID, VOID, VOID).failures == 0


def test_too_many_voids_is_inconclusive_not_passing():
    """A quarter is the stated line: 2 voids in 4 runs is half, well past it."""
    assert result(CLEAN, CLEAN, VOID, VOID).verdict == "INCONCLUSIVE"


def test_a_few_voids_still_let_a_case_report():
    """1 void in 8 is under a quarter, so the other 7 runs still mean something."""
    assert result(CLEAN, CLEAN, CLEAN, CLEAN, CLEAN, CLEAN, CLEAN, VOID).verdict == "PASS"


def test_voids_at_exactly_the_fraction_are_tolerated():
    """"Exceeds one quarter" -- 1 in 4 is not more than a quarter."""
    assert result(CLEAN, CLEAN, CLEAN, VOID).verdict == "PASS"


def test_inconclusive_beats_fail_when_both_apply():
    """A run of voids means we do not know, and "we do not know" is not "it failed".

    Reporting FAIL here would send someone to debug the tutor when the thing
    that broke is the harness.
    """
    assert result(FAILED, VOID, VOID, VOID).verdict == "INCONCLUSIVE"


def test_zero_runs_is_inconclusive():
    assert result().verdict == "INCONCLUSIVE"


def test_the_failure_rate_is_over_all_runs_including_voids():
    """Hiding voids from the denominator would flatter a half-broken run."""
    assert result(FAILED, CLEAN, CLEAN, CLEAN).failure_rate == 0.25


def test_counts_add_up():
    tally = result(CLEAN, CLEAN, FAILED, VOID)

    assert (tally.n, tally.cleans, tally.failures, tally.voids) == (4, 2, 1, 1)


def test_the_void_fraction_is_a_stated_constant():
    """Named so the report can explain itself rather than quoting a magic number."""
    assert MAX_VOID_FRACTION == 0.25


def test_a_run_carries_what_the_tutor_said():
    """The report quotes real answers; a verdict with no text behind it is unauditable."""
    run = Run(outcome=FAILED, text="I remember you struggled", quote="you struggled")

    assert run.text and run.quote in run.text
