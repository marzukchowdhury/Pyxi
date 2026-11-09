"""The Mac side of ``pyxi.secretstore``: tokens at rest, sealed with a key kept in the Keychain.

DPAPI's Mac counterpart is the login Keychain. Pyxi keeps one random 256-bit key there (a generic
password, service "app.pyxi.desktop", account "secret-store-key") and seals each token file with
AES-GCM under it. Copied to another Mac or another user, the file is useless, as on Windows.

If the Keychain can't be reached (no login session, a locked keychain), ``available()`` is False
and files are written as plain JSON, as Pyxi always did off Windows.
"""

from __future__ import annotations

import importlib
import logging
import os
import sys

log = logging.getLogger(__name__)

__all__ = ["MAGIC", "available", "protect", "unprotect"]

MAGIC = b"PYXI-KEYCHAIN-1\n"
SERVICE, ACCOUNT = "app.pyxi.desktop", "secret-store-key"
_AAD = b"Pyxi secret store v1"
_key: bytes | None = None
_unavailable = False


def _security():
    return importlib.import_module("Security")


def _find(S) -> bytes | None:
    query = {
        S.kSecClass: S.kSecClassGenericPassword,
        S.kSecAttrService: SERVICE,
        S.kSecAttrAccount: ACCOUNT,
        S.kSecReturnData: True,
        S.kSecMatchLimit: S.kSecMatchLimitOne,
    }
    status, data = S.SecItemCopyMatching(query, None)
    if status != 0 or data is None:
        return None
    return bytes(data)


def _add(S, key: bytes) -> bool:
    item = {
        S.kSecClass: S.kSecClassGenericPassword,
        S.kSecAttrService: SERVICE,
        S.kSecAttrAccount: ACCOUNT,
        S.kSecAttrLabel: "Pyxi",
        S.kSecValueData: key,
        # Readable only while this Mac is unlocked, and never synced to iCloud or restored to
        # another Mac from a backup.
        S.kSecAttrAccessible: S.kSecAttrAccessibleWhenUnlockedThisDeviceOnly,
    }
    res = S.SecItemAdd(item, None)
    status = res[0] if isinstance(res, tuple) else res
    return status == 0


def key() -> bytes:
    """Pyxi's Keychain key, made on first use. Raises SecretError when there's no Keychain."""
    global _key, _unavailable
    from pyxi.secretstore import SecretError

    if _key is not None:
        return _key
    if _unavailable:
        raise SecretError("the Keychain isn't available")
    try:
        S = _security()
        found = _find(S)
        if found is None:
            new = os.urandom(32)
            if not _add(S, new):
                found = _find(S)  # another Pyxi made it a moment ago
                if found is None:
                    raise SecretError("couldn't save a key in the Keychain")
            else:
                found = new
    except SecretError:
        _unavailable = True
        raise
    except Exception as exc:
        _unavailable = True
        raise SecretError(f"Keychain error: {exc}") from exc
    if len(found) != 32:
        raise SecretError("the Keychain key has the wrong length")
    _key = found
    return _key


def available() -> bool:
    if sys.platform != "darwin":
        return False
    try:
        key()
        return True
    except Exception as exc:
        log.warning("secrets stay unencrypted: %s", exc)
        return False


def protect(data: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = os.urandom(12)
    return nonce + AESGCM(key()).encrypt(nonce, data, _AAD)


def unprotect(blob: bytes) -> bytes:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    from pyxi.secretstore import SecretError

    if len(blob) < 12 + 16:
        raise SecretError("too short")
    try:
        return AESGCM(key()).decrypt(blob[:12], blob[12:], _AAD)
    except InvalidTag as exc:
        raise SecretError("sealed with another key (another Mac or user)") from exc
