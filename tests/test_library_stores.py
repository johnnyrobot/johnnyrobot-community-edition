"""
Each Student Library gets its own File Search store.

The per-Library search boundary prohibits "a shared global index protected only by caller-supplied
metadata filters"; the per-Library provider-store boundary prohibits "shared cross-Library stores" and
"display-name discovery" by name. Until per-Library store isolation these tests could not have
been written: every Student's Course Materials lived in one store found by its
display name, and the owner clause in the metadata filter was the only thing
between one Student Library and another.

The store a search reads is now recorded on the Student's record and passed in.
A caller who does not supply one cannot fall back to a shared store, because
after this change there is no shared store to fall back to.
"""
import pytest

from api.database.repository import get_repository
from api.services.gemini_service import GeminiService

STORE = "fileSearchStores/alice-lib-4k2j9x1m"
OTHER_STORE = "fileSearchStores/bob-lib-8h3n5p7q"


@pytest.fixture
def service():
    """A GeminiService that never reaches the network.

    Nothing here creates, lists, or queries a File Search store: these tests
    are about which store name a search is aimed at, which is decided before
    any request is made.
    """
    return GeminiService()


# -- The store is an argument, not a discovery -------------------------------


def test_a_search_names_the_store_it_was_given(service):
    """The tool config points at the Library store the caller resolved."""
    tool = service.get_search_tool_config("alice", STORE)

    assert tool.file_search.file_search_store_names == [STORE]


def test_two_students_searches_name_different_stores(service):
    """Two Student Libraries are two stores, not two filters over one store.

    This is the property the per-Library search boundary asks for and the shared-store design could
    not provide: aiming Bob's search at Alice's store requires naming it.
    """
    alice = service.get_search_tool_config("alice", STORE)
    bob = service.get_search_tool_config("bob", OTHER_STORE)

    assert alice.file_search.file_search_store_names != bob.file_search.file_search_store_names


def test_a_search_without_a_store_is_a_type_error(service):
    """Forgetting the store must break loudly, never read a default one.

    The same construction that commit a9f4eef gave the owner filter: a
    required positional argument, so a forgotten scope raises rather than
    silently widening. A default value here would reintroduce the shared
    store as a fallback.
    """
    with pytest.raises(TypeError):
        service.get_search_tool_config("alice")


def test_there_is_no_shared_store_to_fall_back_to(service):
    """The display-name lookup the per-Library provider-store boundary prohibits is gone, not merely unused.

    While `_get_or_create_store` exists, a future caller can reach a store
    every Student shares. Its absence is the guarantee.
    """
    assert not hasattr(service, "_get_or_create_store")


@pytest.mark.parametrize(
    "unusable_store",
    [None, "", "   "],
    ids=["none", "empty", "blank"],
)
def test_an_unusable_store_fails_closed(service, unusable_store):
    """A missing store name is refused, not passed to the provider.

    An empty store list would let the provider decide what to search, and a
    provider that defaults to "everything" would be a cross-Library read.
    """
    with pytest.raises(ValueError):
        service.get_search_tool_config("alice", unusable_store)


def test_the_owner_clause_survives_the_change(service):
    """Per-Library stores add a boundary; they do not replace the filter.

    The per-Library search boundary prohibits an index protected *only* by caller-supplied filters.
    With one store per Library the filter is the second layer, so it keeps
    earning its place rather than being retired as redundant.
    """
    tool = service.get_search_tool_config("alice", STORE)

    assert 'uploaded_by = "alice"' in tool.file_search.metadata_filter


# -- Resolving the store from the Student's record ---------------------------


async def test_a_library_store_is_read_from_the_students_record(provider):
    """The store is recorded, because it cannot be derived.

    Gemini appends an opaque suffix to every store it creates, so a Library's
    store name is not a function of the Student identity. It has to be stored
    and read back.
    """
    student_id = provider.add_student(
        "alice@example.com", "alice-password", library_store_name=STORE
    )

    resolved = await GeminiService().resolve_library_store(student_id)

    assert resolved == STORE


async def test_a_student_with_no_uploads_has_no_store(provider):
    """A Library nobody has uploaded to resolves to nothing, not to a default.

    Returning a shared store here — or creating one on the search path — is
    exactly the failure this change exists to remove. The caller answers as
    it already does for an empty Library.
    """
    student_id = provider.add_student("alice@example.com", "alice-password")

    resolved = await GeminiService().resolve_library_store(student_id)

    assert resolved is None


async def test_two_students_resolve_to_their_own_stores(provider):
    """Resolution is per Student, through the owner-scoped repository."""
    alice = provider.add_student(
        "alice@example.com", "alice-password", library_store_name=STORE
    )
    bob = provider.add_student(
        "bob@example.com", "bob-password", library_store_name=OTHER_STORE
    )

    service = GeminiService()

    assert await service.resolve_library_store(alice) == STORE
    assert await service.resolve_library_store(bob) == OTHER_STORE


async def test_recording_a_store_puts_it_on_the_student_record(provider):
    """A newly created store is durable, so the next search can find it."""
    student_id = provider.add_student("alice@example.com", "alice-password")
    service = GeminiService()

    await service.record_library_store(student_id, STORE)

    student = await get_repository().get_student(student_id)
    assert student["library_store_name"] == STORE
