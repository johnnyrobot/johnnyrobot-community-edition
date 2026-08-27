"""
Everything that could reach a Cypher string passes through here first.

Library isolation is implemented as a property plus an unconditional filter,
which is weaker than database-level isolation -- it
depends on every query being written correctly rather than on the engine
refusing. That makes the values going into those queries worth more scrutiny,
not less, so an unusable identity fails closed here exactly the way
`gemini_service._canonical_owner` fails closed rather than being escaped.
"""
import re

# A PocketBase record id: 15 characters, alphanumeric (the PocketBase identity contract). Both a
# Student Library key and a Course Material identity are record ids.
#
# fullmatch, not match: Python's $ also matches before a trailing newline, so
# "...{15}\n" would otherwise pass. Same trap as gemini_service._MATERIAL_ID.
_RECORD_ID = re.compile(r"[a-zA-Z0-9]{15}")

# Prerequisite chains are shallow by nature and an unbounded traversal over a
# cyclic graph is a way to hang the tutor. Five hops is far past anything a
# Student would be read back.
MAX_TRAVERSAL_DEPTH = 5


def canonical_library_key(value) -> str:
    """Validate the Student Library key that scopes every node and every query.

    This is the only thing separating one Student Library from another in a
    Neo4j Community deployment, so it is checked against the record-id shape
    rather than merely escaped -- an identity that is not a record id cannot
    be one this deployment issued.
    """
    candidate = str(value) if value is not None else ""
    if not _RECORD_ID.fullmatch(candidate):
        raise ValueError("Missing or malformed Student Library key")
    return candidate


def canonical_material_id(value) -> str:
    """Validate a Course Material identity."""
    candidate = str(value) if value is not None else ""
    if not _RECORD_ID.fullmatch(candidate):
        raise ValueError("Malformed course material identifier")
    return candidate


def canonical_depth(value) -> int:
    """Validate a traversal depth, which is the one value that cannot be a parameter.

    Cypher does not accept a parameter in a variable-length pattern -- `*1..$d`
    is a syntax error -- so the depth is interpolated into the query text. That
    makes it the single place in this subsystem where a value becomes query
    structure, and it is therefore checked to be an int in range rather than
    merely truthy. `bool` is excluded explicitly because `True == 1` in Python
    and `isinstance(True, int)` is also True.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("Traversal depth must be an integer")
    if not 1 <= value <= MAX_TRAVERSAL_DEPTH:
        raise ValueError(f"Traversal depth must be between 1 and {MAX_TRAVERSAL_DEPTH}")
    return value
