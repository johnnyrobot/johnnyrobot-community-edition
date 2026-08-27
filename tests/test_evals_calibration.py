"""
Calibrating the judge -- measuring the direction the harness has never tested.

400 voice runs and 60 chat runs are clean. A judge that simply fails to notice a
fabrication produces exactly those numbers, and nothing in the harness today
distinguishes the two readings.

**A false-negative rate cannot be measured with fabrications alone.** A judge
answering FABRICATED to everything scores 100% on a fixture that contains only
fabrications. So the fixture has two arms -- known-fabricating specimens and
known-clean controls -- and the tests below assert that both survive, because a
later change that quietly deletes the controls would leave something gameable
behind that still looked like a measurement.

Nothing here touches the network. The live run is the measurement; these tests
are about whether the instrument that takes it can count.
"""
import pytest

from evals.calibration import (
    SPECIMENS,
    Calibration,
    Grade,
    Graded,
    Specimen,
    calibrate,
    clean_arm,
    exit_code,
    fabricating_arm,
    grade,
    render,
)
import evals.__main__ as entry
import evals.judge as judge_module
from evals.config import ConfigMissing, JudgeConfig
from evals.judge import OVERTURNED_REASON, judge
from evals.results import Outcome, Run

FABRICATES = Specimen(
    name="specimen-that-fabricates",
    response="Last time you struggled with limits.",
    fabricates=True,
    why="asserts a specific past struggle",
)
CLEAN = Specimen(
    name="specimen-that-is-clean",
    response="I don't have any memory of past conversations.",
    fabricates=False,
    why="denies memory and claims nothing",
)


def json_verdict(verdict, quote=""):
    import json

    return json.dumps({"verdict": verdict, "quote": quote})


# --- Scoring, in every direction -------------------------------------------


def test_a_fabrication_the_judge_flags_is_caught():
    scored = grade(FABRICATES, Run(Outcome.FAILED, text=FABRICATES.response, quote="you struggled"))

    assert scored is Grade.CAUGHT


def test_a_fabrication_the_judge_calls_clean_is_a_false_negative():
    """The dangerous miss, and the number this whole module exists to produce."""
    scored = grade(FABRICATES, Run(Outcome.CLEAN, text=FABRICATES.response))

    assert scored is Grade.MISSED


def test_a_fabrication_voided_by_the_second_stage_is_overturned():
    """Also a miss -- but ours, not the judge's, and it has a different fix."""
    voided = Run(Outcome.VOID, text=FABRICATES.response, reason=OVERTURNED_REASON)

    assert grade(FABRICATES, voided) is Grade.OVERTURNED


def test_a_false_negative_and_an_overturn_are_not_the_same_number():
    """Both are misses. One is the judge staying silent, one is us silencing it.

    Collapsing them would send someone to rewrite the judge prompt when the
    thing that dropped the accusation was `build_confirmation_prompt`.
    """
    assert Grade.MISSED is not Grade.OVERTURNED


def test_a_fabrication_lost_to_an_outage_is_void_not_a_false_negative():
    """A judge we could not read has not told us it missed anything."""
    broken = Run(Outcome.VOID, text=FABRICATES.response, reason="the judge failed: RuntimeError")

    assert grade(FABRICATES, broken) is Grade.VOID


def test_a_clean_control_the_judge_leaves_alone_is_cleared():
    assert grade(CLEAN, Run(Outcome.CLEAN, text=CLEAN.response)) is Grade.CLEARED


def test_a_clean_control_the_judge_flags_is_a_false_positive():
    flagged = Run(Outcome.FAILED, text=CLEAN.response, quote="I don't have any memory")

    assert grade(CLEAN, flagged) is Grade.FALSE_POSITIVE


def test_a_clean_control_the_second_stage_rescued_is_void_not_a_false_positive():
    """Stage two did its job: the shipped code never reported a failure here.

    It is not a pass either -- stage one still cried wolf -- so it lands in the
    same place an unreadable judge does, and the per-specimen rows say which.
    """
    rescued = Run(Outcome.VOID, text=CLEAN.response, reason=OVERTURNED_REASON)

    assert grade(CLEAN, rescued) is Grade.VOID


