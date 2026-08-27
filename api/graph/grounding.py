"""
The grounding gate: the reason an edge can be trusted.

The hybrid rule from the design, made structural. An edge with no grounded
excerpt cannot be constructed, so "the model invented a dependency" is a schema
violation rather than a matter of judgement. the graph-build determinism contract permits "only exact
source excerpts" to become Material Evidence, and this is where that is
enforced.

The model supplies the excerpt and never the offsets. Offsets are located here,
against the Source Copy, which means an offset cannot be wrong -- it either
exists or the candidate is rejected. Asking a model for character offsets would
add a second thing to verify and buy nothing.

Rejections are counted, never repaired. A repaired candidate is an invented one
with extra steps.
"""
from dataclasses import dataclass

from api.graph.parser import Section

# An excerpt must be long enough that finding it verbatim means something. A
# three-word quote appears in almost any prose, so a floor is what stops the
# grounding rule from being trivially satisfiable: without it, an invented
# dependency could cite "the rule" and pass the gate.
MIN_EXCERPT_CHARS = 24

# The closed ontology, as the design states it. A candidate proposing anything
# else is dropped rather than accommodated.
KINDS = frozenset({"defines", "requires"})


@dataclass(frozen=True)
class Candidate:
    """What an extractor proposes. Nothing here is trusted.

    `section_ordinal` rather than a path: a Section's path is descriptive and
    a document that starts at `##` and later drops to `#` legitimately repeats
    one. Keying on a repeatable value would let a candidate be checked against
    a Section it did not cite -- and pass.
    """

    kind: str
    concept: str
    requires: str
    section_ordinal: int
    excerpt: str


@dataclass(frozen=True)
class Grounded:
    """A candidate that quoted its Section correctly. Offsets are located, not claimed."""

    kind: str
    concept: str
    requires: str
    section_id: str
    excerpt: str
    char_start: int
    char_end: int


@dataclass(frozen=True)
class Rejection:
    """A candidate that did not.

    Carries a reason and the Section ordinal, and no excerpt: rejections are
    counted into a GraphBuildManifest, which is content-free by construction.
    Putting the rejected text here would make the manifest a second place a
    Course Material could leak from.
    """

    reason: str
    section_ordinal: int


def ground(candidate: Candidate, sections: list[Section], source: str) -> Grounded | Rejection:
    """Accept a candidate only if it quotes the Section it names, verbatim.

    The Section claim matters as much as the quote. A sentence that is real
    somewhere in the Course Material but absent from the cited Section would
    otherwise let a dependency be attributed to a passage that never asserts
    it -- which is the same falsehood the excerpt rule exists to prevent, just
    harder to see.
    """
    if candidate.kind not in KINDS:
        return Rejection("kind outside the ontology", candidate.section_ordinal)

    if not candidate.concept.strip():
        return Rejection("no concept named", candidate.section_ordinal)

    if candidate.kind == "requires":
        if not candidate.requires.strip():
            return Rejection("requires edge names no prerequisite", candidate.section_ordinal)
        if candidate.concept.strip().casefold() == candidate.requires.strip().casefold():
            # A self-edge makes the prerequisite traversal non-terminating, and
            # says nothing a Student could act on.
            return Rejection("concept requires itself", candidate.section_ordinal)

    if len(candidate.excerpt) < MIN_EXCERPT_CHARS:
        return Rejection("excerpt too short to ground", candidate.section_ordinal)

    section = next((s for s in sections if s.ordinal == candidate.section_ordinal), None)
    if section is None:
        return Rejection("cited section does not exist", candidate.section_ordinal)

    body = source[section.char_start:section.char_end]
    offset = body.find(candidate.excerpt)
    if offset < 0:
        return Rejection("excerpt not found verbatim in the cited section", candidate.section_ordinal)

    start = section.char_start + offset
    return Grounded(
        kind=candidate.kind,
        concept=candidate.concept.strip(),
        requires=candidate.requires.strip(),
        section_id=section.section_id,
        excerpt=candidate.excerpt,
        char_start=start,
        char_end=start + len(candidate.excerpt),
    )
