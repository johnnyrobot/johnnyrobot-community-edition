"""
The GraphBuildManifest: what a build is accountable for.

The graph-build determinism contract requires PocketBase to store "a content-free GraphBuildManifest of
identities, epochs, counts, warnings, resource handles, and exact stage roots".
Content-free is the operative word and it is a construction property here, not
a convention: this dataclass has no field a heading, title, or excerpt could
occupy, so a future edit that wanted to leak one would have to add a field and
argue for it.

It is also what makes drift visible. Two builds of one Source Copy that
disagree show as differing counts against identical stage digests, which is
The graph-build determinism contract's Drifted outcome rather than a silent difference nobody notices.
"""
from dataclasses import dataclass, field

GRAPH_MANIFESTS = "graph_build_manifests"

# The GraphBuildPolicy this pipeline implements. Immutable per the graph-build determinism contract: a
# change to how builds work is a new policy id and a shadow build, not an
# in-place redefinition of what the old id meant.
POLICY_ID = "cmg-1"


@dataclass
class GraphBuildManifest:
    """One build, recorded. Identities, counts, digests, warnings. No content."""

    material: str
    generation: int
    outcome: str
    policy_id: str = POLICY_ID
    parser_version: str = ""
    extraction_policy_id: str = ""
    extraction_model: str = ""
    source_digest: str = ""
    sections_digest: str = ""
    section_count: int = 0
    concepts_accepted: int = 0
    edges_accepted: int = 0
    candidates_rejected: int = 0
    warnings: list[str] = field(default_factory=list)

    def as_record(self) -> dict:
        """The PocketBase payload. The owner is stamped by the repository."""
        return {
            "material": self.material,
            "generation": self.generation,
            "outcome": self.outcome,
            "policy_id": self.policy_id,
            "parser_version": self.parser_version,
            "extraction_policy_id": self.extraction_policy_id,
            "extraction_model": self.extraction_model,
            "source_digest": self.source_digest,
            "sections_digest": self.sections_digest,
            "section_count": self.section_count,
            "concepts_accepted": self.concepts_accepted,
            "edges_accepted": self.edges_accepted,
            "candidates_rejected": self.candidates_rejected,
            "warnings": self.warnings,
        }
