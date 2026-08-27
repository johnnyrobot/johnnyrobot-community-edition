"""
The persistence seam.

Every Student-owned record reaches storage through `DocumentStore`. Firebase
Firestore used to sit behind this seam; `PocketBaseStore` does now.
Until one is installed, `NotConfiguredStore` fails loudly rather than
pretending to persist.

The interface is deliberately small and PocketBase-shaped: record CRUD by
identity, plus a filtered query. It carries no Firestore concepts (batches,
server timestamps, document references) and no PocketBase concepts (collection
IDs, expand, superuser tokens), so neither provider leaks into callers.

Ownership is NOT enforced here. Every caller must scope its own reads and
writes to the owning Student.
"""
from abc import ABC, abstractmethod
from typing import Any, Optional


class PersistenceNotConfigured(RuntimeError):
    """Raised when a data operation is attempted with no store configured."""


class DuplicateRecord(RuntimeError):
    """Raised when a unique index rejects a create.

    The Source Identity index is the one that matters: a duplicate means the
    caller should take the Material Update path, not the create path.
    """


class UnfilterableValue(ValueError):
    """A value cannot be rendered into a filter, so the query is refused.

    Every store renders `where` into some provider's query language, and every
    one of them meets values it cannot render safely -- a quote that would
    terminate a literal, say. Refusing rather than escaping is the seam's
    answer, so the refusal is the seam's type: `api/main.py` maps it to a 400
    once, for every route and every implementation.

    A named subclass rather than a bare `ValueError`: catching that in the
    handler would also swallow every incidental `ValueError` in the
    application -- a `pydantic` failure while building a response model, say --
    and report a genuine bug as the caller's fault. It still *is* a
    `ValueError`, so existing `except ValueError` handlers keep working.
    """


class DocumentStore(ABC):
    """Record storage, keyed by collection name and record identity.

    Returned records always include their identity under the key ``id``.
    """

    @abstractmethod
    async def create(self, collection: str, data: dict[str, Any]) -> dict[str, Any]:
        """Create a record and return it, including its assigned ``id``.

        The identity is assigned by the provider. Callers do not choose it.
        """

    @abstractmethod
    async def get(self, collection: str, doc_id: str) -> Optional[dict[str, Any]]:
        """Return one record, or None if it does not exist."""

    @abstractmethod
    async def set(
        self, collection: str, doc_id: str, data: dict[str, Any], *, merge: bool = False
    ) -> None:
        """Create or replace a record. With merge=True, leave absent keys alone."""

    @abstractmethod
    async def update(self, collection: str, doc_id: str, data: dict[str, Any]) -> None:
        """Change the given keys of an existing record."""

    @abstractmethod
    async def delete(self, collection: str, doc_id: str) -> None:
        """Remove a record. Removing an absent record is not an error."""

    @abstractmethod
    async def query(
        self,
        collection: str,
        where: Optional[dict[str, Any]] = None,
        *,
        order_by: Optional[str] = None,
        descending: bool = False,
        limit: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """Return records whose fields all equal the given values."""

    @abstractmethod
    async def delete_where(self, collection: str, where: dict[str, Any]) -> int:
        """Remove every matching record and return how many were removed."""


class NotConfiguredStore(DocumentStore):
    """The store in use before a provider is installed.

    `PocketBaseStore` is installed as the API's lifespan opens and as a Tutor
    Session starts, so reaching this one means the deployment has no
    `POCKETBASE_SUPERUSER_PASSWORD`. Every data operation raises,
    deliberately: a silent no-op store would let ownership and removal defects
    pass tests that only check for absent exceptions.
    """

    _MESSAGE = (
        "No persistence provider is configured. Set POCKETBASE_SUPERUSER_PASSWORD "
        "so the store is installed at startup."
    )

    async def create(self, collection: str, data: dict[str, Any]) -> dict[str, Any]:
        raise PersistenceNotConfigured(self._MESSAGE)

    async def get(self, collection: str, doc_id: str) -> Optional[dict[str, Any]]:
        raise PersistenceNotConfigured(self._MESSAGE)

    async def set(
        self, collection: str, doc_id: str, data: dict[str, Any], *, merge: bool = False
    ) -> None:
        raise PersistenceNotConfigured(self._MESSAGE)

    async def update(self, collection: str, doc_id: str, data: dict[str, Any]) -> None:
        raise PersistenceNotConfigured(self._MESSAGE)

    async def delete(self, collection: str, doc_id: str) -> None:
        raise PersistenceNotConfigured(self._MESSAGE)

    async def query(
        self,
        collection: str,
        where: Optional[dict[str, Any]] = None,
        *,
        order_by: Optional[str] = None,
        descending: bool = False,
        limit: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        raise PersistenceNotConfigured(self._MESSAGE)

    async def delete_where(self, collection: str, where: dict[str, Any]) -> int:
        raise PersistenceNotConfigured(self._MESSAGE)


_store: DocumentStore = NotConfiguredStore()


def get_store() -> DocumentStore:
    """Return the configured store."""
    return _store


def set_store(store: DocumentStore) -> None:
    """Install a store. Used by the provider adapter at startup, and by tests."""
    global _store
    _store = store
