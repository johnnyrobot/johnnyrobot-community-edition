"""
The deterministic layer.

The graph-build determinism contract requires that "preprocessing must reproduce exactly". That is the
whole reason this layer exists separately from extraction: a Section is
content-addressed, so an unchanged Source Copy reparsed yields byte-identical
Sections and a rebuild that differs has drifted somewhere a model was involved.
"""
from api.graph.parser import (
    PARSER_VERSION,
    SUPPORTED_SUFFIXES,
    parse_sections,
    sections_digest,
    source_digest,
)

MATERIAL = "abcdefghij12345"

DOC = """Intro prose before any heading.

# Chapter One

Chapter one body.

## Section 1.1

Nested body.

# Chapter Two

Chapter two body.
"""


def test_the_same_source_copy_parses_identically_twice():
    """The property the graph-build determinism contract requires, stated directly."""
    first = parse_sections(DOC, MATERIAL)
    second = parse_sections(DOC, MATERIAL)

    assert first == second


def test_section_ids_are_content_addressed_not_positional():
    """Changing a body must change that Section's identity, not just its digest."""
    changed = DOC.replace("Chapter one body.", "Chapter one body, revised.")

    original = {s.path: s.section_id for s in parse_sections(DOC, MATERIAL)}
    revised = {s.path: s.section_id for s in parse_sections(changed, MATERIAL)}

    assert original["1"] != revised["1"]


def test_an_untouched_section_keeps_its_identity_across_an_edit():
    """A rebuild must be diffable: only what changed may change."""
    changed = DOC.replace("Chapter one body.", "Chapter one body, revised.")

    original = {s.path: s.section_id for s in parse_sections(DOC, MATERIAL)}
    revised = {s.path: s.section_id for s in parse_sections(changed, MATERIAL)}

    assert original["2"] == revised["2"]


def test_two_materials_never_share_a_section_identity():
    """Identical bytes in two Course Materials are still two Sections."""
    mine = parse_sections(DOC, MATERIAL)
    theirs = parse_sections(DOC, "zyxwvutsrq54321")

    assert {s.section_id for s in mine}.isdisjoint({s.section_id for s in theirs})


def test_offsets_index_the_source_copy_exactly():
    """Grounding slices the Source Copy with these offsets, so they must be exact."""
    sections = parse_sections(DOC, MATERIAL)

    chapter_one = next(s for s in sections if s.path == "1")
    assert DOC[chapter_one.char_start:chapter_one.char_end].startswith("# Chapter One")
    assert "Chapter one body." in DOC[chapter_one.char_start:chapter_one.char_end]


def test_a_section_ends_where_the_next_heading_begins():
    """A Chapter must not swallow its own subsections' successors."""
    sections = parse_sections(DOC, MATERIAL)
    by_path = {s.path: s for s in sections}

    assert "Chapter two body." not in DOC[by_path["1.1"].char_start:by_path["1.1"].char_end]


def test_preamble_before_the_first_heading_becomes_a_section():
    """Prose with no heading is still content a Concept can be defined in."""
    sections = parse_sections(DOC, MATERIAL)

    assert sections[0].path == "0"
    assert sections[0].heading == ""
    assert "Intro prose" in DOC[sections[0].char_start:sections[0].char_end]


def test_whitespace_only_preamble_is_not_a_section():
    sections = parse_sections("\n\n# Only Heading\n\nBody.\n", MATERIAL)

    assert [s.path for s in sections] == ["1"]


def test_heading_paths_nest_by_level():
    sections = parse_sections(DOC, MATERIAL)

    assert [s.path for s in sections] == ["0", "1", "1.1", "2"]


def test_a_skipped_heading_level_descends_exactly_one_level():
    """h1 -> h3 invents no empty level. Deterministic rule, stated in the parser."""
    text = "# One\n\nBody.\n\n### Deep\n\nBody.\n"

    assert [s.path for s in parse_sections(text, MATERIAL)] == ["1", "1.1"]


def test_ordinals_are_document_order():
    sections = parse_sections(DOC, MATERIAL)

    assert [s.ordinal for s in sections] == [0, 1, 2, 3]


def test_plain_text_with_no_headings_is_one_preamble_section():
    """A .txt Course Material is all preamble, and path "0" says so."""
    sections = parse_sections("Just prose.\nMore prose.\n", MATERIAL)

    assert len(sections) == 1
    assert (sections[0].path, sections[0].heading) == ("0", "")


def test_sibling_headings_are_siblings_not_a_chain():
    """Two `##` in a row are peers.

    Tracking depth alone gets this wrong: once the first `##` is normalised to
    depth 1, the second looks one level deeper and becomes "1.1".
    """
    assert [s.path for s in parse_sections("## a\n## b\n## c\n", MATERIAL)] == ["1", "2", "3"]


def test_returning_to_a_shallower_level_resumes_its_numbering():
    text = "# a\n## b\n### c\n## d\n# e\n"

    assert [s.path for s in parse_sections(text, MATERIAL)] == ["1", "1.1", "1.1.1", "1.2", "2"]


def test_a_path_is_descriptive_and_the_ordinal_is_the_key():
    """A document starting at `##` and later dropping to `#` repeats a path.

    That is legitimate and is why grounding keys on `ordinal`, which is unique
    by construction, rather than on `path`, which is not.
    """
    sections = parse_sections("## a\n## b\n# c\n", MATERIAL)

    assert [s.path for s in sections] == ["1", "2", "1"]
    assert len({s.ordinal for s in sections}) == len(sections)


def test_an_empty_source_copy_yields_no_sections():
    """A successful parse of nothing, not a crash. The build records zero and stops."""
    assert parse_sections("", MATERIAL) == []


def test_line_endings_are_not_normalised():
    """CRLF and LF are different bytes, so they are different Source Copies.

    Normalising would make the digest disagree with the offsets, and grounding
    slices the Source Copy with those offsets.
    """
    assert source_digest("a\r\nb") != source_digest("a\nb")


def test_the_sections_digest_covers_order_not_just_content():
    """Two builds that found the same Sections in a different order have drifted."""
    sections = parse_sections(DOC, MATERIAL)

    assert sections_digest(sections) != sections_digest(list(reversed(sections)))


def test_the_parser_version_is_pinned():
    """The manifest records this; an unversioned parser makes drift unattributable."""
    assert PARSER_VERSION == "cmg-parser-1"


def test_pdf_is_not_a_supported_suffix():
    """No pinned PDF extractor is installed; the graph-build determinism contract makes that a recorded non-build."""
    assert ".pdf" not in SUPPORTED_SUFFIXES
    assert SUPPORTED_SUFFIXES == frozenset({".md", ".markdown", ".txt"})
