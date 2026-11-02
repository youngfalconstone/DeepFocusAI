"""Turn browser DNS-over-HTTPS off for the length of a session, reversibly.

Firefox with DoH on "Max protection" (and Chrome/Edge/Brave with their own
secure DNS) bypass the hosts file completely. Each browser honours an
enterprise policy in the registry that disables DoH. The elevated helper
writes those policies when a session starts, records exactly what was there
before (value and whether the key existed), and puts it all back at restore.

Browsers read policies at start-up (Chromium also every 90 minutes), so the
switch takes effect when the browser is next launched. The "close browsers at
start" option makes that immediate.

For tests, DEEPFOCUS_POLICY_HIVE=HKCU and DEEPFOCUS_POLICY_PREFIX redirect the
writes to a harmless place.
"""
from __future__ import annotations

import os
import winreg

POLICIES = [
    # (browser, key path, value name, type, value that disables DoH)
    ("Chrome", r"SOFTWARE\Policies\Google\Chrome", "DnsOverHttpsMode", winreg.REG_SZ, "off"),
    ("Edge", r"SOFTWARE\Policies\Microsoft\Edge", "DnsOverHttpsMode", winreg.REG_SZ, "off"),
    ("Brave", r"SOFTWARE\Policies\BraveSoftware\Brave", "DnsOverHttpsMode", winreg.REG_SZ, "off"),
    ("Firefox", r"SOFTWARE\Policies\Mozilla\Firefox\DNSOverHTTPS", "Enabled", winreg.REG_DWORD, 0),
    ("Firefox", r"SOFTWARE\Policies\Mozilla\Firefox\DNSOverHTTPS", "Locked", winreg.REG_DWORD, 1),
]

_ACCESS = winreg.KEY_READ | winreg.KEY_WRITE | winreg.KEY_WOW64_64KEY


def _hive() -> int:
    return winreg.HKEY_CURRENT_USER if os.environ.get("DEEPFOCUS_POLICY_HIVE", "").upper() == "HKCU" \
        else winreg.HKEY_LOCAL_MACHINE


def _prefix() -> str:
    p = os.environ.get("DEEPFOCUS_POLICY_PREFIX", "").strip("\\")
    return p + "\\" if p else ""


def _key_exists(path: str) -> bool:
    try:
        with winreg.OpenKey(_hive(), path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY):
            return True
    except OSError:
        return False


def _missing_levels(path: str) -> list[str]:
    """Key paths along `path` that do not exist yet, deepest last."""
    parts = path.split("\\")
    out = []
    for i in range(1, len(parts) + 1):
        sub = "\\".join(parts[:i])
        if not _key_exists(sub):
            out.append(sub)
    return out


def disable() -> tuple[list[dict], list[str]]:
    """Write the policies. Returns (backup records, errors)."""
    backup: list[dict] = []
    errors: list[str] = []
    for browser, key, name, typ, target in POLICIES:
        path = _prefix() + key
        rec = {"browser": browser, "key": path, "name": name, "type": typ, "created_keys": _missing_levels(path),
               "had_value": False, "old_type": None, "old_value": None}
        try:
            with winreg.CreateKeyEx(_hive(), path, 0, _ACCESS) as k:
                try:
                    old, old_type = winreg.QueryValueEx(k, name)
                    rec.update(had_value=True, old_type=old_type, old_value=old)
                except FileNotFoundError:
                    pass
                winreg.SetValueEx(k, name, 0, typ, target)
            backup.append(rec)
        except OSError as e:
            errors.append(f"{browser} DoH policy: {e.strerror or e}")
    return backup, errors


def is_enforced(backup: list[dict]) -> bool:
    """True if every recorded policy still holds its disabling value."""
    targets = {(p[1], p[2]): p[4] for p in POLICIES}
    for rec in backup:
        if not _known(rec):
            continue
        key = rec["key"][len(_prefix()):] if rec["key"].startswith(_prefix()) else rec["key"]
        want = targets.get((key, rec["name"]))
        try:
            with winreg.OpenKey(_hive(), rec["key"], 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
                val, _ = winreg.QueryValueEx(k, rec["name"])
                if val != want:
                    return False
        except OSError:
            return False
    return True


def _known(rec: dict) -> bool:
    """Only records that name one of our own policy keys are acted on; the session file is user-writable,
    so a record naming any other registry location is ignored rather than written to."""
    if not isinstance(rec, dict) or not isinstance(rec.get("key"), str) or not isinstance(rec.get("name"), str):
        return False
    key = rec["key"][len(_prefix()):] if rec["key"].startswith(_prefix()) else rec["key"]
    return any(key == p[1] and rec["name"] == p[2] for p in POLICIES)


def restore(backup: list[dict]) -> list[str]:
    """Undo `disable` exactly: old values back, our values removed, keys we created deleted if empty."""
    errors: list[str] = []
    created: list[str] = []
    for rec in backup:
        if not _known(rec):
            errors.append("DoH policy record skipped: it does not name one of the known browser policy keys")
            continue
        try:
            with winreg.OpenKey(_hive(), rec["key"], 0, _ACCESS) as k:
                if rec.get("had_value"):
                    winreg.SetValueEx(k, rec["name"], 0, int(rec["old_type"]), rec["old_value"])
                else:
                    try:
                        winreg.DeleteValue(k, rec["name"])
                    except FileNotFoundError:
                        pass
        except FileNotFoundError:
            pass
        except OSError as e:
            errors.append(f"{rec['browser']} DoH policy: {e.strerror or e}")
        # only key levels that are prefixes of our own policy key may be deleted
        for c in rec.get("created_keys", []):
            if isinstance(c, str) and rec["key"].startswith(c) and c not in created:
                created.append(c)
    # deepest first, only if nothing else lives there now
    for path in sorted(created, key=lambda s: -s.count("\\")):
        try:
            with winreg.OpenKey(_hive(), path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
                subkeys, values, _ = winreg.QueryInfoKey(k)
            if subkeys == 0 and values == 0:
                winreg.DeleteKeyEx(_hive(), path, winreg.KEY_WOW64_64KEY, 0)
        except OSError:
            pass
    return errors
