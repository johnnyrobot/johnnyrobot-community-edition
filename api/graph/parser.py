"""
The deterministic layer of the Course Material graph.

The graph-build determinism contract splits a graph build in two: "preprocessing must reproduce exactly,
while extraction or vector differences receive a Drifted outcome". This module
is the half that must reproduce exactly. No model is involved, nothing is
sampled, and the same Source Copy parses to byte-identical Sections forever --
which is what makes a rebuild diffable and drift attributable to extraction
rather than to parsing.

A Section is content-addressed rather than positional. Inserting a chapter
therefore changes that chapter's identity and leaves its neighbours' alone,
so a rebuild can be compared against the previous generation instead of
replaced wholesale.

Offsets are character offsets into the decoded Source Copy, and the text is
deliberately *not* normalised -- no line-ending translation, no whitespace
collapsing. Grounding slices the Source Copy with these offsets to check an
excerpt appears verbatim, so a digest computed over normalised text and an
offset computed over raw text would disagree in exactly the case that matters.
CRLF and LF sources are different bytes and are honestly different Source
Copies.
"""
import hashlib
import re
from dataclasses import dataclass

# Recorded in every GraphBuildManifest. Bump it whenever a change here could
# produce different Sections from the same Source Copy -- an unversioned parser
# makes a drift report unattributable, which is the one thing the manifest
# exists to prevent.
PARSER_VERSION = "cmg-parser-1"

# What this parser can read deterministically. PDF is absent deliberately: no
# pinned text extractor is installed, and the graph-build determinism contract makes an unsupported format
# a recorded non-build on the graph branch rather than a failure that could
# reach the Course Material itself.
SUPPORTED_SUFFIXES = frozenset({".md", ".markdown", ".txt"})

# ATX headings only. Setext headings (underlined with === or ---) are not
# recognised, so a document using them parses as one Section rather than
# silently producing a different shape than its author would expect.
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.*?)[ \t]*$", re.MULTILINE)


@dataclass(frozen=True)
class Section:
    """One structural unit of a Course Material.

    `library_key` is deliberately absent. The parser knows nothing about
    ownership; the store stamps the Student Library at write time. Keeping the
    two apart means a parser test cannot pass because it inherited an owner.
    """

    section_id: str
    material_id: str
    path: str
    ordinal: int
    heading: str
    char_start: int
    char_end: int
    digest: str


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def source_digest(text: str) -> str:
    """The digest of a whole Source Copy, recorded in the manifest."""
    return _digest(text)


def sections_digest(sections: list[Section]) -> str:
    """A digest over the ordered Sections.

    Order is part of what is digested. Two builds that found the same Sections
    in a different order have drifted, and a digest over an unordered set would
    call that agreement.
    """
    return _digest("\n".join(f"{s.ordinal}:{s.section_id}" for s in sections))


def _path_for(level: int, stack: list[list[int]]) -> str:
    """Assign a hierarchical path. `stack` holds [heading_level, counter] pairs.

    The pairs are the point. Tracking only depth cannot tell two sibling `##`
    headings from a parent and a child -- after the first one is normalised to
    depth 1, the second looks one level deeper than the stack and becomes
    "1.1" instead of "2".

    A document that jumps h1 -> h3 invents no empty level: the deeper heading
    becomes a child of the h1, not a grandchild of a section that does not
    exist. Inventing the missing level would put a node in the graph that no
    text in the Source Copy corresponds to.

    A path is descriptive and is NOT guaranteed unique: a document whose first
    headings are `##` and which later drops to `#` legitimately repeats one.
    That is why `ordinal`, not `path`, is what grounding keys on.
    """
    while stack and stack[-1][0] > level:
        stack.pop()
    if stack and stack[-1][0] == level:
        stack[-1][1] += 1
    else:
        stack.append([level, 1])
    return ".".join(str(count) for _, count in stack)


def parse_sections(text: str, material_id: str) -> list[Section]:
    """Split a Source Copy into Sections.

    Prose before the first heading becomes Section "0" when it holds anything
    but whitespace -- a Concept can perfectly well be defined in an
    unheaded introduction, and dropping it would make that Concept ungroundable
    for no reason the Student could see.

    Plain text with no headings at all is therefore one Section at path "0",
    not "1": the whole document is preamble, and saying so is more honest than
    inventing a first chapter it does not have.

    An empty Source Copy yields no Sections. That is a successful parse of
    nothing, and the build records zero and stops; it is not an error.
    """
    if not text:
        return []

    boundaries: list[tuple[int, int, str]] = [
        (match.start(), len(match.group(1)), match.group(2))
        for match in _HEADING.finditer(text)
    ]

    spans: list[tuple[str, str, int, int]] = []
    stack: list[list[int]] = []

    preamble_end = boundaries[0][0] if boundaries else len(text)
    if text[:preamble_end].strip():
        spans.append(("0", "", 0, preamble_end))

    for index, (start, level, heading) in enumerate(boundaries):
        end = boundaries[index + 1][0] if index + 1 < len(boundaries) else len(text)
        spans.append((_path_for(level, stack), heading, start, end))

    sections = []
    for ordinal, (path, heading, start, end) in enumerate(spans):
        body = text[start:end]
        digest = _digest(body)
        sections.append(
            Section(
                # The material identity is part of the address, so identical
                # bytes in two Course Materials are still two Sections. The
                # path is in it too, so a chapter duplicated verbatim within
                # one material does not collapse to a single node.
                section_id=_digest(f"{material_id}\x00{path}\x00{digest}")[:32],
                material_id=material_id,
                path=path,
                ordinal=ordinal,
                heading=heading,
                char_start=start,
                char_end=end,
                digest=digest,
            )
        )
    return sections
