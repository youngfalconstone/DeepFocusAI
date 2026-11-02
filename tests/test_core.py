"""Self-test for the non-GUI, non-admin parts. Run: python -m tests.test_core"""
from __future__ import annotations

import os
import sys
import tempfile
import winreg
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["DEEPFOCUS_HOME"] = tempfile.mkdtemp(prefix="deepfocus_test_")
os.environ["DEEPFOCUS_POLICY_HIVE"] = "HKCU"
os.environ["DEEPFOCUS_POLICY_PREFIX"] = r"Software\DeepFocusAI_Test"
os.environ["DEEPFOCUS_NOTIF_PREFIX"] = r"Software\DeepFocusAI_Test"

from deepfocus import catalog, doh, filehide, guard, hostsfile, notif, paths, schedule, sounds, state  # noqa: E402


def test_hosts_roundtrip():
    base = "# Copyright\r\n127.0.0.1 localhost\r\n::1 localhost\r\n"
    new = hostsfile.apply_block(base, ["YouTube.com", "www.youtube.com", "localhost", "bad host", "youtube.com"], "abc", "2026-01-01T10:00:00")
    assert hostsfile.has_block(new)
    assert new.count("0.0.0.0 youtube.com") == 1, "dedupe"
    assert "0.0.0.0 localhost" not in new, "protected"
    assert "bad host" not in new
    stripped, removed = hostsfile.strip_block(new)
    assert not hostsfile.has_block(stripped)
    assert stripped.replace("\r\n", "\n") == base.replace("\r\n", "\n"), repr(stripped)
    assert removed > 0
    twice = hostsfile.apply_block(new, ["x.com"], "def", "2026-01-01T10:00:00")
    assert twice.count(hostsfile.BEGIN) == 1
    # tamper detection
    hosts = ["youtube.com", "www.youtube.com"]
    good = hostsfile.apply_block(base, hosts, "abc", "2026-01-01T10:00:00")
    assert hostsfile.block_matches(good, hosts, "abc", "2026-01-01T10:00:00")
    tampered = good.replace("0.0.0.0 www.youtube.com\n", "")
    assert tampered != good
    assert not hostsfile.block_matches(tampered, hosts, "abc", "2026-01-01T10:00:00")
    assert not hostsfile.block_matches(base, hosts, "abc", "2026-01-01T10:00:00")
    print("hosts roundtrip ok")


def test_catalog():
    s, hosts = catalog.resolve("youtube", True)
    assert s and s.key == "youtube" and "www.youtube.com" in hosts and "youtubei.googleapis.com" in hosts
    s, hosts = catalog.resolve("Discord", False)
    assert s.key == "discord" and "discord.com" in hosts and "Discord.exe" in s.processes
    s, hosts = catalog.resolve("reddit.com", True)
    assert s.key == "reddit"
    s, hosts = catalog.resolve("somethingunknown", False)
    assert s is None and "somethingunknown.com" in hosts and "www.somethingunknown.com" in hosts
    s, hosts = catalog.resolve("news.example.org", False)
    assert s is None and "news.example.org" in hosts
    assert catalog.search("x")[0].key == "twitter"
    for p, keys in catalog.PRESETS.items():
        for k in keys:
            assert catalog.get(k), f"preset {p} references unknown {k}"
    print(f"catalog ok ({len(catalog.SERVICES)} services)")


def test_filehide():
    d = Path(tempfile.mkdtemp())
    f = d / "secret.txt"
    f.write_text("hello")
    before = filehide.get_attrs(str(f))
    recs, errs = filehide.hide([str(f), str(d / "missing.txt")])
    assert len(recs) == 1 and len(errs) == 1
    assert filehide.is_hidden(str(f))
    assert f.read_text() == "hello"
    # rehide puts the bits back if someone cleared them
    filehide.set_attrs(str(f), before)
    assert filehide.rehide(recs) == 1 and filehide.is_hidden(str(f))
    n, errs = filehide.unhide(recs)
    assert n == 1 and not errs
    after = filehide.get_attrs(str(f))
    assert (after & filehide.MASK) == (before & filehide.MASK), (before, after)
    assert not filehide.is_hidden(str(f))
    print("filehide ok")


