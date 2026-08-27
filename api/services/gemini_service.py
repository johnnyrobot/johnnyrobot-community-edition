"""
Gemini Service for managing Textbooks via File Search API.
"""
from google import genai
from google.genai import types
from api.config import get_settings
from api.database.repository import get_repository
from api.graph.build import build_material_graph, remove_material_graph
import logging
import os
import re
import mimetypes
from uuid import uuid4

logger = logging.getLogger(__name__)
settings = get_settings()

# Prefixes the display name of every Library store, so an Operator taking
# inventory can tell this deployment's stores from anything else in the
# project. It is a label and nothing more: the per-Library provider-store boundary prohibits display-name
# discovery, so no code path ever looks a store up by this string. The
# authoritative name is the one recorded on the Student's record.
STORE_DISPLAY_NAME_PREFIX = "Johnny Robot Community Edition Library"

# The field on the Student's record holding their Library's store.
LIBRARY_STORE_FIELD = "library_store_name"


def _canonical_owner(user_id) -> str:
    """Validate the Student identity that scopes every search.

    This clause is the only thing separating one Student Library from another,
    so an unusable identity fails closed instead of being escaped. Student
    identifiers are alphanumeric, so nothing legitimate is rejected here.
    """
    owner = str(user_id).strip() if user_id is not None else ""
    if not owner or '"' in owner or "\\" in owner:
        raise ValueError("Missing or malformed student identity")
    return owner


# A PocketBase record id: 15 characters, alphanumeric (the PocketBase identity contract).
#
# fullmatch, not match: Python's $ also matches before a trailing
# newline, so "...{15}\n" would otherwise pass.
_MATERIAL_ID = re.compile(r"[a-zA-Z0-9]{15}")


def _canonical_store(store_name) -> str:
    """Validate the Library store a search is about to read.

    A blank store name would leave `file_search_store_names` empty and let the
    provider decide what to search. Whatever it decides, the decision would not
    be this Student's Library, so it fails here instead.
    """
    store = str(store_name).strip() if store_name is not None else ""
    if not store:
        raise ValueError("Missing Student Library store")
    return store


def _document_name(operation, store_name: str) -> str:
    """The full resource name of the Document an import created.

    `ImportFileResponse` carries the bare document id and its parent store
    without the `fileSearchStores/` prefix, while `documents.delete` wants the
    full path. Assembling it here keeps that shape in one place.

    Returns "" if the provider did not report one. Removal treats that as a
    material with nothing to delete rather than guessing at a name.
    """
    response = getattr(operation, "response", None)
    document = getattr(response, "document_name", "") if response is not None else ""
    if not document:
        return ""
    if document.startswith("fileSearchStores/"):
        return document
    return f"{store_name}/documents/{document}"


def _canonical_material_id(material_id) -> str:
    """Normalise a Course Material identity, rejecting anything malformed.

    Identities are PocketBase record ids. The value is interpolated into a
    File Search metadata filter, so one carrying a quote could terminate the
    literal and OR past the owner clause. The pattern check guarantees the
    emitted value is alphanumeric only.
    """
    candidate = str(material_id) if material_id is not None else ""
    if not _MATERIAL_ID.fullmatch(candidate):
        raise ValueError("Malformed course material identifier")
    return candidate


_MIME_TYPES = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
    ".pdf": "application/pdf",
}


def _guess_mime_type(file_path: str) -> str:
    """Resolve a mime type, since Gemini requires one for markdown."""
    mime_type, _ = mimetypes.guess_type(file_path)
    if mime_type:
        return mime_type
    return _MIME_TYPES.get(os.path.splitext(file_path)[1].lower(), "application/octet-stream")