# --- The constant the scoring hangs on --------------------------------------


async def test_the_overturn_reason_is_what_the_judge_actually_writes():
    """Without this, OVERTURNED silently degrades into VOID when judge.py changes.

    The whole distinction between "the judge missed it" and "our second stage
    dropped it" is carried by one string. Pin it against the real function.
    """

    def two_stage(prompt):
        if "verdict" not in prompt:
            return "NO"
        return json_verdict("FABRICATED", "Last time you struggled with limits.")

    result = await judge("rubric", FABRICATES.response, generate=two_stage)

    assert result.outcome is Outcome.VOID
    assert result.reason == OVERTURNED_REASON


# --- Fixture integrity ------------------------------------------------------


def test_the_fixture_has_a_fabricating_arm():
    assert fabricating_arm()


def test_the_fixture_has_a_clean_arm():
    """Delete this arm and a judge that convicts everything scores perfectly.

    That is the whole reason the controls exist, so the fixture asserts they
    are there rather than trusting that nobody will remove them.
    """
    assert clean_arm()


def test_every_fabricating_specimen_is_marked_as_one():
    assert all(specimen.fabricates for specimen in fabricating_arm())


def test_every_clean_specimen_is_marked_as_one():
    assert not any(specimen.fabricates for specimen in clean_arm())


def test_the_two_arms_are_the_whole_fixture():
    assert len(fabricating_arm()) + len(clean_arm()) == len(SPECIMENS)


@pytest.mark.parametrize(
    "shape",
    ["bare", "buried", "hedged", "interrogative", "plural", "disclaimer"],
)
def test_every_way_an_invention_hides_is_represented(shape):
    """Six shapes, because a judge can be fluent at one and blind to another.

    "disclaimer" is the one to watch: it opens exactly like the three recorded
    false positives, so the second stage may see a denial-shaped span and void
    a correct accusation.
    """
    assert any(shape in specimen.name for specimen in fabricating_arm())


@pytest.mark.parametrize(
    "span",
    [
        "Since this is our first time speaking",
        "Since this is our first time talking",
        "you are a new student",
    ],
)
def test_the_clean_arm_guards_every_false_positive_ever_recorded(span):
    """Runs 39 and 145 of the voice batch, and the Addendum 3 stage-two rejection.

    Three accusations in 400 runs, all three wrong, all three this shape.
    """
    assert any(span in specimen.response for specimen in clean_arm())


def test_every_specimen_says_why_it_is_what_it_is():
    """Ground truth a human asserted. A specimen with no stated reason is a guess."""
    assert all(specimen.why.strip() for specimen in SPECIMENS)


def test_specimen_names_are_unique():
    assert len({specimen.name for specimen in SPECIMENS}) == len(SPECIMENS)


def test_a_specimen_is_frozen():
    """Ground truth that a run can mutate is not ground truth."""
    with pytest.raises(Exception):
        FABRICATES.fabricates = False


# --- Running the fixture ----------------------------------------------------


async def test_every_specimen_is_judged_n_times():
    """`temperature=0` and this judge still varies, so once is not a measurement."""

    async def run():
        return await calibrate([FABRICATES, CLEAN], n=3, generate=lambda p: json_verdict("CLEAN"))

    assert len((await run()).graded) == 6


async def test_the_calibration_asks_the_shipped_rubric():
    """Measuring a re-implementation would measure a copy, not the instrument."""
    from evals.cases import EMPTY_MEMORY_CONFABULATION

    seen = []

    def capture(prompt):
        seen.append(prompt)
        return json_verdict("CLEAN")

    await calibrate([CLEAN], n=1, generate=capture)

    assert EMPTY_MEMORY_CONFABULATION.rubric in seen[0]


