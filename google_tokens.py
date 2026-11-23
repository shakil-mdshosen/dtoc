"""Refresh-token encryption for Google Sheets sync.

This is the ONLY module that touches raw Google refresh tokens.
Every other module stores / passes the Fernet ciphertext, never the
plaintext token.

Fail-closed rules (design §5.4, Copilot review):
- The key comes only from the ``GOOGLE_TOKEN_KEY`` environment variable.
  There is no dev default and no generated throwaway key: if the key is
  missing or invalid, encryption/decryption raise
  :class:`GoogleTokenKeyError` and the Google routes refuse to start
  a flow (see ``google_auth.google_sync_available``).
- Decryption failure is treated as "token unusable", never logged
  with the ciphertext, and never retried with a different key.
- The ``cryptography`` package is imported lazily, so the app can still
  start (with sync disabled) on an install without it.
"""

import os


class GoogleTokenKeyError(Exception):
    """GOOGLE_TOKEN_KEY is missing, invalid, or unusable."""


class GoogleTokenUnavailable(Exception):
    """The token store cannot be used (e.g. ``cryptography`` not installed)."""


def _fernet(key=None):
    """Build a Fernet instance from GOOGLE_TOKEN_KEY, or raise.

    ``key`` may be passed explicitly (Flask config, tests); otherwise
    the GOOGLE_TOKEN_KEY environment variable is used.
    """
    try:
        from cryptography.fernet import Fernet, InvalidToken
    except ImportError as exc:  # pragma: no cover - install-dependent
        raise GoogleTokenUnavailable(
            "cryptography package is not installed; Google Sheets sync "
            "is unavailable") from exc
    key = (key or os.environ.get("GOOGLE_TOKEN_KEY") or "").strip()
    if not key:
        raise GoogleTokenKeyError("GOOGLE_TOKEN_KEY is not configured")
    try:
        return Fernet(key.encode("ascii")), InvalidToken
    except Exception as exc:
        raise GoogleTokenKeyError(
            "GOOGLE_TOKEN_KEY is not a valid Fernet key") from exc


def token_key_configured(key=None):
    """True only if GOOGLE_TOKEN_KEY parses as a valid Fernet key."""
    try:
        _fernet(key)
    except (GoogleTokenKeyError, GoogleTokenUnavailable):
        return False
    return True


def encrypt_refresh_token(refresh_token, key=None):
    """Encrypt a refresh token. Returns the ciphertext string."""
    if not refresh_token or not isinstance(refresh_token, str):
        raise ValueError("refresh token must be a non-empty string")
    fernet, _invalid = _fernet(key)
    return fernet.encrypt(refresh_token.encode("utf-8")).decode("ascii")


def decrypt_refresh_token(ciphertext, key=None):
    """Decrypt a stored refresh token.

    Raises :class:`GoogleTokenKeyError` on a wrong/rotated key or a
    corrupt value. Callers must treat that as "token unusable" and ask
    the owner to reconnect; they must never log ``ciphertext``.
    """
    if not ciphertext or not isinstance(ciphertext, str):
        raise GoogleTokenKeyError("no stored refresh token")
    fernet, invalid = _fernet(key)
    try:
        return fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except invalid as exc:
        raise GoogleTokenKeyError(
            "stored refresh token cannot be decrypted with the "
            "current GOOGLE_TOKEN_KEY") from exc
