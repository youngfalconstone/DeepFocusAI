"""Exercise the elevated helper's apply/enforce/restore logic WITHOUT admin rights by
pointing it at a temporary hosts file, state folder and HKCU policy prefix. Also registers
copies of both scheduled tasks at least-privilege level, runs them once, and checks that
they executed the helper. Run: python -m tests.test_helper_sandbox"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import winreg
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
HOME = Path(tempfile.mkdtemp(prefix="deepfocus_sandbox_"))
os.environ["DEEPFOCUS_HOME"] = str(HOME)
hosts = HOME / "hosts"
hosts.write_bytes(b"# test hosts\r\n127.0.0.1 localhost\r\n")
os.environ["DEEPFOCUS_HOSTS"] = str(hosts)
os.environ["DEEPFOCUS_POLICY_HIVE"] = "HKCU"
PREFIX = r"Software\DeepFocusAI_Sandbox"
os.environ["DEEPFOCUS_POLICY_PREFIX"] = PREFIX
os.environ["DEEPFOCUS_NOTIF_PREFIX"] = PREFIX

from deepfocus import doh, filehide, helper, hostsfile, notif, paths, schedule, state  # noqa: E402

TEST_TASK = "DeepFocus AI Restore (sandbox test)"
TEST_ENFORCE = "DeepFocus AI Enforce (sandbox test)"


def _delete_tree(path: str) -> None:
    """Remove a registry subtree under HKCU (leftovers from an earlier aborted run)."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_ALL_ACCESS) as k:
            subs = []
            while True:
                try:
                    subs.append(winreg.EnumKey(k, len(subs)))
                except OSError:
                    break
        for s in subs:
            _delete_tree(path + "\\" + s)
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
    except OSError:
        pass