class GeminiService:
    def __init__(self):
        if not settings.google_api_key:
            logger.warning("Google API Key not set. Gemini Service will not function.")
            self.client = None
        else:
            self.client = genai.Client(api_key=settings.google_api_key)
        
        self.collection_name = "course_materials"

    def _delete_document(self, document_name: str) -> None:
        """Remove an imported Document from its Library store.

        `force` because a plain delete is refused with 400 FAILED_PRECONDITION,
        "Cannot delete non-empty Document" — a Document with chunks in it,
        which is every Document that was ever searchable.
        """
        self.client.file_search_stores.documents.delete(
            name=document_name, config={"force": True}
        )

    async def resolve_library_store(self, user_id: str) -> str | None:
        """The File Search store holding one Student Library, or None.

        Recorded rather than derived: Gemini appends an opaque suffix to every
        store it creates, so a Library's store name is not a function of the
        Student identity and cannot be recomputed. It is also never discovered
        by display name, which the per-Library provider-store boundary prohibits — the record is the only
        authority on which store belongs to which Library.

        None means this Student has never uploaded. The search path must treat
        that as an empty Library, never as a reason to create a store or to
        reach for one someone else is using.
        """
        student = await get_repository().get_student(_canonical_owner(user_id))
        if not student:
            return None
        return (student.get(LIBRARY_STORE_FIELD) or "").strip() or None

    async def record_library_store(self, user_id: str, store_name: str) -> None:
        """Bind a Library to its store, so the next search can find it again."""
        await get_repository().update_student(
            _canonical_owner(user_id),
            {LIBRARY_STORE_FIELD: _canonical_store(store_name)},
        )

    async def _get_or_create_library_store(self, user_id: str) -> str:
        """This Student's Library store, created on first upload.

        Creation happens here and nowhere else. The search path deliberately
        cannot reach this method: a store that appears because someone
        searched would be a store with no Course Materials in it, and the
        code that made it would be the code that must never widen a search.

        Provider note (per-Library store isolation): store creation is rate limited to 60 per
        minute per project, not capped by count — 49 stores existed
        concurrently under test with no count-based refusal. Lazy creation at
        one Student's upload pace never approaches that. A bulk re-provision
        of a whole cohort would, and must throttle.
        """
        existing = await self.resolve_library_store(user_id)
        if existing:
            return existing

        owner = _canonical_owner(user_id)
        try:
            store = self.client.file_search_stores.create(
                config={"display_name": f"{STORE_DISPLAY_NAME_PREFIX} {owner}"}
            )
        except Exception as e:
            logger.error(f"Failed to create the File Search store for Library {owner}: {e}")
            raise

        await self.record_library_store(owner, store.name)
        logger.info(f"Created File Search store {store.name} for Library {owner}")
        return store.name

    def _upload_and_index(
        self,
        file_path: str,
        provider_file_name: str,
        title: str,
        user_id: str,
        material_id: str,
        store_name: str,
    ):
        """Upload a Source Copy and index it in File Search; wait for the import to finish.

        Shared by `upload_textbook` and `update_material_content` — they
        differ only in what happens before this sequence (create a record vs.
        look one up) and after it (first write vs. cutover-then-cleanup), not
        in the metadata schema or polling behavior itself, so that has one
        place to change instead of two.

        Returns the uploaded file and the name of the Document the import
        created. The Document is what makes the material searchable and it
        outlives the file it came from, so removal needs its name and the
        import is the only place the provider ever gives it.
        """
        uploaded_file = self.client.files.upload(
            file=file_path,
            config={
                "name": provider_file_name,
                "display_name": title,
                "mime_type": _guess_mime_type(file_path),
            },
        )

        operation = self.client.file_search_stores.import_file(
            file_search_store_name=store_name,
            file_name=uploaded_file.name,
            config={
                "custom_metadata": [
                    {"key": "textbook_id", "string_value": material_id},
                    {"key": "title", "string_value": title},
                    {"key": "uploaded_by", "string_value": user_id},
                ]
            },
        )
        while not operation.done:
            import time
            time.sleep(1)
            operation = self.client.operations.get(operation)

        return uploaded_file, _document_name(operation, store_name)

    async def upload_textbook(
        self,
        file_path: str,
        title: str,
        user_id: str,
        *,
        source_identity: str = "",
        material_source: str = "upload",
    ) -> str:
        """Index a Course Material and record it in the Student Library.

        The record is created first, because its identity is what goes into
        The provider's metadata and is what the owner filter narrows on
        (the PocketBase identity contract). It starts Processing and ends Ready or Failed: a
        half-finished import stays visible to its Student without becoming
        searchable, which is what Material Status is for.
        """
        if not self.client:
            raise ValueError("Google API Key not configured")

        repository = get_repository()
        material = await repository.create_material(
            user_id,
            {
                "title": title,
                "status": "processing",
                "source_identity": source_identity,
                "material_source": material_source,
            },
        )
        material_id = material["id"]

        try:
            store_name = await self._get_or_create_library_store(user_id)
            logger.info(
                f"Uploading {file_path} as {_guess_mime_type(file_path)} for material {material_id}"
            )

            uploaded_file, document_name = self._upload_and_index(
                file_path, f"cm-{material_id}", title, user_id, material_id, store_name
            )

            await repository.update_material(
                user_id,
                material_id,
                {
                    "status": "ready",
                    "provider_file_name": uploaded_file.name,
                    "provider_uri": uploaded_file.uri,
                    "provider_store_name": store_name,
                    "provider_document_name": document_name,
                },
            )
            logger.info(f"Course Material {material_id} is ready")

            # Build the graph here, and not earlier. The material is Ready and
            # already searchable, so a graph failure costs prerequisites and
            # nothing else -- building before Ready would make upload visibly
            # slower and let this block a material RAG could already serve.
            #
            # Here specifically, rather than in the router, because this is the
            # last point at which the Source Copy exists: the router deletes
            # The staged file in its `finally`, and nothing retains a copy.
            await build_material_graph(user_id, material_id, title, file_path)

            return material_id

        except Exception as e:
            logger.error(f"Failed to index Course Material {material_id}: {e}")
            await repository.update_material(user_id, material_id, {"status": "failed"})
            raise

    async def update_material_content(
        self, material_id: str, file_path: str, title: str, user_id: str
    ) -> None:
        """Replace a Course Material's Source Copy and search representation.

        The replacement is indexed before the existing one is dropped, so
        Ready content stays available throughout and a failure leaves the
        existing material exactly as it was (the atomic-update contract).
        """
        repository = get_repository()
        existing = await repository.get_material(user_id, material_id)
        if not existing:
            raise ValueError(f"Course Material {material_id} is not in this Student Library")

        store_name = await self._get_or_create_library_store(user_id)
        # A fresh suffix on every call: a same-title re-sync (the ordinary
        # steady-state case) must not compute a name identical to the
        # provider file it is about to replace.
        replacement_name = f"cm-{material_id}-{uuid4().hex[:8]}"

        uploaded_file, document_name = self._upload_and_index(
            file_path, replacement_name, title, user_id, material_id, store_name
        )

        # Only now is the cutover safe.
        await repository.update_material(
            user_id,
            material_id,
            {
                "title": title,
                "status": "ready",
                "provider_file_name": uploaded_file.name,
                "provider_uri": uploaded_file.uri,
                "provider_store_name": store_name,
                "provider_document_name": document_name,
            },
        )

        # The superseded Document first: while it is in the store, the old
        # content is still retrievable and a re-sync would leave the Student
        # searching two generations of the same material at once.
        previous_document = existing.get("provider_document_name")
        if previous_document and previous_document != document_name:
            try:
                self._delete_document(previous_document)
            except Exception as e:
                logger.error(
                    f"Superseded document {previous_document} was not deleted, so the "
                    f"previous version of Course Material {material_id} is still "
                    f"searchable: {e}"
                )

        previous = existing.get("provider_file_name")
        if previous and previous != uploaded_file.name:
            try:
                self.client.files.delete(name=previous)
            except Exception as e:
                # The material is correct; the superseded copy is a leak, not
                # a failure of the update. It is logged so a reset can find it.
                logger.error(f"Superseded provider file {previous} was not deleted: {e}")

        # A new generation from the new Source Copy, cut over after it is
        # written. Material Update replaces content atomically and the graph
        # follows; the previous generation goes only once its replacement is
        # in place. Never raises -- a Course Material that updated correctly
        # must not be reported as failed because its graph did not.
        await build_material_graph(user_id, material_id, title, file_path)

    async def list_textbooks(self, user_id: str):
        """List one Student's Course Materials.

        user_id is required. It previously defaulted to None, which meant
        "every Student's materials" — the same class of defect as the search
        scoping bug fixed earlier. Every provider request carries both store and
        owner scope.
        """
        if not user_id:
            raise ValueError("user_id is required; a Student Library is never global")
        return await get_repository().list_materials(user_id)

    async def delete_textbook(self, textbook_id: str, user_id: str) -> None:
        """Remove a Course Material from listing, search, provider storage, and the graph.

        Removal immediately and permanently excludes the material from listing
        and search. Deleting only the metadata record leaves the file
        retrievable in the File Search store, so removal is not complete until
        The provider file is gone too.

        A provider failure is raised rather than swallowed, and the material is
        left Failed rather than silently vanished: reporting success while the
        material remains searchable is the failure this guards against. This is
        immediate Material Removal, not an independently verified purge of every
        recoverable representation.
        """
        repository = get_repository()
        record = await repository.get_material(user_id, textbook_id)
        if not record:
            return

        # Material Removal "immediately makes a Course Material unavailable for
        # listing and search", and the graph is a stored representation, so
        # immediately includes it. First, and synchronously: a graph still
        # holding this material's Sections is one the tutor could still read
        # prerequisites out of. Unlike a build, this is allowed to raise --
        # see remove_material_graph.
        await remove_material_graph(user_id, textbook_id)

        provider_file_name = record.get("provider_file_name")
        if not provider_file_name:
            # Nothing was ever indexed — a Processing or Failed material.
            await repository.delete_material(user_id, textbook_id)
            return

        # The Document before the file, because the Document is what search
        # reads. Deleting the file alone leaves the material searchable:
        # measured against live Gemini, a query still returned the content
        # after `files.delete` and stopped only once the Document was gone
        # (per-Library store isolation). Removal that stops at the file reports success while
        # The material is still retrievable, which is what the immediate-removal contract forbids.
        provider_document_name = record.get("provider_document_name")
        if provider_document_name:
            try:
                self._delete_document(provider_document_name)
                logger.info(
                    f"Deleted document {provider_document_name} for {textbook_id}"
                )
            except Exception as e:
                await repository.update_material(user_id, textbook_id, {"status": "failed"})
                logger.error(
                    f"Course Material {textbook_id} could not be removed: document "
                    f"{provider_document_name} was not deleted, so it is still "
                    f"searchable: {e}"
                )
                raise RuntimeError(
                    f"Course Material {textbook_id} was not removed; it is still "
                    f"searchable in its Student Library"
                ) from e

        try:
            self.client.files.delete(name=provider_file_name)
            logger.info(f"Deleted provider file {provider_file_name} for {textbook_id}")
        except Exception as e:
            await repository.update_material(user_id, textbook_id, {"status": "failed"})
            logger.error(
                f"Course Material {textbook_id} could not be removed: provider file "
                f"{provider_file_name} was not deleted: {e}"
            )
            raise RuntimeError(
                f"Course Material {textbook_id} was not removed; its provider copy "
                f"could not be deleted"
            ) from e

        try:
            await repository.delete_material(user_id, textbook_id)
        except Exception as e:
            # The provider copy is already gone at this point. If the record
            # delete itself now fails (store outage, network blip), leaving
            # The record's prior status untouched would let it keep reading
            # as healthy -- typically "ready" -- while it is already gone
            # from search. That is the mirror image of the guarantee this
            # method exists for, so it is stamped Failed here too, and the
            # failure is still reported rather than swallowed. (this
            # status write can itself fail -- a smaller, accepted residual
            # window, not a transaction across two systems.)
            logger.error(
                f"Course Material {textbook_id}'s provider copy was deleted, but "
                f"the record delete itself failed; marking it failed rather than "
                f"leaving it looking healthy: {e}"
            )
            await repository.update_material(user_id, textbook_id, {"status": "failed"})
            raise

    def get_search_tool_config(self, user_id: str, store_name: str, textbook_id: str = None):
        """
        Get the tool configuration for one Student's Library.

        Each Student Library is its own File Search store (the per-Library search boundary,
        The per-Library provider-store boundary), so the store is the isolation boundary and the owner filter
        is the second layer behind it rather than the only one. `store_name`
        is a required positional argument for the same reason `user_id` is: a
        caller who forgets it raises TypeError instead of quietly reading a
        store every Student shares. There is no shared store to reach.

        The owner filter is applied unconditionally even so. textbook_id can
        only narrow within the owner's materials, never widen beyond them.
        """
        clauses = [f'uploaded_by = "{_canonical_owner(user_id)}"']
        if textbook_id:
            clauses.append(f'textbook_id = "{_canonical_material_id(textbook_id)}"')

        file_search_config = {
            "file_search_store_names": [_canonical_store(store_name)],
            "metadata_filter": " AND ".join(clauses),
        }

        return types.Tool(
            file_search=types.FileSearch(**file_search_config)
        )

    def query_textbook(self, query: str, user_id: str, store_name: str, textbook_id: str = None):
        """
        Query one Student's Library using Gemini File Search.

        `store_name` is resolved by the caller, which has the async access to
        The Student's record that this synchronous method does not. It is
        required for the same reason it is required on
        `get_search_tool_config`: there is no shared store to default to.
        """
        try:
            tool = self.get_search_tool_config(user_id, store_name, textbook_id)
            
            # Use a model that supports File Search
            model_name = "gemini-2.5-flash-lite"  # Same model as chat.py that works
            
            response = self.client.models.generate_content(
                model=model_name,
                contents=query,
                config=types.GenerateContentConfig(
                    tools=[tool]
                )
            )
            
            return response.text
        except Exception as e:
            logger.error(f"Gemini File Search query failed: {e}")
            return "I encountered an error searching the textbook."
