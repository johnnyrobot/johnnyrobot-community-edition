"""
Student Library isolation.

A Tutor Session may search only the authenticated Student's own Course
Materials.
"""
import pytest

from api.services.gemini_service import GeminiService


# The Library store the caller has already resolved from the Student's record.
# This store is per Student rather than shared, while every search also carries
# an owner filter.
STORE = "fileSearchStores/alice-lib-4k2j9x1m"


@pytest.fixture
def service():
    """A GeminiService that never reaches the network.

    The store is supplied by the caller, so nothing here lists, creates, or
    queries a File Search store.
    """
    return GeminiService()


def test_search_is_always_scoped_to_the_owning_student(service):
    """Every search constrains uploaded_by, even with no Material Selection."""
    tool = service.get_search_tool_config(user_id="alice", store_name=STORE)

    assert 'uploaded_by = "alice"' in tool.file_search.metadata_filter


def test_material_selection_narrows_within_the_owner(service):
    """Naming a Course Material adds to the owner filter, never replaces it."""
    material_id = "k27bq9x4m1p8ze0"  # a PocketBase record id (the PocketBase identity contract)

    tool = service.get_search_tool_config(
        user_id="alice", store_name=STORE, textbook_id=material_id
    )

    metadata_filter = tool.file_search.metadata_filter
    assert 'uploaded_by = "alice"' in metadata_filter
    assert f'textbook_id = "{material_id}"' in metadata_filter


def test_a_uuid_is_no_longer_a_valid_material_identity(service):
    """A Course Material identity is a PocketBase record id, not a uuid4 (the PocketBase identity contract).

    PocketBase record ids are 15 characters; a uuid4 is 36 and must be
    rejected outright rather than accepted as a second, looser identity shape.
    """
    uuid_like = "1f7c9a52-3b6e-4d21-9c88-0a5e4b7d3f10"

    with pytest.raises(ValueError):
        service.get_search_tool_config(user_id="alice", store_name=STORE, textbook_id=uuid_like)


def test_a_trailing_newline_on_an_otherwise_valid_id_is_rejected(service):
    """Python's $ also matches just before a trailing newline.

    A 15-character alphanumeric id followed by "\\n" is 16 bytes and not a
    real PocketBase record id; `fullmatch` (not `match` with a `$`-anchored
    pattern) must refuse it rather than silently accept it with the newline
    still attached.
    """
    trailing_newline = "aaaaaaaaaaaaaaa\n"

    with pytest.raises(ValueError):
        service.get_search_tool_config(
            user_id="alice", store_name=STORE, textbook_id=trailing_newline
        )


def test_a_forged_material_selection_cannot_widen_the_search(service):
    """A selection crafted to break out of the quoted literal is rejected.

    Interpolated raw, 'x" OR uploaded_by = "bob' yields
    'uploaded_by = "alice" AND textbook_id = "x" OR uploaded_by = "bob"',
    which OR-s past the owner clause and returns another Student's Library.
    """
    forged = 'x" OR uploaded_by = "bob'

    with pytest.raises(ValueError):
        service.get_search_tool_config(user_id="alice", store_name=STORE, textbook_id=forged)


@pytest.mark.parametrize(
    "unusable_owner",
    [None, "", "   ", 'alice" OR uploaded_by != "'],
    ids=["none", "empty", "blank", "quote-bearing"],
)
def test_search_without_a_usable_owner_fails_closed(service, unusable_owner):
    """No owner means no search, never an unscoped one.

    Emitting uploaded_by = "None" would match nothing by luck rather than by
    rule; a quote-bearing owner would dissolve the clause entirely.
    """
    with pytest.raises(ValueError):
        service.get_search_tool_config(user_id=unusable_owner, store_name=STORE)
