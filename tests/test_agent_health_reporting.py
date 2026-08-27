"""
A voice agent that is not working does not report itself as working.

`restart: unless-stopped` guarantees the agent container keeps coming back. It
does not tell anyone it keeps dying. With no healthcheck, an agent that never
once registered with LiveKit -- retrying sixteen times, exiting, restarting,
failing again -- appears in `docker compose ps` as:

    agent   Up 6 seconds

which is indistinguishable from a working voice tutor. Paired with the browser
showing a green connected session for the same failure, neither the operator's
view nor the Student's reflected reality.

Health is declared in the image here, as `Dockerfile.backend` and
`frontend/Dockerfile` already do, so a container reports on itself wherever it
is run rather than only under this Compose file.

What a liveness check can and cannot see is stated in
`test_the_healthcheck_targets_the_port_the_worker_listens_on`.
"""
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ROOT / "docker-compose.prod.yml"
AGENT_DOCKERFILE = ROOT / "Dockerfile.agent"

# The LiveKit worker's HTTP server listens here. Confirmed against the running
# container: 8080 refuses the connection, 8081 answers.
WORKER_HTTP_PORT = 8081

# Which file each service's healthcheck is declared in. A service that reports
# health in neither place is the defect this module exists to prevent.
HEALTH_DECLARED_IN = {
    "pocketbase": COMPOSE,
    "backend": ROOT / "Dockerfile.backend",
    "frontend": ROOT / "frontend" / "Dockerfile",
    "agent": AGENT_DOCKERFILE,
    "neo4j": COMPOSE,
    "qdrant": COMPOSE,
}


@pytest.fixture(scope="module")
def agent_healthcheck():
    """The HEALTHCHECK instruction from Dockerfile.agent, flags and command."""
    return _dockerfile_healthcheck(AGENT_DOCKERFILE)


def test_the_agent_image_declares_a_healthcheck(agent_healthcheck):
    """Without one, a permanently broken agent is permanently "Up"."""
    assert agent_healthcheck is not None


def test_the_health_probe_targets_the_port_the_worker_listens_on():
    """A probe aimed at 8080 could never succeed; nothing has ever listened there."""
    from agent_health import WORKER_HTTP_PORT as probed

    assert probed == WORKER_HTTP_PORT


def test_the_healthcheck_is_not_slower_than_the_restart_loop(agent_healthcheck):
    """Health must be decided faster than the container can hide a restart.

    The failing worker is up for roughly two and a half minutes of retries and
    down only briefly between restarts. A check whose interval times its retry
    budget exceeds that cycle would sample only the up windows and call a
    crash-looping agent healthy.
    """
    flags = agent_healthcheck["flags"]
    interval = _seconds(flags["interval"])
    retries = int(flags["retries"])

    assert interval * retries <= 60


def test_the_agent_dockerfile_exposes_the_port_the_worker_listens_on():
    """EXPOSE named 8080; nothing has ever listened there.

    Probed inside the running container: 8080 gives ConnectionRefused and 8081
    answers. The one port a healthcheck or metrics scrape would target was the
    one port that was wrong.
    """
    exposed = {int(m) for m in re.findall(r"^EXPOSE\s+(\d+)", AGENT_DOCKERFILE.read_text(), re.M)}

    assert WORKER_HTTP_PORT in exposed
    assert 8080 not in exposed


def test_the_healthcheck_asks_whether_livekit_is_reachable(agent_healthcheck):
    """Liveness is the obvious check and it does not work, so it is not the check.

    Measured against a real crash loop: the worker retries sixteen times over
    roughly two and a half minutes and its HTTP server answers throughout, so a
    process-liveness check reported `Up ... (healthy)` continuously while the
    agent had never once registered. The question that separates the two states
    is whether this process can reach LiveKit with these credentials, which is
    exactly what failed (`401, Invalid response status`).
    """
    assert "agent_health" in agent_healthcheck["command"]


