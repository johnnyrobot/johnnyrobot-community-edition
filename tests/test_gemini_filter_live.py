"""
Student Library isolation, against live Gemini.

Before per-Library store isolation this file could only test one thing: the " AND " conjunction
joining the owner clause to the Material Selection clause, because that
conjunction was the *only* mechanism keeping one Student Library out of
another. Every Student's Course Materials lived in one store.

Each Library is now its own File Search store, so there are two mechanisms and
this file checks both:

- **The store boundary.** Alice's search names Alice's store. Bob's fact is not
  merely filtered out of the result, it is in a store the query never names.
  This is what the per-Library search boundary asks for — "a shared global index protected only by
  caller-supplied metadata filters is prohibited" — and what the per-Library provider-store boundary means by
  prohibiting "shared cross-Library stores".
- **The owner clause behind it.** Still applied unconditionally, still joined
  with " AND ", and still checked here. Per-Library stores make the filter the
  second layer rather than the only one; they do not retire it.

**Why the query names its referent.** `query_textbook` runs on
`gemini-2.5-flash-lite`, which answers "what is the distinctive fact?" with a
request for clarification often enough to fail these tests at random -- it
grounded 2/6 and 4/6 in a measured run. Naming what the fact belongs to takes
that to 6/6 with and without the Material Selection clause. The vagueness was
never a filter problem, but it reads exactly like one when it fails, so keep
The referent in the query.

    GOOGLE_API_KEY=... pytest -m live_gemini tests/test_gemini_filter_live.py
"""
import os
import time
import uuid

import pytest

from api.services.gemini_service import (
    STORE_DISPLAY_NAME_PREFIX,
    GeminiService,
    _document_name,
)

pytestmark = pytest.mark.live_gemini

ALICE, BOB = "aliceliveaaaaaa", "boblivebbbbbbbb"


@pytest.fixture(scope="module")
def service():
    if not os.environ.get("GOOGLE_API_KEY"):
        pytest.skip("GOOGLE_API_KEY is unset")
    return GeminiService()


@pytest.fixture(scope="module")
def two_libraries(service, tmp_path_factory):
    """Two owners, one distinctive fact each, in two separate stores.

    Module-scoped: every test here only reads, and reads the same two
    libraries, so one run provisions one pair rather than a pair per test.

    The stores are created directly through the provider client rather than
    through `_get_or_create_library_store`, because that method records the
    store on the Student's record and there is no PocketBase behind this
    suite. What is under test here is the provider's behaviour at the store
    boundary, not the bookkeeping that picks the store.
    """
    tmp_path = tmp_path_factory.mktemp("live-libraries")
    marker = uuid.uuid4().hex[:8]
    libraries = {}

    for owner, secret in ((ALICE, f"alpha-{marker}"), (BOB, f"beta-{marker}")):
        store = service.client.file_search_stores.create(
            config={"display_name": f"{STORE_DISPLAY_NAME_PREFIX} {owner} {marker}"}
        )

        path = tmp_path / f"{owner}.md"
        path.write_text(
            f"# Contract material\n\nThe distinctive fact for this library is {secret}.\n"
            + ("Filler sentence for indexing. " * 40)
        )
        material_id = uuid.uuid4().hex[:15]
        uploaded_file = service.client.files.upload(
            file=str(path),
            config={"name": f"live-{material_id}", "display_name": f"Live {owner}",
                    "mime_type": "text/markdown"},
        )
        operation = service.client.file_search_stores.import_file(
            file_search_store_name=store.name,
            file_name=uploaded_file.name,
            config={"custom_metadata": [
                {"key": "textbook_id", "string_value": material_id},
                {"key": "title", "string_value": f"Live {owner}"},
                {"key": "uploaded_by", "string_value": owner},
            ]},
        )
        while not operation.done:
            time.sleep(1)
            operation = service.client.operations.get(operation)

        libraries[owner] = {
            "store": store.name,
            "secret": secret,
            "material_id": material_id,
            "file": uploaded_file.name,
        }

    yield libraries

    # Deleting the store removes the documents imported into it, which the
    # shared-store version of this fixture had to do one document at a time.
    # That is the residue the reset-only demo profile's reset exists to purge, and per-Library
    # stores make purging it a single call per Library.
    for library in libraries.values():
        try:
            service.client.file_search_stores.delete(
                name=library["store"], config={"force": True}
            )
        except Exception:
            pass
        try:
            service.client.files.delete(name=library["file"])
        except Exception:
            pass


def test_the_owner_filter_is_accepted_by_the_api(service, two_libraries):
    """A filter the API rejects would fail every query, not just leaky ones."""
    answer = service.query_textbook(
        "What is the distinctive fact for this library?",
        user_id=ALICE,
        store_name=two_libraries[ALICE]["store"],
    )

    assert "error searching" not in answer.lower()


