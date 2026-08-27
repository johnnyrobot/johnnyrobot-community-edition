"""
Student Memory is optional, and it is built once (process-wide memory client construction).

`mem0.AsyncMemoryClient.__init__` validates its API key over the network and
raises `ValueError: Error: Invalid API key` when one is absent or wrong. Every
call site sits inside a request handler or a session start, so without a key
The first chat or voice request would fail outright -- a hard external
dependency on the critical path.

Without a key, chat and voice must still work -- they simply do not remember
across sessions, so a missing or expired key degrades a lab rather than
breaking it.

**Why one seam rather than a guard at each site.** Every site used to build its
own client inline, guarded by its own try/except. The degradation was right and
The cost was not: that validation is a blocking `requests.get` to which mem0
passes no timeout, running on the event loop, once per request. A key that will
never work was refused over and over -- and each refusal held the loop for a
full round trip, so one Student's stalled Mem0 lookup stalled every other
Student queued behind it. `get_memory_client()` fixes all three at once:

  - **once per process.** The outcome is cached, success or failure alike, so
    The round trip is paid at most once rather than per request.
  - **off the event loop.** The build runs in a worker thread, so the loop
    keeps serving while it waits.
  - **bounded.** A build that hangs is abandoned at `mem0_timeout_seconds` --
    mem0's own call would wait on an OS-level TCP timeout instead.

The API warms this as its lifespan opens, so no Student is ever the one who
pays. The voice agent, a separate process, warms it on its first Tutor Session.

Caching the failure has a price, and it is the right one: a deployment whose
Mem0 was unreachable at startup remembers nothing until the process restarts,
even after Mem0 comes back. Re-validating instead would put that round trip
back on the request path -- exactly what this exists to take off it -- and a
restart is a cheap remedy for a Deployment Operator who can see the warning in
The log.

A cold burst can lose a race and build twice; the loser's client is discarded.
That is a bounded, one-time duplication with no user-visible effect, and the
startup warm makes it rare, which is why there is no lock here to go wrong.
"""
import asyncio
import logging

from mem0 import AsyncMemory, AsyncMemoryClient

from api.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# The embedding model self-hosted Student Memory uses, and the width it is
# asked for. Both are stated here rather than left to mem0's defaults: mem0
# defaults to `models/text-embedding-004`, which this API version has retired.
EMBEDDING_MODEL = "models/gemini-embedding-001"
EMBEDDING_DIMENSIONS = 768


class NoOpMemoryClient:
    """Stands in for `mem0.AsyncMemoryClient` when Mem0 is not usable.

    The methods are the ones call sites actually await -- `add`, `search`,
    `get_all`, `delete` -- plus `delete_all` for interface completeness. Each
    returns what an empty Student Memory would return, so a caller cannot tell
    The difference between "nothing remembered yet" and "remembering is off".
    """

    async def add(self, *args, **kwargs):
        return {"results": []}

    async def search(self, *args, **kwargs):
        return []

    async def get_all(self, *args, **kwargs):
        return []

    async def delete(self, *args, **kwargs):
        return {"message": "deleted"}

    async def delete_all(self, *args, **kwargs):
        return {"message": "deleted"}


class SelfHostedMemoryClient:
    """Gives mem0's OSS memory the shape the hosted client has.

    The two disagree about what a lookup returns. `AsyncMemoryClient.search`
    and `.get_all` answer with a list; `AsyncMemory`'s answer with
    `{"results": [...]}`. Call sites iterate the answer -- `[m['memory'] for m
    in memories]` -- so handed the OSS shape they iterate the dict's keys and
    fail with `string indices must be integers`, reaching the log only as
    "Mem0 error (non-fatal)" while the deployment silently stops remembering.

    Rather than teach every call site which backend is installed, the
    difference is absorbed here: `NoOpMemoryClient` already promises the hosted
    shapes, and this promises the same ones, so nothing downstream can tell the
    three apart. That is the whole point of the seam.
    """

    def __init__(self, memory):
        self._memory = memory

    @staticmethod
    def _as_list(answer):
        """Unwrap `{"results": [...]}`, tolerating a bare list."""
        if isinstance(answer, dict):
            return answer.get("results", [])
        return answer or []

    async def add(self, *args, **kwargs):
        # The hosted client answers `add` with a dict, and so does this.
        return await self._memory.add(*args, **kwargs)

    async def search(self, *args, **kwargs):
        return self._as_list(await self._memory.search(*args, **kwargs))

    async def get_all(self, *args, **kwargs):
        return self._as_list(await self._memory.get_all(*args, **kwargs))

    async def delete(self, *args, **kwargs):
        return await self._memory.delete(*args, **kwargs)

    async def delete_all(self, *args, **kwargs):
        return await self._memory.delete_all(*args, **kwargs)


# Distinct from None, which is a perfectly good answer to "what is installed?"
# in a process that has decided the answer is nothing. This says "not yet
# asked", and it is the only state a build can start from.
_UNBUILT = object()

_client = _UNBUILT


async def get_memory_client():
    """The Student Memory client this process uses, built at most once.

    Never raises and never returns None: a Mem0 that is absent, rejecting, or
    unreachable comes back as `NoOpMemoryClient`, and that answer is cached
    like any other so the next caller pays nothing for it.
    """
    global _client
    if _client is _UNBUILT:
        _client = await _build_memory_client()
    return _client


def set_memory_client(client) -> None:
    """Install a client, or pass None to hand the seam back for a fresh build.

    Mirrors `set_store` / `set_provider_client`: the client is process-wide by
    design -- one deployment, one Mem0 -- so anything that installs one has to
    be able to put it back. Tests clear it between cases so a cache built from
    one test's settings cannot answer the next test's question.
    """
    global _client
    _client = _UNBUILT if client is None else client


