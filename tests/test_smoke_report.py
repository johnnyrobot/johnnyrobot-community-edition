"""
The table an operator reads twenty minutes before a demo.

Two things it must never do: hide a leg, and exit zero on a run that
established nothing.
"""
from evals.smoke.report import exit_code, render
from evals.smoke.results import LegResult, SmokeRun, Verdict

PASS, FAIL, VOID = Verdict.PASS, Verdict.FAIL, Verdict.VOID


def run(*pairs):
    return SmokeRun(legs=tuple(LegResult(name=n, verdict=v) for n, v in pairs))


ALL_GREEN = run(("auth", PASS), ("documents", PASS), ("chat", PASS), ("voice", PASS))


def test_every_leg_appears_in_the_report():
    printed = render(ALL_GREEN, "http://localhost")

    for name in ("auth", "documents", "chat", "voice"):
        assert name in printed


def test_the_report_names_the_deployment_it_drove():
    """A green table means nothing if you cannot see what it was pointed at."""
    assert "http://localhost" in render(ALL_GREEN, "http://localhost")


def test_a_failing_leg_shows_its_detail():
    """A count alone is not reviewable."""
    failed = SmokeRun(
        legs=(LegResult(name="voice", verdict=FAIL, detail="no tutor joined"),)
    )

    assert "no tutor joined" in render(failed, "http://localhost")


def test_a_failing_leg_names_its_screenshot():
    failed = SmokeRun(
        legs=(LegResult(name="voice", verdict=FAIL, screenshot="/tmp/voice.png"),)
    )

    assert "/tmp/voice.png" in render(failed, "http://localhost")


def test_an_empty_run_says_nothing_ran():
    assert "No legs ran" in render(SmokeRun(), "http://localhost")


def test_the_summary_lists_every_leg_even_when_some_failed():
    """No existing test isolates the summary loop from the detail block that
    follows it. `test_every_leg_appears_in_the_report` above uses an all-PASS
    run, so a mutant that filtered non-PASS legs out of the *summary* loop
    only would find nothing to filter and pass anyway. The single-leg failure
    tests (`test_a_failing_leg_shows_its_detail` etc.) can't catch it either:
    that leg's name also appears in the detail block below the summary, so a
    bare "name in printed" check would pass for the wrong reason even with the
    summary loop broken.

    A MIXED run is the shape that actually isolates it: this only proves the
    point if the assertion looks at the summary section alone, not the whole
    rendered string -- so this slices out the block between the first blank
    line (after the header) and the second (before the detail block) and
    checks every leg's name against just that."""
    mixed = run(("auth", PASS), ("documents", FAIL), ("chat", VOID), ("voice", PASS))

    all_lines = render(mixed, "http://localhost").splitlines()
    first_blank = all_lines.index("")
    second_blank = all_lines.index("", first_blank + 1)
    summary = "\n".join(all_lines[first_blank + 1 : second_blank])

    for name in ("auth", "documents", "chat", "voice"):
        assert name in summary


def test_the_tally_line_reports_the_counts():
    """`SmokeRun.passes/failures/voids` exist but nothing in the rendered
    report ever surfaced them -- an operator reading the table under time
    pressure had to count rows by hand. One line, under the per-leg list."""
    mixed = run(("auth", PASS), ("documents", FAIL), ("chat", VOID), ("voice", PASS))

    printed = render(mixed, "http://localhost")

    assert "4 legs: 2 passed, 1 failed, 1 void" in printed


def test_all_green_exits_zero():
    assert exit_code(ALL_GREEN) == 0


def test_a_failing_leg_exits_non_zero():
    assert exit_code(run(("auth", PASS), ("voice", FAIL))) != 0


def test_a_void_leg_exits_non_zero():
    """Not knowing is not passing -- the rule evals/report.py already follows."""
    assert exit_code(run(("auth", PASS), ("voice", VOID))) != 0


def test_a_run_with_no_legs_exits_non_zero():
    assert exit_code(SmokeRun()) != 0