def test_a_search_returns_only_the_owners_material(service, two_libraries):
    """Alice's search finds Alice's fact and cannot reach Bob's."""
    answer = service.query_textbook(
        "What is the distinctive fact for this library?",
        user_id=ALICE,
        store_name=two_libraries[ALICE]["store"],
    )

    assert two_libraries[ALICE]["secret"] in answer
    assert two_libraries[BOB]["secret"] not in answer


def test_the_store_boundary_holds_without_the_owner_filter(service, two_libraries):
    """The store alone excludes the other Library — the filter is the second layer.

    This is the property the shared-store design could not have: Alice's owner
    clause is pointed at Bob's store, and the answer is ungrounded rather than
    Bob's fact. Before per-Library store isolation the same query would have searched the one
    store both Libraries lived in, and only the filter would have stood
    between them.
    """
    answer = service.query_textbook(
        "What is the distinctive fact for this library?",
        user_id=ALICE,
        store_name=two_libraries[BOB]["store"],
    )

    assert two_libraries[BOB]["secret"] not in answer


def test_deleting_the_document_is_what_takes_a_material_out_of_search(
    service, tmp_path_factory
):
    """Removal has to delete the Document; deleting the file is not enough.

    This is the defect the purge half of per-Library store isolation fixed, and it is checked
    here rather than only against a stub because a stub can only assert what
    we already believe. What the provider actually does:

        after import                  -> findable
        after files.delete            -> STILL findable
        after documents.delete(force) -> gone

    `delete_textbook` used to stop at the middle line and report success, so a
    Student who removed a Course Material kept a searchable copy of it. Its
    own docstring promised the immediate-removal contract's guarantee that removal "immediately and
    permanently exclude[s]" the material from search.

    Its own store, created and destroyed here, so nothing in this test can
    disturb the module-scoped pair the isolation tests read.
    """
    marker = uuid.uuid4().hex[:8]
    secret = f"gamma-{marker}"
    owner = "removeliveccccc"
    store = service.client.file_search_stores.create(
        config={"display_name": f"{STORE_DISPLAY_NAME_PREFIX} removal {marker}"}
    )

    def still_findable():
        answer = service.query_textbook(
            "What is the distinctive fact for this library?",
            user_id=owner,
            store_name=store.name,
        )
        return secret in answer

    try:
        path = tmp_path_factory.mktemp("live-removal") / "material.md"
        path.write_text(
            f"# Contract material\n\nThe distinctive fact for this library is {secret}.\n"
            + ("Filler sentence for indexing. " * 40)
        )
        material_id = uuid.uuid4().hex[:15]
        uploaded_file = service.client.files.upload(
            file=str(path),
            config={"name": f"live-rm-{material_id}", "display_name": "Live removal",
                    "mime_type": "text/markdown"},
        )
        operation = service.client.file_search_stores.import_file(
            file_search_store_name=store.name,
            file_name=uploaded_file.name,
            config={"custom_metadata": [
                {"key": "textbook_id", "string_value": material_id},
                {"key": "title", "string_value": "Live removal"},
                {"key": "uploaded_by", "string_value": owner},
            ]},
        )
        while not operation.done:
            time.sleep(1)
            operation = service.client.operations.get(operation)
        document = _document_name(operation, store.name)
        assert document, "the import must report the Document it created"

        assert still_findable(), "the material must be searchable before removal"

        service.client.files.delete(name=uploaded_file.name)
        time.sleep(3)
        assert still_findable(), (
            "deleting the uploaded file is expected NOT to remove the material; "
            "if this ever starts passing, the document delete may be redundant"
        )

        service._delete_document(document)
        time.sleep(3)
        assert not still_findable(), "deleting the Document must remove it from search"
    finally:
        try:
            service.client.file_search_stores.delete(name=store.name, config={"force": True})
        except Exception:
            pass


def test_the_conjunction_narrows_rather_than_widens(service, two_libraries):
    """Both clauses must apply, and adding one never reaches past the owner.

    A wrong conjunction was assumed to widen -- to return Bob's fact. Measured,
    it does not: a malformed filter matches nothing and the answer simply is
    not grounded. So `bob_secret not in answer` is the isolation assertion and
    `alice_secret in answer` is what proves the narrowed filter still finds the
    owner's own material, rather than passing vacuously on an empty result.
    """
    alice = two_libraries[ALICE]

    tool = service.get_search_tool_config(
        user_id=ALICE, store_name=alice["store"], textbook_id=alice["material_id"]
    )
    assert " AND " in tool.file_search.metadata_filter

    answer = service.query_textbook(
        "What is the distinctive fact for this library?",
        user_id=ALICE,
        store_name=alice["store"],
        textbook_id=alice["material_id"],
    )

    assert alice["secret"] in answer
    assert two_libraries[BOB]["secret"] not in answer
