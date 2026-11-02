"""Filesystem locations shared by the GUI and the elevated helper.

State lives in %USERPROFILE%\\.deepfocus rather than AppData on purpose: the
Microsoft Store build of Python virtualises writes under AppData, which would
make the state invisible to Task Scheduler and PowerShell.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))

STATE_DIR = Path(os.environ.get("DEEPFOCUS_HOME") or (Path.home() / ".deepfocus"))
SESSION_FILE = STATE_DIR / "session.json"
HISTORY_FILE = STATE_DIR / "history.json"
SETTINGS_FILE = STATE_DIR / "settings.json"
PRESETS_FILE = STATE_DIR / "presets.json"
AI_BLOCKS_FILE = STATE_DIR / "ai_blocks.json"
CHAT_FILE = STATE_DIR / "chat.json"
BACKUP_DIR = STATE_DIR / "hosts_backups"
RESULTS_DIR = STATE_DIR / "results"
LOG_FILE = STATE_DIR / "deepfocus.log"

HOSTS_PATH = Path(os.environ.get("DEEPFOCUS_HOSTS")
                  or Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "drivers" / "etc" / "hosts")

if FROZEN:
    PROJECT_DIR = Path(sys.executable).resolve().parent
    ASSET_DIR = Path(getattr(sys, "_MEIPASS", PROJECT_DIR)) / "assets"
else:
    PROJECT_DIR = Path(__file__).resolve().parent.parent
    ASSET_DIR = PROJECT_DIR / "assets"

ICON_FILE = ASSET_DIR / "deepfocus.ico"
TASK_NAME = "DeepFocus AI Restore"
ENFORCE_TASK_NAME = "DeepFocus AI Enforce"
APP_USER_MODEL_ID = "FalconStone.DeepFocusAI"
MUTEX_NAME = "Local\\FalconStone.DeepFocusAI.SingleInstance"
MAX_BACKUPS = 20
MAX_LOG_BYTES = 1_000_000


def ensure_dirs() -> None:
    for d in (STATE_DIR, BACKUP_DIR, RESULTS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def python_exe(windowed: bool = True) -> str:
    """Interpreter path usable from Task Scheduler and UAC (source checkout only)."""
    name = "pythonw.exe" if windowed else "python.exe"
    local = os.environ.get("LOCALAPPDATA")
    if local:
        alias = Path(local) / "Microsoft" / "WindowsApps" / name
        if alias.exists():
            return str(alias)
    exe = Path(sys.executable)
    if windowed:
        w = exe.with_name("pythonw.exe")
        if w.exists():
            return str(w)
    return str(exe)


def helper_command() -> tuple[str, list[str]]:
    """(executable, leading args) that run the elevated helper, in both packaged and source form."""
    if FROZEN:
        return str(Path(sys.executable).resolve()), ["helper"]
    return python_exe(windowed=True), ["-m", "deepfocus.helper"]
