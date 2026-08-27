"""
What a case's precondition name means, and the refusal when it does not hold.

Separate from `stack.py` because these are different jobs: that module knows
how to call the deployment, this one knows what state a case requires before
its rubric means anything.

`empty_memory` is not an assumption, it is an action followed by a check. Both
surfaces write to Mem0 during a run -- api/routers/chat.py:150 adds the
incoming message before searching, and agent.py's shutdown callback writes the
whole session history -- so every run starts by undoing the last one.
"""
import httpx

from evals.drivers.base import Student
from evals.stack import clear_memory, memory_total


class PreconditionFailed(RuntimeError):
    """The state a case requires could not be established, so the run is void.

    Not a failure of the tutor. A tutor that genuinely had memories would be
    right to mention them, so judging one against the empty-memory rubric would
    be measuring the harness's own broken setup and blaming the tutor for it.
    """


async def ensure(name: str, client: httpx.AsyncClient, student: Student) -> None:
    """Put the deployment into the state this case needs, or refuse."""
    if name == "none":
        return

    if name != "empty_memory":
        raise PreconditionFailed(
            f"Unknown precondition {name!r}. A case naming one that does not exist "
            "would otherwise run against whatever state happened to be there."
        )

    await clear_memory(client, student)

    # The verify is what makes this a precondition rather than a hope. A clear
    # that reported success while Mem0 was degraded would leave memories in
    # place, and the run would silently measure something else.
    remaining = await memory_total(client, student)
    if remaining:
        raise PreconditionFailed(
            f"Student Memory still holds {remaining} memories after being cleared, "
            "so this run cannot establish anything about an empty memory."
        )