def _self_hosted_config():
    """The mem0 config for a self-hosted deployment, or None if it cannot be built.

    Gemini is the LLM and embedder deliberately. requirements.txt records an
    accepted conflict between mem0ai's declared `openai<1.110.0` and the
    `openai>=2` that livekit-agents pins; routing mem0 through Gemini leaves
    that conflict dormant rather than exercising it on every remembered turn.

    A missing graph is not a missing memory. the optional graph boundary requires a
    graph-disabled deployment to stay valid, so an unset `NEO4J_URL` drops the
    graph and keeps the vector store -- that costs relationships between
    memories, not memory.

    A missing vector store is different, and answers None. There would be
    nowhere to put embeddings, and mem0's own default would quietly reach for a
    Qdrant on localhost that nobody configured.
    """
    if not settings.google_api_key:
        logger.warning(
            "Student Memory is a no-op: MEM0_SELF_HOSTED is set but GOOGLE_API_KEY "
            "is not, and Gemini is its LLM and embedder"
        )
        return None

    if not settings.qdrant_host:
        logger.warning(
            "Student Memory is a no-op: MEM0_SELF_HOSTED is set but QDRANT_HOST is not, "
            "so there is nowhere to keep what is remembered"
        )
        return None

    config = {
        "llm": {
            "provider": "gemini",
            "config": {"model": "gemini-2.5-flash", "api_key": settings.google_api_key},
        },
        "embedder": {
            "provider": "gemini",
            # Named explicitly because mem0's own default is
            # `models/text-embedding-004`, which this API version has retired:
            # left to itself the embedder answers every call with
            # "404 NOT_FOUND ... is not supported for embedContent", so a
            # self-hosted deployment would appear to build and then remember
            # nothing.
            #
            # `embedding_dims` is passed to Gemini as `output_dimensionality`
            # and is also the width Qdrant's collection is created at, so the
            # two cannot be allowed to drift. 768 is a supported truncation of
            # gemini-embedding-001's native width.
            "config": {
                "model": EMBEDDING_MODEL,
                "embedding_dims": EMBEDDING_DIMENSIONS,
                "api_key": settings.google_api_key,
            },
        },
        "vector_store": {
            "provider": "qdrant",
            # `embedding_model_dims` is a second, separate statement of the
            # same number as the embedder's `embedding_dims`, and mem0 defaults
            # it to OpenAI's 1536 regardless of which embedder is configured.
            # Left alone it creates the collection 1536 wide, then writes 768
            # wide vectors into it, and Qdrant answers every call with a bare
            # `400 (Bad Request)` -- which surfaces only as "Mem0 error
            # (non-fatal)" and a deployment that quietly never remembers.
            "config": {
                "host": settings.qdrant_host,
                "port": settings.qdrant_port,
                "collection_name": "student_memory",
                "embedding_model_dims": EMBEDDING_DIMENSIONS,
            },
        },
    }

    if settings.neo4j_url:
        config["graph_store"] = {
            "provider": "neo4j",
            "config": {
                "url": settings.neo4j_url,
                "username": settings.neo4j_username,
                "password": settings.neo4j_password,
            },
        }
    else:
        logger.info("NEO4J_URL is not configured; Student Memory runs without a graph")

    return config


async def _build_memory_client():
    """Build the real client, or answer with the no-op and say why."""
    # Self-hosted first: a deployment that has opted in has said where its
    # Student Memory lives, and a leftover hosted key must not quietly send
    # remembered exchanges somewhere the Operator did not choose.
    if settings.mem0_self_hosted:
        config = _self_hosted_config()
        if config is None:
            return NoOpMemoryClient()
        # `AsyncMemory.from_config` is a coroutine function whose body is
        # synchronous -- it validates the config and then constructs, and the
        # construction opens sockets to Qdrant and Neo4j on the calling thread.
        # Awaiting it here would block the event loop for exactly as long as
        # those connections take, which is the hazard this seam exists to
        # avoid. Driving it on its own loop inside a worker thread keeps the
        # blocking off ours without assuming its body stays await-free.
        memory = await _bounded(lambda: asyncio.run(AsyncMemory.from_config(config)))
        if isinstance(memory, NoOpMemoryClient):
            return memory
        return SelfHostedMemoryClient(memory)

    if not settings.mem0_api_key:
        logger.info("MEM0_API_KEY is not configured; Student Memory is a no-op")
        return NoOpMemoryClient()

    return await _bounded(lambda: AsyncMemoryClient(api_key=settings.mem0_api_key))


async def _bounded(build):
    """Run a blocking build off the loop, bounded, degrading to the no-op.

    Shared by both backends because the hazard is the same either way: the
    constructor opens sockets synchronously, and a host that drops packets
    rather than refusing them would hold the event loop -- and every Student
    queued behind it -- for however long the OS decides.
    """
    timeout = settings.mem0_timeout_seconds
    try:
        # A thread, because these constructors are blocking.
        building = asyncio.to_thread(build)
        return await (asyncio.wait_for(building, timeout) if timeout else building)
    except asyncio.TimeoutError:
        # The thread is left to finish on its own -- it is holding a socket,
        # not a lock, and nothing is waiting on its answer any more.
        logger.warning(
            f"Student Memory is a no-op: Mem0 did not answer within {timeout}s"
        )
        return NoOpMemoryClient()
    except Exception as mem_err:
        logger.warning(f"Student Memory is a no-op: Mem0 unavailable ({mem_err})")
        return NoOpMemoryClient()