def test_lock():
    d = Path(tempfile.mkdtemp())
    f = d / "locked.txt"
    f.write_text("secret")
    sub = d / "folder"
    sub.mkdir()
    (sub / "inner.txt").write_text("inner")
    recs, _ = filehide.hide([str(f), str(sub)])
    n, errs = filehide.lock(recs)
    assert n == 2 and not errs, errs
    assert all(r.get("acl_sid") for r in recs)
    try:
        f.read_text()
        raise AssertionError("file was readable while locked")
    except PermissionError:
        pass
    try:
        list(sub.iterdir())
        raise AssertionError("folder was listable while locked")
    except PermissionError:
        pass
    n, errs = filehide.unlock(recs)
    assert n == 2 and not errs, errs
    assert f.read_text() == "secret" and (sub / "inner.txt").read_text() == "inner"
    filehide.unhide(recs)
    # second lock on a path that already carries a deny entry is refused, not stacked
    recs2, _ = filehide.hide([str(f)])
    filehide.lock(recs2)
    recs3 = [{"path": str(f), "attrs": 32}]
    n, errs = filehide.lock(recs3)
    assert n == 0 and errs and "already" in errs[0]
    filehide.unlock(recs2)
    filehide.unhide(recs2)
    assert f.read_text() == "secret"
    print("lock ok")


def test_guard():
    home = Path.home()
    sysroot = os.environ.get("SystemRoot", r"C:\Windows")
    bad = [sysroot, sysroot + r"\System32\drivers\etc\hosts", os.environ["ProgramFiles"], str(home), "C:\\",
           os.environ["APPDATA"], str(paths.STATE_DIR), str(paths.STATE_DIR / "session.json"), str(paths.PROJECT_DIR),
           str(guard.desktop_folder()), "relative\\path"]
    for b in bad:
        assert guard.forbidden_reason(b), f"should refuse {b}"
    good = [str(home / "Documents" / "notes.txt"), str(guard.desktop_folder() / "game.lnk"), str(home / "Videos"),
            "D:\\games\\launcher.exe"]
    for g in good:
        assert guard.forbidden_reason(g) is None, f"should allow {g}: {guard.forbidden_reason(g)}"
    ok, refused = guard.filter_paths([bad[0], good[0]])
    assert ok == [good[0]] and len(refused) == 1 and "refused" in refused[0]
    assert guard.forbidden_reason("C:\\x\x00y") == "is empty"
    # links: a junction (no admin needed) must be refused, and so must anything below it
    import subprocess
    base = home / "deepfocus_guard_test"
    base.mkdir(exist_ok=True)
    target = base / "real"
    target.mkdir(exist_ok=True)
    (target / "file.txt").write_text("x")
    link = base / "junction"
    if link.exists():
        subprocess.run(["cmd", "/c", "rmdir", str(link)], capture_output=True)
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True,
                       creationflags=0x08000000)
    try:
        if r.returncode == 0:
            assert guard.has_link(str(link)) and guard.has_link(str(link / "file.txt"))
            assert "junction" in (guard.forbidden_reason(str(link / "file.txt")) or "")
            assert guard.forbidden_reason(str(target / "file.txt")) is None
        else:
            print("  (mklink /J not permitted here, link test skipped)")
        assert guard.state_dir_problem() is None
    finally:
        if link.exists():
            subprocess.run(["cmd", "/c", "rmdir", str(link)], capture_output=True)
        (target / "file.txt").unlink(missing_ok=True)
        target.rmdir()
        base.rmdir()
    print("guard ok")