def test_apply_enforce_restore():
    _delete_tree(PREFIX)
    # The temp folder sits under AppData, which the path guard refuses, so the test file lives in the profile.
    work = Path.home() / f"DeepFocusAI_sandbox_{HOME.name[-8:]}"
    work.mkdir(exist_ok=True)
    secret = work / "secret.txt"
    secret.write_text("keep me")
    before = filehide.get_attrs(str(secret))
    sysroot = os.environ.get("SystemRoot", r"C:\Windows")
    session = state.new_session("sandbox", datetime.now() + timedelta(minutes=3), ["example.com", "www.example.com"],
                                [], [str(secret), str(HOME / "missing.txt"), sysroot], ["custom"],
                                {"ipv6": True, "doh_off": True, "lock_files": True, "block_notifications": True})
    plan = HOME / "plan.json"
    plan.write_text(json.dumps(session))
    result = HOME / "r1.json"
    code = helper.apply(str(plan), str(result))
    r = json.loads(result.read_text())
    assert code == 0 and r["ok"], r
    text = hosts.read_text()
    assert "0.0.0.0 example.com" in text and "::1 www.example.com" in text and "127.0.0.1 localhost" in text
    assert filehide.is_hidden(str(secret))
    try:
        secret.read_text()
        raise AssertionError("locked file was readable")
    except PermissionError:
        pass
    s = state.load_session()
    assert s["status"] == "active" and len(s["hidden_files"]) == 1 and s["hosts_backup"]
    assert s["hidden_files"][0].get("acl_sid"), "lock not recorded"
    assert any("missing.txt" in w for w in r["warnings"])
    assert any(sysroot in w and "refused" in w for w in r["warnings"]), r["warnings"]
    assert len(s["doh_backup"]) == len(doh.POLICIES) and doh.is_enforced(s["doh_backup"])
    assert s["notif_backup"] == {"had_value": False, "old_value": None} and notif.is_blocked()
    print("apply ok  (tasks registered:", r["task"], r["enforce"], "- expected False without admin)")

    # not expired -> if-expired restore must skip
    result2 = HOME / "r2.json"
    helper.restore(str(result2), if_expired=True, reason="scheduled")
    assert json.loads(result2.read_text()).get("skipped") and hostsfile.has_block(hosts.read_text())
    print("if-expired skip ok")

    # add to the running session: new hostnames written at once, invalid/protected entries dropped with a reason,
    # a request for another session id or a malformed request refused
    ext = HOME / "extend.json"
    rx = HOME / "rx.json"
    ext.write_text(json.dumps({"id": s["id"], "hostnames": ["Reddit.com", "localhost", "not a host", "example.com"],
                               "services": ["reddit", "Bad Key!"],
                               "processes": ["Foo.exe", "bad/exe", "explorer.exe", "foo.exe"]}))
    assert helper.extend(str(ext), str(rx)) == 0
    x = json.loads(rx.read_text())
    assert x["ok"] and x["added"] == 1 and x["total"] == 3 and not x["paused"], x
    assert any("localhost" in w for w in x["warnings"]) and any("not a host" in w for w in x["warnings"])
    assert any("explorer.exe" in w for w in x["warnings"]) and any("bad/exe" in w for w in x["warnings"]), x["warnings"]
    text = hosts.read_text()
    assert "0.0.0.0 reddit.com" in text and "0.0.0.0 example.com" in text and "127.0.0.1 localhost" in text
    s = state.load_session()
    assert s["hostnames"] == ["example.com", "www.example.com", "reddit.com"], s["hostnames"]
    assert s["services"] == ["custom", "reddit"] and s["processes"] == ["Foo.exe"], (s["services"], s["processes"])
    assert len(list(paths.BACKUP_DIR.glob("hosts_*.bak"))) >= 2, "extend must back the hosts file up too"
    ext.write_text(json.dumps({"id": "someone-else", "hostnames": ["x.com"]}))
    assert helper.extend(str(ext), str(rx)) == 2 and "running session" in json.loads(rx.read_text())["error"]
    ext.write_text(json.dumps({"id": s["id"], "hostnames": "x.com"}))
    assert helper.extend(str(ext), str(rx)) == 2 and "list of strings" in json.loads(rx.read_text())["error"]
    ext.write_text("[]")
    assert helper.extend(str(ext), str(rx)) == 2
    assert helper.extend(str(Path.home() / "nowhere.json"), str(rx)) == 2, "extension file must live in the state folder"
    assert state.load_session()["hostnames"] == s["hostnames"], "refused requests change nothing"
    print("extend ok")

    # tamper with everything, then enforce must put it back
    hostsfile.write_hosts(hosts, hostsfile.strip_block(hosts.read_text())[0])
    filehide.set_attrs(str(secret), before)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, PREFIX + r"\SOFTWARE\Policies\Google\Chrome", 0, winreg.KEY_SET_VALUE) as k:
        winreg.DeleteValue(k, "DnsOverHttpsMode")
    notif.restore({"had_value": False})  # someone turned notifications back on by hand
    assert not notif.is_blocked()
    result_e = HOME / "re.json"
    helper.enforce(str(result_e))
    re_ = json.loads(result_e.read_text())
    assert re_["ok"] and any("hosts" in f for f in re_["fixes"]) and any("re-hidden" in f for f in re_["fixes"]) \
        and any("DoH" in f for f in re_["fixes"]) and any("notifications" in f for f in re_["fixes"]), re_
    assert notif.is_blocked()
    assert hostsfile.block_matches(hosts.read_text(), s["hostnames"], s["id"], s["restore_at"])
    assert filehide.is_hidden(str(secret))
    assert doh.is_enforced(s["doh_backup"])
    assert len(state.load_session()["enforce_fixes"]) >= 3
    # a second enforce with nothing wrong changes nothing (task registration needs admin, so ignore that line)
    helper.enforce(str(result_e))
    assert [f for f in json.loads(result_e.read_text())["fixes"] if "task" not in f] == []
    print("enforce ok")

    # force expiry -> enforce performs the restore itself
    s = state.load_session()
    s["restore_at"] = (datetime.now() - timedelta(seconds=5)).strftime(state.ISO)
    state.save_session(s)
    result3 = HOME / "r3.json"
    helper.enforce(str(result3))
    r3 = json.loads(result3.read_text())
    assert r3["ok"] and r3["files_restored"] == 1, r3
    assert hosts.read_text().replace("\r\n", "\n") == "# test hosts\n127.0.0.1 localhost\n", repr(hosts.read_text())
    assert (filehide.get_attrs(str(secret)) & filehide.MASK) == (before & filehide.MASK)
    assert secret.read_text() == "keep me", "lock must be gone"
    assert not notif.is_blocked(), "notifications switch must be restored"
    assert state.load_session() is None
    ext.write_text(json.dumps({"id": s["id"], "hostnames": ["x.com"]}))
    assert helper.extend(str(ext), str(rx)) == 2 and "no running session" in json.loads(rx.read_text())["error"]
    hist = state.load_history()
    assert len(hist) == 1 and hist[0]["end_reason"] == "scheduled"
    try:
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, PREFIX + r"\SOFTWARE\Policies\Mozilla\Firefox\DNSOverHTTPS")
        raise AssertionError("DoH policy key should have been removed")
    except FileNotFoundError:
        pass
    print("expired restore via enforce ok")

    # orphan block with no session -> enforce cleans it
    hostsfile.write_hosts(hosts, hostsfile.apply_block(hostsfile.read_hosts(hosts), ["orphan.com"], "zzz", "x"))
    result4 = HOME / "r4.json"
    helper.enforce(str(result4))
    assert not hostsfile.has_block(hosts.read_text())
    print("orphan cleanup ok")
    secret.unlink()
    work.rmdir()
    _delete_tree(PREFIX)


