"""
Main FastAPI application for Multi-User LiveKit Voice Agent with RAG.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from api.config import get_settings
from api.database.pocketbase_client import ProviderUnavailable
from api.database.store import UnfilterableValue
from api.rate_limit import LoginRateLimitMiddleware
from api.routers import auth, users, sessions, memory, canvas, textbooks, chat, capabilities  # documents temporarily disabled
from api.services.student_memory import get_memory_client

import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

# Get settings
settings = get_settings()

# The process comes up holding the Deployment Operator's PocketBase credential
# and goes down releasing the connections it opened with it. Both halves live in
# one context manager because they are one story: what the API installs on the
# way in is exactly what it has to hand back on the way out.
#
# This was a pair of `@app.on_event` handlers until lifespan-based startup wiring. `on_event` is
# deprecated and slated for removal, and what it carries here is load-bearing --
# The store install and the client close -- so its removal would have failed the
# API at import rather than degrading it.
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Install the PocketBase client and store, and release them afterwards."""
    from api.database.pocketbase_client import PocketBaseClient
    from api.database.pocketbase_store import PocketBaseStore
    from api.database.store import set_store
    from api.dependencies import (
        IdentityProviderNotConfigured,
        get_provider_client,
        set_provider_client,
    )

    logger.info("🚀 Starting Johnny Robot Community Edition API")
    logger.info(f"Environment: {settings.environment}")
    logger.info(f"Debug mode: {settings.debug}")

    # Build the Student Memory client here, where waiting on Mem0 costs a
    # Student nothing -- no request is being served yet. It is built once for
    # The process either way; this only decides who waits for it (process-wide memory client construction).
    # Never raises: an absent, rejected, or unreachable Mem0 comes back as the
    # no-op, because a lab whose Mem0 is down still has to start.
    await get_memory_client()

    if settings.pocketbase_superuser_password:
        client = PocketBaseClient(
            base_url=settings.pocketbase_url,
            superuser_email=settings.pocketbase_superuser_email,
            superuser_password=settings.pocketbase_superuser_password,
            timeout=settings.pocketbase_timeout_seconds,
        )
        set_provider_client(client)
        set_store(PocketBaseStore(client))
        logger.info(f"PocketBase configured at {settings.pocketbase_url}")
    else:
        # Loud and early. A deployment without this credential can neither
        # authenticate nor persist, and should not look healthy (the private persistence boundary).
        # `NotConfiguredStore` stays installed and fails on first use.
        logger.error(
            "POCKETBASE_SUPERUSER_PASSWORD is unset; persistence stays unconfigured"
        )

    try:
        yield
    finally:
        # `finally`, so a serving loop that ended badly still gives the
        # connections back. Nothing raised in here may escape: shutdown is the
        # one moment where a traceback buys the Deployment Operator nothing.
        try:
            await get_provider_client().aclose()
        except IdentityProviderNotConfigured:
            pass  # never installed; there is nothing to close
        except Exception as exc:
            logger.warning(f"Provider client did not close cleanly: {exc}")
        set_provider_client(None)
        logger.info("👋 Shutting down Johnny Robot Community Edition API")


# Create FastAPI app
app = FastAPI(
    title="Johnny Robot Community Edition API",
    description="Multi-user learning assistant with voice and RAG capabilities",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# Rate-limit login attempts before CORS wraps the response, so a 429 still
# passes back out through CORSMiddleware and carries CORS headers. Starlette
# makes the most-recently-added middleware the outermost layer, so this call
# must precede the CORSMiddleware registration below.
app.add_middleware(LoginRateLimitMiddleware)

# CORS. Production is same-origin behind Caddy, so this list is empty there;
# it exists for local development where Vite serves on another port.
#
# allow_credentials is deliberately False. the private persistence boundary holds the caller's token
# in browser storage and sends it as an explicit Authorization header, which
# is not a "credential" in CORS terms -- that word means cookies and TLS
# client certificates -- so bearer auth is unaffected. Leaving it True would
# be a foot-gun: if CORS_ORIGINS is ever set to "*" (easy muscle memory,
# with cookie transport), Starlette's
# CORSMiddleware cannot emit a literal wildcard when credentials are
# enabled, so it echoes back whatever Origin the request sent instead --
# any origin, with credentials, accepted. Revisit this if the deployment
# ever moves to HttpOnly cookie transport, which the private persistence boundary already records
# as a prerequisite for carrying real Student data.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_cors_origins_list(),
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    max_age=3600,
)

# Include routers
app.include_router(auth.router, prefix=settings.api_prefix)
app.include_router(users.router, prefix=settings.api_prefix)
app.include_router(sessions.router, prefix=settings.api_prefix)
# app.include_router(documents.router, prefix=settings.api_prefix)  # Temporarily disabled
app.include_router(memory.router, prefix=settings.api_prefix)
app.include_router(canvas.router, prefix=settings.api_prefix)
app.include_router(textbooks.router, prefix=settings.api_prefix)
app.include_router(chat.router, prefix=settings.api_prefix)
app.include_router(capabilities.router, prefix=settings.api_prefix)


# Two failures can escape any route body, and both have a right answer that is
# The same everywhere. Registering them once here beats repeating the same
# except-clause in every handler, and it reaches the routes that carry no
# try/except at all.
#
# Neither handler is reached when a route already catches the exception itself
# -- a local `except Exception` wins -- so the PocketBase-backed routers that
# do carry a catch-all name `ProviderUnavailable` explicitly ahead of it.
#
# Note these are registered for specific exception classes, not for `Exception`:
# Starlette routes an `Exception` handler through `ServerErrorMiddleware`, which
# always re-raises after responding. A class-specific handler goes through
# `ExceptionMiddleware` instead, which sits inside `CORSMiddleware`, so the
# response carries the CORS headers a browser needs to read the status at all.


@app.exception_handler(ProviderUnavailable)
async def provider_unavailable_handler(request: Request, exc: ProviderUnavailable):
    """PocketBase timed out, refused the connection, or failed on its own account.

    503, never 401: the browser clears auth state on 401, so reporting an
    outage as a bad credential would sign every Student out over a blip. This
    extends the guarantee `get_current_user` already makes during authentication
    to a failure that happens after it, inside a route body (the private persistence boundary).
    """
    logger.error(f"Storage unavailable during {request.method} {request.url.path}: {exc}")
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"detail": "This service is temporarily unavailable"},
    )


@app.exception_handler(UnfilterableValue)
async def unfilterable_value_handler(request: Request, exc: UnfilterableValue):
    """A caller-supplied identifier could not be rendered into an owner filter.

    A crafted identifier is refused rather than escaped, and that refusal has
    to reach the caller as a clean 400 on every
    route -- never as a traceback, and never quoting the value back.
    """
    logger.warning(f"Refused a filter value on {request.method} {request.url.path}: {exc}")
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": "Invalid identifier in request"},
    )


@app.get("/")
async def root():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": "Johnny Robot Community Edition API",
        "version": "1.0.0",
        "environment": settings.environment
    }


@app.get("/health")
async def health_check():
    """Detailed health check with database connectivity."""
    from api.database.store import get_store

    checks = {
        "api": "ok",
        "database": "unknown"
    }

    # Test storage connectivity with a bounded read.
    try:
        await get_store().query("users", limit=1)
        checks["database"] = "ok"
    except Exception as e:
        logger.error(f"Health check database error: {e}")
        checks["database"] = "error"
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "checks": checks
            }
        )

    return {
        "status": "healthy",
        "checks": checks,
        "environment": settings.environment
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.debug
    )
