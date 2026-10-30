"""Windows DPAPI, for the passwords the launcher remembers.

``CryptProtectData`` encrypts with a key Windows derives from the signed-in
user's credentials, so a remembered password can only be read back by the
same Windows account on the same machine: copying ``launcher-client.json``
elsewhere carries nothing usable. The entropy ties each password to the
account and service it was remembered for. Standard library only, through
``ctypes``, so the frozen executables stay dependency-free.
"""

from __future__ import annotations

import ctypes
import os

# Remembering is offered only where the passwords can be protected; anywhere
# else they are never written down.
AVAILABLE = os.name == "nt"

CRYPTPROTECT_UI_FORBIDDEN = 0x01


class _Blob(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.c_uint32),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def _blob(data: bytes) -> tuple[_Blob, ctypes.Array]:
    # The buffer is returned alongside so it outlives the call.
    buffer = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer


def _call(name: str, data: bytes, entropy: bytes) -> bytes:
    if not AVAILABLE:
        raise OSError("password protection needs Windows")
    function = getattr(ctypes.windll.crypt32, name)
    function.argtypes = [
        ctypes.POINTER(_Blob),
        ctypes.c_void_p,
        ctypes.POINTER(_Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(_Blob),
    ]
    function.restype = ctypes.c_int
    local_free = ctypes.windll.kernel32.LocalFree
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p

    source, _source_buffer = _blob(data)
    salt, _salt_buffer = _blob(entropy)
    result = _Blob()
    if not function(
        ctypes.byref(source),
        None,
        ctypes.byref(salt),
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(result),
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        local_free(ctypes.cast(result.pbData, ctypes.c_void_p))


def protect(data: bytes, entropy: bytes) -> bytes:
    """Encrypt ``data`` for the current Windows user."""

    return _call("CryptProtectData", data, entropy)


def unprotect(blob: bytes, entropy: bytes) -> bytes:
    """Decrypt what :func:`protect` produced with the same ``entropy``.

    Raises ``OSError`` for another user's blob, a different entropy, or
    damaged data.
    """

    return _call("CryptUnprotectData", blob, entropy)