def test_weekly_schedule():
    """A weekly schedule started inside a free window applies nothing, then enforce switches the
    blocks on when the window ends, off again when the next one starts, and restores at the end."""
    _delete_tree(PREFIX)
    work = Path.home() / f"DeepFocusAI_sandbox_{HOME.name[-8:]}w"
    work.mkdir(exist_ok=True)
    secret = work / "secret.txt"
    secret.write_text("weekend")
    before = filehide.get_attrs(str(secret))
    now = datetime.now()
    today = str(now.weekday())
    other = str((now.weekday() + 3) % 7)
    free_now = {"kind": "weekly", "weeks": 2, "free": {today: [0, 1440]}}       # this whole day is free
    blocked_now = {"kind": "weekly", "weeks": 2, "free": {other: [0, 1440]}}    # some other day is free
    session = state.new_session("weekly", now + timedelta(weeks=2), ["example.org"], [], [str(secret)], ["custom"],
                                {"ipv6": True, "doh_off": True, "lock_files": False}, free_now)
    plan = HOME / "plan_w.json"
    plan.write_text(json.dumps(session))
    result = HOME / "w1.json"
    code = helper.apply(str(plan), str(result))
    r = json.loads(result.read_text())
    assert code == 0 and r["ok"] and r["paused"] and r["hidden"] == 0, r
    assert not hostsfile.has_block(hosts.read_text())
    assert not filehide.is_hidden(str(secret))
    s = state.load_session()
    assert s["status"] == "active" and s["paused"] and s["doh_backup"] == [] and s["schedule_log"]
    # if-expired restore must still skip while paused
    helper.restore(str(HOME / "w2.json"), if_expired=True, reason="scheduled")
    assert json.loads((HOME / "w2.json").read_text()).get("skipped")
    # enforce in a free window: nothing to fix, still paused
    helper.enforce(str(HOME / "we0.json"))
    e0 = json.loads((HOME / "we0.json").read_text())
    assert e0["paused"] and e0["schedule"] == [] and [f for f in e0["fixes"] if "task" not in f] == [], e0
    print("weekly apply in free window ok")

    # the free window ends -> blocks go on
    s["schedule"] = blocked_now
    state.save_session(s)
    helper.enforce(str(HOME / "we1.json"))
    e1 = json.loads((HOME / "we1.json").read_text())
    assert not e1["paused"] and any("back on" in c for c in e1["schedule"]), e1
    s = state.load_session()
    assert hostsfile.block_matches(hosts.read_text(), s["hostnames"], s["id"], s["restore_at"])
    assert filehide.is_hidden(str(secret)) and len(s["hidden_files"]) == 1
    assert doh.is_enforced(s["doh_backup"])
    # tamper while blocking -> fixed as usual
    hostsfile.write_hosts(hosts, hostsfile.strip_block(hosts.read_text())[0])
    helper.enforce(str(HOME / "we2.json"))
    assert any("hosts" in f for f in json.loads((HOME / "we2.json").read_text())["fixes"])
    print("weekly blocks on ok")

    # the next free window starts -> blocks lift, files reappear exactly, DoH back
    s = state.load_session()
    s["schedule"] = free_now
    state.save_session(s)
    helper.enforce(str(HOME / "we3.json"))
    e3 = json.loads((HOME / "we3.json").read_text())
    assert e3["paused"] and any("lifted" in c for c in e3["schedule"]), e3
    assert not hostsfile.has_block(hosts.read_text())
    assert (filehide.get_attrs(str(secret)) & filehide.MASK) == (before & filehide.MASK)
    s = state.load_session()
    assert s["hidden_files"] == [] and s["doh_backup"] == [] and len(s["schedule_log"]) >= 3
    try:
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, PREFIX + r"\SOFTWARE\Policies\Mozilla\Firefox\DNSOverHTTPS")
        raise AssertionError("DoH policy key should be gone during a free window")
    except FileNotFoundError:
        pass
    print("weekly blocks lifted ok")

    # the schedule ends -> full restore and archive
    s["restore_at"] = (datetime.now() - timedelta(seconds=5)).strftime(state.ISO)
    state.save_session(s)
    helper.enforce(str(HOME / "we4.json"))
    assert json.loads((HOME / "we4.json").read_text())["ok"] and state.load_session() is None
    hist = state.load_history()
    assert hist[-1]["schedule"]["kind"] == "weekly" and hist[-1]["end_reason"] == "scheduled"
    # a schedule with no blocked time is refused
    bad = dict(session, schedule={"kind": "weekly", "weeks": 1, "free": {str(d): [0, 1440] for d in range(7)}})
    plan.write_text(json.dumps(bad))
    assert helper.apply(str(plan), str(HOME / "w5.json")) == 2
    assert "no blocked time" in json.loads((HOME / "w5.json").read_text())["error"]
    print("weekly end + validation ok")
    secret.unlink()
    work.rmdir()
    _delete_tree(PREFIX)


