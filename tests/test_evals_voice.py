"""
The voice driver's reply-settling logic, without a LiveKit server.

The room plumbing is thin and needs a real server to mean anything. The part
with actual decisions in it -- which transcriptions are the agent's, and when
The answer has stopped arriving -- is separated into `Reply` so it can be
tested against a fake clock.

Text in, text out, no audio. The agent registers a text handler on `lk.chat`
by default (RoomInputOptions.text_enabled; room_io/types.py:192) and publishes
its replies on `lk.transcription` (livekit/agents/types.py:68-69). Synthesizing
audio would add TTS, STT, and turn detection -- three sources of flake that
have nothing to do with whether the tutor invents a history.
"""
import pytest

from evals.drivers.voice_room import SETTLE_SECONDS, Reply

OURS = "aaaaaaaaaa11111"
AGENT = "agent-7f3a"


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def reply(clock=None):
    return Reply(own_identity=OURS, settle=SETTLE_SECONDS, clock=clock or FakeClock())


def test_the_agents_text_is_collected():
    answer = reply()
    answer.accept(AGENT, "I have no memory of you.")

    assert answer.text() == "I have no memory of you."


def test_our_own_transcription_is_ignored():
    """A room transcribes the Student too. Judging our own prompt would be absurd.

    api/routers/sessions.py:61 sets the participant identity to the Student's
    record id, and _output.py streams each transcription with its speaker's
    identity, so the two are distinguishable.
    """
    answer = reply()
    answer.accept(OURS, "What do you remember about me?")

    assert answer.text() == ""


def test_segments_are_joined_in_arrival_order():
    answer = reply()
    answer.accept(AGENT, "I have no memory.")
    answer.accept(AGENT, "What would you like to work on?")

    assert answer.text() == "I have no memory. What would you like to work on?"


def test_a_reply_is_not_settled_before_anything_arrives():
    """An empty room must not look like a finished answer."""
    assert reply().settled() is False


def test_a_reply_is_not_settled_immediately_after_a_segment():
    clock = FakeClock()
    answer = reply(clock)
    answer.accept(AGENT, "I have no memory.")

    assert answer.settled() is False


def test_a_reply_settles_after_the_quiet_interval():
    clock = FakeClock()
    answer = reply(clock)
    answer.accept(AGENT, "I have no memory.")
    clock.advance(SETTLE_SECONDS + 0.1)

    assert answer.settled() is True


def test_a_new_segment_restarts_the_interval():
    """A pause mid-answer must not be mistaken for the end of it."""
    clock = FakeClock()
    answer = reply(clock)
    answer.accept(AGENT, "I have no memory.")
    clock.advance(SETTLE_SECONDS - 0.1)
    answer.accept(AGENT, "What would you like to work on?")
    clock.advance(SETTLE_SECONDS - 0.1)

    assert answer.settled() is False


def test_our_own_transcription_does_not_restart_the_interval():
    """Otherwise the Student's own captions could hold a finished reply open."""
    clock = FakeClock()
    answer = reply(clock)
    answer.accept(AGENT, "I have no memory.")
    clock.advance(SETTLE_SECONDS - 0.1)
    answer.accept(OURS, "some caption of ours")
    clock.advance(0.2)

    assert answer.settled() is True


def test_blank_segments_are_ignored():
    """A whitespace-only chunk is not the agent saying something."""
    answer = reply()
    answer.accept(AGENT, "   ")

    assert answer.text() == "" and answer.settled() is False


def test_the_settle_interval_is_two_seconds():
    """Tuned, not derived -- the first thing to change if voice runs void."""
    assert SETTLE_SECONDS == 2.0


async def test_await_reply_waits_for_a_segment_still_arriving():
    """The settle clock only advances when a stream completes.

    A closed segment plus one still streaming would otherwise look finished,
    and the in-flight reader is cancelled in the `finally` -- returning a
    truncated answer as though it were the whole reply.
    """
    import asyncio

    from evals.drivers.voice_room import VoiceRoomDriver

    clock = FakeClock()
    answer = reply(clock)
    answer.accept(AGENT, "first segment.")
    clock.advance(SETTLE_SECONDS + 0.1)
    assert answer.settled() is True  # settled, yet a reader is still in flight

    readers = set()
    in_flight = asyncio.create_task(asyncio.sleep(0.3))
    readers.add(in_flight)
    in_flight.add_done_callback(readers.discard)

    await VoiceRoomDriver._await_reply(answer, readers)

    assert in_flight.done(), "returned while a segment was still arriving"


# -- Waiting for the agent to be ready, not merely present -------------------


class FakeParticipant:
    def __init__(self, state=None):
        self.attributes = {} if state is None else {"lk.agent.state": state}


class FakeRoom:
    """A room whose agent takes a moment to finish starting."""

    def __init__(self, states):
        self._states = list(states)
        self.remote_participants = {}

    def tick(self):
        if self._states:
            state = self._states.pop(0)
            self.remote_participants = {} if state is None else {"agent": FakeParticipant(state)}


async def test_a_present_but_initializing_agent_is_not_ready():
    """The race this exists to close.

    The agent registers its lk.chat handler inside session.start(), after it
    joins. Text sent while it is still "initializing" is dropped silently and
    The run times out looking like a broken agent.
    """
    import asyncio

    from evals.drivers.voice_room import VoiceRoomDriver

    room = FakeRoom([None, "initializing", "initializing", "listening"])

    async def drive():
        while True:
            room.tick()
            await asyncio.sleep(0.01)

    driver = asyncio.create_task(drive())
    try:
        await VoiceRoomDriver._await_agent(room)
    finally:
        driver.cancel()

    assert room.remote_participants["agent"].attributes["lk.agent.state"] == "listening"


async def test_an_agent_that_never_becomes_ready_raises():
    from evals.drivers import voice_room
    from evals.drivers.base import DriverError
    from evals.drivers.voice_room import VoiceRoomDriver

    room = FakeRoom([])
    room.remote_participants = {"agent": FakeParticipant("initializing")}

    original = voice_room.AGENT_JOIN_SECONDS
    voice_room.AGENT_JOIN_SECONDS = 0.3
    try:
        with pytest.raises(DriverError):
            await VoiceRoomDriver._await_agent(room)
    finally:
        voice_room.AGENT_JOIN_SECONDS = original
