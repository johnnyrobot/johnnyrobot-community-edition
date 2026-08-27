"""
Extraction: the half that may Drift.

The graph-build determinism contract puts extraction on the non-deterministic side of the line, so nothing
here is asked to be reproducible. What it *is* asked to do is stay inside a
closed ontology and never raise: "anything an extractor proposes outside this
shape is dropped rather than accommodated", and a model that returns prose
where JSON was asked for produces a build with zero edges, not a failed one.
"""
from api.graph import extraction
from api.graph.extraction import EXTRACTION_POLICY_ID, build_prompt, parse_candidates
from api.graph.parser import parse_sections

MATERIAL = "abcdefghij12345"
DOC = "# Rule of Terminal Reversal\n\nApplying this rule requires Damp Tension.\n"
SECTIONS = parse_sections(DOC, MATERIAL)

GOOD = """
[
  {"kind": "requires", "concept": "Rule of Terminal Reversal",
   "requires": "Damp Tension", "excerpt": "Applying this rule requires Damp Tension."}
]
"""


def test_a_well_formed_answer_becomes_candidates():
    candidates = parse_candidates(GOOD, 0)

    assert len(candidates) == 1
    assert candidates[0].concept == "Rule of Terminal Reversal"


def test_the_section_identity_comes_from_us_not_the_model():
    """A model naming its own Section could attribute a quote to a passage it never read."""
    raw = GOOD.replace('"kind"', '"section_ordinal": 99, "kind"')

    assert parse_candidates(raw, 0)[0].section_ordinal == 0


def test_a_fenced_answer_is_still_parsed():
    """Models wrap JSON in ```json fences constantly; that is not a malformed answer."""
    assert len(parse_candidates(f"```json\n{GOOD}\n```", 0)) == 1


def test_an_out_of_ontology_kind_is_dropped():
    raw = GOOD.replace('"kind": "requires"', '"kind": "contradicts"')

    assert parse_candidates(raw, 0) == []


def test_out_of_ontology_fields_are_ignored_not_carried():
    raw = GOOD.replace('"kind"', '"confidence": 0.9, "weight": 3, "kind"')
    candidates = parse_candidates(raw, 0)

    assert len(candidates) == 1
    assert not hasattr(candidates[0], "confidence")


def test_prose_instead_of_json_yields_no_candidates():
    """Zero edges is a successful build. An exception here would fail one."""
    assert parse_candidates("I could not find any dependencies in this text.", 0) == []


def test_a_json_object_where_a_list_was_asked_for_yields_nothing():
    assert parse_candidates('{"kind": "requires"}', 0) == []


def test_an_empty_answer_yields_nothing():
    assert parse_candidates("", 0) == []


def test_a_candidate_missing_its_excerpt_is_dropped():
    """Grounding would reject it anyway; dropping here keeps the counts honest."""
    raw = '[{"kind": "requires", "concept": "A", "requires": "B"}]'

    assert parse_candidates(raw, 0) == []


def test_a_non_string_field_is_dropped_rather_than_coerced():
    raw = '[{"kind": "requires", "concept": 42, "requires": "B", "excerpt": "a long enough quote"}]'

    assert parse_candidates(raw, 0) == []


def test_the_prompt_carries_the_section_body_and_asks_only_for_the_ontology():
    prompt = build_prompt(SECTIONS[0], DOC)

    assert "Applying this rule requires Damp Tension." in prompt
    assert "requires" in prompt and "defines" in prompt


def test_the_prompt_demands_verbatim_quotes():
    """The one instruction the whole design rests on."""
    assert "verbatim" in build_prompt(SECTIONS[0], DOC).lower()


async def test_a_model_failure_costs_that_section_and_no_more(monkeypatch):
    """One Section's failure must not fail the build."""
    calls = []

    def flaky(prompt, model):
        calls.append(prompt)
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(extraction, "_generate", flaky)

    sections = parse_sections("# A\n\nBody one.\n\n# B\n\nBody two.\n", MATERIAL)
    result = await extraction.extract_candidates(sections, "# A\n\nBody one.\n\n# B\n\nBody two.\n")

    assert result == []
    assert len(calls) == 2  # it kept going


async def test_extraction_asks_once_per_section(monkeypatch):
    monkeypatch.setattr(extraction, "_generate", lambda prompt, model: GOOD)

    sections = parse_sections("# A\n\nBody one.\n\n# B\n\nBody two.\n", MATERIAL)
    result = await extraction.extract_candidates(sections, "# A\n\nBody one.\n\n# B\n\nBody two.\n")

    assert len(result) == 2


def test_the_policy_is_identified():
    """The graph-build determinism contract makes drift reportable only if the policy that produced it is named."""
    assert EXTRACTION_POLICY_ID == "cmg-extract-1"