def test_the_agent_can_still_recover_on_its_own():
    """Restarting is how a transient LiveKit outage heals without an operator.

    `on-failure` looks like the tidier policy and is a trap here: the worker
    exits 0 even when it has failed, so Docker treats a dead agent as a clean
    shutdown and never restarts it. Measured: `Exited (0)`, `RestartCount=0`.
    Visibility is the healthcheck's job, not the restart policy's.
    """
    services = yaml.safe_load(COMPOSE.read_text())["services"]

    assert services["agent"]["restart"] == "unless-stopped"


def test_every_long_running_service_reports_health():
    """The agent was the only service exempt from saying whether it works.

    Walking the Compose file rather than checking the agent alone keeps a newly
    added service from inheriting the same silence. It has already earned its
    keep once: merging the self-hosted Student Memory work added `neo4j` and
    `qdrant`, and this failed on the merge because Qdrant had no healthcheck --
    a gap git had no way to notice, since both sides merged cleanly.
    """
    services = yaml.safe_load(COMPOSE.read_text())["services"]

    silent = []
    for name in services:
        # caddy is the reverse proxy: it is the thing an external check reaches
        # through, so its liveness is established by the checks behind it.
        if name == "caddy":
            continue
        source = HEALTH_DECLARED_IN.get(name)
        if source is None or not _declares_health(name, source, services):
            silent.append(name)

    assert silent == [], f"services that never report health: {silent}"


# -- the probe's own decisions ---------------------------------------------


@pytest.mark.parametrize(
    "url,key,secret,reason",
    [
        ("", "k", "s", "no URL"),
        ("wss://your-project.livekit.cloud", "k", "s", "the .env.example placeholder"),
        ("wss://real.livekit.cloud", "", "s", "no API key"),
        ("wss://real.livekit.cloud", "k", "", "no API secret"),
        ("   ", "k", "s", "whitespace URL"),
    ],
)
async def test_livekit_is_unreachable_without_usable_credentials(monkeypatch, url, key, secret, reason):
    """Each of these is a deployment that cannot serve a Tutor Session.

    The placeholder case is the one that actually happened: it is non-empty, so
    it passes every emptiness check while resolving to a project nobody owns.
    None of these should reach the network to find that out.
    """
    import agent_health

    monkeypatch.setenv("LIVEKIT_URL", url)
    monkeypatch.setenv("LIVEKIT_API_KEY", key)
    monkeypatch.setenv("LIVEKIT_API_SECRET", secret)

    assert await agent_health.livekit_is_reachable() is False, f"should have refused: {reason}"


async def test_a_worker_that_is_not_listening_is_not_running(monkeypatch):
    """The process-alive half still matters for a hang or an OOM."""
    import agent_health

    def _refused(*args, **kwargs):
        raise ConnectionRefusedError("nothing listening")

    monkeypatch.setattr(agent_health.urllib.request, "urlopen", _refused)

    assert agent_health.worker_is_running() is False


async def test_the_probe_fails_when_the_worker_is_down(monkeypatch):
    """A failing probe must exit non-zero, or the healthcheck reports success."""
    import agent_health

    monkeypatch.setattr(agent_health, "worker_is_running", lambda: False)

    assert agent_health.main() == 1


def _declares_health(name, source: Path, services) -> bool:
    if source == COMPOSE:
        return "healthcheck" in services[name]
    return _dockerfile_healthcheck(source) is not None


def _dockerfile_healthcheck(path: Path):
    """Read a HEALTHCHECK instruction, joining any line continuations."""
    text = re.sub(r"\\\s*\n", " ", path.read_text())
    match = re.search(r"^HEALTHCHECK\s+(.*)$", text, re.M)
    if not match:
        return None
    body = match.group(1)
    flags = {k: v for k, v in re.findall(r"--(\w+)=(\S+)", body)}
    command = body.split("CMD", 1)[1].strip() if "CMD" in body else ""
    return {"flags": flags, "command": command}


def _seconds(value) -> float:
    """Parse a duration ("10s", "1m30s") into seconds."""
    text = str(value).strip()
    total = 0.0
    for amount, unit in re.findall(r"(\d+(?:\.\d+)?)([smh])", text):
        total += float(amount) * {"s": 1, "m": 60, "h": 3600}[unit]
    return total or float(text)