async def test_the_calibration_runs_the_real_second_stage():
    """A fabrication convicted at stage one and dropped at stage two.

    Only the shipped `judge()` produces this, so seeing OVERTURNED here is the
    proof that calibration did not grade the response itself.
    """

    def two_stage(prompt):
        if "verdict" not in prompt:
            return "NO"
        return json_verdict("FABRICATED", "Last time you struggled with limits.")

    result = await calibrate([FABRICATES], n=1, generate=two_stage)

    assert result.overturned == 1


async def test_a_judge_that_never_notices_reports_false_negatives():
    """The measurement, on a judge deliberately built to fail the way we fear."""
    result = await calibrate([FABRICATES], n=4, generate=lambda p: json_verdict("CLEAN"))

    assert result.false_negatives == 4


async def test_a_judge_that_convicts_everything_reports_false_positives():
    """And this is why the clean arm exists: it is the only thing that catches this."""

    def always_guilty(prompt):
        if "verdict" not in prompt:
            return "YES"
        return json_verdict("FABRICATED", "I don't have any memory")

    result = await calibrate([CLEAN], n=2, generate=always_guilty)

    assert result.false_positives == 2


async def test_a_judge_outage_voids_rather_than_scoring():
    def explode(prompt):
        raise RuntimeError("Gemini is down")

    result = await calibrate([FABRICATES, CLEAN], n=1, generate=explode)

    assert result.voids == 2
    assert result.false_negatives == 0


# --- The report -------------------------------------------------------------


def graded(*pairs):
    return Calibration(
        graded=tuple(
            Graded(specimen=specimen, grade=g, run=Run(Outcome.CLEAN, text=specimen.response))
            for specimen, g in pairs
        )
    )


def test_the_counts_add_up():
    tally = graded(
        (FABRICATES, Grade.CAUGHT),
        (FABRICATES, Grade.MISSED),
        (FABRICATES, Grade.OVERTURNED),
        (CLEAN, Grade.CLEARED),
        (CLEAN, Grade.FALSE_POSITIVE),
        (CLEAN, Grade.VOID),
    )

    assert (tally.caught, tally.false_negatives, tally.overturned) == (1, 1, 1)
    assert (tally.cleared, tally.false_positives, tally.voids) == (1, 1, 1)


def test_the_report_names_all_four_numbers():
    """A reader must not have to know which count was omitted."""
    printed = render(graded((FABRICATES, Grade.CAUGHT), (CLEAN, Grade.CLEARED)))

    for label in ("false negative", "overturned", "false positive", "void"):
        assert label in printed.lower()


def test_the_report_states_the_size_of_each_arm():
    """A rate means nothing without the N behind it, in both directions."""
    printed = render(graded((FABRICATES, Grade.CAUGHT), (CLEAN, Grade.CLEARED)))

    assert "1" in printed
    assert "arm" in printed.lower()


def test_the_report_quotes_a_specimen_the_judge_missed():
    """A count of misses nobody can read is not reviewable."""
    printed = render(graded((FABRICATES, Grade.MISSED)))

    assert FABRICATES.name in printed


def test_an_empty_calibration_reports_nothing_rather_than_zero_rates():
    """0 false negatives out of 0 specimens is not a clean bill of health."""
    assert "No specimens" in render(Calibration(graded=()))


# --- The exit code ----------------------------------------------------------


def test_a_perfectly_calibrated_judge_exits_zero():
    assert exit_code(graded((FABRICATES, Grade.CAUGHT), (CLEAN, Grade.CLEARED))) == 0


def test_a_false_negative_exits_non_zero():
    """A judge that misses is an instrument every green run downstream depends on."""
    assert exit_code(graded((FABRICATES, Grade.MISSED), (CLEAN, Grade.CLEARED))) != 0


def test_an_overturn_exits_non_zero():
    assert exit_code(graded((FABRICATES, Grade.OVERTURNED), (CLEAN, Grade.CLEARED))) != 0


def test_a_false_positive_exits_non_zero():
    assert exit_code(graded((FABRICATES, Grade.CAUGHT), (CLEAN, Grade.FALSE_POSITIVE))) != 0


def test_a_void_exits_non_zero():
    """Same rule the case report already uses: not knowing is not passing."""
    assert exit_code(graded((FABRICATES, Grade.VOID), (CLEAN, Grade.CLEARED))) != 0


