"""Elevated worker. Everything that needs administrator rights lives here so
the GUI itself can stay unelevated and ask first.

Usage (always invoked by the GUI or Task Scheduler, rarely by hand):
    DeepFocus.exe helper apply   <plan.json> --result <result.json>
    DeepFocus.exe helper extend  <extend.json> --result <result.json>   (more hostnames for the running session)
    DeepFocus.exe helper restore [--result <result.json>] [--if-expired] [--reason early|scheduled|manual|startup]
    DeepFocus.exe helper enforce [--result <result.json>]
    DeepFocus.exe helper status

`restore` is idempotent and defensive: it strips any DeepFocus block from the
hosts file even if the session record is missing, unlocks and unhides every
recorded file, puts browser DoH policies back, removes both scheduled tasks,
and archives the session.

`enforce` runs every minute from its own scheduled task while a session is
active. It re-applies anything that was undone by hand (hosts block, file
attributes, DoH policies, the restore task) and, once the session has expired,
performs the restore itself as a second line of defence. For a weekly schedule
it also switches the blocks off at the start of each free window and back on
at its end (the session stays active and both tasks stay registered).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from xml.sax.saxutils import escape

from . import aiblock, doh, filehide, guard, hostsfile, notif, paths, schedule, state

CREATE_NO_WINDOW = 0x08000000


def _sys(tool: str) -> str:
    """Absolute path of a Windows tool, so the elevated helper never resolves it through PATH or the cwd."""
    return str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / tool)


def _run(cmd: list[str]) -> tuple[int, str]:
    p = subprocess.run(cmd, capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _write_result(path: str | None, data: dict) -> None:
    if not path:
        return
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except OSError:
        pass


def flush_dns() -> None:
    _run([_sys("ipconfig.exe"), "/flushdns"])


# ------------------------------------------------------------ scheduler ---
def _principal_user() -> str:
    return os.environ.get("USERDOMAIN", "") + "\\" + os.environ.get("USERNAME", "")


def _task_xml(description: str, triggers: str, args: list[str], extra_settings: str = "") -> str:
    exe, lead = paths.helper_command()
    arg_str = " ".join(lead + args)
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>{escape(description)}</Description>
  </RegistrationInfo>
  <Triggers>
{triggers}
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{escape(_principal_user())}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>true</WakeToRun>
    <ExecutionTimeLimit>PT10M</ExecutionTimeLimit>
    <Priority>5</Priority>
{extra_settings}
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(exe)}</Command>
      <Arguments>{escape(arg_str)}</Arguments>
      <WorkingDirectory>{escape(str(paths.PROJECT_DIR))}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def restore_task_xml(restore_at: datetime) -> str:
    triggers = f"""    <TimeTrigger>
      <StartBoundary>{restore_at.strftime('%Y-%m-%dT%H:%M:%S')}</StartBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{escape(_principal_user())}</UserId>
      <Delay>PT20S</Delay>
    </LogonTrigger>"""
    return _task_xml("DeepFocus AI: restores the hosts file and hidden files when the focus session ends. "
                     "Safe to run at any time.", triggers, ["restore", "--if-expired", "--reason", "scheduled"])


def enforce_task_xml(restore_at: datetime) -> str:
    start = datetime.now() + timedelta(seconds=45)
    end = restore_at + timedelta(days=2)
    triggers = f"""    <TimeTrigger>
      <Repetition>
        <Interval>PT1M</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <StartBoundary>{start.strftime('%Y-%m-%dT%H:%M:%S')}</StartBoundary>
      <EndBoundary>{end.strftime('%Y-%m-%dT%H:%M:%S')}</EndBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{escape(_principal_user())}</UserId>
      <Delay>PT30S</Delay>
    </LogonTrigger>"""
    return _task_xml("DeepFocus AI: while a focus session is active, re-applies anything that was undone by hand and "
                     "restores everything once the session has ended. Deletes itself afterwards.",
                     triggers, ["enforce"], "    <DeleteExpiredTaskAfter>PT1H</DeleteExpiredTaskAfter>")


def _register(name: str, xml: str) -> tuple[bool, str]:
    fd, tmp = tempfile.mkstemp(suffix=".xml", prefix="deepfocus_task_")
    os.close(fd)
    try:
        Path(tmp).write_text(xml, encoding="utf-16")
        code, out = _run([_sys("schtasks.exe"), "/Create", "/TN", name, "/XML", tmp, "/F"])
        return code == 0, out.strip()
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def register_task(restore_at: datetime) -> tuple[bool, str]:
    return _register(paths.TASK_NAME, restore_task_xml(restore_at))


def register_enforce_task(restore_at: datetime) -> tuple[bool, str]:
    return _register(paths.ENFORCE_TASK_NAME, enforce_task_xml(restore_at))


def delete_task(name: str = paths.TASK_NAME) -> tuple[bool, str]:
    code, out = _run([_sys("schtasks.exe"), "/Delete", "/TN", name, "/F"])
    return code == 0, out.strip()


def task_exists(name: str = paths.TASK_NAME) -> bool:
    code, _ = _run([_sys("schtasks.exe"), "/Query", "/TN", name])
    return code == 0


# ------------------------------------------------------- block / unblock ---
def _block_all(session: dict, warnings: list[str]) -> str | None:
    """Put every block of the session in place: hosts, hidden files, lock, DoH policies.
    Saves the session after each step so a crash can never lose a record. Returns an
    error message if the hosts file could not be written, else None."""
    opts = session.get("options", {})

    # hosts: back up, then write the marked block
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = paths.BACKUP_DIR / f"hosts_{stamp}_{session['id']}.bak"
    try:
        shutil.copy2(paths.HOSTS_PATH, backup)
        session["hosts_backup"] = str(backup)
    except OSError as e:
        warnings.append(f"Could not back up hosts file: {e}")
    state.prune_backups()
    text = hostsfile.read_hosts(paths.HOSTS_PATH)
    new_text = hostsfile.apply_block(text, session["hostnames"], session["id"], session["restore_at"],
                                     ipv6=bool(opts.get("ipv6", True)))
    try:
        hostsfile.write_hosts(paths.HOSTS_PATH, new_text)
    except OSError as e:
        return f"Could not write hosts file: {e}"
    flush_dns()

    # files: hide (record first), then optional lock
    records, errs = filehide.hide(session.get("files_requested", []))
    session["hidden_files"] = records
    warnings.extend(errs)
    session["paused"] = False
    state.save_session(session)
    if opts.get("lock_files") and records:
        _, errs = filehide.lock(records)
        warnings.extend(errs)
        state.save_session(session)

    # browser DNS-over-HTTPS policies
    if opts.get("doh_off", True):
        backup_recs, errs = doh.disable()
        session["doh_backup"] = backup_recs
        warnings.extend(errs)
        state.save_session(session)

    # Windows notifications (current user's switch; recorded for exact restore)
    if opts.get("block_notifications"):
        try:
            session["notif_backup"] = notif.block()
        except OSError as e:
            warnings.append(f"Could not turn off Windows notifications: {e}")
        state.save_session(session)
    return None


def _safe_records(session: dict, warnings: list[str]) -> list[dict]:
    """The recorded hidden files, re-checked against the path guard. The session file is user-writable,
    so the elevated helper only touches attributes and permissions of paths the guard would allow today."""
    out: list[dict] = []
    recs = session.get("hidden_files", [])
    if not isinstance(recs, list):
        warnings.append("hidden_files record is not a list, ignored")
        return out
    for rec in recs:
        if not isinstance(rec, dict) or not isinstance(rec.get("path"), str):
            warnings.append("malformed hidden-file record skipped")
            continue
        why = guard.forbidden_reason(rec["path"])
        if why:
            warnings.append(f"{rec['path']}: not touched, it {why}")
            continue
        out.append(rec)
    return out


def _unblock_all(session: dict | None, warnings: list[str]) -> tuple[int, int]:
    """Undo every block: strip the hosts block (even without a session), unlock and unhide
    recorded files, put DoH policies back. Returns (hosts lines removed, files restored)."""
    text = hostsfile.read_hosts(paths.HOSTS_PATH)
    removed = 0
    if hostsfile.has_block(text):
        new_text, removed = hostsfile.strip_block(text)
        hostsfile.write_hosts(paths.HOSTS_PATH, new_text)  # OSError propagates to the caller
        flush_dns()
    restored = 0
    if session:
        records = _safe_records(session, warnings)
        _, errs = filehide.unlock(records)
        warnings.extend(errs)
        restored, errs = filehide.unhide(records)
        warnings.extend(errs)
        if session.get("doh_backup"):
            backup = session["doh_backup"]
            if isinstance(backup, list):
                warnings.extend(doh.restore(backup))
            else:
                warnings.append("doh_backup record is not a list, ignored")
            session["doh_backup"] = []
        if session.get("notif_backup"):
            err = notif.restore(session["notif_backup"])
            if err:
                warnings.append(err)
            session["notif_backup"] = None
    return removed, restored


# ----------------------------------------------------------------- apply ---
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_HOSTNAMES = 5000
MAX_FILES = 2000


class PlanError(ValueError):
    pass


def validate_plan(session) -> dict:
    """The helper never trusts the GUI: every field of plan.json that it acts on is re-checked here.
    Hostnames and paths are validated again by hostsfile/guard when used; this catches shape and size."""
    if not isinstance(session, dict):
        raise PlanError("plan is not a JSON object")
    sid = session.get("id")
    if not isinstance(sid, str) or not _ID_RE.match(sid):
        raise PlanError("plan has no valid session id")
    try:
        state.parse_iso(str(session.get("restore_at", "")))
    except (ValueError, TypeError):
        raise PlanError("plan has no valid restore time")
    for key, cap in (("hostnames", MAX_HOSTNAMES), ("files_requested", MAX_FILES), ("processes", 500),
                     ("services", 500)):
        v = session.get(key, [])
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            raise PlanError(f"plan field '{key}' must be a list of strings")
        if len(v) > cap:
            raise PlanError(f"plan field '{key}' has too many entries ({len(v)} > {cap})")
        session[key] = [x[:1024] for x in v]
    if not isinstance(session.get("options", {}), dict):
        raise PlanError("plan field 'options' must be an object")
    session.setdefault("options", {})
    goal = session.get("goal", "")
    session["goal"] = (goal if isinstance(goal, str) else "")[:500]
    notes = session.get("notes", "")
    session["notes"] = (notes if isinstance(notes, str) else "")[:20000]
    return session


def _plan_path_ok(plan_path: str) -> str | None:
    """The plan must live in the state folder and must not be reached through a link."""
    try:
        real = os.path.normcase(os.path.realpath(plan_path))
        sd = os.path.normcase(os.path.realpath(str(paths.STATE_DIR)))
    except OSError as e:
        return f"plan path cannot be resolved: {e}"
    if not real.startswith(sd.rstrip("\\") + "\\"):
        return "the plan file must be inside the DeepFocus state folder"
    if guard.has_link(plan_path):
        return "the plan file is reached through a symbolic link or junction"
    return None


def _state_ok(result_path: str | None) -> bool:
    problem = guard.state_dir_problem()
    if problem:
        state.log(f"helper refused: {problem}")
        _write_result(result_path, {"ok": False, "error": f"Refused: {problem}."})
        return False
    return True


def apply(plan_path: str, result_path: str | None) -> int:
    if not _state_ok(result_path):
        return 2
    paths.ensure_dirs()
    warnings: list[str] = []
    problem = _plan_path_ok(plan_path)
    if problem:
        _write_result(result_path, {"ok": False, "error": f"Refused: {problem}."})
        return 2
    with open(plan_path, "r", encoding="utf-8") as f:
        session = json.load(f)
    try:
        session = validate_plan(session)
    except PlanError as e:
        _write_result(result_path, {"ok": False, "error": f"Refused: {e}."})
        return 2
    restore_at = state.parse_iso(session["restore_at"])
    if restore_at <= datetime.now():
        _write_result(result_path, {"ok": False, "error": "Restore time is already in the past."})
        return 2
    sched = schedule.normalize(session.get("schedule"))
    session["schedule"] = sched
    if not schedule.has_blocked_time(sched):
        _write_result(result_path, {"ok": False, "error": "The weekly schedule has no blocked time at all."})
        return 2

    # 0. the plan may only name paths that are safe to hide
    allowed, refused = guard.filter_paths(session.get("files_requested", []))
    warnings.extend(refused)
    session["files_requested"] = allowed
    session["hidden_files"] = []
    session["doh_backup"] = []
    session["status"] = "active"
    session["started"] = state.now_iso()

    # 1. blocks, unless the weekly schedule says this moment is a free window
    blocking = schedule.is_blocking(sched)
    if blocking:
        err = _block_all(session, warnings)
        if err:
            _write_result(result_path, {"ok": False, "error": err, "warnings": warnings})
            return 3
    else:
        session["paused"] = True
        nxt = schedule.next_change(sched)
        session.setdefault("schedule_log", []).append(
            f"{state.now_iso()} started inside a free window, blocks switch on "
            f"{nxt.strftime('%a %d %b %H:%M') if nxt else 'later'}")
        state.save_session(session)
    records = session.get("hidden_files", [])
    locked = sum(1 for r in records if r.get("acl_sid"))

    # 2. the independent restore task and the enforcement task
    ok, out = register_task(restore_at)
    if not ok:
        warnings.append(f"Scheduled restore task could not be registered: {out}")
    session["task_registered"] = ok
    ok2, out2 = register_enforce_task(restore_at)
    if not ok2:
        warnings.append(f"Enforcement task could not be registered: {out2}")
    session["enforce_task_registered"] = ok2

    # 3. final record
    state.save_session(session)
    state.log(f"apply: session {session['id']} active ({sched['kind']}{', paused' if not blocking else ''}), "
              f"{len(session['hostnames'])} hosts, {len(records)} files hidden ({locked} locked), "
              f"doh={'off' if session.get('doh_backup') else 'untouched'}, "
              f"notifications={'off' if session.get('notif_backup') else 'untouched'}, "
              f"task={'ok' if ok else 'FAILED'}, enforce={'ok' if ok2 else 'FAILED'}")
    if warnings:
        state.log("apply warnings: " + " | ".join(warnings))
    _write_result(result_path, {"ok": True, "warnings": warnings, "hidden": len(records), "locked": locked,
                                "hostnames": len(session["hostnames"]), "task": ok, "enforce": ok2,
                                "paused": not blocking})
    return 0


# ---------------------------------------------------------------- extend ---
_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9:_-]{0,79}$")
_PROC_RE = re.compile(r"^[A-Za-z0-9 _.\-+()!&~]{1,80}\.exe$", re.IGNORECASE)


def validate_extension(ext, session: dict) -> dict:
    """Re-check an extension request (more hostnames, services and processes for the running session). Returns
    the cleaned lists plus the entries that were dropped and why. Raises PlanError on a malformed request."""
    if not isinstance(ext, dict):
        raise PlanError("extension is not a JSON object")
    if not isinstance(ext.get("id"), str) or ext.get("id") != session.get("id"):
        raise PlanError("extension does not name the running session")
    for key, cap in (("hostnames", MAX_HOSTNAMES), ("services", 200), ("processes", 500)):
        v = ext.get(key, [])
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            raise PlanError(f"extension field '{key}' must be a list of strings")
        if len(v) > cap:
            raise PlanError(f"extension field '{key}' has too many entries ({len(v)} > {cap})")
        ext[key] = [x[:1024] for x in v]
    hosts: list[str] = []
    dropped: list[str] = []
    for h in ext["hostnames"]:
        n = hostsfile.normalise_host(h)
        if not n:
            dropped.append(f"{h.strip()[:80]}: not a valid hostname")
        elif n in hostsfile.PROTECTED:
            dropped.append(f"{n}: protected, blocking it would break Windows or sign-in")
        elif n not in hosts:
            hosts.append(n)
    services = [k for k in ext["services"] if _KEY_RE.match(k)]
    procs: list[str] = []
    for p in ext["processes"]:
        p = p.strip()
        if not _PROC_RE.match(p):
            dropped.append(f"{p[:80]}: not a valid program name")
        elif p.lower() in aiblock.PROTECTED_PROCESSES or p.lower().startswith("deepfocus"):
            dropped.append(f"{p}: a Windows or DeepFocus process is never closed")
        elif p.lower() not in {x.lower() for x in procs}:
            procs.append(p)
    return {"hostnames": hosts, "services": services, "processes": procs, "dropped": dropped}


def extend(plan_path: str, result_path: str | None) -> int:
    """Add hostnames (and the services/processes they came from) to the running session. The hosts block is
    rewritten at once unless the session is inside a free window, in which case the enforce task applies the
    new list when the window ends. Everything else about the session (files, options, schedule) is untouched."""
    if not _state_ok(result_path):
        return 2
    paths.ensure_dirs()
    problem = _plan_path_ok(plan_path)
    if problem:
        _write_result(result_path, {"ok": False, "error": f"Refused: {problem}."})
        return 2
    with open(plan_path, "r", encoding="utf-8") as f:
        ext = json.load(f)
    session = state.load_session()
    if not state.session_is_active(session) or state.session_expired(session):
        _write_result(result_path, {"ok": False, "error": "There is no running session to add to."})
        return 2
    try:
        clean = validate_extension(ext, session)
    except PlanError as e:
        _write_result(result_path, {"ok": False, "error": f"Refused: {e}."})
        return 2
    have = {h.lower() for h in session.get("hostnames", []) if isinstance(h, str)}
    new_hosts = [h for h in clean["hostnames"] if h not in have]
    if len(have) + len(new_hosts) > MAX_HOSTNAMES:
        _write_result(result_path, {"ok": False, "error": f"The session would exceed {MAX_HOSTNAMES} hostnames."})
        return 2
    session["hostnames"] = [h for h in session.get("hostnames", []) if isinstance(h, str)] + new_hosts
    for k in clean["services"]:
        if k not in session.setdefault("services", []):
            session["services"].append(k)
    seen = {p.lower() for p in session.get("processes", []) if isinstance(p, str)}
    for p in clean["processes"]:
        if p.lower() not in seen:
            seen.add(p.lower())
            session.setdefault("processes", []).append(p)
    warnings = list(clean["dropped"])
    paused = bool(session.get("paused"))
    if new_hosts and not paused:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        try:
            shutil.copy2(paths.HOSTS_PATH, paths.BACKUP_DIR / f"hosts_{stamp}_{session['id']}_add.bak")
        except OSError as e:
            warnings.append(f"Could not back up hosts file: {e}")
        state.prune_backups()
        opts = session.get("options", {})
        text = hostsfile.read_hosts(paths.HOSTS_PATH)
        new_text = hostsfile.apply_block(text, session["hostnames"], session["id"], session["restore_at"],
                                         ipv6=bool(opts.get("ipv6", True)))
        try:
            hostsfile.write_hosts(paths.HOSTS_PATH, new_text)
        except OSError as e:
            _write_result(result_path, {"ok": False, "error": f"Could not write hosts file: {e}", "warnings": warnings})
            return 3
        flush_dns()
    session.setdefault("schedule_log", []).append(
        f"{state.now_iso()} added {len(new_hosts)} hostname(s)" + (" (applies when the free window ends)" if paused else ""))
    session["schedule_log"] = session["schedule_log"][-200:]
    state.save_session(session)
    state.log(f"extend: session {session['id']} +{len(new_hosts)} hosts -> {len(session['hostnames'])}, "
              f"+{len(clean['services'])} services, +{len(clean['processes'])} processes"
              + (", paused (applied later)" if paused else "")
              + (" | " + " | ".join(warnings) if warnings else ""))
    _write_result(result_path, {"ok": True, "added": len(new_hosts), "total": len(session["hostnames"]),
                                "paused": paused, "warnings": warnings})
    return 0


# --------------------------------------------------------------- restore ---
def restore(result_path: str | None, if_expired: bool, reason: str) -> int:
    if not _state_ok(result_path):
        return 2
    paths.ensure_dirs()
    session = state.load_session()
    if session is not None and not isinstance(session, dict):
        state.log("restore: session record is not an object, treating it as absent")
        session = None
    reason = re.sub(r"[^a-z-]", "", str(reason).lower())[:32] or "manual"
    warnings: list[str] = []

    if if_expired and state.session_is_active(session) and not state.session_expired(session):
        _write_result(result_path, {"ok": True, "skipped": True, "reason": "session still active"})
        state.log(f"restore(if-expired): skipped, session {session['id']} still active")
        return 0

    # hosts (always, whether or not we know about a session), files, DoH policies
    try:
        removed, restored = _unblock_all(session, warnings)
    except OSError as e:
        warnings.append(f"Could not write hosts file: {e}")
        _write_result(result_path, {"ok": False, "error": str(e), "warnings": warnings})
        return 3

    # scheduled tasks (both; the enforce task may be the one running us, which is fine)
    for name in (paths.TASK_NAME, paths.ENFORCE_TASK_NAME):
        if task_exists(name):
            ok, out = delete_task(name)
            if not ok:
                warnings.append(f"Could not delete scheduled task {name}: {out}")

    # archive
    if session:
        session["status"] = "ended"
        session["ended"] = state.now_iso()
        session["end_reason"] = reason
        if session.get("hidden_files"):
            session["hidden_files_restored"] = restored
        state.append_history(session)
        state.clear_session()

    state.log(f"restore({reason}): hosts lines removed={removed}, files restored={restored}, warnings={len(warnings)}"
              + (" | " + " | ".join(warnings) if warnings else ""))
    _write_result(result_path, {"ok": True, "removed_lines": removed, "files_restored": restored, "warnings": warnings})
    return 0


# --------------------------------------------------------------- enforce ---
def enforce(result_path: str | None) -> int:
    if not _state_ok(result_path):
        return 2
    paths.ensure_dirs()
    session = state.load_session()
    if session is not None and not isinstance(session, dict):
        state.log("enforce: session record is not an object, treating it as absent")
        session = None
    if not state.session_is_active(session):
        # Nothing to protect. Clean up any leftovers (including this task) and go away.
        text = hostsfile.read_hosts(paths.HOSTS_PATH)
        if hostsfile.has_block(text) or task_exists(paths.ENFORCE_TASK_NAME) or task_exists(paths.TASK_NAME):
            return restore(result_path, if_expired=False, reason="enforce-cleanup")
        state.log("enforce: idle, nothing to protect")
        _write_result(result_path, {"ok": True, "idle": True})
        return 0
    if state.session_expired(session):
        return restore(result_path, if_expired=True, reason="scheduled")

    fixes: list[str] = []
    warnings: list[str] = []
    changes: list[str] = []
    opts = session.get("options", {})
    sched = session.get("schedule")

    # weekly schedule: switch the blocks off at the start of a free window and on at its end
    blocking = schedule.is_blocking(sched)
    paused = bool(session.get("paused"))
    if blocking and paused:
        err = _block_all(session, warnings)
        if err:
            changes.append(f"free window over but the blocks could not be re-applied: {err}")
        else:
            nxt = schedule.next_change(sched)
            changes.append("free window over, blocks back on"
                           + (f" until {nxt.strftime('%a %d %b %H:%M')}" if nxt else ""))
    elif not blocking and not paused:
        try:
            _unblock_all(session, warnings)
            session["paused"] = True
            session["hidden_files"] = []
            nxt = schedule.next_change(sched)
            changes.append("free window started, blocks lifted"
                           + (f" until {nxt.strftime('%a %d %b %H:%M')}" if nxt else ""))
        except OSError as e:
            changes.append(f"free window started but the hosts block could not be removed: {e}")
    if changes:
        stamp = state.now_iso()
        session.setdefault("schedule_log", []).extend(f"{stamp} {c}" for c in changes)
        session["schedule_log"] = session["schedule_log"][-200:]
        state.save_session(session)
        state.log("enforce schedule: " + "; ".join(changes) + (f" (warnings: {warnings})" if warnings else ""))
    paused = bool(session.get("paused"))

    if not paused:
        text = hostsfile.read_hosts(paths.HOSTS_PATH)
        if not hostsfile.block_matches(text, session["hostnames"], session["id"], session["restore_at"],
                                       ipv6=bool(opts.get("ipv6", True))):
            new_text = hostsfile.apply_block(text, session["hostnames"], session["id"], session["restore_at"],
                                             ipv6=bool(opts.get("ipv6", True)))
            try:
                hostsfile.write_hosts(paths.HOSTS_PATH, new_text)
                flush_dns()
                fixes.append("hosts block re-applied")
            except OSError as e:
                fixes.append(f"hosts block could not be re-applied: {e}")

        n = filehide.rehide(_safe_records(session, warnings))
        if n:
            fixes.append(f"{n} file(s) re-hidden")

        if isinstance(session.get("doh_backup"), list) and session["doh_backup"] \
                and not doh.is_enforced(session["doh_backup"]):
            # re-write our values without touching the recorded originals
            _, errs = doh.disable()
            fixes.append("browser DoH policies re-applied" if not errs else "DoH policies could not be re-applied")

        if session.get("notif_backup") and not notif.is_blocked():
            try:
                notif.block()  # keep the recorded original, just re-apply ours
                fixes.append("Windows notifications turned back off")
            except OSError:
                fixes.append("Windows notifications could not be turned back off")

    if not task_exists(paths.TASK_NAME):
        ok, _ = register_task(state.parse_iso(session["restore_at"]))
        fixes.append("restore task re-registered" if ok else "restore task could not be re-registered")
        session["task_registered"] = ok

    if fixes:
        stamp = state.now_iso()
        session.setdefault("enforce_fixes", []).extend(f"{stamp} {f}" for f in fixes)
        session["enforce_fixes"] = session["enforce_fixes"][-200:]
        state.save_session(session)
        state.log("enforce: " + "; ".join(fixes))
    if warnings and not changes:
        state.log("enforce warnings: " + " | ".join(warnings))
    _write_result(result_path, {"ok": True, "fixes": fixes, "schedule": changes, "paused": paused})
    return 0


def status() -> int:
    session = state.load_session()
    text = hostsfile.read_hosts(paths.HOSTS_PATH)
    print(json.dumps({
        "session": session,
        "hosts_block_present": hostsfile.has_block(text),
        "task_exists": task_exists(),
        "enforce_task_exists": task_exists(paths.ENFORCE_TASK_NAME),
        "elevated": is_admin(),
    }, indent=2))
    return 0


def is_admin() -> bool:
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="deepfocus.helper")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("apply")
    a.add_argument("plan")
    a.add_argument("--result")
    r = sub.add_parser("restore")
    r.add_argument("--result")
    r.add_argument("--if-expired", action="store_true")
    r.add_argument("--reason", default="manual")
    e = sub.add_parser("enforce")
    e.add_argument("--result")
    x = sub.add_parser("extend")
    x.add_argument("plan")
    x.add_argument("--result")
    sub.add_parser("status")
    ns = ap.parse_args(argv)
    result_path = getattr(ns, "result", None)
    try:
        if ns.cmd == "apply":
            return apply(ns.plan, result_path)
        if ns.cmd == "extend":
            return extend(ns.plan, result_path)
        if ns.cmd == "restore":
            return restore(result_path, ns.if_expired, ns.reason)
        if ns.cmd == "enforce":
            return enforce(result_path)
        return status()
    except Exception as e:  # never die silently: the GUI waits on the result file
        state.log("helper crashed: " + traceback.format_exc())
        _write_result(result_path, {"ok": False, "error": f"{type(e).__name__}: {e}"})
        return 1


if __name__ == "__main__":
    sys.exit(main())
