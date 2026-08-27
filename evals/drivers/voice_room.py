"""
The voice tutor, driven with text instead of audio.

The agent already accepts text and already emits it, so no microphone is
needed:

  - `RoomInputOptions.text_enabled` defaults to NOT_GIVEN, and
    `room_io/types.py:192` treats anything but an explicit False as enabled, so
    The agent registers a text-stream handler on `lk.chat`.
  - Replies are published as transcriptions on `lk.transcription`.
  - Both topic names are `livekit/agents/types.py:68-69`.

**Why not audio.** Synthesizing speech and recognising the reply would add TTS,
STT, and turn detection to the harness. Each has its own failure modes, none of
them has anything to do with whether the tutor invents a Student's history, and
every one of them would surface as flake indistinguishable from the behaviour
being measured.

**Knowing whose text is whose.** Every transcription is streamed with its
speaker's identity (`voice/room_io/_output.py:487-489`), and the Student's
LiveKit identity is their PocketBase record id
(`api/routers/sessions.py:61`). So the driver ignores anything attributed to
itself and keeps the rest.
"""
import asyncio
import contextlib
import logging
import time

import httpx

from evals.drivers.base import DriverError, Student

logger = logging.getLogger(__name__)

TOPIC_CHAT = "lk.chat"
TOPIC_TRANSCRIPTION = "lk.transcription"

# The agent session publishes its own readiness as a participant attribute
# (`livekit/agents/types.py`). "initializing" means it has joined but has not
# finished starting; the states below mean it can take input.
AGENT_STATE_ATTRIBUTE = "lk.agent.state"
READY_STATES = frozenset({"listening", "thinking", "speaking"})

# Quiet interval after which a reply is considered finished. Tuned rather than
# derived: it trades latency against the risk of cutting a reply at a pause. If
# voice runs start voiding on timeout or returning clipped text, this is the
# first number to change.
SETTLE_SECONDS = 2.0

# Hard bound from the moment the prompt is sent. A timeout voids the run rather
# than judging a partial answer, because a truncated reply is not what the
# tutor said.
TIMEOUT_SECONDS = 60.0

# How long to wait for the agent to join. The agent is a separate process that
# LiveKit dispatches on room creation, so it is never instant.
AGENT_JOIN_SECONDS = 30.0


class Reply:
    """Accumulates the agent's transcriptions and decides when it has stopped.

    Separated from the room plumbing because this is the only part with
    decisions in it, and the only part testable without a LiveKit server.
    """

    def __init__(self, own_identity: str, settle: float = SETTLE_SECONDS, clock=time.monotonic):
        self._own_identity = own_identity
        self._settle = settle
        self._clock = clock
        self._segments: list[str] = []
        self._last_at: float | None = None

    def accept(self, identity: str, text: str) -> None:
        """Take one transcription segment, if it is the agent's and says anything."""
        if identity == self._own_identity:
            # A room transcribes the Student too. Ours must neither be judged
            # nor allowed to hold a finished reply open.
            return
        if not text or not text.strip():
            return
        self._segments.append(text.strip())
        self._last_at = self._clock()

    def settled(self) -> bool:
        """True once the agent has said something and then gone quiet."""
        if self._last_at is None:
            return False
        return (self._clock() - self._last_at) >= self._settle

    def text(self) -> str:
        return " ".join(self._segments)


