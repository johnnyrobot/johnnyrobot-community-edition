"""
Every Cypher string in this application, and the Library boundary they carry.

The optional graph boundary describes a Graph Cell: one Aura instance and user database per
Student Library, with the engine refusing a cross-Library read. Neo4j Community
has no multi-database support, so that is unachievable here, and the design
implements the bound as an immutable `library_key` property plus a filter every
query applies unconditionally.

That is weaker, and the weakness has a precise shape: it depends on every query
being written correctly rather than on the engine refusing. So the queries live
here, in one dict, where `tests/test_graph_isolation.py` can read them and fail
a build that adds one without the filter. A query written inline somewhere else
would be invisible to that check, which is why there are none.

Required before this holds real Student content: either an isolation-capable
binding as the optional graph boundary describes, or an amendment recording property-bounding as
an accepted Community implementation. This module implements the bound; it does
not grant the amendment.

Values are always parameters. Concept display names come from a model and
contain whatever it produced, so nothing is interpolated -- with one documented
exception, traversal depth, which Cypher will not accept as a parameter inside
a variable-length pattern and which `canonical_depth` therefore validates to a
bounded int.
"""
import logging

from api.graph.client import get_graph_client
from api.graph.grounding import Grounded
from api.graph.identity import canonical_depth, canonical_library_key, canonical_material_id
from api.graph.parser import Section

logger = logging.getLogger(__name__)


def _concept_key(display_name: str) -> str:
    """Normalise a Concept name into its identity within a Student Library.

    Known limitation, recorded in the design and not solved here: two materials
    calling the same idea by different names produce two Concepts, and one name
    meaning two things produces one. Good enough for a Library of a handful of
    materials; do not read it as solved, and do not carry it to a shared corpus.
    """
    return " ".join(display_name.split()).casefold()


CYPHER = {
    "merge_material": """
        MERGE (m:Material {library_key: $library_key, material_id: $material_id})
        SET m.title = $title, m.source_digest = $source_digest, m.generation = $generation
    """,
    "merge_section": """
        MATCH (m:Material {library_key: $library_key, material_id: $material_id})
        MERGE (s:Section {library_key: $library_key, section_id: $section_id})
        SET s.material_id = $material_id, s.path = $path, s.ordinal = $ordinal,
            s.heading = $heading, s.char_start = $char_start, s.char_end = $char_end,
            s.digest = $digest, s.generation = $generation
        MERGE (s)-[:PART_OF]->(m)
    """,
    "link_follows": """
        MATCH (a:Section {library_key: $library_key, section_id: $previous_id})
        MATCH (b:Section {library_key: $library_key, section_id: $section_id})
        MERGE (a)-[:FOLLOWS]->(b)
    """,
    "merge_defined_in": """
        MATCH (s:Section {library_key: $library_key, section_id: $section_id})
        MERGE (c:Concept {library_key: $library_key, concept_key: $concept_key})
        SET c.display_name = $display_name, c.generation = $generation
        MERGE (c)-[d:DEFINED_IN]->(s)
        SET d.excerpt = $excerpt, d.char_start = $char_start, d.char_end = $char_end
    """,
    "merge_requires": """
        MATCH (s:Section {library_key: $library_key, section_id: $section_id})
        MERGE (c:Concept {library_key: $library_key, concept_key: $concept_key})
        SET c.display_name = $display_name, c.generation = $generation
        MERGE (p:Concept {library_key: $library_key, concept_key: $requires_key})
        ON CREATE SET p.display_name = $requires_name, p.generation = $generation
        MERGE (c)-[r:REQUIRES]->(p)
        SET r.excerpt = $excerpt, r.char_start = $char_start, r.char_end = $char_end,
            r.section_id = $section_id, r.generation = $generation
    """,
    # Cutover. Only this material's earlier generations go; Concepts are
    # Library-scoped and survive on their grounding, which the orphan reap
    # below is what actually decides.
    "delete_prior_generations": """
        MATCH (s:Section {library_key: $library_key, material_id: $material_id})
        WHERE s.generation < $generation
        DETACH DELETE s
    """,
    "delete_material_sections": """
        MATCH (s:Section {library_key: $library_key, material_id: $material_id})
        DETACH DELETE s
    """,
    "delete_material_node": """
        MATCH (m:Material {library_key: $library_key, material_id: $material_id})
        DETACH DELETE m
    """,
    # A Concept with no DEFINED_IN left has no grounding left. Deleting it is
    # what stops a removed Course Material's vocabulary surviving in the graph
    # and being spoken by the tutor. DETACH takes its REQUIRES edges with it.
    "reap_orphaned_concepts": """
        MATCH (c:Concept {library_key: $library_key})
        WHERE NOT (c)-[:DEFINED_IN]->(:Section)
        WITH collect(c) AS orphans
        FOREACH (orphan IN orphans | DETACH DELETE orphan)
        RETURN size(orphans) AS reaped
    """,
    "count_material_sections": """
        MATCH (s:Section {library_key: $library_key, material_id: $material_id})
        RETURN count(s) AS total
    """,
    "count_orphaned_concepts": """
        MATCH (c:Concept {library_key: $library_key})
        WHERE NOT (c)-[:DEFINED_IN]->(:Section)
        RETURN count(c) AS total
    """,
    # The traversal. `ALL(n IN nodes(path) ...)` is the whole isolation story
    # for a multi-hop read: without it the walk starts inside the Library and
    # is free to leave it by the first REQUIRES edge it meets. The `{depth}`
    # placeholder is filled by `canonical_depth`, never by a caller's value.
    "prerequisites_of": """
        MATCH (c:Concept {{library_key: $library_key, concept_key: $concept_key}})
        MATCH path = (c)-[:REQUIRES*1..{depth}]->(p:Concept)
        WHERE ALL(n IN nodes(path) WHERE n.library_key = $library_key)
        RETURN DISTINCT p.concept_key AS concept_key,
                        p.display_name AS display_name,
                        length(path) AS hops
        ORDER BY hops, concept_key
    """,
}


