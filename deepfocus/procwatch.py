"""Background watcher that closes blocked desktop apps while a session runs.

Runs only while the GUI is open (it needs no admin rights). The hosts block
and hidden files do not depend on it.
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

CREATE_NO_WINDOW = 0x08000000


def _sys(tool: str) -> str:
    """Absolute path of a Windows tool, so nothing is resolved through PATH or the current folder."""
    return str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / tool)

BROWSERS = ["chrome.exe", "msedge.exe", "brave.exe", "firefox.exe", "opera.exe", "opera_gx.exe", "vivaldi.exe",
            "arc.exe", "iexplore.exe"]


def running_images() -> set[str]:
    try:
        p = subprocess.run([_sys("tasklist.exe"), "/FO", "CSV", "/NH"], capture_output=True, text=True,
                           creationflags=CREATE_NO_WINDOW, timeout=10)
    except (subprocess.SubprocessError, OSError):
        return set()
    names = set()
    for line in p.stdout.splitlines():
        if line.startswith('"'):
            names.add(line.split('","')[0].strip('"').lower())
    return names


def kill(image: str) -> bool:
    try:
        p = subprocess.run([_sys("taskkill.exe"), "/F", "/IM", image, "/T"], capture_output=True, text=True,
                           creationflags=CREATE_NO_WINDOW, timeout=15)
        return p.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def kill_many(images: list[str]) -> int:
    live = running_images()
    n = 0
    for img in images:
        if img.lower() in live and kill(img):
            n += 1
    return n


class ProcessWatcher:
    def __init__(self, images: list[str], on_kill: Callable[[str], None] | None = None, interval: float = 2.0):
        self.images = {i.lower() for i in images}
        self.on_kill = on_kill
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.kills = 0

    def start(self) -> None:
        if self._thread or not self.images:
            return
        self._thread = threading.Thread(target=self._loop, name="deepfocus-procwatch", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            live = running_images()
            for img in self.images & live:
                if kill(img):
                    self.kills += 1
                    if self.on_kill:
                        self.on_kill(img)
            self._stop.wait(self.interval)