def test_safe_records():
    from deepfocus import helper
    sysroot = os.environ.get("SystemRoot", r"C:\Windows")
    home = str(Path.home())
    session = {"hidden_files": [
        {"path": home + r"\Desktop\game.lnk", "attrs": 32},
        {"path": sysroot + r"\System32\notepad.exe", "attrs": 32},   # protected: never touched
        {"path": home, "attrs": 16},                                   # whole profile: never touched
        {"attrs": 32}, "junk",                                         # malformed
    ]}
    warnings: list[str] = []
    ok = helper._safe_records(session, warnings)
    assert [r["path"] for r in ok] == [home + r"\Desktop\game.lnk"], ok
    assert len(warnings) == 4, warnings
    warnings = []
    assert helper._safe_records({"hidden_files": "nope"}, warnings) == [] and warnings
    print("safe records ok")


def test_plan_validation():
    from deepfocus import helper
    good = {"id": "abc-123_X", "restore_at": "2030-01-01T10:00:00", "hostnames": ["a.com"], "files_requested": [],
            "processes": [], "services": [], "options": {}, "goal": "g" * 1000}
    out = helper.validate_plan(dict(good))
    assert len(out["goal"]) == 500 and out["hostnames"] == ["a.com"]
    for bad in ([], {}, {**good, "id": "..\\..\\evil"}, {**good, "id": "x" * 65}, {**good, "restore_at": "soon"},
                {**good, "hostnames": "a.com"}, {**good, "hostnames": [1, 2]}, {**good, "options": []},
                {**good, "files_requested": ["x"] * (helper.MAX_FILES + 1)}):
        try:
            helper.validate_plan(bad)
            assert False, f"accepted {str(bad)[:60]}"
        except helper.PlanError:
            pass
    # the plan must live in the state folder and not behind a link
    assert helper._plan_path_ok(str(paths.STATE_DIR / "plan.json")) is None
    assert helper._plan_path_ok(str(Path.home() / "plan.json"))
    print("plan validation ok")


def test_hosts_write_preserves_bytes():
    p = Path(paths.STATE_DIR) / "hosts_test"
    raw = b"# caf\xe9 comment (latin-1)\r\n127.0.0.1 localhost\r\n# \xe4\xbd\xa0\xe5\xa5\xbd utf-8\r\n"
    p.write_bytes(raw)
    text = hostsfile.read_hosts(p)
    hostsfile.write_hosts(p, hostsfile.apply_block(text, ["x.com"], "id1", "2030-01-01T00:00:00"))
    after = p.read_bytes()
    assert after.startswith(raw.rstrip(b"\r\n")) and b"0.0.0.0 x.com" in after, after
    stripped, _ = hostsfile.strip_block(hostsfile.read_hosts(p))
    hostsfile.write_hosts(p, stripped)
    assert p.read_bytes() == raw, p.read_bytes()
    assert not list(p.parent.glob("hosts_test.deepfocus.tmp")), "temp file left behind"
    p.unlink()
    print("hosts byte round trip ok")


