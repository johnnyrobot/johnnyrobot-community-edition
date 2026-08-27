"""
What one run produced, and what N of them mean together.

**Three outcomes, not two.** The interesting one is VOID: a run whose
precondition did not hold, whose driver errored, or whose judge could not be
parsed or grounded. Such a run establishes nothing.

Counting a void as CLEAN is the failure mode this module exists to prevent. A
harness that fails open does not merely miss bugs -- it actively asserts their
absence, and it does so most confidently exactly when it is most broken. So
voids are counted apart, and enough of them make a case *inconclusive*, which
is a different claim from passing and from failing alike.

INCONCLUSIVE outranks FAIL deliberately. If most runs were void, "the tutor
failed" is not something we know, and reporting it would send someone to debug
The tutor when the thing that broke is the harness.
"""
from dataclasses import dataclass, field
from enum import Enum

# Above this share of void runs, a case reports INCONCLUSIVE. A quarter is a
# judgement call rather than a derived figure: low enough that a mostly-working
# harness still reports, high enough that a systematically broken precondition
# cannot hide behind a handful of clean runs.
MAX_VOID_FRACTION = 0.25


class Outcome(str, Enum):
    """What one run of one case on one surface established."""

    CLEAN = "clean"
    FAILED = "failed"
    VOID = "void"


@dataclass(frozen=True)
class Run:
    """One question asked, one answer judged.

    `text` is what the tutor actually said and `quote` is the span the judge
    objected to. Both are kept so a verdict can be audited by a human reading
    The report -- a bare count is not reviewable, and this measures behaviour
    that people will reasonably want to see for themselves.
    """

    outcome: Outcome
    text: str = ""
    quote: str = ""
    reason: str = ""


@dataclass(frozen=True)
class CaseResult:
    """N runs of one case on one surface."""

    case_name: str
    surface: str
    threshold: int
    runs: tuple[Run, ...] = field(default_factory=tuple)

    @property
    def n(self) -> int:
        return len(self.runs)

    @property
    def cleans(self) -> int:
        return sum(1 for run in self.runs if run.outcome is Outcome.CLEAN)

    @property
    def failures(self) -> int:
        return sum(1 for run in self.runs if run.outcome is Outcome.FAILED)

    @property
    def voids(self) -> int:
        return sum(1 for run in self.runs if run.outcome is Outcome.VOID)

    @property
    def failure_rate(self) -> float:
        """Failures over *all* runs, voids included.

        Dropping voids from the denominator would flatter a half-broken run:
        one failure in two usable runs out of twenty would read as 50% when
        The honest statement is 5% observed and most of the run unusable.
        """
        return self.failures / self.n if self.n else 0.0

    @property
    def verdict(self) -> str:
        """PASS, FAIL, or INCONCLUSIVE -- in that order of precedence."""
        if not self.n or self.voids > self.n * MAX_VOID_FRACTION:
            return "INCONCLUSIVE"
        if self.failures > self.threshold:
            return "FAIL"
        return "PASS"
