"""Hide and restore files by toggling Windows attributes - nothing is moved,
renamed, copied or modified. Original attribute bits are recorded so restore
is exact.

Hidden + System together keep a file out of Explorer even when "Show hidden
files" is on (as long as "Hide protected operating system files" stays on,
which is the Windows default).

Optional strict hide (`lock`): adds one deny entry to the file's ACL that
blocks reading and executing for the current account, so the file cannot be
opened even if it is found. The entry is removed again by `unlock`; nothing
else in the ACL is touched. Files are still never moved or modified.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
from ctypes import wintypes
from pathlib import Path

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.GetFileAttributesW.argtypes = [wintypes.LPCWSTR]
_k32.GetFileAttributesW.restype = wintypes.DWORD
_k32.SetFileAttributesW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
_k32.SetFileAttributesW.restype = wintypes.BOOL

READONLY = 0x1
HIDDEN = 0x2
SYSTEM = 0x4
DIRECTORY = 0x10
INVALID = 0xFFFFFFFF
MASK = HIDDEN | SYSTEM
CREATE_NO_WINDOW = 0x08000000


def _long(path: str) -> str:
    p = str(Path(path))
    if not p.startswith("\\\\?\\") and len(p) > 240:
        p = "\\\\?\\" + p
    return p


def get_attrs(path: str) -> int | None:
    a = _k32.GetFileAttributesW(_long(path))
    return None if a == INVALID else int(a)


def set_attrs(path: str, attrs: int) -> None:
    if not _k32.SetFileAttributesW(_long(path), attrs):
        raise OSError(ctypes.get_last_error(), ctypes.FormatError(ctypes.get_last_error()), path)


def hide(paths: list[str]) -> tuple[list[dict], list[str]]:
    """Hide each path. Returns (records, errors). Records carry the original attrs."""
    records, errors = [], []
    for p in paths:
        a = get_attrs(p)
        if a is None:
            errors.append(f"{p}: not found")
            continue
        rec = {"path": str(Path(p)), "attrs": a}
        try:
            set_attrs(p, a | MASK)
            records.append(rec)
        except OSError as e:
            errors.append(f"{p}: {e.strerror}")
    return records, errors


def unhide(records: list[dict]) -> tuple[int, list[str]]:
    """Restore the exact original attributes. Returns (restored_count, errors)."""
    restored, errors = 0, []
    for rec in records:
        p = rec.get("path")
        orig = int(rec.get("attrs", 0))
        cur = get_attrs(p)
        if cur is None:
            errors.append(f"{p}: no longer exists")
            continue
        # Keep any non-visibility bits the file picked up meanwhile (e.g. Archive),
        # but force Hidden/System back to what they were.
        target = (cur & ~MASK) | (orig & MASK)
        try:
            set_attrs(p, target)
            restored += 1
        except OSError as e:
            errors.append(f"{p}: {e.strerror}")
    return restored, errors


def rehide(records: list[dict]) -> int:
    """Put Hidden+System back on any recorded file that lost them. Returns how many were fixed."""
    fixed = 0
    for rec in records:
        p = rec.get("path")
        cur = get_attrs(p)
        if cur is not None and (cur & MASK) != MASK:
            try:
                set_attrs(p, cur | MASK)
                fixed += 1
            except OSError:
                pass
    return fixed


def is_hidden(path: str) -> bool:
    a = get_attrs(path)
    return a is not None and bool(a & HIDDEN)


# ------------------------------------------------------------ strict lock ---
def _sys(tool: str) -> str:
    """Absolute path of a Windows tool, so nothing is resolved through PATH or the cwd."""
    return str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / tool)


def _run(cmd: list[str]) -> tuple[int, str]:
    p = subprocess.run(cmd, capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def current_sid() -> str | None:
    code, out = _run([_sys("whoami.exe"), "/user", "/fo", "csv", "/nh"])
    if code != 0:
        return None
    parts = [x.strip().strip('"') for x in out.strip().splitlines()[-1].split(",")]
    return parts[-1] if parts and parts[-1].startswith("S-1-") else None


def _rights(path: str) -> str:
    a = get_attrs(path) or 0
    # RD = read data / list folder, X = execute / traverse. Nothing else is denied, so attributes,
    # permissions and ownership stay readable and the entry can always be removed again.
    return "(OI)(CI)(RD,X)" if a & DIRECTORY else "(RD,X)"


def lock(records: list[dict]) -> tuple[int, list[str]]:
    """Add the deny entry to each recorded (already hidden) path. Marks records with acl_sid."""
    sid = current_sid()
    if not sid:
        return 0, ["Could not determine the current account SID; files hidden but not locked."]
    locked, errors = 0, []
    for rec in records:
        p = rec["path"]
        code, out = _run([_sys("icacls.exe"), p])
        if code != 0:
            errors.append(f"{p}: ACL not readable, hidden but not locked")
            continue
        if "(DENY)" in out.upper():
            errors.append(f"{p}: already has a deny entry, hidden but not locked so it stays untouched")
            continue
        code, out = _run([_sys("icacls.exe"), p, "/deny", f"*{sid}:{_rights(p)}"])
        if code == 0:
            rec["acl_sid"] = sid
            locked += 1
        else:
            errors.append(f"{p}: lock failed ({out.strip().splitlines()[-1] if out.strip() else code})")
    return locked, errors


def unlock(records: list[dict]) -> tuple[int, list[str]]:
    """Remove our deny entry from every record that has one."""
    unlocked, errors = 0, []
    for rec in records:
        sid = rec.get("acl_sid")
        if not sid:
            continue
        p = rec["path"]
        if get_attrs(p) is None:
            continue
        code, out = _run([_sys("icacls.exe"), p, "/remove:d", f"*{sid}"])
        if code == 0:
            unlocked += 1
        else:
            errors.append(f"{p}: unlock failed ({out.strip().splitlines()[-1] if out.strip() else code})")
    return unlocked, errors


def is_locked(path: str, sid: str) -> bool:
    code, out = _run([_sys("icacls.exe"), path])
    return code == 0 and "(DENY)" in out.upper()
