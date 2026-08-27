"""
What one leg of a smoke run established.

Three verdicts, not two, for the same reason `evals/results.py` has three
outcomes: a leg that could not run has established nothing, and the two ways of
reporting it wrongly are both bad in specific ways. Counted as a pass, a
harness whose auth never worked reports green. Counted as a failure, an
operator gets sent to debug a tutor when the thing that broke was their network.

`Verdict` is deliberately not a reuse of `evals.results.Outcome`. They share a
philosophy but not their meanings -- `Outcome.CLEAN` claims the tutor asserted
nothing false, which this harness never claims about anything -- and one enum
serving two vocabularies would make both harder to read.
"""
from dataclasses import dataclass
from enum import Enum


class Verdict(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    VOID = "void"


@dataclass(frozen=True)
class LegResult:
    """One leg, run once.

    `detail` is in an operator's words, not a stack trace: this report is read
    under time pressure by someone deciding what to fix. `screenshot` is a path
    when something went wrong, because "voice failed" is far less actionable
    than a picture of the page.
    """

    name: str
    verdict: Verdict
    detail: str = ""
    screenshot: str = ""


@dataclass(frozen=True)
class SmokeRun:
    """Every leg of one invocation."""

    legs: tuple[LegResult, ...] = ()

    def _count(self, wanted: Verdict) -> int:
        return sum(1 for leg in self.legs if leg.verdict is wanted)

    @property
    def n(self) -> int:
        return len(self.legs)

    @property
    def passes(self) -> int:
        return self._count(Verdict.PASS)

    @property
    def failures(self) -> int:
        return self._count(Verdict.FAIL)

    @property
    def voids(self) -> int:
        return self._count(Verdict.VOID)
