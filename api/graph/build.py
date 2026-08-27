"""
The build: parse, extract, ground, write, cut over, record.

Four stages across the graph-build determinism contract's determinism line. Stages 1 and 2 reproduce
exactly; 3 and 4 may Drift, and stage 4 -- grounding -- is where trust comes
from, because a quote either matches the Source Copy or it does not.

**Nothing in this module raises into a caller.** A build runs behind a Course
Material that is already Ready and already searchable. There is no failure here
that is worth costing a Student their material, and the graph-build determinism contract says so directly:
a graph failure must "fail or quarantine only the Optional/Shadow graph branch,
never weakening the authorized Gemini baseline". For the live demo the rule is
blunter still -- nothing in this subsystem may stop the tutor answering.

The one deliberate exception is `remove_material_graph`, which does raise. See
its docstring: a removal that cannot confirm the graph is clear is an
incomplete Material Removal, and reporting that as success is the failure
The immediate-removal contract exists to prevent.
"""
import logging
from pathlib import PurePath

from api.config import get_settings
from api.database.repository import get_repository
from api.graph import store
from api.graph.client import get_graph_client
from api.graph.extraction import EXTRACTION_POLICY_ID, extract_candidates
from api.graph.grounding import Grounded, ground
from api.graph.manifest import GraphBuildManifest
from api.graph.parser import (
    PARSER_VERSION,
    SUPPORTED_SUFFIXES,
    parse_sections,
    sections_digest,
    source_digest,
)

logger = logging.getLogger(__name__)
settings = get_settings()


async def _next_generation(student_id: str, material_id: str) -> int:
    """One past the highest generation this material has recorded.

    Read from the manifest history rather than the graph: the manifest is
    written for every outcome including failures and skips, so the counter
    cannot be reset by a graph that was wiped or was never configured.
    """
    try:
        history = await get_repository().list_graph_manifests(student_id, material_id)
    except Exception:
        # A history we cannot read is not a reason to skip the build. Starting
        # again at 1 would at worst re-cut-over content the write just made.
        logger.warning("Could not read graph build history; starting from generation 1")
        return 1
    return max((int(m.get("generation") or 0) for m in history), default=0) + 1


async def _record(student_id: str, manifest: GraphBuildManifest) -> GraphBuildManifest:
    """Persist the manifest. A failure to record is logged, never raised."""
    try:
        await get_repository().create_graph_manifest(student_id, manifest.as_record())
    except Exception as record_err:
        logger.error(f"A graph build was not recorded: {record_err}")
    return manifest


async def build_material_graph(
    student_id: str, material_id: str, title: str, file_path: str
) -> GraphBuildManifest:
    """Build one Course Material's graph and record what happened.

    Called after a material reaches Ready, and never before: building earlier
    would make upload visibly slower and let a graph failure block a material
    RAG could already serve.

    Returns a manifest in every case, including every failure. Callers do not
    check it -- it is recorded so a Deployment Operator can see the difference
    between "no dependencies in this material" and "extraction is broken",
    which identical zero-edge builds would otherwise hide.
    """
    generation = await _next_generation(student_id, material_id)
    manifest = GraphBuildManifest(
        material=material_id,
        generation=generation,
        outcome="failed",
        parser_version=PARSER_VERSION,
        extraction_policy_id=EXTRACTION_POLICY_ID,
        extraction_model=settings.graph_extraction_model,
    )

    client = await get_graph_client()
    if not client.is_configured:
        manifest.outcome = "skipped_no_graph"
        manifest.warnings.append("no graph is configured for this deployment")
        return await _record(student_id, manifest)

    suffix = PurePath(file_path).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        # The graph-build determinism contract puts unsupported formats on the graph branch, so this costs
        # prerequisites and nothing else. The material stays Ready and RAG
        # keeps serving it, because Gemini reads formats this parser does not.
        manifest.outcome = "skipped_unsupported_format"
        manifest.warnings.append(f"no pinned parser for {suffix or 'this format'}")
        return await _record(student_id, manifest)

    try:
        source = _read_source(file_path)
        sections = parse_sections(source, material_id)

        manifest.source_digest = source_digest(source)
        manifest.sections_digest = sections_digest(sections)
        manifest.section_count = len(sections)

        candidates = await extract_candidates(sections, source)

        accepted: list[Grounded] = []
        for candidate in candidates:
            result = ground(candidate, sections, source)
            if isinstance(result, Grounded):
                accepted.append(result)
            else:
                manifest.candidates_rejected += 1

        manifest.edges_accepted = sum(1 for e in accepted if e.kind == "requires")
        manifest.concepts_accepted = len({e.concept for e in accepted})

        await store.write_generation(
            student_id, material_id, title, generation, sections, accepted,
            source_digest=manifest.source_digest,
        )
        # Only now: a failure above leaves the previous generation untouched,
        # which is the graph analogue of "a failed update leaves the existing
        # content unchanged".
        await store.cut_over(student_id, material_id, generation)

        # Zero groundable edges is a successful build, not a failure. Recorded
        # as such so "this material states no dependencies" stays legible next
        # to "extraction returned nothing usable".
        manifest.outcome = "built"
        if not accepted:
            manifest.warnings.append("no candidate could be grounded")

    except Exception as build_err:
        logger.warning(
            f"The graph build for Course Material {material_id} failed; the material is "
            f"unaffected and remains searchable: {build_err}"
        )
        manifest.outcome = "failed"
        # The reason, not the content. Manifests hold no material text.
        manifest.warnings.append(type(build_err).__name__)

    return await _record(student_id, manifest)


def _read_source(file_path: str) -> str:
    """Read a Source Copy as text.

    `errors="strict"` deliberately: a file that is not valid UTF-8 is not one
    this parser can address offsets into, and silently replacing bytes would
    shift every offset after the replacement and make grounding reject quotes
    that are genuinely present.
    """
    with open(file_path, "r", encoding="utf-8", errors="strict") as handle:
        return handle.read()


async def remove_material_graph(student_id: str, material_id: str) -> None:
    """Delete a Course Material's graph, synchronously, as part of removal.

    **This one raises**, unlike everything else here, and the asymmetry is the
    point. A build that fails costs prerequisites. A removal that fails leaves
    a removed material's Sections and vocabulary in a graph the tutor can still
    read from -- so reporting it as success is precisely the defect the immediate-removal contract
    exists to prevent, and it is the same posture
    `gemini_service.delete_textbook` already takes toward a provider file it
    could not delete.

    A deployment with no graph has nothing to remove and returns quietly.
    """
    client = await get_graph_client()
    if not client.is_configured:
        return
    await store.delete_material(student_id, material_id)


async def verify_material_purged(student_id: str, material_id: str) -> bool:
    """Establish absence rather than assume it (the optional graph boundary's purge-proof shape).

    Material Purge "eliminates every stored representation" and independently
    establishes that it cannot expose the material. This is that check for the
    graph: zero Sections for the material identity and zero ungrounded Concepts
    in the Library, read back after the delete rather than inferred from it.
    """
    client = await get_graph_client()
    if not client.is_configured:
        return True

    sections = await store.count_material_sections(student_id, material_id)
    orphans = await store.count_orphaned_concepts(student_id)
    return sections == 0 and orphans == 0
