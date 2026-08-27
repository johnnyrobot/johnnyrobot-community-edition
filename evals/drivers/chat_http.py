"""
The text tutor, over the same HTTP the browser uses.

Nothing subtle here, which is the point: this driver exists so the interesting
driver (voice) has something to be compared against, and so a chat regression
is caught by the same case that catches a voice one.
"""
import httpx

from evals.drivers.base import DriverError, Student

# Generous: the route calls Mem0 twice and Gemini once, all serially, and a
# slow-but-successful answer is still an answer worth judging.
TIMEOUT_SECONDS = 120.0


class ChatHttpDriver:
    """Asks `POST /api/v1/chat/message` and returns the tutor's reply."""

    name = "chat"

    def __init__(self, client: httpx.AsyncClient, base_url: str):
        self._client = client
        self._base_url = base_url.rstrip("/")

    async def ask(self, student: Student, prompt: str) -> str:
        """One turn, with no history.

        `history` is empty on every run deliberately. Carrying it would let run
        N see run N-1's exchange and quietly defeat the empty-memory
        precondition through a different door than Mem0.
        """
        try:
            response = await self._client.post(
                f"{self._base_url}/api/v1/chat/message",
                headers=student.headers,
                json={"message": prompt, "history": []},
                timeout=TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as unreachable:
            raise DriverError(f"the chat route was unreachable: {unreachable}") from unreachable

        if response.status_code >= 400:
            raise DriverError(f"the chat route answered HTTP {response.status_code}")

        said = (response.json().get("response") or "").strip()
        if not said:
            # api/routers/chat.py substitutes an apology when Gemini returns
            # nothing. Judging an empty answer would record a CLEAN run that
            # The tutor never actually produced.
            raise DriverError("the chat route returned an empty response")
        return said
