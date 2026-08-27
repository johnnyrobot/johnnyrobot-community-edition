"""
Behaviour while PocketBase is unreachable.

An outage must never look like a bad credential. The browser clears auth state
on 401 and leaves it alone on 503, so mapping an outage to 401 signs every
Student out over a blip.
"""
import re

import pytest
from fastapi.routing import APIRoute

from api.dependencies import get_current_user, get_current_user_id
from api.main import app

API = "/api/v1"

PROTECTED_ROUTES = [
    ("GET", f"{API}/auth/me", None),
    ("GET", f"{API}/users/me", None),
    ("PATCH", f"{API}/users/me", {"name": "New Name"}),
    ("GET", f"{API}/users/me/language", None),
    ("PATCH", f"{API}/users/me/language", {"language": "es-ES"}),
    ("POST", f"{API}/textbooks/upload", None),
    ("GET", f"{API}/textbooks/", None),
    ("DELETE", f"{API}/textbooks/aaaaaaaaaaaaaaa", None),
    ("POST", f"{API}/canvas/token", {"api_token": "dummy-canvas-token"}),
    ("GET", f"{API}/canvas/token", None),
    ("DELETE", f"{API}/canvas/token", None),
    ("POST", f"{API}/canvas/sync", None),
    ("GET", f"{API}/canvas/data", None),
    ("GET", f"{API}/canvas/courses", None),
    ("GET", f"{API}/canvas/stats", None),
    ("POST", f"{API}/session/token", None),
    ("POST", f"{API}/session/end", {"room_name": "dummy-room"}),
    ("GET", f"{API}/session/history", None),
    ("GET", f"{API}/memory/", None),
    ("DELETE", f"{API}/memory/aaaaaaaaaaaaaaa", None),
    ("DELETE", f"{API}/memory/", None),
    ("POST", f"{API}/chat/message", {"message": "hi"}),
    ("GET", f"{API}/capabilities", None),
]


@pytest.mark.parametrize("mode", ["timeout", "refused", "server-error"], ids=["timeout", "refused", "server-error"])
@pytest.mark.parametrize("method,path,body", PROTECTED_ROUTES, ids=[f"{m}-{p}" for m, p, _ in PROTECTED_ROUTES])
async def test_an_outage_produces_service_unavailable(client, provider, alice, mode, method, path, body):
    provider.fail_with(mode)

    response = await client.request(method, path, headers=alice["headers"], json=body)

    assert response.status_code == 503


@pytest.mark.parametrize("mode", ["timeout", "refused", "server-error"], ids=["timeout", "refused", "server-error"])
async def test_an_outage_never_produces_unauthorized(client, provider, alice, mode):
    provider.fail_with(mode)

    statuses = set()
    for method, path, body in PROTECTED_ROUTES:
        response = await client.request(method, path, headers=alice["headers"], json=body)
        statuses.add(response.status_code)

    assert 401 not in statuses


async def test_login_during_an_outage_is_unavailable_not_unauthorized(client, provider, alice):
    provider.fail_with("refused")

    response = await client.post(
        f"{API}/auth/login", json={"email": alice["email"], "password": alice["password"]}
    )

    assert response.status_code == 503


# -- the completeness guard: a completeness guard on the hand-maintained roster ---------
#
# PROTECTED_ROUTES is deliberately hand-maintained rather than generated, so
# each case gets a readable test id and a body that matches its route's
# request model. That is only safe if something else proves the list is
# actually complete -- this is that something else.
def _iter_route_dependants():
    """Yield (full_path, methods, dependant) for every dispatchable route.

    Installed FastAPI (0.141.1) no longer flattens an included sub-router's
    routes directly into `app.routes`. `app.include_router(...)` instead
    leaves a `fastapi.routing._IncludedRouter` wrapper there, whose
    `.original_router.routes` holds that sub-router's own `APIRoute` objects
    -- paths relative to the sub-router's own prefix, e.g. "/auth/login" --
    and whose `.include_context.prefix` holds the prefix `include_router` was
    given, e.g. "/api/v1". Verified directly against the live `app` object
    (not assumed from documentation) that this is genuinely how 0.141.1
    represents an included router, not a stray third-party wrapper: every
    router this app includes shows up this way, and combining the two prefixes
    reproduces exactly the paths the test client actually dispatches to.
    """
    for route in app.routes:
        if isinstance(route, APIRoute):
            yield route.path, route.methods, route.dependant
            continue
        if type(route).__name__ == "_IncludedRouter":
            prefix = route.include_context.prefix or ""
            for sub in route.original_router.routes:
                if isinstance(sub, APIRoute):
                    yield prefix + sub.path, sub.methods, sub.dependant
                elif type(sub).__name__ == "_IncludedRouter":
                    # This walk descends one level. A router included inside
                    # an included router would be skipped, quietly shrinking
                    # what this guard checks, so it stops the test instead.
                    raise AssertionError(
                        f"A router included under {prefix!r} itself includes another "
                        "router. This walk only descends one level, so those routes "
                        "would go undiscovered and the outage guarantee would be "
                        "checked against fewer routes than exist. Make the walk "
                        "recursive."
                    )
        # Anything else (Starlette's plain Route, used for /docs,
        # /openapi.json, /redoc) carries no dependant tree and is never
        # protected -- falling through without yielding is correct for it.


def _depends_on_current_user(dependant) -> bool:
    """True if `dependant`, or anything it (recursively) depends on, is
    `get_current_user` or `get_current_user_id`."""
    if dependant.call in (get_current_user, get_current_user_id):
        return True
    return any(_depends_on_current_user(sub) for sub in dependant.dependencies)


def _path_template_pattern(template: str) -> "re.Pattern[str]":
    """Turn a FastAPI path template into a regex matching a concrete URL.

    `{memory_id}`-style segments become a one-segment wildcard, so
    "/api/v1/memory/{memory_id}" matches the roster's concrete
    "/api/v1/memory/aaaaaaaaaaaaaaa".
    """
    segments = [
        r"[^/]+" if seg.startswith("{") and seg.endswith("}") else re.escape(seg)
        for seg in template.split("/")
    ]
    return re.compile("^" + "/".join(segments) + "$")


def test_the_roster_covers_every_protected_route():
    """A new protected route must not slip past the 503-not-401 guarantee.

    The roster below is hand-maintained so each case has a readable test id.
    This test is what keeps it honest.
    """
    discovered = set()
    for full_path, methods, dependant in _iter_route_dependants():
        if not _depends_on_current_user(dependant):
            continue
        for method in methods:
            if method == "HEAD":
                continue
            discovered.add((method, full_path))

    uncovered = {
        (method, template)
        for method, template in discovered
        if not any(
            roster_method == method and _path_template_pattern(template).match(roster_path)
            for roster_method, roster_path, _ in PROTECTED_ROUTES
        )
    }

    assert not uncovered, (
        "PROTECTED_ROUTES is missing route(s) that depend on "
        f"get_current_user/get_current_user_id: {sorted(uncovered)}"
    )
