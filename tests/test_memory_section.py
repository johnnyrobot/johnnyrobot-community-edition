"""
The empty-memory case: the tutor invents a Student's history when memory is empty.

The mechanism, exactly. `api/routers/chat.py:154` built a memory block only
when there was something to put in it:

    if memories:
        memory_context = "Relevant memories:\\n" + ... + "\\n\\n"

With none, the system prompt said nothing whatsoever about memory -- while
`prompts.py` told the tutor it had one. The model was told it remembers, given
nothing, asked what it remembers, and left to fill the gap. It was not
misbehaving so much as completing.

`agent.py:284` had its own copy of the same shape, which is why the fix is in
prompts.py: one helper both surfaces call, so neither can drift back.

These tests check the prompt text. That the change actually stops the
behaviour is what the eval harness measures -- a thing no unit test can assert.
"""
from pathlib import Path

from prompts import AGENT_INSTRUCTION, memory_section


def source_of(module_path: str) -> str:
    """Read a module as text.

    Rather than importing it: `agent.py` runs `load_dotenv()` and pulls in the
    whole LiveKit stack at import, none of which these assertions need, and
    all of which could fail for reasons unrelated to what is being checked.
    """
    return Path(module_path).read_text()


def memory_block_of(instruction: str) -> str:
    """The `## Memory System` section alone, lowercased."""
    return instruction.split("## Memory System", 1)[1].split("##", 1)[0].lower()


def test_remembered_facts_appear():
    section = memory_section(["Student is studying quantum basketry"])

    assert "quantum basketry" in section


def test_an_empty_memory_still_produces_a_section():
    """The whole defect in one assertion. Before the fix this was "" -- silence."""
    assert memory_section([]).strip()


def test_the_empty_section_says_there_is_nothing_remembered():
    section = memory_section([]).lower()

    assert "no" in section and "memor" in section


def test_the_empty_section_forbids_inventing_a_history():
    """Stating the absence is not enough; the model must be told what to do about it."""
    section = memory_section([]).lower()

    assert "invent" in section or "make up" in section or "fabricate" in section


def test_the_empty_section_offers_the_honest_answer():
    """The observed good response said it had no memory. Name that as correct."""
    section = memory_section([]).lower()

    assert "say" in section


def test_a_populated_section_does_not_claim_emptiness():
    """Or the tutor would be told both things at once."""
    section = memory_section(["Student is studying quantum basketry"]).lower()

    assert "no remembered" not in section


def test_none_is_treated_as_empty():
    """Call sites read from Mem0, which can answer None on a degraded path."""
    assert memory_section(None).strip() == memory_section([]).strip()


def test_blank_memories_are_treated_as_empty():
    """A list of empty strings is not a remembered history."""
    assert memory_section(["", "   "]).strip() == memory_section([]).strip()


def test_the_instruction_no_longer_claims_memory_unconditionally():
    """prompts.py:49 said "You remember previous conversations with each student"."""
    assert "You remember previous conversations with each student" not in AGENT_INSTRUCTION


def test_the_instruction_says_memory_may_be_empty():
    block = memory_block_of(AGENT_INSTRUCTION)

    assert "empty" in block or "no memories" in block


def test_the_instruction_forbids_inventing_a_history():
    assert "never" in memory_block_of(AGENT_INSTRUCTION)


def test_the_academic_integrity_policy_survived_the_edit():
    """The one thing in this file that must not be disturbed (the academic-integrity constraint)."""
    assert "NEVER complete graded work" in AGENT_INSTRUCTION


def test_both_surfaces_use_the_helper():
    """The property that keeps the two from drifting apart again.

    A future edit that inlines the block in one call site puts that surface
    back where the empty-memory case found it, and this is what catches that.

    Read as text rather than imported: `agent.py` runs `load_dotenv()` and
    pulls in the whole LiveKit stack at import, none of which this assertion
    needs, and all of which could fail for reasons unrelated to what is being
    checked.
    """
    for module in ("api/routers/chat.py", "agent.py"):
        assert "memory_section(" in source_of(module), module


def test_neither_surface_still_builds_its_own_memory_block():
    """The exact shape the empty-memory case was: a block emitted only when non-empty."""
    assert 'memory_context = "Relevant memories:' not in source_of("api/routers/chat.py")
    assert "## Previous Conversation Memories" not in source_of("agent.py")


def test_incomplete_empty_section_does_not_claim_never_spoken():
    """chat.py's `remembered` is a relevance search, not a full read.

    Empty there is routine for a returning Student who changed topic, so this
    branch must never say they have never spoken -- that is the false
    statement the empty-memory case is about, pointed the other way.
    """
    section = memory_section([], complete=False).lower()

    assert "never spoken" not in section


def test_incomplete_empty_section_still_forbids_invention():
    section = memory_section([], complete=False).lower()

    assert "invent" in section or "fabricate" in section


def test_complete_empty_section_still_says_never_spoken():
    """The default, used by agent.py's full `get_all` read, is unchanged."""
    section = memory_section([], complete=True).lower()

    assert "never spoken" in section


def test_both_empty_variants_are_non_empty_strings():
    assert memory_section([], complete=True).strip()
    assert memory_section([], complete=False).strip()


def test_incomplete_populated_section_lists_the_memory_and_does_not_claim_emptiness():
    section = memory_section(["Student is studying quantum basketry"], complete=False)

    assert "quantum basketry" in section
    assert "nothing recalled" not in section.lower()
    assert "never spoken" not in section.lower()


def test_the_chat_route_passes_complete_false():
    """The call site that reads from a relevance search, not a full memory read."""
    assert "complete=False" in source_of("api/routers/chat.py")
