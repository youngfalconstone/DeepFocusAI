"""API keys in the Windows Credential Manager (generic credentials), never in a file.

CredWriteW / CredReadW / CredDeleteW through ctypes. Secrets are stored per user, encrypted by Windows, and
show up under "Windows Credentials" in Control Panel as "DeepFocus AI/<provider>".
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
ERROR_NOT_FOUND = 1168
PREFIX = "DeepFocus AI/"


class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


class CREDENTIALW(ctypes.Structure):
    _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
                ("Comment", wintypes.LPWSTR), ("LastWritten", FILETIME), ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)), ("Persist", wintypes.DWORD),
                ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]


def _adv():
    adv = ctypes.windll.advapi32
    adv.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIALW), wintypes.DWORD]
    adv.CredWriteW.restype = wintypes.BOOL
    adv.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(CREDENTIALW))]
    adv.CredReadW.restype = wintypes.BOOL
    adv.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    adv.CredDeleteW.restype = wintypes.BOOL
    adv.CredFree.argtypes = [ctypes.c_void_p]
    return adv


def target(name: str) -> str:
    return PREFIX + name


def save(name: str, secret: str, user: str = "api-key") -> None:
    """Store `secret` under DeepFocus AI/<name>. Raises OSError on failure."""
    blob = secret.encode("utf-16-le")
    buf = (ctypes.c_byte * len(blob)).from_buffer_copy(blob)
    cred = CREDENTIALW()
    cred.Flags = 0
    cred.Type = CRED_TYPE_GENERIC
    cred.TargetName = target(name)
    cred.Comment = "Stored by DeepFocus AI for its AI Block feature"
    cred.CredentialBlobSize = len(blob)
    cred.CredentialBlob = ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte))
    cred.Persist = CRED_PERSIST_LOCAL_MACHINE
    cred.AttributeCount = 0
    cred.Attributes = None
    cred.TargetAlias = None
    cred.UserName = user
    if not _adv().CredWriteW(ctypes.byref(cred), 0):
        raise ctypes.WinError(ctypes.get_last_error() or ctypes.GetLastError())


def load(name: str) -> str | None:
    """The stored secret, or None when there is none."""
    adv = _adv()
    p = ctypes.POINTER(CREDENTIALW)()
    if not adv.CredReadW(target(name), CRED_TYPE_GENERIC, 0, ctypes.byref(p)):
        return None
    try:
        c = p.contents
        if not c.CredentialBlobSize:
            return ""
        raw = ctypes.string_at(c.CredentialBlob, c.CredentialBlobSize)
        return raw.decode("utf-16-le", errors="replace")
    finally:
        adv.CredFree(p)


def delete(name: str) -> bool:
    """Remove the secret. True if something was deleted."""
    adv = _adv()
    if adv.CredDeleteW(target(name), CRED_TYPE_GENERIC, 0):
        return True
    return False


def has(name: str) -> bool:
    return load(name) is not None
