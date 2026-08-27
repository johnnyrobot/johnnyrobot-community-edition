"""
CORS origin configuration.

An operator who deliberately blanks `CORS_ORIGINS` is declaring "no
cross-origin access needed" -- the same-origin-behind-Caddy production shape
(the private persistence boundary). An unset variable keeps the development
default; the two cases must not collapse to the same behaviour.
"""
from api.config import Settings

# Fields with no class default -- supplied directly so these tests don't
# depend on this worktree's .env contents.
_REQUIRED = dict(
    livekit_url="wss://example.test",
    livekit_api_key="key",
    livekit_api_secret="secret",
    app_secret_key="secret",
)


def test_an_unset_cors_origins_keeps_the_development_default(monkeypatch):
    monkeypatch.delenv("CORS_ORIGINS", raising=False)

    settings = Settings(_env_file=None, **_REQUIRED)

    assert settings.get_cors_origins_list() == [
        "http://localhost:3000",
        "http://localhost:5173",
    ]


def test_an_explicitly_empty_cors_origins_yields_no_origins(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "")

    settings = Settings(_env_file=None, **_REQUIRED)

    assert settings.get_cors_origins_list() == []