async def write_generation(
    library_key: str,
    material_id: str,
    title: str,
    generation: int,
    sections: list[Section],
    grounded: list[Grounded],
    source_digest: str = "",
) -> None:
    """Write one generation of a Course Material's graph.

    Nothing is mutated in place. A rebuild writes a new generation and
    `cut_over` removes the previous one afterwards, mirroring Material Update:
    "Existing Ready content remains available until its replacement is Ready."
    A half-built graph is therefore never the one being queried.
    """
    key = canonical_library_key(library_key)
    material = canonical_material_id(material_id)
    client = await get_graph_client()

    await client.run(
        CYPHER["merge_material"],
        library_key=key,
        material_id=material,
        title=title,
        source_digest=source_digest,
        generation=generation,
    )

    previous_id = None
    for section in sections:
        await client.run(
            CYPHER["merge_section"],
            library_key=key,
            material_id=material,
            section_id=section.section_id,
            path=section.path,
            ordinal=section.ordinal,
            heading=section.heading,
            char_start=section.char_start,
            char_end=section.char_end,
            digest=section.digest,
            generation=generation,
        )
        if previous_id is not None:
            await client.run(
                CYPHER["link_follows"],
                library_key=key,
                previous_id=previous_id,
                section_id=section.section_id,
            )
        previous_id = section.section_id

    for edge in grounded:
        if edge.kind == "defines":
            await client.run(
                CYPHER["merge_defined_in"],
                library_key=key,
                section_id=edge.section_id,
                concept_key=_concept_key(edge.concept),
                display_name=edge.concept,
                excerpt=edge.excerpt,
                char_start=edge.char_start,
                char_end=edge.char_end,
                generation=generation,
            )
        else:
            await client.run(
                CYPHER["merge_requires"],
                library_key=key,
                section_id=edge.section_id,
                concept_key=_concept_key(edge.concept),
                display_name=edge.concept,
                requires_key=_concept_key(edge.requires),
                requires_name=edge.requires,
                excerpt=edge.excerpt,
                char_start=edge.char_start,
                char_end=edge.char_end,
                generation=generation,
            )


async def cut_over(library_key: str, material_id: str, generation: int) -> None:
    """Remove every earlier generation of this material, after the new one is written.

    Called only once `write_generation` has returned. A failed build therefore
    leaves the previous generation exactly as it was, which is the graph
    analogue of "a failed update leaves the existing content unchanged".
    """
    key = canonical_library_key(library_key)
    material = canonical_material_id(material_id)
    client = await get_graph_client()

    await client.run(
        CYPHER["delete_prior_generations"],
        library_key=key,
        material_id=material,
        generation=generation,
    )
    await reap_orphaned_concepts(key)


async def delete_material(library_key: str, material_id: str) -> None:
    """Remove a Course Material from the graph, synchronously.

    Material Removal "immediately makes a Course Material unavailable for
    listing and search", and the graph is a stored representation, so
    immediately includes this. Not queued: a queue would put a window between
    removal and the tutor no longer being able to speak the material's
    vocabulary.
    """
    key = canonical_library_key(library_key)
    material = canonical_material_id(material_id)
    client = await get_graph_client()

    await client.run(CYPHER["delete_material_sections"], library_key=key, material_id=material)
    await client.run(CYPHER["delete_material_node"], library_key=key, material_id=material)
    await reap_orphaned_concepts(key)


async def reap_orphaned_concepts(library_key: str) -> int:
    """Delete Concepts with no grounding left, and say how many.

    A Concept is Library-scoped and may be grounded in several materials. When
    its last DEFINED_IN goes it has no grounding at all, and an ungrounded
    Concept would let a removed material's vocabulary survive in the graph
    where the tutor could still speak it.
    """
    key = canonical_library_key(library_key)
    client = await get_graph_client()
    rows = await client.run(CYPHER["reap_orphaned_concepts"], library_key=key)
    return rows[0]["reaped"] if rows else 0


async def count_material_sections(library_key: str, material_id: str) -> int:
    """How many Sections this material still has. Material Purge checks this."""
    key = canonical_library_key(library_key)
    material = canonical_material_id(material_id)
    client = await get_graph_client()
    rows = await client.run(CYPHER["count_material_sections"], library_key=key, material_id=material)
    return rows[0]["total"] if rows else 0


async def count_orphaned_concepts(library_key: str) -> int:
    """How many ungrounded Concepts remain. Material Purge checks this too."""
    key = canonical_library_key(library_key)
    client = await get_graph_client()
    rows = await client.run(CYPHER["count_orphaned_concepts"], library_key=key)
    return rows[0]["total"] if rows else 0


async def prerequisites_of(library_key: str, concept_key: str, depth: int = 3) -> list[dict]:
    """What a Student needs to understand before this Concept.

    The one question this graph exists to answer, and the only claim it makes.
    """
    key = canonical_library_key(library_key)
    client = await get_graph_client()
    cypher = CYPHER["prerequisites_of"].format(depth=canonical_depth(depth))
    return await client.run(cypher, library_key=key, concept_key=_concept_key(concept_key))