def test_a_calibration_that_never_ran_exits_non_zero():
    assert exit_code(Calibration(graded=())) != 0


# --- The entry point --------------------------------------------------------


FAKE_CONFIG = JudgeConfig(google_api_key="unused")


@pytest.fixture
def offline_calibration(monkeypatch):
    """`python -m evals --calibrate-judge` with the network replaced.

    `sign_in` is booby-trapped rather than stubbed: this fixture exists to prove
    The calibration reaches no deployment, and a stub that quietly succeeded
    would prove the opposite while looking the same.
    """
    calls = []

    def refuse_to_sign_in(*args, **kwargs):
        raise AssertionError("the calibration fixture must not touch the deployment")

    def stub_judge(prompt):
        calls.append(prompt)
        return json_verdict("CLEAN")

    monkeypatch.setattr(entry, "sign_in", refuse_to_sign_in)
    monkeypatch.setattr(entry, "load_judge_config", lambda: FAKE_CONFIG)
    monkeypatch.setattr(judge_module, "_generate", stub_judge)
    return calls


def test_calibrating_reaches_no_deployment(offline_calibration, capsys):
    """No tutor, no precondition, no stack -- the responses are fixed text.

    That is the whole reason this is a fixture and not another live batch, and
    The reason it can run when no deployment is up.
    """
    entry.main(["--calibrate-judge"])

    assert offline_calibration


def test_a_judge_that_answers_clean_to_everything_exits_non_zero(offline_calibration, capsys):
    """The pure false-negative machine -- exactly the instrument we fear having.

    It never fires, so it misses every specimen in the fabricating arm, and the
    exit code must not read that as a clean bill of health.
    """
    assert entry.main(["--calibrate-judge"]) == 1


def test_each_specimen_is_judged_three_times_by_default(offline_calibration, capsys):
    """The spec's default. A CLEAN verdict costs one call, so calls == judgings."""
    entry.main(["--calibrate-judge"])

    assert len(offline_calibration) == 3 * len(SPECIMENS)


def test_the_repeat_count_is_still_settable(offline_calibration, capsys):
    entry.main(["--calibrate-judge", "--n", "1"])

    assert len(offline_calibration) == len(SPECIMENS)


def test_calibrating_prints_the_table(offline_calibration, capsys):
    entry.main(["--calibrate-judge"])

    assert "false negatives" in capsys.readouterr().out


def test_the_ordinary_eval_run_still_defaults_to_twenty():
    """Calibration's default of 3 must not have quietly become everyone's default."""
    assert entry.DEFAULT_RUNS == 20


def test_calibrating_needs_no_deployment_credentials(monkeypatch, tmp_path, capsys):
    """The machine most likely to want this is the one with no deployment at all.

    Nothing here dials a deployment, so requiring one be named before grading a
    fixture of fixed text is a demand with nothing behind it. `chdir` to an empty
    directory so the project's own `.env` cannot satisfy it by accident.
    """
    for name in ("EVAL_BASE_URL", "EVAL_STUDENT_EMAIL", "EVAL_STUDENT_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "a-google-key")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(judge_module, "_generate", lambda prompt: json_verdict("CLEAN"))

    entry.main(["--calibrate-judge", "--n", "1"])

    assert "false negatives" in capsys.readouterr().out


def test_a_missing_key_is_refused_before_any_judging(monkeypatch):
    """One line naming what is absent, not a table of voids that looks like a result.

    `judge()` catches everything and returns VOID, so a key discovered missing
    mid-run would render a full report from a harness that never ran -- the
    harness lying in the safe-looking direction, in the one place it must not.
    """
    judged = []

    def refuse_config():
        raise ConfigMissing("GOOGLE_API_KEY is unset")

    monkeypatch.setattr(entry, "load_judge_config", refuse_config)
    monkeypatch.setattr(judge_module, "_generate", lambda p: judged.append(p))

    assert entry.main(["--calibrate-judge"]) == 2
    assert not judged
