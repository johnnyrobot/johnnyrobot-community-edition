"""
Extraction: the half of a build that may Drift.

The graph-build determinism contract puts this on the non-deterministic side of the line, and nothing here
pretends otherwise -- two runs over one Source Copy may disagree, and the
manifest is what makes that disagreement visible rather than silent.

Two things constrain it. The ontology is closed, so anything proposed outside
it is dropped rather than accommodated. And the model is asked only for a
verbatim quote, never for offsets and never for a Section identity: it names a
Concept and quotes the sentence that supports the claim, and `grounding.ground`
decides whether that quote is real. A model that invents a dependency has to
invent a sentence to go with it, and that sentence will not be in the text.

Nothing here raises. A model that is unavailable, rate-limited, or answering in
prose costs candidates, and a build with no candidates is a successful build
with zero edges (recorded as such), not a failure that could reach the Course
Material.
"""
import asyncio
import json
import logging
import re

from api.config import get_settings
from api.graph.grounding import KINDS, Candidate
from api.graph.parser import Section

logger = logging.getLogger(__name__)
settings = get_settings()

# Recorded in every GraphBuildManifest alongside the model name. Bump it when
# The prompt or the parsing rules change: two builds that disagree are only
# attributable to drift if the policy behind each is identified.
EXTRACTION_POLICY_ID = "cmg-extract-1"

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.MULTILINE)

_PROMPT = """You are reading one section of a course material to find concept dependencies.

Return a JSON array. Each element is one of:

  {{"kind": "defines", "concept": "<name>", "excerpt": "<verbatim quote>"}}
  {{"kind": "requires", "concept": "<name>", "requires": "<prerequisite name>", "excerpt": "<verbatim quote>"}}

Rules:
- "excerpt" MUST be copied verbatim from the section text below, character for
  character, including its line breaks and spacing. An excerpt that is not an
  exact substring of the section is discarded.
- Quote at least a full sentence. A short fragment is discarded.
- Only claim "requires" when the section text itself says the dependency
  exists. Do not infer it from what you know about the subject.
- Return [] if the section states no definitions and no dependencies. That is a
  normal and expected answer.
- Return only the JSON array, with no commentary.

Section heading: {heading}

Section text:
{body}
"""


def build_prompt(section: Section, source: str) -> str:
    """Ask for the ontology and nothing else.

    The Section identity is deliberately absent from what the model answers
    with -- it sees one Section at a time and the caller knows which. A model
    that could name its own Section could attribute a real quote to a passage
    it never read, which grounding would then accept.
    """
    body = source[section.char_start:section.char_end]
    return _PROMPT.format(heading=section.heading or "(no heading)", body=body)


def parse_candidates(raw: str, section_ordinal: int) -> list[Candidate]:
    """Turn a model's answer into candidates, dropping everything else.

    Never raises. Prose where JSON was asked for, a bare object instead of an
    array, a field of the wrong type -- each is zero candidates, because a
    build with no candidates is a real and expected outcome and an exception
    here would turn it into a failure.
    """
    if not raw or not raw.strip():
        return []

    try:
        parsed = json.loads(_FENCE.sub("", raw).strip())
    except (json.JSONDecodeError, ValueError):
        logger.info("A section's extraction answer was not JSON; it contributes no candidates")
        return []

    if not isinstance(parsed, list):
        return []

    candidates = []
    for item in parsed:
        if not isinstance(item, dict):
            continue

        kind = item.get("kind")
        concept = item.get("concept")
        requires = item.get("requires", "")
        excerpt = item.get("excerpt")

        # Types are checked rather than coerced. `str(42)` would turn a model's
        # confused output into a Concept named "42" and put it in a Student's
        # graph; dropping it keeps the rejection counts honest instead.
        if kind not in KINDS:
            continue
        if not isinstance(concept, str) or not isinstance(excerpt, str):
            continue
        if not isinstance(requires, str):
            continue

        candidates.append(
            Candidate(
                kind=kind,
                concept=concept,
                requires=requires,
                # Ours, never the model's.
                section_ordinal=section_ordinal,
                excerpt=excerpt,
            )
        )
    return candidates


def _generate(prompt: str, model: str) -> str:
    """One blocking call to Gemini. Split out so tests replace the network."""
    from google import genai

    client = genai.Client(api_key=settings.google_api_key)
    response = client.models.generate_content(model=model, contents=prompt)
    return response.text or ""


async def extract_candidates(sections: list[Section], source: str, model: str = None) -> list[Candidate]:
    """Propose candidates for every Section, one call each.

    One Section's failure costs that Section and no more. A build that lost
    three sections to rate limiting is a build with fewer edges, and the
    manifest's warnings are where that becomes visible -- stopping at the first
    failure would instead lose the whole graph to one transient error.

    The calls are sequential. Concurrency here would be a per-build burst
    against a shared quota, and a build already runs behind a Ready material
    where nobody is waiting on it.
    """
    chosen = model or settings.graph_extraction_model
    candidates: list[Candidate] = []

    for section in sections:
        prompt = build_prompt(section, source)
        try:
            raw = await asyncio.to_thread(_generate, prompt, chosen)
        except Exception as extract_err:
            logger.warning(
                f"Section {section.path} contributed no candidates: extraction failed "
                f"({extract_err})"
            )
            continue
        candidates.extend(parse_candidates(raw, section.ordinal))

    return candidates
