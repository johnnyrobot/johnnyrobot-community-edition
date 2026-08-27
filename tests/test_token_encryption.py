"""
Canvas token encryption.

The synthetic-content constraint of the reset-only demo profile excepts Canvas credentials: a
Canvas token is real regardless of what it reaches. Possession of pb_data alone
must not decrypt one, so the key lives outside PocketBase.
"""
import pytest

from api.security import crypto


@pytest.fixture(autouse=True)
def key(monkeypatch):
    from api.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("CANVAS_TOKEN_KEY", crypto.generate_key())
    monkeypatch.setenv("CANVAS_TOKEN_KEY_VERSION", "3")
    yield
    get_settings.cache_clear()


def test_a_token_round_trips():
    ciphertext, version = crypto.encrypt_canvas_token("canvas-secret-token")

    assert crypto.decrypt_canvas_token(ciphertext, version) == "canvas-secret-token"


def test_the_ciphertext_does_not_contain_the_plaintext():
    ciphertext, _ = crypto.encrypt_canvas_token("canvas-secret-token")

    assert "canvas-secret-token" not in ciphertext


def test_the_configured_key_version_is_recorded():
    _, version = crypto.encrypt_canvas_token("canvas-secret-token")

    assert version == 3


def test_the_same_token_encrypts_differently_each_time():
    """A deterministic ciphertext would let equal tokens be recognised."""
    first, _ = crypto.encrypt_canvas_token("canvas-secret-token")
    second, _ = crypto.encrypt_canvas_token("canvas-secret-token")

    assert first != second


def test_a_tampered_ciphertext_is_refused():
    ciphertext, version = crypto.encrypt_canvas_token("canvas-secret-token")
    tampered = ciphertext[:-4] + "AAAA"

    with pytest.raises(ValueError):
        crypto.decrypt_canvas_token(tampered, version)


def test_an_unknown_key_version_is_refused():
    """Silently decrypting under the current key would hide a botched rotation."""
    ciphertext, version = crypto.encrypt_canvas_token("canvas-secret-token")

    with pytest.raises(ValueError):
        crypto.decrypt_canvas_token(ciphertext, version + 1)


def test_encryption_without_a_key_fails_loudly(monkeypatch):
    from api.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("CANVAS_TOKEN_KEY", "")

    with pytest.raises(crypto.EncryptionNotConfigured):
        crypto.encrypt_canvas_token("canvas-secret-token")

    get_settings.cache_clear()


@pytest.mark.parametrize(
    "malformed",
    ["not-a-real-fernet-key", "c2hvcnQ="],
    ids=["not-base64", "wrong-length"],
)
def test_encryption_with_a_malformed_key_fails_the_same_way(malformed, monkeypatch):
    """A key that is present but unusable is a misconfiguration exactly as an
    absent one is, and must arrive as `EncryptionNotConfigured` rather than a
    bare `ValueError` escaping from inside Fernet — the routes above map the
    former to a clean 503 and would report the latter as a bug in the
    Student's request. Whatever was configured must not travel in the
    message either; the message is static apart from the library's own
    complaint about the shape.
    """
    from api.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("CANVAS_TOKEN_KEY", malformed)

    with pytest.raises(crypto.EncryptionNotConfigured) as raised:
        crypto.encrypt_canvas_token("canvas-secret-token")

    assert malformed not in str(raised.value)
    get_settings.cache_clear()