class VoiceRoomDriver:
    """Joins a real LiveKit room, sends text, and reads the agent's transcript."""

    name = "voice"

    def __init__(self, client: httpx.AsyncClient, base_url: str):
        self._client = client
        self._base_url = base_url.rstrip("/")

    async def _token(self, student: Student) -> tuple[str, str, str]:
        """One call supplies the token, the LiveKit URL, and the room name alike."""
        try:
            response = await self._client.post(
                f"{self._base_url}/api/v1/session/token", headers=student.headers, timeout=30.0
            )
        except httpx.HTTPError as unreachable:
            raise DriverError(f"the session token route was unreachable: {unreachable}") from unreachable

        if response.status_code >= 400:
            raise DriverError(f"the session token route answered HTTP {response.status_code}")

        body = response.json()
        return body["token"], body["url"], body["room_name"]

    async def ask(self, student: Student, prompt: str) -> str:
        """One turn in a fresh room, then leave and end the Tutor Session.

        A fresh room every run, because a Tutor Session carries its own history
        and reusing one would let run N see run N-1 -- the same leak the chat
        driver avoids by sending an empty history.

        Ending the session is best-effort and does not fully close that leak:
        agent.py writes the whole Tutor Session history to Mem0 in a shutdown
        callback that fires after the room disconnects, so run N's write can
        still land after run N+1's clear-and-verify has already passed.
        """
        from livekit import rtc

        token, url, room_name = await self._token(student)
        reply = Reply(own_identity=student.student_id)
        room = rtc.Room()
        readers: set[asyncio.Task] = set()

        async def drain(reader, identity: str) -> None:
            try:
                reply.accept(identity, await reader.read_all())
            except Exception as stream_err:  # a dropped stream costs one segment
                logger.warning(f"A transcription stream failed: {stream_err}")

        def on_transcription(reader, participant_identity: str) -> None:
            # Registered before connecting: a handler attached afterwards can
            # miss the first segment of a fast reply.
            task = asyncio.create_task(drain(reader, participant_identity))
            readers.add(task)
            task.add_done_callback(readers.discard)

        room.register_text_stream_handler(TOPIC_TRANSCRIPTION, on_transcription)

        try:
            try:
                await asyncio.wait_for(room.connect(url, token), timeout=AGENT_JOIN_SECONDS)
            except Exception as connect_err:
                # Broad on purpose, and `asyncio.TimeoutError` needs no separate
                # arm -- it is an `Exception` subclass, so naming both would be
                # redundant rather than more careful.
                raise DriverError(f"could not join the LiveKit room: {connect_err}") from connect_err

            await self._await_agent(room)
            await room.local_participant.send_text(prompt, topic=TOPIC_CHAT)
            await self._await_reply(reply, readers)

            said = reply.text()
            if not said:
                raise DriverError("the voice agent said nothing")
            return said
        finally:
            for task in list(readers):
                task.cancel()
            if readers:
                await asyncio.gather(*readers, return_exceptions=True)
            with contextlib.suppress(Exception):
                await room.disconnect()
            # Best-effort: agent.py writes the whole Tutor Session history to
            # Mem0 in a shutdown callback that fires after the room
            # disconnects, so ending the session here narrows but cannot close
            # The window in which run N's write lands after run N+1's
            # clear-and-verify has already passed.
            with contextlib.suppress(Exception):
                await self._client.post(
                    f"{self._base_url}/api/v1/session/end",
                    headers=student.headers,
                    json={"room_name": room_name},
                    timeout=30.0,
                )

    @staticmethod
    async def _await_agent(room) -> None:
        """Wait for the agent to be *ready*, not merely present.

        A participant appearing is not enough, and the difference is not
        cosmetic. The agent registers its `lk.chat` text handler inside
        `AgentSession.start()`, which finishes some time after it joins the
        room -- and LiveKit does not buffer a text stream for a handler that
        is not there yet. A prompt sent on arrival is dropped silently, the
        run times out sixty seconds later, and the failure is indistinguishable
        from an agent that is broken or absent.

        `lk.agent.state` is the signal to wait on: the session publishes it as
        a participant attribute, "initializing" while it is still starting and
        "listening" once it can actually take input.
        """
        deadline = time.monotonic() + AGENT_JOIN_SECONDS
        while time.monotonic() < deadline:
            for participant in room.remote_participants.values():
                if (participant.attributes or {}).get(AGENT_STATE_ATTRIBUTE) in READY_STATES:
                    return
            await asyncio.sleep(0.25)
        raise DriverError(
            f"no agent became ready in the room within {AGENT_JOIN_SECONDS}s; "
            "is the agent container running?"
        )

    @staticmethod
    async def _await_reply(reply: Reply, readers: set) -> None:
        """Wait for the reply to settle, or give up and void the run.

        `readers` must be empty as well as `reply` settled. The settle clock
        only advances when a stream *completes*, so a segment still streaming
        is invisible to `Reply` -- and without this, a closed segment plus one
        in flight looks finished, the `finally` cancels the live reader, and a
        truncated answer gets judged as if it were the whole reply.
        """
        deadline = time.monotonic() + TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if reply.settled() and not readers:
                return
            await asyncio.sleep(0.1)
        if not reply.text():
            raise DriverError(f"the voice agent did not answer within {TIMEOUT_SECONDS}s")
        raise DriverError(
            f"the voice agent was still speaking after {TIMEOUT_SECONDS}s; "
            "a truncated reply is not what it said"
        )
