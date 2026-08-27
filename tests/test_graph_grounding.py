"""
The grounding gate.

This is where trust comes from. The model is not asked to be right, only to be
quotable, and a quote either appears verbatim in the Section it names or it
does not. the graph-build determinism contract: "only exact source excerpts may become Material Evidence."

Testable without a model, deliberately. Every case here is a fixed candidate
against fixed text.
"""
from pathlib import Path

from api.graph.grounding import MIN_EXCERPT_CHARS, Candidate, Grounded, Rejection, ground
from api.graph.parser import parse_sections

MATERIAL = "abcdefghij12345"
SOURCE = (Path(__file__).parent / "fixtures" / "quantum_basketry.md").read_text()
SECTIONS = parse_sections(SOURCE, MATERIAL)

# The line breaks are load-bearing. The fixture wraps this sentence across
# three lines, and "verbatim" means verbatim -- a version of this string with
# spaces where the file has newlines is the near-miss two tests below reject.
REVERSAL_QUOTE = (
    "Applying this rule requires Damp Tension,\n"
    "because a reversal that is not tension-corrected unwinds the neighbouring\n"
    "strand."
)


def ordinal_of(heading):
    return next(s.ordinal for s in SECTIONS if s.heading == heading)


def requires(excerpt, ordinal=None):
    return Candidate(
        kind="requires",
        concept="Rule of Terminal Reversal",
        requires="Damp Tension",
        section_ordinal=ordinal_of("The Rule of Terminal Reversal") if ordinal is None else ordinal,
        excerpt=excerpt,
    )


def test_a_verbatim_excerpt_is_accepted():
    result = ground(requires(REVERSAL_QUOTE), SECTIONS, SOURCE)

    assert isinstance(result, Grounded)


def test_acceptance_computes_offsets_rather_than_trusting_them():
    """The model never supplies offsets, so offsets can never be wrong."""
    result = ground(requires(REVERSAL_QUOTE), SECTIONS, SOURCE)

    assert SOURCE[result.char_start:result.char_end] == REVERSAL_QUOTE


def test_the_grounded_edge_names_the_section_it_was_found_in():
    result = ground(requires(REVERSAL_QUOTE), SECTIONS, SOURCE)

    expected = next(
        s.section_id for s in SECTIONS if s.heading == "The Rule of Terminal Reversal"
    )
    assert result.section_id == expected


def test_a_paraphrase_is_rejected():
    """The plausible near-miss. Every word is true and none of them are quoted."""
    result = ground(
        requires("Using this rule depends on Damp Tension being established first."),
        SECTIONS,
        SOURCE,
    )

    assert isinstance(result, Rejection)


def test_normalised_whitespace_is_rejected():
    """A quote reflowed onto one line is no longer verbatim.

    This is the near-miss a model produces most often, because it reads the
    text as prose and re-emits it as prose.
    """
    result = ground(requires(" ".join(REVERSAL_QUOTE.split())), SECTIONS, SOURCE)

    assert isinstance(result, Rejection)


def test_an_excerpt_from_a_different_section_is_rejected():
    """Verbatim somewhere in the material is not verbatim in the Section claimed.

    Without this, a candidate could cite a real sentence from anywhere and
    ground a dependency the cited Section never asserts.
    """
    parity = ordinal_of("The Rule of Lattice Parity")

    result = ground(requires(REVERSAL_QUOTE, ordinal=parity), SECTIONS, SOURCE)

    assert isinstance(result, Rejection)
    # The quote is real -- it is simply not in the Section that was cited.
    assert REVERSAL_QUOTE in SOURCE


def test_an_excerpt_naming_no_real_section_is_rejected():
    result = ground(requires(REVERSAL_QUOTE, ordinal=99), SECTIONS, SOURCE)

    assert isinstance(result, Rejection)


def test_a_too_short_excerpt_is_rejected():
    """A three-word quote matches almost anywhere, so it grounds nothing.

    Not in the spec's list of near-misses; added because without a floor the
    grounding rule is trivially satisfiable and an invented dependency could
    cite "the rule" and pass.
    """
    result = ground(requires("requires"), SECTIONS, SOURCE)

    assert isinstance(result, Rejection)
    assert len("requires") < MIN_EXCERPT_CHARS


def test_an_empty_excerpt_is_rejected():
    assert isinstance(ground(requires(""), SECTIONS, SOURCE), Rejection)


def test_a_requires_edge_pointing_at_itself_is_rejected():
    """A Concept cannot be its own prerequisite; the traversal would not terminate."""
    self_edge = Candidate(
        kind="requires",
        concept="Damp Tension",
        requires="Damp Tension",
        section_ordinal=ordinal_of("The Rule of Terminal Reversal"),
        excerpt=REVERSAL_QUOTE,
    )

    assert isinstance(ground(self_edge, SECTIONS, SOURCE), Rejection)


def test_a_candidate_with_no_concept_name_is_rejected():
    nameless = Candidate(
        kind="requires", concept="", requires="Damp Tension",
        section_ordinal=ordinal_of("The Rule of Terminal Reversal"), excerpt=REVERSAL_QUOTE,
    )

    assert isinstance(ground(nameless, SECTIONS, SOURCE), Rejection)


def test_a_defines_candidate_needs_no_prerequisite():
    definition = Candidate(
        kind="defines",
        concept="Damp Tension",
        requires="",
        section_ordinal=ordinal_of("Damp Tension"),
        excerpt="Damp Tension is the residual pull a strand exerts on its neighbours",
    )

    assert isinstance(ground(definition, SECTIONS, SOURCE), Grounded)


def test_a_rejection_carries_no_excerpt_text():
    """Rejections are counted into a content-free manifest, so they hold no content."""
    result = ground(requires("a plausible sounding invention about strands"), SECTIONS, SOURCE)

    assert not any("strand" in str(value) for value in vars(result).values())


def test_an_unknown_kind_is_rejected():
    """The ontology is closed. Anything outside it is dropped, not accommodated."""
    odd = Candidate(
        kind="contradicts", concept="A", requires="B",
        section_ordinal=ordinal_of("The Rule of Terminal Reversal"), excerpt=REVERSAL_QUOTE,
    )

    assert isinstance(ground(odd, SECTIONS, SOURCE), Rejection)
