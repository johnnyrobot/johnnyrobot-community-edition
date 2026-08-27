"""
Canvas token encryption.

Every Course Material in this deployment is synthetic (the reset-only demo profile), but a Canvas
token is exempt from that: it is a real credential regardless of what it
reaches. It is encrypted in FastAPI and decrypted only at the moment a Canvas
call needs it, under a key custodied outside PocketBase — so possession of
pb_data alone decrypts nothing.

The key version travels with the ciphertext so a later rotation can tell which
key applies. A version that does not match the configured one is refused rather
than attempted, because silently decrypting under the current key would hide a
botched rotation until the tokens were already unreadable.
"""
from cryptography.fernet import Fernet, InvalidToken

from api.config import get_settings


class EncryptionNotConfigured(RuntimeError):
    """Raised when CANVAS_TOKEN_KEY is unset."""


def generate_key() -> str:
    """Generate a key suitable for CANVAS_TOKEN_KEY."""
    return Fernet.generate_key().decode()


def _cipher() -> Fernet:
    key = get_settings().canvas_token_key
    if not key:
        raise EncryptionNotConfigured(
            "CANVAS_TOKEN_KEY is unset; a Canvas token cannot be stored safely"
        )
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as e:
        raise EncryptionNotConfigured(f"CANVAS_TOKEN_KEY is malformed: {e}") from e


def encrypt_canvas_token(plaintext: str) -> tuple[str, int]:
    """Return the ciphertext and the version of the key that produced it."""
    settings = get_settings()
    ciphertext = _cipher().encrypt(plaintext.encode()).decode()
    return ciphertext, settings.canvas_token_key_version


def decrypt_canvas_token(ciphertext: str, key_version: int) -> str:
    """Recover a Canvas token encrypted under the configured key."""
    settings = get_settings()
    if int(key_version) != settings.canvas_token_key_version:
        raise ValueError(
            f"Canvas token was encrypted under key version {key_version}, "
            f"but version {settings.canvas_token_key_version} is configured"
        )
    try:
        return _cipher().decrypt(ciphertext.encode()).decode()
    except InvalidToken as e:
        raise ValueError("Canvas token ciphertext is not valid") from e
