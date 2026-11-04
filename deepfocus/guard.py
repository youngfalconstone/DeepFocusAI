"""Refuses paths that must never be hidden or locked.

The helper hides anything the plan asks for, so the plan must never be able
to name Windows itself, installed programs, the user profile root, the shell
folder Explorer draws the desktop from, this app, or the state the restore
depends on. Both the GUI (when a path is added) and the elevated helper (when
the plan is applied) call `forbidden_reason`, so a hand-edited plan file is
caught too.
"""
from __future__ import annotations

import os
from pathlib import Path

from . import paths


def _env_path(name: str) -> Path | None:
    v = os.environ.get(name)
    return Path(v) if v else None


def _norm(p: Path) -> str:
    try:
        return os.path.normcase(str(p.resolve()))
    except OSError:
        return os.path.normcase(str(p))


def _is_within(child: str, parent: str) -> bool:
    return child == parent or child.startswith(parent.rstrip("\\") + "\\")


def protected_roots() -> list[tuple[Path, str]]:
    """(path, reason) pairs. A requested path inside any of them is refused."""
    out: list[tuple[Path, str]] = []
    sysroot = _env_path("SystemRoot") or Path(r"C:\Windows")
    out.append((sysroot, "is part of Windows"))
    for var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "ProgramData"):
        p = _env_path(var)
        if p:
            out.append((p, "holds installed programs"))
    for var in ("APPDATA", "LOCALAPPDATA"):
        p = _env_path(var)
        if p:
            out.append((p, "holds application data Windows and your apps depend on"))
    out.append((paths.STATE_DIR, "is where DeepFocus keeps the record it needs to restore everything"))
    out.append((paths.PROJECT_DIR, "is the DeepFocus app itself"))
    out.append((paths.HOSTS_PATH, "is the hosts file"))
    return out


def protected_exact() -> list[tuple[Path, str]]:
    """Paths refused only when named exactly (their children are fine)."""
    out: list[tuple[Path, str]] = []
    out.append((Path.home(), "is your whole user profile"))
    for var in ("USERPROFILE", "PUBLIC"):
        p = _env_path(var)
        if p:
            out.append((p, "is a whole user profile"))
    out.append((desktop_folder(), "is the Desktop folder itself; hide the items on it instead"))
    return out


def desktop_folder() -> Path:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
            val, _ = winreg.QueryValueEx(k, "Desktop")
            return Path(os.path.expandvars(val))
    except OSError:
        return Path.home() / "Desktop"


def _is_link(p: str) -> bool:
    """True for a symbolic link or an NTFS junction (not for OneDrive cloud files, which are other reparse points)."""
    try:
        return os.path.islink(p) or os.path.isjunction(p)
    except (OSError, ValueError):
        return False


def has_link(path: str) -> bool:
    """True if the path itself or any of its ancestors is a symlink or junction."""
    cur = os.path.abspath(str(path))
    while True:
        if _is_link(cur):
            return True
        parent = os.path.dirname(cur)
        if not parent or parent == cur:
            return False
        cur = parent


def forbidden_reason(path: str) -> str | None:
    """None if the path may be hidden, otherwise a short reason it is refused."""
    raw = str(path).strip()
    if not raw or "\x00" in raw:
        return "is empty"
    p = Path(raw)
    if not p.is_absolute():
        return "is not an absolute path"
    n = _norm(p)
    if len(n.rstrip("\\")) <= 2 or n.rstrip("\\").endswith(":"):
        return "is a drive root"
    for root, why in protected_roots():
        if _is_within(n, _norm(root)):
            return why
    for exact, why in protected_exact():
        if n == _norm(exact):
            return why
    # a link could be re-pointed at something protected after the check, so links are never hidden or locked
    if has_link(raw):
        return "is, or lies under, a symbolic link or junction"
    return None


def state_dir_problem() -> str | None:
    """Why the state folder must not be used right now, or None. The elevated helper writes there, so a
    junction planted in its place could redirect those writes anywhere."""
    sd = str(paths.STATE_DIR)
    if has_link(sd):
        return f"the state folder {sd} is, or lies under, a symbolic link or junction"
    if os.path.exists(sd) and not os.path.isdir(sd):
        return f"the state folder {sd} is not a folder"
    # the helper also copies into and deletes from these two; a link planted there would redirect that elsewhere
    for sub in (paths.BACKUP_DIR, paths.RESULTS_DIR):
        if os.path.lexists(sub) and (_is_link(str(sub)) or not os.path.isdir(sub)):
            return f"{sub} is a link or is not a folder"
    return None


def install_dir_problem() -> str | None:
    """Why the folder the helper runs from is a weak spot, or None.

    The scheduled tasks run the helper with administrator rights from wherever the executable lives. If the
    ordinary account can write to that folder, any program running as that account could replace the executable
    and have Task Scheduler run its own code elevated, without a UAC prompt. Called by the GUI (unelevated), so
    the write test reflects what the ordinary account can do. Folders only administrators can write to by
    default (Program Files, Windows) are accepted without a test."""
    exe, _ = paths.helper_command()
    try:
        folder = Path(exe).resolve().parent
    except OSError:
        return None
    n = _norm(folder)
    for var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "SystemRoot"):
        root = _env_path(var)
        if root and _is_within(n, _norm(root)):
            return None
    probe = folder / f".deepfocus_write_test_{os.getpid()}"
    try:
        with open(probe, "w"):
            pass
    except OSError:
        return None
    try:
        probe.unlink()
    except OSError:
        pass
    return (f"DeepFocus runs from {folder}, a folder your account can write to. The scheduled tasks run the helper "
            "there with administrator rights, so a program running under your account could replace it and gain "
            "those rights. Fine on a PC that is yours alone; on a shared PC, or if you do not trust every program "
            "that runs as you, move DeepFocus to a folder only administrators can write to, such as "
            "C:\\Program Files.")


def filter_paths(items: list[str]) -> tuple[list[str], list[str]]:
    """Split into (allowed, refusal messages)."""
    ok, refused = [], []
    for p in items:
        why = forbidden_reason(p)
        if why:
            refused.append(f"{p}: refused, it {why}")
        else:
            ok.append(p)
    return ok, refused
