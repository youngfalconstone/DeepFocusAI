"""Block Windows toast notifications for the length of a session, with exact restore.

Windows keeps the global "Notifications" switch of the Settings app in the current user's
registry: NOC_GLOBAL_SETTING_TOASTS_ENABLED under
HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Notifications\\Settings (absent = on).
Writing 0 there silences every toast (banner and Action Center) at once, no admin needed,
and takes effect as soon as the shell notices the change. The previous value (or its absence)
is recorded so restore puts the switch back exactly as it was.

Tests redirect the key with DEEPFOCUS_NOTIF_PREFIX (a subkey path under HKCU).
"""
from __future__ import annotations

import os
import winreg

VALUE = "NOC_GLOBAL_SETTING_TOASTS_ENABLED"
_PATH = r"Software\Microsoft\Windows\CurrentVersion\Notifications\Settings"


def _key_path() -> str:
    prefix = os.environ.get("DEEPFOCUS_NOTIF_PREFIX", "")
    return (prefix.rstrip("\\") + "\\" + _PATH) if prefix else _PATH


def _read() -> tuple[bool, int | None]:
    """(value present, value)."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _key_path()) as k:
            val, typ = winreg.QueryValueEx(k, VALUE)
            return True, int(val)
    except FileNotFoundError:
        return False, None
    except OSError:
        return False, None


def block() -> dict:
    """Turn toasts off. Returns the backup record to store in the session."""
    had, old = _read()
    backup = {"had_value": had, "old_value": old}
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _key_path(), 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, VALUE, 0, winreg.REG_DWORD, 0)
    return backup


def is_blocked() -> bool:
    had, old = _read()
    return had and old == 0


def restore(backup: dict | None) -> str | None:
    """Put the switch back the way it was. Returns an error message or None."""
    if not backup:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _key_path(), 0, winreg.KEY_SET_VALUE) as k:
            if backup.get("had_value"):
                winreg.SetValueEx(k, VALUE, 0, winreg.REG_DWORD, int(backup.get("old_value") or 0))
            else:
                try:
                    winreg.DeleteValue(k, VALUE)
                except FileNotFoundError:
                    pass
    except OSError as e:
        return f"Could not restore the Windows notifications setting: {e}"
    return None
