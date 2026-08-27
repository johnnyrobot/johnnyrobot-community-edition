"""
The rate table.

Two things it must always show. **The rate**, passing or failing alike, so that
3/20 -> 0/20 reads as progress while a case is still red. And **N**, because N
bounds what a green run means: at a true 15% failure rate, twenty runs miss it
entirely about four percent of the time. A green run is evidence, not proof,
and the report should not let a reader forget which one they are holding.
"""
import textwrap

from evals.results import MAX_VOID_FRACTION, CaseResult, Outcome


def render(results: list[CaseResult]) -> str:
    """One block per case per surface."""
    if not results:
        return "No cases ran.\n"

    lines: list[str] = []
    for result in results:
        lines.append(
            f"case: {result.case_name}   surface: {result.surface}   N={result.n}"
        )
        lines.append(
            f"  failed       {result.failures}/{result.n}   ({result.failure_rate:.0%})"
        )
        lines.append(f"  void         {result.voids}/{result.n}")
        lines.append(f"  threshold    {result.threshold}                {result.verdict}")
        if result.verdict == "INCONCLUSIVE":
            lines.append(
                f"  -> more than {MAX_VOID_FRACTION:.0%} of runs established nothing, so this "
                "says nothing about the tutor."
            )

        # Every failure, quoted. A count alone is not reviewable: a rate of
        # 1/20 is exactly the shape a human has to read for themselves before
        # believing it, and re-running to see what happened costs another
        # twenty runs at the rate that made it interesting.
        for index, run in enumerate(result.runs, start=1):
            if run.outcome is not Outcome.FAILED:
                continue
            lines.append(f"  run {index} — the judge objected to:")
            lines.append(f"      {run.quote!r}")
            lines.append("    in:")
            for chunk in textwrap.wrap(run.text, width=88)[:8]:
                lines.append(f"      {chunk}")

        lines.append("")

    lines.append(
        "N bounds what a green run means: at a true 15% rate, 20 runs miss it "
        "entirely about 4% of the time."
    )
    return "\n".join(lines) + "\n"


def exit_code(results: list[CaseResult]) -> int:
    """0 only if every case passed.

    INCONCLUSIVE exits non-zero deliberately. It is not success, and a harness
    whose preconditions were broken all run must not report green.
    """
    if not results:
        return 1
    return 0 if all(result.verdict == "PASS" for result in results) else 1
