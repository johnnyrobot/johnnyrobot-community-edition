"""
What every driver is, and the only thing they have in common.

A driver's whole job is: given a Student and a prompt, return what the tutor
said. It knows nothing about cases, thresholds, or the judge -- which is what
lets one case run on two surfaces without either surface knowing what is being
measured.
"""
from dataclasses import dataclass
from typing import Protocol


class DriverError(RuntimeError):
    """A driver could not obtain an answer, so the run establishes nothing.

    Distinct from "the tutor said something bad": a timeout, a refused
    connection, or a room that never admitted an agent are all reasons the run
    is void rather than failed. The runner catches this and records VOID.
    """


@dataclass(frozen=True)
class Student:
    """The eval Student, signed in.

    `student_id` is the PocketBase record id (the PocketBase identity contract), which is also the
    LiveKit participant identity -- api/routers/sessions.py:61 sets
    `token.with_identity(user_id)`. The voice driver needs it to tell its own
    transcriptions from the agent's.
    """

    student_id: str
    token: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


class Driver(Protocol):
    name: str

    async def ask(self, student: Student, prompt: str) -> str:
        """Ask the tutor one thing and return exactly what it said."""
        ...