def test_doh():
    prefix = os.environ["DEEPFOCUS_POLICY_PREFIX"]
    # plant a pre-existing Chrome value so we can check it comes back
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, prefix + r"\SOFTWARE\Policies\Google\Chrome", 0,
                            winreg.KEY_ALL_ACCESS) as k:
        winreg.SetValueEx(k, "DnsOverHttpsMode", 0, winreg.REG_SZ, "automatic")
    backup, errs = doh.disable()
    assert not errs, errs
    assert len(backup) == len(doh.POLICIES)
    assert doh.is_enforced(backup)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, prefix + r"\SOFTWARE\Policies\Mozilla\Firefox\DNSOverHTTPS") as k:
        assert winreg.QueryValueEx(k, "Enabled")[0] == 0 and winreg.QueryValueEx(k, "Locked")[0] == 1
    # tamper, detect, re-apply
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, prefix + r"\SOFTWARE\Policies\Microsoft\Edge", 0, winreg.KEY_SET_VALUE) as k:
        winreg.DeleteValue(k, "DnsOverHttpsMode")
    assert not doh.is_enforced(backup)
    doh.disable()
    assert doh.is_enforced(backup)
    # a record naming any other registry location is ignored, never written to or deleted
    other = prefix + r"\SOFTWARE\Policies\Other"
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, other, 0, winreg.KEY_ALL_ACCESS) as k:
        winreg.SetValueEx(k, "Keep", 0, winreg.REG_SZ, "yes")
    bogus = [{"browser": "x", "key": other, "name": "Keep", "type": 1, "had_value": True, "old_type": 1,
              "old_value": "changed", "created_keys": [other]}, "junk", {"key": 5}]
    errs = doh.restore(bogus)
    assert len(errs) == 3, errs
    assert not doh.is_enforced(bogus) or True  # must not raise on junk
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, other) as k:
        assert winreg.QueryValueEx(k, "Keep")[0] == "yes", "unknown key must stay untouched"
    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, other)
    errs = doh.restore(backup)
    assert not errs, errs
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, prefix + r"\SOFTWARE\Policies\Google\Chrome") as k:
        assert winreg.QueryValueEx(k, "DnsOverHttpsMode")[0] == "automatic", "pre-existing value must come back"
    for sub in (r"\SOFTWARE\Policies\Microsoft\Edge", r"\SOFTWARE\Policies\Mozilla\Firefox\DNSOverHTTPS",
                r"\SOFTWARE\Policies\Mozilla"):
        try:
            winreg.OpenKey(winreg.HKEY_CURRENT_USER, prefix + sub)
            raise AssertionError(f"key we created should be gone: {sub}")
        except FileNotFoundError:
            pass
    # cleanup planted key
    for sub in (r"\SOFTWARE\Policies\Google\Chrome", r"\SOFTWARE\Policies\Google", r"\SOFTWARE\Policies", r"\SOFTWARE", ""):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, prefix + sub)
        except OSError:
            pass
    print("doh ok")


def test_state():
    s = state.new_session("write report", datetime.now() + timedelta(minutes=5), ["a.com"], [], [], [], {})
    state.save_session(s)
    assert state.load_session()["goal"] == "write report"
    assert not state.session_is_active(s)
    s["status"] = "active"
    assert state.session_is_active(s) and not state.session_expired(s)
    s["restore_at"] = (datetime.now() - timedelta(seconds=1)).strftime(state.ISO)
    assert state.session_expired(s)
    s["started"] = (datetime.now() - timedelta(minutes=30)).strftime(state.ISO)
    s["ended"] = datetime.now().strftime(state.ISO)
    s["status"] = "ended"
    state.append_history(s)
    st = state.stats(state.load_history())
    assert st["sessions"] == 1 and 1700 < st["total_seconds"] < 1900 and st["streak_days"] == 1
    state.clear_session()
    assert state.load_session() is None
    # backup pruning
    paths.ensure_dirs()
    for i in range(25):
        p = paths.BACKUP_DIR / f"hosts_2026010{i % 10}_{i:06d}_x.bak"
        p.write_text("x")
        os.utime(p, (1_700_000_000 + i, 1_700_000_000 + i))
    assert state.prune_backups(20) == 5
    assert len(list(paths.BACKUP_DIR.glob("hosts_*.bak"))) == 20
    assert not (paths.BACKUP_DIR / "hosts_20260100_000000_x.bak").exists(), "oldest should go first"
    print("state ok")


