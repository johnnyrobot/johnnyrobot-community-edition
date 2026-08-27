"""
What the voice Tutor is told about a Student it does not remember.

`agent.py` assembles the voice Tutor's instructions inline in `entrypoint`,
between a LiveKit connection and a Mem0 read. The unconditional
`memory_section` call must remain covered even when memory is empty.

The text Tutor's instruction is covered in `test_chat_instruction.py`. This
is the same guarantee for the surface a Student reaches by speaking: drive the
real code path and capture what it produces at the boundary rather than
restating the assembly.

The boundary is `Assistant(instructions=...)` -- the last point before the text
becomes the model's system prompt. Everything above it here is stubbed because
none of it is what is under test: a room to connect to, a participant to greet,
a memory client to read, and a realtime model that would otherwise need an API
key.
"""
import pytest

import agent as agent_module
from prompts import AGENT_INSTRUCTION, SESSION_INSTRUCTION, memory_section


class _Participant:
    """A Student who has joined the room. Identity is their PocketBase record id."""

    identity = "pb-record-id"
    name = "Alice"


class _Room:
    remote_participants: dict = {}

    def on(self, _event):
        """`entrypoint` registers a track handler by decoration; keep the function."""

        def register(handler):
            return handler

        return register


class _Ctx:
    """Enough of a `JobContext` for `entrypoint` to reach the instruction."""

    def __init__(self):
        self.room = _Room()
        self.shutdown_callbacks: list = []

    async def connect(self):
        pass

    async def wait_for_participant(self):
        return _Participant()

    def add_shutdown_callback(self, callback):
        self.shutdown_callbacks.append(callback)


@pytest.fixture
def entrypoint_run(monkeypatch):
    """Run the real `entrypoint` against a given Mem0 result.

    Returns everything the run produced at the boundaries worth watching: what
    The Tutor was told, and what it was asked to do once the session was live.
    A change to either is visible here whether or not the assembly still looks
    The same.
    """

    async def run(mem0_records, *, greeting_raises=False):
        captured = {"replies": []}

        class _FakeMem0:
            async def get_all(self, user_id):
                return mem0_records

        async def _memory_client():
            return _FakeMem0()

        class _FakeSession:
            def __init__(self, *args, **kwargs):
                self.history = None

            async def start(self, **kwargs):
                captured["started"] = True

            async def generate_reply(self, **kwargs):
                # Order matters: a reply generated before `start` would be
                # spoken into a room the Student has not been connected to.
                captured["replies"].append(
                    {"after_start": captured.get("started", False), **kwargs}
                )
                if greeting_raises:
                    raise RuntimeError("the realtime model refused")

        class _CapturingAssistant:
            def __init__(self, instructions, user_id, language):
                captured["instructions"] = instructions

        async def _language(_user_id):
            return "en-US"

        import api.services.user_service as user_service

        monkeypatch.setattr(agent_module, "install_store", lambda: None)
        monkeypatch.setattr(agent_module, "set_user_context", lambda _user_id: None)
        monkeypatch.setattr(agent_module, "get_memory_client", _memory_client)
        monkeypatch.setattr(
            agent_module, "create_realtime_model", lambda language=None: (object(), "")
        )
        monkeypatch.setattr(agent_module, "AgentSession", _FakeSession)
        monkeypatch.setattr(agent_module, "Assistant", _CapturingAssistant)
        monkeypatch.setattr(user_service, "get_user_language_preference", _language)

        await agent_module.entrypoint(_Ctx())
        return captured

    return run


@pytest.fixture
def instructions_for(entrypoint_run):
    """Just the Tutor's instruction, for the cases that only care about that."""

    async def run(mem0_records):
        return (await entrypoint_run(mem0_records))["instructions"]

    return run


