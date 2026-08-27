"""
N runs of one case on one surface.

**The rule this module enforces:** no failure anywhere in a run may escape and
abort the remaining runs, and none may be recorded as CLEAN. A driver that
times out, a precondition that will not hold, a judge that dies -- each costs
exactly one run, is recorded as VOID with its reason, and the harness carries
on. Twenty runs are worth having partly because any one of them can go wrong.

Runs are serial, deliberately. The precondition is per-Student state on a
single shared eval Student, so two concurrent runs would clear each other's
memory and both would be measuring something other than what they think.
"""
import logging

import httpx

from evals.cases import Case
from evals.drivers.base import Student
from evals.judge import judge as default_judge
from evals.precondition import ensure
from evals.results import CaseResult, Outcome, Run

logger = logging.getLogger(__name__)


async def run_case(
    case: Case,
    driver,
    client: httpx.AsyncClient,
    student: Student,
    n: int,
    judge_fn=None,
) -> CaseResult:
    """Ask one case n times on one surface and tally what came back."""
    adjudicate = judge_fn or default_judge
    runs: list[Run] = []

    for attempt in range(1, n + 1):
        runs.append(await _one_run(case, driver, client, student, adjudicate, attempt, n))

    return CaseResult(
        case_name=case.name, surface=driver.name, threshold=case.threshold, runs=tuple(runs)
    )


async def _one_run(case, driver, client, student, adjudicate, attempt, n) -> Run:
    """One precondition, one question, one verdict. Never raises."""
    try:
        # Before every run, not once: each run writes to Mem0, so run N would
        # otherwise start with run N-1's exchange already remembered.
        await ensure(case.precondition, client, student)
    except Exception as setup_err:
        logger.warning(f"[{driver.name} {attempt}/{n}] void: {setup_err}")
        return Run(Outcome.VOID, reason=str(setup_err))

    try:
        said = await driver.ask(student, case.prompt)
    except Exception as ask_err:
        # Deliberately broad. A DriverError is expected; anything else is a bug
        # in a driver, and a bug in a driver should cost one run rather than
        # abort a twenty-minute suite on its nineteenth.
        logger.warning(f"[{driver.name} {attempt}/{n}] void: {ask_err}")
        return Run(Outcome.VOID, reason=f"the driver failed: {ask_err}")

    try:
        verdict = await adjudicate(case.rubric, said)
    except Exception as judge_err:
        # judge.py promises never to raise, and the default path keeps that
        # promise. An injected judge_fn is not bound by it, and this module's
        # guarantee -- one failure costs one run -- must not depend on another
        # module's internals to hold.
        logger.warning(f"[{driver.name} {attempt}/{n}] void: {judge_err}")
        return Run(Outcome.VOID, text=said, reason=f"the judge failed: {judge_err}")

    logger.info(f"[{driver.name} {attempt}/{n}] {verdict.outcome.value}")
    return verdict
