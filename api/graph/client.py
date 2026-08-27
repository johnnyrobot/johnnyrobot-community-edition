"""
The Neo4j seam: one client per process, bounded, degrading to a no-op.

Shaped after `api/services/student_memory.py`, and for the same reasons. The
driver's constructor does not open a socket, but `verify_connectivity` does,
and a host that drops packets rather than refusing them would hold the event
loop -- and every Student queued behind it -- for however long the OS decides.

Neo4j being unavailable costs prerequisites, never answers. Every consumer
checks `is_configured` and degrades; nothing here raises into a request.

**The cached failure, and its price.** A deployment whose Neo4j was unreachable
when the first build ran has no graph until the process restarts, even after
Neo4j comes back. `student_memory` accepts exactly this trade and states it,
and one seam behaving two ways would be worse than either behaviour: an
Operator who knows "restart to pick the graph back up" should not have to also
know which subsystem re-tries and which does not. Per-build reconnection is a
follow-on, not a silent difference.
"""
import asyncio
import logging

from api.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class NoOpGraphClient:
    """Stands in for the driver when Neo4j is not usable.

    Answers every query the way an empty graph would, so a caller cannot tell
    "there is no graph" from "there is nothing in the graph". That is the
    point: the tutor loses prerequisites and keeps answering.
    """

    is_configured = False

    async def run(self, cypher: str, **params) -> list[dict]:
        return []

    async def close(self) -> None:
        return None


class Neo4jGraphClient:
    """The real client. Holds the driver and hands back plain dicts.

    Plain dicts rather than neo4j `Record`s deliberately: the store above this
    is the only module that knows Cypher, and letting driver types past it
    would put a `neo4j` import in every consumer and make the no-op above a
    liar about its return type.
    """

    is_configured = True

    def __init__(self, driver):
        self._driver = driver

    async def run(self, cypher: str, **params) -> list[dict]:
        async with self._driver.session() as session:
            result = await session.run(cypher, **params)
            return [record.data() async for record in result]

    async def close(self) -> None:
        await self._driver.close()


_UNBUILT = object()
_client = _UNBUILT


async def _connect():
    """Open and verify a driver. Split out so tests can replace the network."""
    from neo4j import AsyncGraphDatabase

    driver = AsyncGraphDatabase.driver(
        settings.neo4j_url,
        auth=(settings.neo4j_username, settings.neo4j_password),
    )
    await driver.verify_connectivity()
    return driver


async def _build_graph_client():
    """Build the real client, or answer with the no-op and say why."""
    if not settings.neo4j_url:
        logger.info("NEO4J_URL is not configured; the Course Material graph is off")
        return NoOpGraphClient()

    if not settings.neo4j_password:
        # Neo4j refuses anonymous auth, so without this the deployment would
        # look configured and fail on its first build instead of here.
        logger.warning(
            "The Course Material graph is off: NEO4J_URL is set but NEO4J_PASSWORD is not"
        )
        return NoOpGraphClient()

    timeout = settings.graph_build_timeout_seconds
    try:
        connecting = _connect()
        driver = await (asyncio.wait_for(connecting, timeout) if timeout else connecting)
        return Neo4jGraphClient(driver)
    except asyncio.TimeoutError:
        logger.warning(f"The Course Material graph is off: Neo4j did not answer within {timeout}s")
        return NoOpGraphClient()
    except Exception as graph_err:
        logger.warning(f"The Course Material graph is off: Neo4j unavailable ({graph_err})")
        return NoOpGraphClient()


async def get_graph_client():
    """The graph client this process uses, built at most once.

    Never raises and never returns None: an absent, misconfigured, or
    unreachable Neo4j comes back as `NoOpGraphClient`, and that answer is
    cached like any other.
    """
    global _client
    if _client is _UNBUILT:
        _client = await _build_graph_client()
    return _client


def set_graph_client(client) -> None:
    """Install a client, or pass None to hand the seam back for a fresh build.

    Mirrors `set_store` and `set_memory_client`: the client is process-wide by
    design, so anything that installs one has to be able to put it back. Tests
    clear it between cases so a client built from one test's settings cannot
    answer the next test's question.
    """
    global _client
    _client = _UNBUILT if client is None else client
