"""
Whether this voice agent can actually do its job.

Run as the container's healthcheck. Exit 0 means the agent is able to serve a
Tutor Session; any non-zero exit means it is not, and the container is reported
unhealthy.

Liveness alone is the wrong check here, which is worth recording because it is
The obvious one. The LiveKit worker retries a failed connection sixteen times
over roughly two and a half minutes before exiting, and its HTTP server answers
throughout -- so a check that asks "is the process up?" reports healthy for the
entire failure. Measured against a real crash loop: `docker compose ps` showed
`Up ... (healthy)` continuously while the agent had never once registered.

So this asks the question that actually distinguishes the two states: can this
process reach LiveKit with these credentials? That is what failed in the
observed outage (`401, message='Invalid response status'`), and it is what a
Student needs to be true before pressing "Start Voice Session".

What it still cannot see: a worker that is connected and registered but taking
no jobs. The worker's own HTTP endpoint reports load, identity, and SDK
version, never registration state, so that failure needs a signal from LiveKit
rather than from here.
"""
import asyncio
import os
import sys
import urllib.request

WORKER_HTTP_PORT = int(os.getenv("AGENT_HTTP_PORT", "8081"))

# Shipped in .env.example, and found in this repo's working .env. A placeholder
# is non-empty, so it satisfies every emptiness check while resolving to a
# project nobody owns.
PLACEHOLDER_URL = "wss://your-project.livekit.cloud"

TIMEOUT_SECONDS = 4


def worker_is_running() -> bool:
    """The worker's own HTTP server answers, so the process is alive."""
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{WORKER_HTTP_PORT}/", timeout=TIMEOUT_SECONDS
        ) as response:
            return response.status == 200
    except Exception:
        return False


async def livekit_is_reachable() -> bool:
    """LiveKit answers an authenticated call with this deployment's credentials.

    `list_rooms` is the cheapest authenticated round trip available. It fails
    for every reason that would stop the agent working -- wrong key, wrong
    secret, placeholder or unroutable URL, LiveKit down -- and succeeds only
    when a Tutor Session could genuinely be served.
    """
    url = (os.getenv("LIVEKIT_URL") or "").strip()
    key = (os.getenv("LIVEKIT_API_KEY") or "").strip()
    secret = (os.getenv("LIVEKIT_API_SECRET") or "").strip()

    if not url or url == PLACEHOLDER_URL or not key or not secret:
        return False

    from livekit import api

    client = api.LiveKitAPI(url, key, secret)
    try:
        await asyncio.wait_for(
            client.room.list_rooms(api.ListRoomsRequest()), timeout=TIMEOUT_SECONDS
        )
        return True
    except Exception:
        return False
    finally:
        try:
            await client.aclose()
        except Exception:
            pass


def main() -> int:
    if not worker_is_running():
        print("agent: worker HTTP server is not answering", file=sys.stderr)
        return 1

    if not asyncio.run(livekit_is_reachable()):
        print("agent: cannot reach LiveKit with the configured credentials", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
