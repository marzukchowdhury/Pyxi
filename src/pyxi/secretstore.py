"""Tokens at rest, encrypted with Windows DPAPI (CurrentUser scope).

Google's OAuth tokens, LinkedIn's and the beta token used to sit in plain JSON in the data
folder, readable by anything that can read the user's files (a backup, a synced folder, another
account with admin rights copying AppData). DPAPI ties them to this Windows user on this PC:
copied elsewhere, the file is useless.

- :func:`write_json` encrypts and replaces the file in one step.
- :func:`read_json` decrypts. A plain JSON file from an older Pyxi is read once and rewritten
  encrypted (migration). A file that can't be read (corrupt, or copied from another PC or user)
  is removed and reads as None: that service asks to sign in again, it never crashes Pyxi.

Off Windows (development on another OS) there's no DPAPI: files are written as plain JSON, as
before.
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

MAGIC = b"PYXI-DPAPI-1\n"
# Ties the blobs to Pyxi: another program calling CryptUnprotectData on the file without this
# gets nothing. (Malware running as the user could still pass it; DPAPI's job is the file at rest.)
_ENTROPY = b"Pyxi secret store v1"
_UI_FORBIDDEN = 0x1  # never pop a dialog


class SecretError(Exception):
    """The blob can't be decrypted here (corrupt, another user, another PC)."""


def available() -> bool:
    return sys.platform == "win32"


if sys.platform == "win32":
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_Blob), wintypes.LPCWSTR, ctypes.POINTER(_Blob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob),
    ]  # fmt: skip
    _crypt32.CryptProtectData.restype = wintypes.BOOL
    _crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.POINTER(_Blob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob),
    ]  # fmt: skip
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p

    def _blob(data: bytes) -> tuple[_Blob, Any]:
        buf = ctypes.create_string_buffer(data, len(data))
        return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    def _call(fn: Any, data: bytes, *, protect: bool) -> bytes:
        src, _keep = _blob(data)
        ent, _keep2 = _blob(_ENTROPY)
        out = _Blob()
        args = (ctypes.byref(src), "Pyxi" if protect else None, ctypes.byref(ent), None, None)
        if not fn(*args, _UI_FORBIDDEN, ctypes.byref(out)):
            raise SecretError(f"DPAPI error {ctypes.get_last_error()}")
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            _kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))

    def protect(data: bytes) -> bytes:
        return _call(_crypt32.CryptProtectData, data, protect=True)

    def unprotect(blob: bytes) -> bytes:
        return _call(_crypt32.CryptUnprotectData, blob, protect=False)

else:  # pragma: no cover - development off Windows

    def protect(data: bytes) -> bytes:
        raise SecretError("DPAPI needs Windows")

    def unprotect(blob: bytes) -> bytes:
        raise SecretError("DPAPI needs Windows")


def encrypted(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(len(MAGIC)) == MAGIC
    except OSError:
        return False


def write_json(path: Path, data: dict[str, Any]) -> None:
    raw = json.dumps(data).encode("utf-8")
    body = MAGIC + protect(raw) if available() else raw
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(body)
    os.replace(tmp, path)


def _drop(path: Path, why: str) -> None:
    log.warning("%s can't be read (%s): sign in again", path.name, why)
    try:
        path.unlink()
    except OSError:
        pass


def read_json(path: Path) -> dict[str, Any] | None:
    """The saved secret, or None when there's none (or it was unreadable and has been dropped)."""
    try:
        body = path.read_bytes()
    except OSError:
        return None
    if body.startswith(MAGIC):
        try:
            data = json.loads(unprotect(body[len(MAGIC):]).decode("utf-8"))
        except (SecretError, ValueError) as exc:
            _drop(path, str(exc) or exc.__class__.__name__)
            return None
        if not isinstance(data, dict):
            _drop(path, "not an object")
            return None
        return data
    try:  # a plain file from an older Pyxi
        data = json.loads(body.decode("utf-8"))
    except ValueError:
        _drop(path, "corrupt")
        return None
    if not isinstance(data, dict):
        _drop(path, "not an object")
        return None
    if available():
        try:
            write_json(path, data)  # migrate: encrypted from now on
        except (OSError, SecretError) as exc:
            log.warning("couldn't encrypt %s: %s", path.name, exc)
    return data
