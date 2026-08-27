"""
Application configuration management using pydantic-settings.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator
from functools import lru_cache


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # LiveKit
    livekit_url: str
    livekit_api_key: str
    livekit_api_secret: str

    # PocketBase (the private persistence boundary). Reached over the Compose network only; never
    # published to the host and never known to the browser.
    pocketbase_url: str = "http://pocketbase:8090"
    pocketbase_superuser_email: str = ""
    pocketbase_superuser_password: str = ""
    pocketbase_timeout_seconds: float = 10.0

    # Canvas token encryption (the reset-only demo profile). A Canvas token is a real credential
    # regardless of what it reaches, so it is held as ciphertext under a key
    # custodied outside PocketBase.
    canvas_token_key: str = ""
    canvas_token_key_version: int = 1

    # Which Canvas a deployment syncs from. Deliberately no default: this is
    # self-hosted software installed by an Operator (the self-hosted configuration boundary), and a
    # committed hostname would ship one institution's Canvas to every other
    # installation. Unset, connecting a Canvas source is refused rather than
    # guessed -- the same shape as an absent `canvas_token_key`.
    canvas_base_url: str = ""

    # Login throttling. PocketBase's own per-address limiter cannot help here:
    # every login reaches it from one container address (the private persistence boundary).
    login_rate_limit_attempts: int = 10
    login_rate_limit_window_seconds: int = 300
    # Caddy is the only thing in front of FastAPI, so its forwarded header is
    # The real client address. Set false if FastAPI is ever exposed directly.
    trust_forwarded_for: bool = True

    # The reset-only demo profile. The reset script refuses to run against anything else.
    deployment_profile: str = "ResetOnly"

    # Google Gemini API (used by LiveKit plugin and File Search)
    google_api_key: str = ""

    # OpenAI (optional if using Gemini Voice in future, but Agent currently uses OpenAI)
    openai_api_key: str = ""

    # Mem0 (existing). Student Memory is optional: every field here can
    # be left unset, and chat and voice still work without remembering.
    mem0_api_key: str = ""
    # How long to wait for mem0 to validate the key while building its client.
    # mem0 passes no timeout to its own `requests.get`, so without this the
    # wait is whatever the OS decides -- minutes, against a host that drops
    # packets rather than refusing them. 0 disables the bound. See process-wide memory client construction.
    mem0_timeout_seconds: float = 10.0

    # Where Student Memory lives. Off by default, so an existing deployment
    # keeps whatever it already had.
    #
    # Self-hosted keeps every remembered exchange on the Deployment Operator's
    # own infrastructure instead of Mem0's. It runs mem0's OSS `AsyncMemory`
    # over a local vector store and graph, with Gemini as the LLM and embedder
    # -- deliberately Gemini rather than OpenAI, because requirements.txt
    # records an accepted version conflict between mem0ai and the openai>=2
    # that livekit-agents pins. Routing mem0 through Gemini leaves that
    # conflict dormant instead of exercising it.
    mem0_self_hosted: bool = False

    # The graph behind self-hosted Student Memory, and (later) the Course
    # Material graph. Unset means no graph: memory still works from the vector
    # store alone, which is the degradation the optional graph boundary requires to stay valid.
    neo4j_url: str = ""
    neo4j_username: str = "neo4j"
    neo4j_password: str = ""

    # How long to wait for Neo4j while building the graph client. The driver's
    # constructor does not connect, but `verify_connectivity` does, and a host
    # that drops packets rather than refusing them would otherwise hold the
    # event loop for however long the OS decides. Same hazard, and the same
    # answer, as `mem0_timeout_seconds`. 0 disables the bound.
    graph_build_timeout_seconds: float = 10.0

    # The model that proposes Concepts and prerequisite edges. Named here
    # rather than inline so a GraphBuildManifest can record which model a
    # build used -- the graph-build determinism contract makes drift reportable only if the policy that
    # produced it is identified.
    graph_extraction_model: str = "gemini-2.5-flash"

    # The vector store behind self-hosted Student Memory.
    qdrant_host: str = ""
    qdrant_port: int = 6333

    # AI Provider Selection
    ai_provider: str = "google"  # Options: "google" or "openai"

    # Language Settings
    agent_language: str = "en-US"  # Options: "en-US", "es-ES", "vi-VN", etc.

    # Application
    app_secret_key: str
    environment: str = "development"
    debug: bool = True

    # API Settings
    api_prefix: str = "/api/v1"
    # CORS origins - accepts comma-separated string or JSON array
    cors_origins: str = "http://localhost:3000,http://localhost:5173"

    @field_validator('cors_origins', mode='before')
    @classmethod
    def parse_cors_origins(cls, v):
        """
        Accept CORS origins as a comma-separated string.

        An unset `CORS_ORIGINS` uses this field's development default; that
        case never reaches here as an empty value (pydantic-settings hands
        The validator the default string itself). Only a genuine `None` --
        never produced by the environment, only by constructing `Settings`
        directly with `cors_origins=None` -- falls back here.

        An *explicitly* blank `CORS_ORIGINS=` must NOT be coerced: it is an
        operator declaring "no cross-origin access needed", the same-origin
        behind-Caddy production shape (the private persistence boundary, the CORS contract). Coercing it
        back to a default silently widens access the operator meant to shut
        off. See `get_cors_origins_list()`.
        """
        if v is None:
            return "http://localhost:3000,http://localhost:5173"
        return v

    def get_cors_origins_list(self) -> list[str]:
        """
        Get CORS origins as a list, splitting on comma if needed.

        An explicitly empty `cors_origins` yields an empty list, not a
        fallback default -- see `parse_cors_origins`.
        """
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]
    
    # Document Processing
    use_docling_processor: bool = False  # Feature flag for Docling vs legacy processor
    
    # Canvas MCP Server
    canvas_mcp_url: str = ""  # URL of Canvas MCP server (e.g., http://localhost:8000/mcp)
    canvas_mcp_token: str = ""  # Bearer token for Canvas MCP server authentication
    canvas_mcp_enabled: bool = False  # Feature flag to enable Canvas MCP integration
    
    # Class-based `Config` until the Pydantic settings compatibility requirement. Pydantic deprecated that form in
    # V2.0 and removes it in V3.0, and the removal is silent: a config class
    # Pydantic no longer reads is just an unused inner class, so the deployment
    # would lose its `.env` and start refusing the extra keys already in it
    # with nothing raised to say why.
    model_config = SettingsConfigDict(
        # Relative to the working directory, which is the deployment root in
        # every container and every `uvicorn api.main:app` a developer runs.
        env_file=".env",
        case_sensitive=False,
        # A `.env` outlives the code that reads it -- variables get added for
        # one service and left behind when it goes. One the current build has
        # no field for must not stop the process starting.
        extra="ignore",
    )


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
