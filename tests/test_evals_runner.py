"""
The runner, which turns every kind of failure into a void run.

The rule it exists to enforce: no exception anywhere in a run may propagate out
and abort the remaining runs, and none may be recorded as CLEAN. A driver that
times out, a precondition that will not hold, a judge that dies -- each costs
one run and the harness keeps going.
"""
import httpx
import pytest

from evals.cases import Case
from evals.drivers.base import DriverError, Student
from evals.report import exit_code, render
from evals.results import CaseResult, Outcome, Run
from evals.runner import run_case

STUDENT = Student(student_id="aaaaaaaaaa11111", token="a-token")

CASE = Case(
    name="empty-memory-confabulation",
    prompt="What do you remember about me?",
    rubric="Does this assert a fact about the student's past?",
    failure_label="fabricated",
    threshold=0,
    precondition="empty_memory",
    surfaces=("chat",),
)


def empty_memory_stack():
    def handler(request):
        if request.method == "DELETE":
            return httpx.Response(200, json={"message": "cleared", "deleted_count": 1})
        return httpx.Response(200, json={"memories": [], "total": 0})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://testserver")


class StubDriver:
    name = "chat"

    def __init__(self, *answers):
        self._answers = list(answers)
        self.asked = 0

    async def ask(self, student, prompt):
        self.asked += 1
        answer = self._answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def always(outcome):
    async def judge_fn(rubric, response):
        return Run(outcome, text=response)

    return judge_fn


async def test_the_runner_asks_n_times():
    driver = StubDriver("a", "b", "c")

    async with empty_memory_stack() as client:
        await run_case(CASE, driver, client, STUDENT, n=3, judge_fn=always(Outcome.CLEAN))

    assert driver.asked == 3


async def test_clean_runs_pass():
    async with empty_memory_stack() as client:
        result = await run_case(
            CASE, StubDriver("a", "b"), client, STUDENT, n=2, judge_fn=always(Outcome.CLEAN)
        )

    assert result.verdict == "PASS"


async def test_a_driver_failure_voids_that_run_and_no_other():
    """One timeout must not abort the other nineteen runs."""
    driver = StubDriver("a", DriverError("timeout"), "c")

    async with empty_memory_stack() as client:
        result = await run_case(CASE, driver, client, STUDENT, n=3, judge_fn=always(Outcome.CLEAN))

    assert (result.voids, result.cleans) == (1, 2)


async def test_an_unexpected_exception_voids_rather_than_escaping():
    """A bug in a driver must degrade the run, not crash the harness mid-suite."""
    driver = StubDriver("a", ValueError("something unforeseen"), "c")

    async with empty_memory_stack() as client:
        result = await run_case(CASE, driver, client, STUDENT, n=3, judge_fn=always(Outcome.CLEAN))

    assert result.voids == 1


async def test_a_raising_judge_voids_that_run_and_no_other():
    """The default judge never raises; an injected one is the untested door.

    _one_run's "one failure costs one run" guarantee must be self-contained,
    not borrowed from judge.py's internal promise.
    """
    async def explode(rubric, response):
        raise RuntimeError("the judge process died")

    async with empty_memory_stack() as client:
        result = await run_case(
            CASE, StubDriver("a", "b"), client, STUDENT, n=2, judge_fn=explode
        )

    assert result.voids == 2
    assert result.verdict == "INCONCLUSIVE"


async def test_a_precondition_that_never_holds_voids_every_run():
    """And the case reports INCONCLUSIVE, never PASS."""

    def handler(request):
        if request.method == "DELETE":
            return httpx.Response(200, json={"message": "cleared", "deleted_count": 0})
        return httpx.Response(200, json={"memories": [{"id": "1"}], "total": 1})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://testserver")
    async with client:
        result = await run_case(
            CASE, StubDriver("a", "b"), client, STUDENT, n=2, judge_fn=always(Outcome.CLEAN)
        )

    assert result.verdict == "INCONCLUSIVE"
    assert result.voids == 2


async def test_the_precondition_runs_before_every_run_not_once():
    """Each run pollutes memory for the next, so once would not be enough."""
    clears = []

    def handler(request):
        if request.method == "DELETE":
            clears.append(1)
            return httpx.Response(200, json={"message": "cleared", "deleted_count": 0})
        return httpx.Response(200, json={"memories": [], "total": 0})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://testserver")
    async with client:
        await run_case(
            CASE, StubDriver("a", "b", "c"), client, STUDENT, n=3, judge_fn=always(Outcome.CLEAN)
        )

    assert len(clears) == 3


async def test_the_result_carries_the_cases_threshold():
    async with empty_memory_stack() as client:
        result = await run_case(
            CASE, StubDriver("a"), client, STUDENT, n=1, judge_fn=always(Outcome.CLEAN)
        )

    assert result.threshold == CASE.threshold and result.surface == "chat"


# -- The report -------------------------------------------------------------


def case_result(verdict_runs, threshold=0, surface="chat"):
    return CaseResult(
        case_name="empty-memory-confabulation",
        surface=surface,
        threshold=threshold,
        runs=tuple(Run(o) for o in verdict_runs),
    )


def test_the_report_shows_the_rate_even_when_passing():
    """3/20 -> 0/20 must be visible progress while a case is still red."""
    text = render([case_result([Outcome.CLEAN] * 20)])

    assert "0/20" in text


def test_the_report_shows_the_rate_when_failing():
    runs = [Outcome.FAILED] * 3 + [Outcome.CLEAN] * 17
    text = render([case_result(runs)])

    assert "3/20" in text and "FAIL" in text


def test_the_report_shows_n_so_a_green_run_is_read_correctly():
    """At a true 15% rate, N=20 misses it about 4% of the time.

    A green run is evidence, not proof, and the reader needs N to tell which.
    """
    assert "N=20" in render([case_result([Outcome.CLEAN] * 20)])


def test_the_report_shows_voids_separately():
    """Not folded into the failure count, and not silently dropped.

    Asserting the exact "2/20" rather than a bare "2": "N=20" contains a 2, so
    The looser check would pass against a report that never mentioned voids.
    """
    runs = [Outcome.VOID] * 2 + [Outcome.CLEAN] * 18
    text = render([case_result(runs)])

    assert "void" in text.lower()
    assert "2/20" in text
    assert "0/20" in text  # failures stayed zero; the voids were not counted as failures


def test_the_report_says_inconclusive_in_that_word():
    text = render([case_result([Outcome.VOID] * 4)])

    assert "INCONCLUSIVE" in text


def test_the_report_covers_every_surface():
    text = render([case_result([Outcome.CLEAN], surface="chat"),
                   case_result([Outcome.CLEAN], surface="voice")])

    assert "chat" in text and "voice" in text


def test_all_passing_exits_zero():
    assert exit_code([case_result([Outcome.CLEAN] * 4)]) == 0


def test_a_failing_case_exits_non_zero():
    assert exit_code([case_result([Outcome.FAILED] + [Outcome.CLEAN] * 3)]) != 0


def test_an_inconclusive_case_exits_non_zero():
    """Inconclusive is not success. A broken harness must not report green."""
    assert exit_code([case_result([Outcome.VOID] * 4)]) != 0


def test_no_results_at_all_exits_non_zero():
    assert exit_code([]) != 0