def _task_result(name: str) -> tuple[str, str]:
    """(last run time, last result) of a task as schtasks reports them."""
    p = subprocess.run(["schtasks", "/Query", "/TN", name, "/V", "/FO", "LIST"], capture_output=True, text=True)
    run, result = "", ""
    for line in p.stdout.splitlines():
        if line.startswith("Last Run Time:"):
            run = line.split(":", 1)[1].strip()
        elif line.startswith("Last Result:"):
            result = line.split(":", 1)[1].strip()
    return run, result


def _run_task(name: str, xml: str, expect: tuple[str, ...]):
    """Register the real task XML (least privilege so no admin needed), run it, confirm the helper ran.
    The task runs against the real state folder. A healthy live session makes `enforce` exit without
    logging, so a clean exit (last result 0 after a fresh run) counts as well as the expected log line."""
    xml = xml.replace("HighestAvailable", "LeastPrivilege")
    real_log = Path.home() / ".deepfocus" / "deepfocus.log"
    tmp = HOME / (name.replace(" ", "_") + ".xml")
    tmp.write_text(xml, encoding="utf-16")
    before_size = real_log.stat().st_size if real_log.exists() else 0
    p = subprocess.run(["schtasks", "/Create", "/TN", name, "/XML", str(tmp), "/F"], capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    try:
        run_before, _ = _task_result(name)
        p = subprocess.run(["schtasks", "/Run", "/TN", name], capture_output=True, text=True)
        assert p.returncode == 0, p.stdout + p.stderr
        deadline = time.time() + 30
        tail = ""
        while time.time() < deadline:
            if real_log.exists() and real_log.stat().st_size > before_size:
                tail = real_log.read_text(encoding="utf-8").splitlines()[-1]
                if any(e in tail for e in expect):
                    print(f"task '{name}' executes ok:", tail)
                    return
            run_after, result = _task_result(name)
            if run_after and run_after != run_before and "N/A" not in run_after and result == "0":
                print(f"task '{name}' executes ok: exit 0 at {run_after} (nothing to log)")
                return
            time.sleep(1)
        raise AssertionError(f"scheduled task {name} did not run the helper (last log line: {tail!r})")
    finally:
        subprocess.run(["schtasks", "/Delete", "/TN", name, "/F"], capture_output=True)


def test_task_xml_executes():
    _run_task(TEST_TASK, helper.restore_task_xml(datetime.now() + timedelta(days=1)), ("restore(scheduled)", "skipped"))
    _run_task(TEST_ENFORCE, helper.enforce_task_xml(datetime.now() + timedelta(days=1)), ("enforce", "restore("))


if __name__ == "__main__":
    print("sandbox:", HOME)
    test_apply_enforce_restore()
    test_weekly_schedule()
    test_task_xml_executes()
    print("ALL OK")
