"""Bind local master keys to this machine with Windows DPAPI.

A master key sits beside the ciphertext it protects, so file ACLs were its only guard: a backup,
sync client or disk image that copied the runtime folder carried both. Machine-scope DPAPI keeps
the key usable by the owner and the service account on this machine only. Other platforms keep
the owner-only file.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

from sqlctx.core.errors import SqlCtxError

WRAPPED_PREFIX = b"sqlctx-dpapi-v1\n"
_ENTROPY = b"sql-context-pack/master-key"
_CRYPTPROTECT_UI_FORBIDDEN = 0x1
_CRYPTPROTECT_LOCAL_MACHINE = 0x4


def wrapping_supported() -> bool:
    return sys.platform == "win32"


def is_wrapped(stored: bytes) -> bool:
    return stored.startswith(WRAPPED_PREFIX)


def _dpapi(data: bytes, *, protect: bool) -> bytes:
    if sys.platform != "win32":
        raise SqlCtxError("KEY_PROTECTION_UNAVAILABLE", "DPAPI is available on Windows only.")
    import ctypes
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def blob(value: bytes) -> tuple[DataBlob, ctypes.Array[ctypes.c_char]]:
        buffer = ctypes.create_string_buffer(value, len(value))
        return DataBlob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer

    source, _source_buffer = blob(data)
    entropy, _entropy_buffer = blob(_ENTROPY)
    output = DataBlob()
    crypt32 = ctypes.windll.crypt32
    flags = _CRYPTPROTECT_UI_FORBIDDEN | (_CRYPTPROTECT_LOCAL_MACHINE if protect else 0)
    call = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    ok = call(
        ctypes.byref(source), None, ctypes.byref(entropy), None, None, flags, ctypes.byref(output)
    )
    if not ok:
        raise SqlCtxError(
            "KEY_PROTECTION_FAILED",
            "This machine could not protect or open the local master key.",
        )
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(output.pbData)


def wrap(raw: bytes) -> bytes:
    if not wrapping_supported():
        return raw
    wrapped = WRAPPED_PREFIX + _dpapi(raw, protect=True)
    if unwrap(wrapped) != raw:
        raise SqlCtxError("KEY_PROTECTION_FAILED", "The protected master key did not round-trip.")
    return wrapped


def unwrap(stored: bytes) -> bytes:
    if not is_wrapped(stored):
        return stored
    return _dpapi(stored[len(WRAPPED_PREFIX) :], protect=False)


def load_master_key(
    path: Path, *, generate: Callable[[], bytes], write: Callable[[Path, bytes], None]
) -> bytes:
    """Return the raw key, creating it protected or upgrading a legacy plain key in place.

    The upgrade keeps the same key bytes, so every existing ciphertext stays readable; it only
    replaces the file once the protected form has been proven to open again.
    """
    if not path.exists():
        raw = generate()
        write(path, wrap(raw))
        return raw
    stored = path.read_bytes()
    raw = unwrap(stored)
    if wrapping_supported() and not is_wrapped(stored):
        # Best effort: a key that cannot be upgraded now still opens, and doctor keeps
        # reporting CREDENTIAL_KEY_UNWRAPPED until a later read succeeds.
        try:
            write(path, wrap(raw))
        except (OSError, SqlCtxError):
            pass
    return raw