async def test_the_memory_block_is_present_when_nothing_is_remembered(instructions_for):
    """the empty-memory case, on the surface where it was measured.

    A Student with an empty memory must still get a memory block. Before the
    fix this was `if memories:`, so the block was omitted entirely while
    `AGENT_INSTRUCTION` went on telling the Tutor it remembered previous
    conversations -- told it remembers, given nothing, and asked what it
    remembers, the model filled the gap.
    """
    instructions = await instructions_for([])

    assert memory_section([]) in instructions


async def test_the_voice_surface_speaks_for_a_complete_read(instructions_for):
    """`agent.py` reads `get_all`, so it may say there is nothing.

    `api/routers/chat.py` runs a relevance search and must not, which is why
    `memory_section` takes `complete`. Asserting the complete text pins this
    surface to the side of that distinction its read actually supports.
    """
    instructions = await instructions_for([])

    assert memory_section([], complete=True) in instructions
    assert memory_section([], complete=False) not in instructions


async def test_what_is_remembered_reaches_the_tutor(instructions_for):
    """The non-empty case, so the guard above cannot be satisfied by a constant."""
    instructions = await instructions_for(
        [{"memory": "Prefers to be called Al", "updated_at": "2026-08-01"}]
    )

    assert memory_section(["Prefers to be called Al"]) in instructions


async def test_a_student_with_only_canvas_records_still_gets_the_memory_block(
    instructions_for,
):
    """Canvas data is not conversation memory, and must not stand in for it.

    These records are filtered into a separate list, so this Student has
    nothing remembered from talking -- the case the empty-memory case is about -- while
    `context_parts` is non-empty for another reason. A length check on that
    list would pass here and still leave the Tutor with no memory block.
    """
    instructions = await instructions_for(
        [
            {
                "memory": "Essay on Reconstruction due Friday",
                "updated_at": "",
                "metadata": {
                    "source": "canvas",
                    "data_type": "assignment",
                    "course_name": "HIST 101",
                },
            }
        ]
    )

    assert memory_section([]) in instructions


async def test_the_academic_integrity_policy_is_still_appended(instructions_for):
    """The memory block is added to the policy, never in place of it."""
    instructions = await instructions_for([])

    assert AGENT_INSTRUCTION in instructions


# -- The opening the Student actually hears ---------------------------------
#
# `SESSION_INSTRUCTION` has described a warm greeting since the repo was
# written and nothing ever called it: `entrypoint` started the session and
# returned, so a Student who joined met silence until they spoke first. The
# instruction was dead text, which is worse than no instruction -- it reads
# like a covered behaviour.


async def test_the_tutor_speaks_first(entrypoint_run):
    """A Student who joins is greeted, not left to open the conversation.

    Silence on join is a bad first five seconds for a Student and an actively
    misleading one: a tutor that says nothing is indistinguishable from a
    tutor that failed to connect.
    """
    run = await entrypoint_run([])

    assert run["replies"], "the Tutor generated no opening turn"


async def test_the_greeting_uses_the_session_instruction(entrypoint_run):
    """The greeting is the one in prompts.py, not one improvised at the call site.

    Both Tutor surfaces take their words from `prompts.py` so there is exactly
    one place a policy or a tone lives. A greeting written inline here would be
    a second source that nothing keeps in step.
    """
    run = await entrypoint_run([])

    assert run["replies"][0].get("instructions") == SESSION_INSTRUCTION


async def test_the_greeting_comes_after_the_session_is_started(entrypoint_run):
    """A reply generated before `start` is spoken into a room nobody is in."""
    run = await entrypoint_run([])

    assert run["replies"][0]["after_start"] is True


async def test_a_greeting_failure_never_costs_the_session(entrypoint_run):
    """The tutor still works if the opening turn does not.

    `generate_reply` reaches the realtime model, so it can fail for every
    reason a model call can. A Student who has already connected must not lose
    The session to a failed hello -- they can simply speak first, which is what
    they did before this greeting existed.
    """
    run = await entrypoint_run([], greeting_raises=True)  # must not raise

    assert run["replies"], "the greeting was never attempted"
    assert run["instructions"], "the session was built despite the greeting failing"