def test_schedule():
    w = schedule.default_weekly(4)
    # 2026-11-02 is a Monday
    mon = datetime(2026, 11, 2, 10, 0)
    wed, fri = mon + timedelta(days=2), mon + timedelta(days=4)
    sat, sun = mon + timedelta(days=5), mon + timedelta(days=6)
    assert schedule.is_blocking(w, mon) and schedule.is_blocking(w, wed)
    assert schedule.is_blocking(w, fri.replace(hour=14, minute=59))
    assert not schedule.is_blocking(w, fri.replace(hour=15, minute=0))
    assert not schedule.is_blocking(w, sat) and not schedule.is_blocking(w, sun.replace(hour=23, minute=59))
    assert schedule.is_blocking(w, mon + timedelta(days=7, hours=-10))  # next Monday 00:00
    assert schedule.next_change(w, wed) == fri.replace(hour=15, minute=0)
    assert schedule.next_change(w, fri.replace(hour=16)) == mon + timedelta(days=7, hours=-10)
    assert schedule.next_change(w, sat) == mon + timedelta(days=7, hours=-10)
    assert schedule.prev_change(w, sat) == fri.replace(hour=15, minute=0)
    assert schedule.prev_change(w, wed) == mon.replace(hour=0)
    assert schedule.describe(w) == "Blocked all week except Friday from 15:00, Saturday and Sunday.", schedule.describe(w)
    assert "Free time starts Friday 15:00" in schedule.phase_text(w, wed)
    assert "Blocks return Monday 00:00" in schedule.phase_text(w, sat)
    # one-shot schedules always block and never change
    assert schedule.is_blocking(schedule.once(), sat) and schedule.next_change(None, sat) is None
    # normalisation: bad entries dropped, weeks clamped, non-weekly -> once/long
    n = schedule.normalize({"kind": "weekly", "weeks": 999, "free": {"9": [0, 1440], "x": [1, 2], "2": [600, 500],
                                                                   "3": [-5, 99999]}})
    assert n == {"kind": "weekly", "weeks": 52, "free": {"3": [0, 1440]}}, n
    assert schedule.normalize({"kind": "long"}) == {"kind": "long"} and schedule.normalize(None) == {"kind": "once"}
    assert not schedule.has_blocked_time({"kind": "weekly", "weeks": 1, "free": {str(d): [0, 1440] for d in range(7)}})
    assert schedule.has_blocked_time(w)
    # a weekday-evenings schedule: free every day from 18:00
    ev = schedule.normalize({"kind": "weekly", "weeks": 2, "free": {str(d): [1080, 1440] for d in range(7)}})
    assert schedule.is_blocking(ev, wed) and not schedule.is_blocking(ev, wed.replace(hour=18))
    assert schedule.next_change(ev, wed.replace(hour=19)) == wed + timedelta(days=1, hours=-10)
    print("schedule ok")


def test_notif():
    path = r"Software\DeepFocusAI_Test\Software\Microsoft\Windows\CurrentVersion\Notifications\Settings"
    # 1. value absent before -> blocked -> restored to absent
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path) as k:
        try:
            winreg.DeleteValue(k, notif.VALUE)
        except FileNotFoundError:
            pass
    b = notif.block()
    assert b == {"had_value": False, "old_value": None} and notif.is_blocked()
    assert notif.restore(b) is None and not notif.is_blocked()
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
        try:
            winreg.QueryValueEx(k, notif.VALUE)
            raise AssertionError("value should have been removed")
        except FileNotFoundError:
            pass
    # 2. value present (1) before -> blocked -> restored to 1
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, notif.VALUE, 0, winreg.REG_DWORD, 1)
    b = notif.block()
    assert b == {"had_value": True, "old_value": 1} and notif.is_blocked()
    notif.restore(b)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
        assert winreg.QueryValueEx(k, notif.VALUE)[0] == 1
    print("notif ok")


def test_sounds():
    for kind, _ in sounds.SOUNDS:
        if kind != "off":
            p = sounds.file_for(kind)
            assert p.exists() and p.stat().st_size > 100_000, kind
    sounds.play("rain")
    assert sounds.current() == "rain"
    sounds.stop()
    assert sounds.current() == "off"
    print("sounds ok")


if __name__ == "__main__":
    test_hosts_roundtrip()
    test_catalog()
    test_filehide()
    test_lock()
    test_guard()
    test_plan_validation()
    test_safe_records()
    test_hosts_write_preserves_bytes()
    test_doh()
    test_state()
    test_schedule()
    test_notif()
    test_sounds()
    print("ALL OK")
