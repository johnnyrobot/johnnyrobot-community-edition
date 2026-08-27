"""
`DocumentStore` implemented against PocketBase.

Ownership is NOT enforced here; the repository above it does that. This layer
translates the small record vocabulary of `DocumentStore` into PocketBase's
records API and nothing more.
"""
import logging
from typing import Any, Optional


from api.database.pocketbase_client import PocketBaseClient
from api.database.store import DocumentStore, DuplicateRecord, UnfilterableValue

logger = logging.getLogger(__name__)

# PocketBase caps perPage at 500; the collections here are small enough that
# one page is always sufficient, and a silent truncation would read as an
# empty Student Library.
_PAGE_SIZE = 500


def build_filter(where: Optional[dict[str, Any]]) -> str:
    """Render an equality conjunction as a PocketBase filter.

    Filter construction lives in exactly one function so the fake and the live
    contract test have a single shape to agree about. Values are rendered as
    quoted literals; a value carrying a quote would terminate the literal, so
    it is refused rather than escaped.
    """
    if not where:
        return ""
    clauses = []
    for field, value in where.items():
        rendered = "" if value is None else str(value)
        if '"' in rendered or "\\" in rendered:
            raise UnfilterableValue(
                f"Refusing to filter on a quote-bearing value for {field!r}"
            )
        clauses.append(f'{field} = "{rendered}"')
    return " && ".join(clauses)


class PocketBaseStore(DocumentStore):
    def __init__(self, client: PocketBaseClient):
        self._client = client

    async def create(self, collection: str, data: dict[str, Any]) -> dict[str, Any]:
        response = await self._client.request(
            "POST", f"/api/collections/{collection}/records", json=data
        )
        if response.status_code == 400:
            # Check if this is a unique-index rejection; any other 400 is a normal validation error
            try:
                body = response.json()
                # Inspect the data dict for validation_not_unique codes
                if isinstance(body, dict) and isinstance(body.get("data"), dict):
                    for field_errors in body["data"].values():
                        if isinstance(field_errors, dict) and field_errors.get("code") == "validation_not_unique":
                            raise DuplicateRecord(
                                f"PocketBase refused a create in {collection}: {body.get('data')}"
                            )
            except (ValueError, KeyError, AttributeError):
                # Body is not JSON, not a dict, or lacks expected structure; fall through to raise_for_status
                pass
        # Not a unique-index rejection; raise for any error status code
        response.raise_for_status()
        return response.json()

    async def get(self, collection: str, doc_id: str) -> Optional[dict[str, Any]]:
        response = await self._client.request(
            "GET", f"/api/collections/{collection}/records/{doc_id}"
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    async def set(
        self, collection: str, doc_id: str, data: dict[str, Any], *, merge: bool = False
    ) -> None:
        if not merge:
            raise NotImplementedError(
                "PocketBase can only merge fields via PATCH. To replace a record, "
                "delete it and create a new one, so the intent is explicit."
            )
        response = await self._client.request(
            "PATCH", f"/api/collections/{collection}/records/{doc_id}", json=data
        )
        if response.status_code == 404:
            # The fallback create is checked like any other write. Returning
            # on an unread response would make a refusal -- a locked
            # createRule, a validation error -- indistinguishable from a
            # stored record, which is the silent no-op store `DocumentStore`
            # exists to rule out.
            fallback = await self._client.request(
                "POST", f"/api/collections/{collection}/records", json={**data, "id": doc_id}
            )
            fallback.raise_for_status()
            return
        response.raise_for_status()

    async def update(self, collection: str, doc_id: str, data: dict[str, Any]) -> None:
        response = await self._client.request(
            "PATCH", f"/api/collections/{collection}/records/{doc_id}", json=data
        )
        response.raise_for_status()

    async def delete(self, collection: str, doc_id: str) -> None:
        response = await self._client.request(
            "DELETE", f"/api/collections/{collection}/records/{doc_id}"
        )
        if response.status_code == 404:
            return
        response.raise_for_status()

    async def query(
        self,
        collection: str,
        where: Optional[dict[str, Any]] = None,
        *,
        order_by: Optional[str] = None,
        descending: bool = False,
        limit: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"perPage": limit or _PAGE_SIZE}
        expression = build_filter(where)
        if expression:
            params["filter"] = expression
        if order_by:
            params["sort"] = f"{'-' if descending else ''}{order_by}"

        response = await self._client.request(
            "GET", f"/api/collections/{collection}/records", params=params
        )
        response.raise_for_status()
        body = response.json()
        items = body.get("items", [])
        total_items = body.get("totalItems", len(items))

        # Warn if the query was truncated by page size
        if total_items > len(items):
            logger.warning(
                f"Query of {collection} was truncated: {len(items)} items returned, "
                f"but {total_items} total items exist. Consider paging or increasing limit."
            )

        return items

    async def delete_where(self, collection: str, where: dict[str, Any]) -> int:
        total_removed = 0
        last_ids: set[str] = set()
        # Loop until no more matches remain; PocketBase paginates at _PAGE_SIZE
        while True:
            matches = await self.query(collection, where, limit=_PAGE_SIZE)
            if not matches:
                break
            current_ids = {record["id"] for record in matches}
            # Guard against infinite loop: if the same records come back after deletion, stop
            if current_ids == last_ids:
                logger.warning(
                    f"delete_where on {collection} stopped: deletes may have failed silently. "
                    f"Total removed: {total_removed}."
                )
                break
            for record in matches:
                await self.delete(collection, record["id"])
            total_removed += len(matches)
            last_ids = current_ids
        return total_removed
