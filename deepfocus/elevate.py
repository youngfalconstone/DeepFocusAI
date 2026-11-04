"""Launch the elevated helper through UAC and wait for it.

Uses ShellExecuteExW with the "runas" verb so Windows shows its own consent
prompt; the GUI has already explained what will happen before this is called.
"""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import time
import uuid
from ctypes import wintypes
from pathlib import Path

from . import paths

SEE_MASK_NOCLOSEPROCESS = 0x00000040
SEE_MASK_NO_CONSOLE = 0x00008000
SW_HIDE = 0
INFINITE = 0xFFFFFFFF
ERROR_CANCELLED = 1223


class SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("fMask", wintypes.ULONG),
        ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR),
        ("lpFile", wintypes.LPCWSTR),
        ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR),
        ("nShow", ctypes.c_int),
        ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", wintypes.LPCWSTR),
        ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD),
        ("hIconOrMonitor", wintypes.HANDLE),
        ("hProcess", wintypes.HANDLE),
    ]


class ElevationCancelled(Exception):
    pass


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _command_line(args: list[str]) -> str:
    """One command line from a list of arguments, escaped the way CommandLineToArgvW splits it again (quotes and
    runs of backslashes handled exactly as the C runtime expects), so a path or name with an unusual character can
    never be split into extra arguments for the elevated helper."""
    return subprocess.list2cmdline(args)


def run_helper(args: list[str], timeout: float = 180.0) -> dict:
    """Run `python -m deepfocus.helper <args>` elevated and return its result JSON."""
    paths.ensure_dirs()
    result_path = paths.RESULTS_DIR / f"{uuid.uuid4().hex}.json"
    exe, lead = paths.helper_command()
    full_args = [*lead, *args, "--result", str(result_path)]
    params = _command_line(full_args)

    if is_admin():
        # Already elevated (rare): run directly.
        subprocess.run([exe, *full_args], cwd=str(paths.PROJECT_DIR), timeout=timeout,
                       creationflags=0x08000000)
    else:
        sei = SHELLEXECUTEINFOW()
        sei.cbSize = ctypes.sizeof(sei)
        sei.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NO_CONSOLE
        sei.hwnd = None
        sei.lpVerb = "runas"
        sei.lpFile = exe
        sei.lpParameters = params
        sei.lpDirectory = str(paths.PROJECT_DIR)
        sei.nShow = SW_HIDE
        if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei)):
            err = ctypes.get_last_error() or ctypes.GetLastError()
            if err == ERROR_CANCELLED:
                raise ElevationCancelled("You declined the administrator prompt.")
            raise OSError(err, ctypes.FormatError(err))
        if sei.hProcess:
            ms = int(timeout * 1000) if timeout else INFINITE
            ctypes.windll.kernel32.WaitForSingleObject(sei.hProcess, ms)
            ctypes.windll.kernel32.CloseHandle(sei.hProcess)

    # Read the result, allowing a moment for the file system.
    deadline = time.time() + 5
    while time.time() < deadline:
        if result_path.exists():
            try:
                data = json.loads(result_path.read_text(encoding="utf-8"))
                try:
                    os.remove(result_path)
                except OSError:
                    pass
                return data
            except json.JSONDecodeError:
                time.sleep(0.1)
        else:
            time.sleep(0.1)
    return {"ok": False, "error": "The helper did not report back. Check deepfocus.log in the state folder."}
