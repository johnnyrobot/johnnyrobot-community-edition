"""
The errors a store may raise belong to the seam, not to one adapter.

`api/main.py` maps a refused filter value to a clean 400 for every route at
once. That mapping is keyed on an exception class, so whichever module owns the
class decides who the mapping covers. Owned by `pocketbase_store`, it covered
exactly one `DocumentStore` implementation: a second one would raise its own
class, miss the handler, and hand the caller a 500 for a refusal the seam's
contract says is a 400 -- while `api/database/store.py` went on claiming in its
own docstring that no provider leaks into callers.

`PersistenceNotConfigured` and `DuplicateRecord` were already seam-owned.
`UnfilterableValue` now is too.
"""
import inspect

import pytest

from api.database.store import (
    NotConfiguredStore,
    UnfilterableValue,
    set_store,
)

API = "/api/v1"

REFUSED = "Invalid identifier in request"


class _RefusingStore(NotConfiguredStore):
    """A `DocumentStore` that is not the PocketBase one, refusing a query.

    Subclassing `NotConfiguredStore` keeps this to the one method the test is
    about; every other operation stays loud. What matters is that the class
    raised below is the seam's, reached through a store the adapter knows
    nothing about.
    """

    async def query(self, collection, where=None, **kwargs):
        raise UnfilterableValue("Refusing to filter on a quote-bearing value for 'id'")


async def test_a_refusal_from_any_store_is_a_clean_400(client, alice):
    """The 400 belongs to the seam's contract, not to one adapter's filter code.

    The identifier-validation cases in `test_error_responses.py` prove this for the PocketBase
    store, which is also the store that renders the filter -- so they would
    pass just as well with the handler keyed on an adapter-private class. This
    one cannot: the refusal arrives from a store `pocketbase_store` never sees.
    """
    set_store(_RefusingStore())

    response = await client.delete(f"{API}/textbooks/some-material", headers=alice["headers"])

    assert response.status_code == 400
    assert response.json()["detail"] == REFUSED


async def test_a_refusal_from_any_store_is_not_quoted_back(client, alice):
    """A second store inherits the whole guarantee, not just the status code."""
    set_store(_RefusingStore())

    response = await client.delete(f"{API}/textbooks/some-material", headers=alice["headers"])

    assert "Refusing to filter" not in response.text


def test_the_seam_owns_the_refusal():
    """`store.py` declares it, so every implementation raises the same class."""
    assert UnfilterableValue.__module__ == "api.database.store"
    assert issubclass(UnfilterableValue, ValueError)


@pytest.mark.parametrize(
    "module",
    ["api.main", "api.routers.textbooks", "api.routers.canvas"],
)
def test_no_caller_reaches_into_the_adapter_for_it(module):
    """Callers above the seam import from the seam.

    The class is the same object either way, so this cannot be caught by
    behaviour -- and an import of a provider module from `api/main.py` is
    exactly what the seam's docstring promises does not happen.
    """
    import importlib

    source = inspect.getsource(importlib.import_module(module))

    assert "pocketbase_store import UnfilterableValue" not in source
    assert "from api.database.store import" in source
