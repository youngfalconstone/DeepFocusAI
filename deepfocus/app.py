"""DeepFocus AI desktop application (tkinter, Windows 11 styled widget kit in ui.py)."""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import threading
import tkinter as tk
import winreg
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import (APP_NAME, __version__, aiblock, catalog, credstore, elevate, guard, hostsfile, llm, notify, paths,
               procwatch, schedule, sounds, state)
from .ui import (ICON, Button, Check, Chip, Field, Hero, IconButton, ListPanel, Scroll, Segmented, Stepper, Surface,
                 TextArea, Theme, Toggle, animate, animate_synced, autowrap, blend, divider, flush_paint, fmt_clock,
                 fmt_duration, gradient_ring, gradient_shape, infobar, invalidate, label, popup_menu, row, rounded,
                 system_theme, title_block)

STRICT_PHRASE = "I am choosing to end this session"
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_CAPTION_COLOR = 35
DWMWA_SYSTEMBACKDROP_TYPE = 38
DWMSBT_MAINWINDOW = 2  # Mica

_mutex_handle = None


def desktop_dir() -> Path:
    return guard.desktop_folder() if guard.desktop_folder().exists() else Path.home() / "Desktop"


def open_folder(p: Path) -> None:
    try:
        subprocess.Popen([str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "explorer.exe"), str(p)])
    except OSError:
        pass


def style_window(win: tk.Misc, theme: Theme) -> None:
    """Windows 11 chrome: dark/light title bar, Mica backdrop where available, app icon."""
    try:
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()
        dark = ctypes.c_int(1 if theme.name == "dark" else 0)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(dark), 4)
        backdrop = ctypes.c_int(DWMSBT_MAINWINDOW)
        hr = ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_SYSTEMBACKDROP_TYPE, ctypes.byref(backdrop), 4)
        if hr != 0:  # Windows 10: paint the caption in the window colour instead
            c = theme["bg"]
            colorref = ctypes.c_int(int(c[5:7], 16) << 16 | int(c[3:5], 16) << 8 | int(c[1:3], 16))
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_CAPTION_COLOR, ctypes.byref(colorref), 4)
    except Exception:
        pass
    try:
        if paths.ICON_FILE.exists():
            win.iconbitmap(default=str(paths.ICON_FILE))
    except Exception:
        pass


def single_instance() -> bool:
    """True if this is the only running GUI. Otherwise brings the existing window forward."""
    global _mutex_handle
    try:
        k32 = ctypes.windll.kernel32
        _mutex_handle = k32.CreateMutexW(None, False, paths.MUTEX_NAME)
        if k32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            hwnd = ctypes.windll.user32.FindWindowW(None, APP_NAME)
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                ctypes.windll.user32.SetForegroundWindow(hwnd)
            return False
    except Exception:
        pass
    return True


# ======================================================================= plan
def normalise_app_input(raw: str) -> str | None:
    """What the user typed or picked, as a bare executable name: a path is reduced to its file name and a name
    without an extension gets ".exe". None if it still is not a plausible executable name."""
    s = aiblock.clean_text(raw, 200).split("\\")[-1].split("/")[-1].strip().strip('"')
    if not s:
        return None
    if "." not in s:
        s += ".exe"
    return aiblock.normalise_app(s)


def clean_apps(items) -> list[str]:
    """A stored list of executable names made safe: strings only, normalised, protected names dropped, no duplicates."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in items if isinstance(items, list) else []:
        exe = normalise_app_input(raw) if isinstance(raw, str) else None
        if not exe or aiblock.app_status(exe)[0] == "protected" or exe.lower() in seen:
            continue
        seen.add(exe.lower())
        out.append(exe)
    return out[:200]


class Plan:
    """Everything the session builder collects before a session starts."""

    def __init__(self, settings: dict):
        self.goal = ""
        self.services: dict[str, catalog.Service] = {}
        self.custom: list[str] = []
        self.removed: set[str] = set()
        self.files: list[str] = []
        # desktop apps added by hand; they carry over from the last session via settings["remembered_apps"]
        self.apps: list[str] = clean_apps(settings.get("remembered_apps", []))
        self.restore_at = datetime.now() + timedelta(minutes=int(settings.get("default_duration_min", 60)))
        self.schedule: dict = schedule.once()
        self.extended = bool(settings.get("extended_subdomains", True))
        self.options = {
            "strict": bool(settings.get("strict_default", False)),
            "end_delay_seconds": int(settings.get("end_delay_seconds", 30)),
            "pomodoro": bool(settings.get("pomodoro_default", False)),
            "pomodoro_work_min": int(settings.get("pomodoro_work_min", 25)),
            "pomodoro_break_min": int(settings.get("pomodoro_break_min", 5)),
            "close_browsers": bool(settings.get("close_browsers_default", False)),
            "process_watch": bool(settings.get("process_watch_default", True)),
            "ipv6": bool(settings.get("ipv6", True)),
            "doh_off": bool(settings.get("doh_off_default", True)),
            "lock_files": bool(settings.get("lock_files_default", False)),
            "block_notifications": bool(settings.get("block_notifications_default", False)),
        }

    def hostnames(self) -> list[str]:
        out: list[str] = []
        for s in self.services.values():
            for h in catalog.hostnames_for(s, self.extended):
                if h not in out:
                    out.append(h)
        for h in self.custom:
            if h not in out:
                out.append(h)
        return [h for h in out if h not in self.removed]

    def processes(self) -> list[str]:
        """Executables to close: the selected services' processes plus the apps added by hand."""
        out: list[str] = []
        seen: set[str] = set()
        for p in [p for s in self.services.values() for p in s.processes] + list(self.apps):
            if p.lower() not in seen:
                seen.add(p.lower())
                out.append(p)
        return out

    def add_app(self, exe: str) -> bool:
        """Add one executable name (already normalised). False if it was there already."""
        if exe.lower() in {a.lower() for a in self.apps}:
            return False
        self.apps.append(exe)
        return True

    def remove_apps(self, exes: set[str]) -> None:
        low = {e.lower() for e in exes}
        self.apps = [a for a in self.apps if a.lower() not in low]

    def is_weekly(self) -> bool:
        return schedule.is_weekly(self.schedule)

    def length_text(self) -> str:
        if self.is_weekly():
            w = int(self.schedule.get("weeks", 1))
            return f"{w} week{'s' if w != 1 else ''}, weekly schedule"
        return fmt_duration(int((self.restore_at - datetime.now()).total_seconds()))

    def to_preset(self) -> dict:
        return {"services": list(self.services), "custom": self.custom, "removed": sorted(self.removed),
                "files": self.files, "apps": self.apps, "options": self.options, "goal": self.goal,
                "schedule": self.schedule,
                "duration_min": max(1, int((self.restore_at - datetime.now()).total_seconds() // 60))}

    def load_preset(self, p: dict) -> None:
        self.services = {k: catalog.get(k) for k in p.get("services", []) if catalog.get(k)}
        self.custom = list(p.get("custom", []))
        self.removed = set(p.get("removed", []))
        self.files = [f for f in p.get("files", []) if not guard.forbidden_reason(f)]
        if "apps" in p:
            self.apps = clean_apps(p.get("apps", []))
        self.options.update(p.get("options", {}))
        self.goal = p.get("goal", self.goal)
        self.schedule = schedule.normalize(p.get("schedule"))
        if self.is_weekly():
            self.restore_at = datetime.now() + timedelta(weeks=int(self.schedule.get("weeks", 1)))
        else:
            self.restore_at = datetime.now() + timedelta(minutes=int(p.get("duration_min", 60)))


# ======================================================================== app
class App(tk.Tk):
    def __init__(self):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(paths.APP_USER_MODEL_ID)
        except Exception:
            pass
        super().__init__()
        self.withdraw()
        self.settings = state.load_settings()
        self.theme = Theme(self.settings.get("theme", "dark"), bool(self.settings.get("system_accent", False)))
        self.theme.init_fonts(self)
        self.title(APP_NAME)
        self.geometry("1180x800")
        self.minsize(1000, 680)
        self.configure(bg=self.theme["bg"])
        try:
            # DEEPFOCUS_DPI lets the test captures emulate 100 / 125 / 150 % display scaling
            dpi = int(os.environ.get("DEEPFOCUS_DPI") or ctypes.windll.user32.GetDpiForSystem())
            self.tk.call("tk", "scaling", dpi / 72.0)
        except Exception:
            pass
        self._resize_job = None
        self.bind("<Configure>", self._root_resized)
        self.watcher: procwatch.ProcessWatcher | None = None
        self.plan = Plan(self.settings)
        self.pages: dict[str, tk.Frame] = {}
        self.nav: dict[str, NavItem] = {}
        self.current: str | None = None
        self._build()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        style_window(self, self.theme)
        self.deiconify()
        for i, key in enumerate(("dashboard", "builder", "presets", "history", "settings", "help", "chat"), start=1):
            self.bind_all(f"<Control-Key-{i}>", lambda e, k=key: self.show(k))
        self.after(300, self.startup_check)
        self.after(10000, self._theme_poll)

    # ------------------------------------------------------------- layout --
    def _build(self):
        t = self.theme
        for w in self.winfo_children():
            w.destroy()
        self.configure(bg=t["bg"])
        self._style_ttk()

        self.sidebar = tk.Frame(self, bg=t["sidebar"], width=232)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        tk.Frame(self, bg=t["border"], width=1).pack(side="left", fill="y")

        brand = tk.Frame(self.sidebar, bg=t["sidebar"], padx=16, pady=22)
        brand.pack(fill="x")
        logo = tk.Canvas(brand, width=38, height=36, bg=t["sidebar"], highlightthickness=0)
        logo.pack(side="left")
        self._draw_brain(logo, t)
        bt = tk.Frame(brand, bg=t["sidebar"])
        bt.pack(side="left", padx=(10, 0))
        label(bt, t, "DeepFocus AI", "brand").pack(anchor="w")
        label(bt, t, "Version " + __version__, "tiny", "faint").pack(anchor="w")

        nav = tk.Frame(self.sidebar, bg=t["sidebar"], padx=12)
        nav.pack(fill="x", pady=(4, 0))
        for key, glyph, text in (("dashboard", ICON["play"], "Focus Session"), ("chat", ICON["chat"], "Assistant"),
                                 ("history", ICON["chart"], "Analytics"), ("settings", ICON["settings"], "Settings"),
                                 ("help", ICON["help"], "Help")):
            item = NavItem(nav, t, glyph, text, lambda k=key: self.show(k))
            item.pack(fill="x", pady=3)
            self.nav[key] = item

        self.status_var = tk.StringVar(value="Idle")
        st = Surface(self.sidebar, t, pad=10, fill=t["surface"], radius=t.ctrl_radius + 2)
        st.pack(side="bottom", fill="x", padx=14, pady=(8, 14))
        self.status_dot = tk.Label(st.body, text=ICON["dot"], bg=t["surface"], fg=t["faint"], font=t.fonts["icon_s"])
        self.status_dot.pack(side="left", padx=(2, 0))
        tk.Label(st.body, textvariable=self.status_var, bg=t["surface"], fg=t["muted"], font=t.fonts["small"],
                 wraplength=160, justify="left").pack(side="left", padx=8)

        def banner_layout(w, h):
            banner.delete("fg")
            banner.create_text(18, 22, text="Stay focused.\nAchieve more.", anchor="nw", fill=t["hero_text"],
                               font=t.fonts["h3"], tags="fg")
        banner = Hero(self.sidebar, t, height=150, seed=23, stars=28, radius=14, on_layout=banner_layout)
        banner.pack(side="bottom", fill="x", padx=14)

        self.content = tk.Frame(self, bg=t["bg"])
        self.content.pack(side="left", fill="both", expand=True)

        self.pages = {
            "dashboard": DashboardPage(self.content, self),
            "builder": BuilderPage(self.content, self),
            "presets": PresetsPage(self.content, self),
            "history": HistoryPage(self.content, self),
            "settings": SettingsPage(self.content, self),
            "help": HelpPage(self.content, self),
            "chat": ChatPage(self.content, self),
        }
        self.current = None
        self.show("dashboard", animated=False)

    @staticmethod
    def _draw_brain(c: tk.Canvas, t: Theme):
        """Two-lobed brain mark in the accent gradient (drawn, not an image)."""
        g1, g2 = t["grad1"], t["grad2"]
        mid = blend(g1, g2, 0.5)
        # lobes
        c.create_oval(3, 6, 21, 30, fill=g1, outline=g1)
        c.create_oval(17, 6, 35, 30, fill=g2, outline=g2)
        c.create_oval(9, 3, 29, 20, fill=mid, outline=mid)
        c.create_oval(8, 15, 30, 31, fill=mid, outline=mid)
        # stem
        c.create_polygon(16, 29, 22, 29, 20, 35, 18, 35, fill=g1, outline=g1)
        # folds
        dark = blend(g1, "#000000", 0.45)
        c.create_line(19, 6, 19, 30, fill=dark, width=1.6)
        c.create_line(8, 13, 13, 16, 10, 22, fill=dark, width=1.4, smooth=True)
        c.create_line(30, 13, 25, 16, 28, 22, fill=dark, width=1.4, smooth=True)
        c.create_line(12, 24, 16, 22, fill=dark, width=1.2)
        c.create_line(26, 24, 22, 22, fill=dark, width=1.2)

    def _style_ttk(self):
        t = self.theme
        s = ttk.Style(self)
        try:
            s.theme_use("clam")
        except tk.TclError:
            pass
        s.configure("Treeview", background=t["surface2"], fieldbackground=t["surface2"], foreground=t["text"],
                    bordercolor=t["surface2"], lightcolor=t["surface2"], darkcolor=t["surface2"], rowheight=30,
                    font=t.fonts["body"], relief="flat")
        s.configure("Treeview.Heading", background=t["surface2"], foreground=t["muted"], relief="flat",
                    font=t.fonts["small"], padding=6)
        s.map("Treeview", background=[("selected", t["sel"])], foreground=[("selected", t["text"])])
        s.map("Treeview.Heading", background=[("active", t["surface3"])])

    def _root_resized(self, e):
        """A resize relayouts (moves) hundreds of child windows; repaint them all once it settles."""
        if e.widget is not self:
            return
        if self._resize_job:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(40, lambda: (setattr(self, "_resize_job", None), invalidate(self)))

    def show(self, key: str, animated: bool = True):
        if key == self.current:
            return
        old = self.pages.get(self.current) if self.current else None
        self.current = key
        page = self.pages[key]
        nav_key = key if key in self.nav else "dashboard"  # builder and presets belong to Focus Session
        for k, item in self.nav.items():
            item.set_active(k == nav_key)
        if hasattr(page, "refresh"):
            page.refresh()
        if old is not None:
            old.place_forget()
        if animated:  # WinUI entrance transition: slide up into place
            page.place(x=0, y=24, relwidth=1, relheight=1)

            def step(t):
                if not page.winfo_exists():
                    return
                page.place_configure(y=int(24 * (1 - t)))
                # the page moved up: content entering at the bottom may have been copied unpainted
                w, h = page.winfo_width(), page.winfo_height()
                invalidate(page, (0, max(0, h - 72), w, h))
                if t >= 1.0:
                    invalidate(page)
                flush_paint(page)
            animate_synced(self, 200, step)
        else:
            page.place(x=0, y=0, relwidth=1, relheight=1)

    def set_status(self, text: str, kind: str = "idle"):
        self.status_var.set(text)
        color = {"idle": "faint", "active": "success", "warn": "warn", "error": "danger"}.get(kind, "faint")
        self.status_dot.configure(fg=self.theme[color])

    def rebuild_theme(self, keep: str | None = None):
        keep = keep or self.current or "dashboard"
        self.theme = Theme(self.settings.get("theme", "dark"), bool(self.settings.get("system_accent", False)))
        self.theme.init_fonts(self)
        self._build()
        style_window(self, self.theme)
        self.show(keep, animated=False)

    def apply_theme(self, mode: str):
        if mode == self.settings.get("theme"):
            return
        self.settings["theme"] = mode
        state.save_settings(self.settings)
        self.rebuild_theme("settings")

    def _theme_poll(self):
        if self.settings.get("theme", "system") == "system" and system_theme() != self.theme.name:
            self.rebuild_theme()
        self.after(10000, self._theme_poll)

    def toast(self, title: str, body: str):
        if self.settings.get("notifications", True):
            notify.toast(title, body)

    def llm_config(self) -> llm.Config:
        """Provider settings from settings.json plus the key from the Windows Credential Manager."""
        provider = self.settings.get("ai_provider", "anthropic")
        if provider not in llm.PROVIDERS:
            provider = "anthropic"
        try:
            key = credstore.load(provider) or ""
        except Exception:
            key = ""
        return llm.Config(provider=provider, model=str(self.settings.get("ai_model", "")), api_key=key,
                          base_url=str(self.settings.get("ai_base_url", "")))

    def chat_context(self) -> str:
        """What the assistant may know about the current state: session, schedule phase, stats, saved lists.
        No file paths, hostnames or personal data beyond the goal text the user typed."""
        now = datetime.now()
        lines = [f"Now: {now:%A %d %B %Y, %H:%M}."]
        s = state.load_session()
        if state.session_is_active(s):
            names = [aiblock.clean_text(catalog.get(k).name, 60) for k in s.get("services", [])[:20] if catalog.get(k)]
            what = ", ".join(names) if names else f"{len(s.get('hostnames', []))} hostnames"
            ends = state.parse_iso(s["restore_at"])
            goal = aiblock.clean_text(s.get("goal") or "", 200) or "none set"
            line = (f"A focus session is active. Goal: {goal}. Blocking: {what}. "
                    f"Time left: {fmt_duration(state.seconds_remaining(s))} (ends {ends:%a %d %b %H:%M}).")
            if schedule.is_weekly(s.get("schedule")):
                line += " Weekly schedule: " + schedule.phase_text(s.get("schedule"))
            if s.get("paused"):
                line += " Currently in a free window, blocks are lifted."
            lines.append(line)
            lines.append(f"{len(s.get('hidden_files', []))} files hidden, {s.get('kills', 0)} distracting apps closed, "
                         f"{len(s.get('enforce_fixes', []))} tamper attempts undone.")
        else:
            lines.append("No focus session is active right now.")
        st = state.stats(state.load_history())
        lines.append(f"Focus time today: {fmt_duration(st['today_seconds'])}. Total: {fmt_duration(st['total_seconds'])}. "
                     f"Sessions completed: {st['completed']}, ended early: {st['early']}. Day streak: {st['streak_days']}.")
        ai = state.load_ai_blocks()
        if ai:
            names = sorted(aiblock.clean_text(r.get("name", k), 60) for k, r in list(ai.items())[:12])
            lines.append("Saved AI block lists: " + ", ".join(n for n in names if n) + ".")
        lines.append(f"Default session length: {self.settings.get('default_duration_min', 60)} minutes. "
                     f"Built-in presets: {', '.join(catalog.PRESETS)}.")
        return "\n".join(lines)

    # ---------------------------------------------------------- elevation --
    def run_elevated(self, args: list[str], on_done, busy_text: str = "Waiting for administrator approval"):
        dlg = BusyDialog(self, busy_text)

        def work():
            try:
                res = elevate.run_helper(args)
            except elevate.ElevationCancelled as e:
                res = {"ok": False, "cancelled": True, "error": str(e)}
            except Exception as e:  # noqa: BLE001
                res = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            self.after(0, lambda: (dlg.close(), on_done(res)))

        threading.Thread(target=work, daemon=True).start()

    def restore_now(self, reason: str, on_done=None):
        def done(res):
            if res.get("ok"):
                self.stop_watcher()
                self.set_status("Everything restored", "idle")
                self.toast(APP_NAME, "Blocks removed and files restored.")
            elif not res.get("cancelled"):
                messagebox.showerror(APP_NAME, "Restore failed:\n" + res.get("error", "unknown error"), parent=self)
            self.pages["dashboard"].refresh()
            if self.current == "settings":
                self.pages["settings"].refresh()
            if on_done:
                on_done(res)

        self.run_elevated(["restore", "--reason", reason], done, "Restoring hosts file and files")

    # ------------------------------------------------------------ startup --
    def startup_check(self):
        """Runs 300 ms after start. The question boxes are modal, so the window may be closed while one is
        open; every step after a box re-checks that the window still exists."""
        if not self.winfo_exists():
            return
        session = state.load_session()
        if state.session_is_active(session):
            if state.session_expired(session):
                self.set_status("Previous session overdue", "warn")
                if messagebox.askyesno(APP_NAME,
                                       f"A focus session should have ended at {session['restore_at'].replace('T', ' ')} "
                                       "but its blocks are still recorded.\n\nRestore the hosts file and hidden files now? "
                                       "(Administrator prompt)", parent=self) and self.winfo_exists():
                    self.restore_now("startup")
            else:
                self.resume_watcher(session)
                self.set_status("Session active", "active")
        else:
            text = hostsfile.read_hosts(paths.HOSTS_PATH)
            if hostsfile.has_block(text):
                if messagebox.askyesno(APP_NAME, "The hosts file still contains a DeepFocus block from a session "
                                       "that is no longer tracked.\n\nRemove it now? (Administrator prompt)",
                                       parent=self) and self.winfo_exists():
                    self.restore_now("orphan-cleanup")
        if self.winfo_exists():
            self.pages["dashboard"].refresh()

    # ------------------------------------------------------------ watcher --
    def resume_watcher(self, session: dict):
        self.stop_watcher()
        procs = session.get("processes", [])
        if session.get("paused"):
            return  # free window of a weekly schedule: nothing is blocked, so nothing is closed either
        if procs and session.get("options", {}).get("process_watch", True):
            def on_kill(img):
                s = state.load_session()
                if s and s.get("id") == session.get("id"):
                    s["kills"] = int(s.get("kills", 0)) + 1
                    state.save_session(s)
                    self.after(0, lambda: self.set_status(f"Closed {img}", "active"))
            self.watcher = procwatch.ProcessWatcher(procs, on_kill)
            self.watcher.start()

    def stop_watcher(self):
        if self.watcher:
            self.watcher.stop()
            self.watcher = None

    # -------------------------------------------------------------- close --
    def on_close(self):
        session = state.load_session()
        if state.session_is_active(session):
            weekly = ("  - The weekly schedule keeps switching the blocks off and on at your free windows.\n"
                      if schedule.is_weekly(session.get("schedule")) else "")
            msg = ("Your focus session keeps running after the window closes:\n\n"
                   "  - Blocked domains stay blocked and are re-applied every minute.\n"
                   "  - Hidden files stay hidden.\n" + weekly +
                   f"  - Everything is restored automatically at {session['restore_at'].replace('T', ' ')} "
                   "by the scheduled tasks, even after a reboot.\n\n"
                   "Only the closing of distracting desktop apps pauses while the window is closed.\n\nClose the window?")
            if not messagebox.askyesno(APP_NAME, msg, parent=self):
                return
        self.stop_watcher()
        self.destroy()


class NavItem(tk.Canvas):
    """Sidebar item: 44 px, rounded, filled with a violet tint and a left indicator pill when selected."""

    def __init__(self, parent, theme: Theme, glyph: str, text: str, command):
        super().__init__(parent, height=44, bg=parent["bg"], highlightthickness=0, bd=0, cursor="hand2")
        self.theme, self.command, self.glyph, self.text = theme, command, glyph, text
        self.active = False
        self._hover = 0.0
        self._tok: list = []
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Enter>", lambda e: self._to(1.0))
        self.bind("<Leave>", lambda e: self._to(0.0))
        self.bind("<Button-1>", lambda e: self.command())

    def set_active(self, on: bool):
        self.active = on
        self._draw()

    def _to(self, target):
        start = self._hover

        def step(t):
            self._hover = start + (target - start) * t
            self._draw()
        animate(self, 120, step, token=self._tok)

    def _draw(self):
        self.delete("all")
        t = self.theme
        w, h = self.winfo_width(), 44
        base = t["sidebar"]
        if self.active:
            fill = blend(base, t["accent"], 0.28)
            outline = blend(base, t["accent"], 0.45)
        else:
            fill = blend(base, t["text"], 0.05 * self._hover)
            outline = fill
        if self.active or self._hover > 0.01:
            rounded(self, 0.5, 0.5, w - 0.5, h - 0.5, 10, fill=fill, outline=outline)
        if self.active:
            rounded(self, 0, 13, 3, 31, 1.5, fill=t["grad2"], outline=t["grad2"])
        fg = t["text"] if (self.active or self._hover > 0.5) else t["muted"]
        ic = t["grad2"] if self.active else blend(t["muted"], t["text"], self._hover)
        self.create_text(18, h / 2, text=self.glyph, anchor="w", font=t.fonts["icon"], fill=ic)
        self.create_text(50, h / 2, text=self.text, anchor="w", font=t.fonts["bold" if self.active else "body"], fill=fg)


# ===================================================================== dialogs
class BusyDialog(tk.Toplevel):
    def __init__(self, app: App, text: str):
        super().__init__(app, bg=app.theme["bg"])
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        s = Surface(self, app.theme, pad=24, outline=app.theme["stroke"])
        s.pack()
        label(s.body, app.theme, text, "h3").pack()
        label(s.body, app.theme, "Windows is asking for permission in a separate prompt.", "small", "muted").pack(pady=(6, 0))
        self.update_idletasks()
        x = app.winfo_rootx() + (app.winfo_width() - self.winfo_reqwidth()) // 2
        y = app.winfo_rooty() + (app.winfo_height() - self.winfo_reqheight()) // 2
        self.geometry(f"+{x}+{y}")
        self.grab_set()

    def close(self):
        try:
            self.grab_release()
            self.destroy()
        except tk.TclError:
            pass


class ModalDialog(tk.Toplevel):
    """ContentDialog: content area plus a tinted footer with equal-width buttons."""

    def __init__(self, app: App, title: str, width: int = 640, height: int = 560):
        super().__init__(app, bg=app.theme["bg"])
        self.app = app
        self.theme = app.theme
        self.title(title)
        self.transient(app)
        self.resizable(False, False)
        x = app.winfo_rootx() + (app.winfo_width() - width) // 2
        y = app.winfo_rooty() + (app.winfo_height() - height) // 2
        self.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")
        self.footer = tk.Frame(self, bg=self.theme["surface"], padx=24, pady=20)
        self.footer.pack(side="bottom", fill="x")
        tk.Frame(self, bg=self.theme["border"], height=1).pack(side="bottom", fill="x")
        self.body = tk.Frame(self, bg=self.theme["bg"], padx=24, pady=22)
        self.body.pack(fill="both", expand=True)
        style_window(self, self.theme)
        self.grab_set()
        self.bind("<Escape>", lambda e: self.destroy())

    def buttons(self, items: list[tuple[str, object, str]]) -> list[Button]:
        """items: (text, command, kind). Laid out WinUI-style: equal widths filling the footer."""
        out = []
        for i, (text, cmd, kind) in enumerate(items):
            self.footer.columnconfigure(i, weight=1, uniform="btn")
            b = Button(self.footer, self.theme, text, cmd, kind=kind, pady=7)
            b.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 8, 0))
            b.bind("<Configure>", lambda e, b=b: b.resize_to(e.width))
            out.append(b)
        return out


class ReviewDialog(ModalDialog):
    """Shows exactly what will change and why admin rights are needed, then starts."""

    def __init__(self, app: App, plan: Plan, on_start):
        extra = (70 if plan.is_weekly() else 0) + (60 if plan.restore_at - datetime.now() > timedelta(days=2) else 0)
        super().__init__(app, "Review and start", 760, 700 + extra)
        t = self.theme
        b = self.body
        label(b, t, "Review your session", "h1").pack(anchor="w")
        label(b, t, "Nothing has changed yet. This is exactly what happens when you press Start.", "small", "muted")\
            .pack(anchor="w", pady=(0, 14))

        hosts = plan.hostnames()
        procs = plan.processes() if plan.options["process_watch"] else []
        ends = plan.restore_at.strftime("%a %d %b at %H:%M")
        o = plan.options

        grid = Surface(b, t, pad=16)
        grid.pack(fill="x")
        g = grid.body
        g.columnconfigure(1, weight=1)
        mode = ["Strict" if o["strict"] else "Normal", f"end-early delay {o['end_delay_seconds']} s"]
        if o["pomodoro"]:
            mode.append("Pomodoro")
        if o["close_browsers"]:
            mode.append("browsers closed at start")
        if o.get("lock_files"):
            mode.append("files locked")
        if o.get("doh_off", True):
            mode.append("browser DoH off")
        if o.get("block_notifications"):
            mode.append("Windows notifications off")
        rows = [("Goal", plan.goal or "(none)")]
        if plan.is_weekly():
            now_free = not schedule.is_blocking(plan.schedule)
            nxt = schedule.next_change(plan.schedule)
            rows.append(("Schedule", schedule.describe(plan.schedule) + f" Repeats for {plan.length_text().split(',')[0]}."))
            rows.append(("Right now", ("A free window: nothing is blocked until " if now_free else "Blocking until ")
                         + (nxt.strftime("%A %H:%M") if nxt else "the end") + ". Switches happen within a minute."))
            rows.append(("Ends for good", f"{ends}   ({fmt_duration(int((plan.restore_at - datetime.now()).total_seconds()))} from now)"))
        else:
            rows.append(("Ends", f"{ends}   ({fmt_duration(int((plan.restore_at - datetime.now()).total_seconds()))} from now)"))
        rows += [
            ("Blocking", f"{len(hosts)} hostnames from {len(plan.services)} services"
                         + (f" and {len(plan.custom)} custom entries" if plan.custom else "")),
            ("Closing apps", ", ".join(procs) if procs else "none"),
            ("Hiding", f"{len(plan.files)} file(s) or folder(s)"),
            ("Mode", ", ".join(mode)),
        ]
        for i, (k, v) in enumerate(rows):
            label(g, t, k, "small", "muted").grid(row=i, column=0, sticky="nw", padx=(0, 16), pady=3)
            label(g, t, v, "body", wraplength=560, justify="left").grid(row=i, column=1, sticky="w", pady=3)

        lists = tk.Frame(b, bg=t["bg"])
        lists.pack(fill="both", expand=True, pady=(12, 0))
        lists.columnconfigure(0, weight=1)
        lists.columnconfigure(1, weight=1)
        label(lists, t, "Hostnames", "small", "muted").grid(row=0, column=0, sticky="w", pady=(0, 4))
        label(lists, t, "Files to hide", "small", "muted").grid(row=0, column=1, sticky="w", padx=(10, 0), pady=(0, 4))
        l1 = ListPanel(lists, t, height=6, selectmode="browse")
        l2 = ListPanel(lists, t, height=6, selectmode="browse")
        l1.grid(row=1, column=0, sticky="nsew")
        l2.grid(row=1, column=1, sticky="nsew", padx=(10, 0))
        for h in hosts:
            l1.list.insert("end", h)
        for f in plan.files:
            l2.list.insert("end", f)

        needs = [f"Write the block to the hosts file. A backup is saved first in {paths.BACKUP_DIR}.",
                 f"Register the scheduled task \"{paths.TASK_NAME}\" for {ends}. It restores everything even if this "
                 "app is closed, crashes, or the PC reboots.",
                 f"Register \"{paths.ENFORCE_TASK_NAME}\", which re-applies the block every minute if it is removed by "
                 "hand and acts as a second restore."]
        if o.get("doh_off", True):
            needs.append("Set the Chrome, Edge, Brave and Firefox policies that turn off DNS-over-HTTPS, so browsers "
                         "cannot bypass the hosts file. The previous values are recorded and put back at restore.")
        if o.get("lock_files") and plan.files:
            needs.append("Add one read-deny entry to each hidden file's permissions so it cannot be opened. "
                         "The entry is removed at restore; nothing else changes.")
        adm = Surface(b, t, pad=14, fill=t["accent_soft"], outline=blend(t["accent_soft"], t["accent"], 0.3))
        adm.pack(fill="x", pady=(14, 0))
        hdr = tk.Frame(adm.body, bg=t["accent_soft"])
        hdr.pack(fill="x")
        tk.Label(hdr, text=ICON["shield"], bg=t["accent_soft"], fg=t["accent"], font=t.fonts["icon"]).pack(side="left")
        label(hdr, t, f"Administrator access is needed for {len(needs)} things (one UAC prompt)", "h3")\
            .pack(side="left", padx=(8, 0))
        label(adm.body, t, "\n".join(f"{i}.  {n}" for i, n in enumerate(needs, 1)),
              "small", "muted", wraplength=680, justify="left").pack(anchor="w", pady=(6, 0))
        if plan.restore_at - datetime.now() > timedelta(days=2):
            infobar(b, t, f"This is a long block: it stays on across reboots and sign-ins until {ends}. Ending early "
                    f"needs the {o['end_delay_seconds']} s cooling-off delay"
                    + (" and the strict-mode phrase." if o["strict"] else "."), "warning", wrap=660)\
                .pack(fill="x", pady=(10, 0))
        if o.get("doh_off", True) and not o.get("close_browsers"):
            infobar(b, t, "Browsers pick the DoH policy up when they restart. Turn on \"Close all browsers when the "
                    "session starts\" to make it immediate.", "info", wrap=660).pack(fill="x", pady=(10, 0))

        self.buttons([("Cancel", self.destroy, "standard"),
                      ("Start session", lambda: (self.destroy(), on_start()), "primary")])


class EndEarlyDialog(ModalDialog):
    def __init__(self, app: App, session: dict, on_confirm):
        strict = bool(session.get("options", {}).get("strict"))
        super().__init__(app, "End session early", 560, 400 if strict else 320)
        t = self.theme
        b = self.body
        self.on_confirm = on_confirm
        self.remaining = int(session.get("options", {}).get("end_delay_seconds", 30))
        label(b, t, "End this session early?", "h1").pack(anchor="w")
        goal = session.get("goal") or "your goal"
        left = ("Schedule left" if schedule.is_weekly(session.get("schedule")) else "Time left")
        label(b, t, f"You set out to work on: {goal}\n{left}: {fmt_duration(state.seconds_remaining(session))}. "
              "Ending now is recorded as an early stop.", "body", "muted", wraplength=500, justify="left")\
            .pack(anchor="w", pady=(8, 12))
        self.phrase_ok = not strict
        if strict:
            label(b, t, f"Strict mode. Type exactly:  {STRICT_PHRASE}", "small", "warn", wraplength=500,
                  justify="left").pack(anchor="w")
            self.var = tk.StringVar()
            e = Field(b, t, textvariable=self.var)
            e.pack(fill="x", pady=(6, 10))
            e.focus()
            self.var.trace_add("write", lambda *_: self._check())
        self.countdown = label(b, t, "", "body", "muted")
        self.countdown.pack(anchor="w")
        self.end_btn, _ = self.buttons([("End early", self._confirm, "danger_soft"), ("Keep going", self.destroy, "primary")])
        self.end_btn.set_enabled(False)
        self._tick()

    def _check(self):
        self.phrase_ok = self.var.get().strip() == STRICT_PHRASE
        self._update_btn()

    def _update_btn(self):
        self.end_btn.set_enabled(self.remaining <= 0 and self.phrase_ok)

    def _tick(self):
        if not self.winfo_exists():
            return
        if self.remaining > 0:
            self.countdown.configure(text=f"Take a breath. The button unlocks in {self.remaining} s.")
            self.remaining -= 1
            self.after(1000, self._tick)
        else:
            self.countdown.configure(text="You can end the session now." if self.phrase_ok
                                     else "Type the phrase above to unlock the button.")
        self._update_btn()

    def _confirm(self):
        self.destroy()
        self.on_confirm()


def simple_prompt(app: App, title: str, prompt: str, initial: str = "", ok_text: str = "Save") -> str | None:
    d = ModalDialog(app, title, 520, 230)
    t = app.theme
    label(d.body, t, prompt, "h3").pack(anchor="w")
    var = tk.StringVar(value=initial)
    e = Field(d.body, t, textvariable=var)
    e.pack(fill="x", pady=(10, 0))
    e.focus()
    if initial:
        e.entry.selection_range(0, "end")
    out = {"v": None}

    def ok():
        out["v"] = var.get().strip() or None
        d.destroy()

    e.bind_return(ok)
    d.buttons([("Cancel", d.destroy, "standard"), (ok_text, ok, "primary")])
    app.wait_window(d)
    return out["v"]


def validate_app(app: App, raw: str, interactive: bool = True) -> str | None:
    """User input for an app to close, as a normalised exe name, or None when it is refused. With `interactive`
    the refusal (invalid name, Windows process) is explained in a box and browsers ask for confirmation."""
    exe = normalise_app_input(raw)
    if not exe:
        if interactive:
            messagebox.showwarning(APP_NAME, f"\"{aiblock.clean_text(raw, 80)}\" is not a valid program name. Use the "
                                   "executable name, for example Spotify.exe.", parent=app)
        return None
    status, why = aiblock.app_status(exe)
    if status == "protected":
        if interactive:
            messagebox.showwarning(APP_NAME, f"{exe} will not be added. {why}", parent=app)
        return None
    if status == "broad" and interactive and not messagebox.askyesno(
            APP_NAME, f"{exe} is a web browser. Closing it every 2 seconds affects everything you do online, "
            "not just one service. Add it anyway?", parent=app):
        return None
    return exe


def running_apps_menu(app: App, anchor, exclude: list[str], on_pick) -> None:
    """Popup of the programs running right now (system processes, browsers and `exclude` left out)."""
    have = {p.lower() for p in exclude}
    names = sorted({n for n in procwatch.running_images()
                    if n.endswith(".exe") and n not in have and aiblock.app_status(n)[0] == "ok"})
    items = [(n, (lambda n=n: on_pick(n))) for n in names[:40]]
    if not items:
        items = [("No other programs are running", None)]
    popup_menu(anchor, app.theme, items, anchor.winfo_rootx(), anchor.winfo_rooty() + anchor.winfo_height() + 4)


class AppsDialog(ModalDialog):
    """Edit the apps that are closed during a session: the ones added by hand can be added and removed, the ones
    that come with a blocked service are shown but fixed. `on_save` receives the new hand-added list."""

    def __init__(self, app: App, own: list[str], service_apps: list[str], on_save):
        super().__init__(app, "Apps to close", 640, 520)
        t = self.theme
        self.own = list(own)
        self.service_apps = list(service_apps)
        self.on_save = on_save
        b = self.body
        label(b, t, "Apps to close", "h1").pack(anchor="w")
        label(b, t, "Closed every 2 seconds while the DeepFocus window is open. Apps you add here are remembered "
              "for your next session.", "small", "muted", wraplength=580, justify="left").pack(anchor="w", pady=(0, 12))
        r = row(b)
        self.add = Field(r, t, placeholder="App name or exe, e.g. Spotify or Spotify.exe")
        self.add.pack(side="left", fill="x", expand=True)
        self.add.bind_return(self.add_name)
        Button(r, t, "Add", self.add_name, kind="standard", padx=14).pack(side="left", padx=(8, 0))
        self.btn_running = Button(r, t, "Running now", self.running, kind="standard", padx=10, icon=ICON["list"])
        self.btn_running.pack(side="left", padx=(8, 0))
        Button(r, t, "Browse", self.browse, kind="standard", padx=10, icon=ICON["folder"]).pack(side="left", padx=(8, 0))
        hr = row(b, pady=(12, 4))
        self.count = label(hr, t, "", "small", "muted")
        self.count.pack(side="left")
        Button(hr, t, "Remove selected", self.remove, kind="subtle", font="small", padx=10, pady=4).pack(side="right")
        lp = ListPanel(b, t, height=8)
        lp.pack(fill="both", expand=True)
        self.list = lp.list
        self.buttons([("Cancel", self.destroy, "standard"), ("Save", self.save, "primary")])
        self.add.focus()
        self.refresh()

    def all_apps(self) -> list[str]:
        seen = {a.lower() for a in self.own}
        return self.own + [p for p in self.service_apps if p.lower() not in seen]

    def refresh(self):
        self.list.delete(0, "end")
        for a in self.own:
            self.list.insert("end", a)
        low = {a.lower() for a in self.own}
        for p in self.service_apps:
            if p.lower() not in low:
                self.list.insert("end", f"{p}   (from a blocked service)")
        extra = f", {len(self.own)} added by you" if self.own else ""
        self.count.configure(text=f"Apps to close ({len(self.all_apps())}{extra})")

    def _add(self, raw: str, interactive: bool = True) -> bool:
        exe = validate_app(self.app, raw, interactive)
        if not exe or exe.lower() in {a.lower() for a in self.all_apps()}:
            return False
        self.own.append(exe)
        self.refresh()
        return True

    def add_name(self):
        raw = self.add.value().strip()
        if raw and (self._add(raw) or normalise_app_input(raw)):
            self.add.clear()

    def running(self):
        running_apps_menu(self.app, self.btn_running, self.all_apps(), self._add)

    def browse(self):
        p = filedialog.askopenfilename(parent=self, title="Program to close during the session",
                                       filetypes=[("Programs", "*.exe"), ("All files", "*.*")])
        if p:
            self._add(p)

    def remove(self):
        sel = {self.list.get(i).split("   (")[0] for i in self.list.curselection()}
        own = {a for a in self.own if a in sel}
        if len(sel) > len(own):
            messagebox.showinfo(APP_NAME, "Apps that come from a blocked service stay until the session ends.",
                                parent=self)
        self.own = [a for a in self.own if a not in own]
        self.refresh()

    def save(self):
        self.on_save(list(self.own))
        self.destroy()


class AddBlockDialog(ModalDialog):
    """Add services or hostnames to the running session. Builds the same kind of list as the session builder,
    shows exactly what is new, then hands it to the elevated helper's `extend` command (one UAC prompt)."""

    def __init__(self, app: App, session: dict, on_done=None):
        super().__init__(app, "Add to block", 720, 640)
        t = self.theme
        self.session = session
        self.on_done = on_done
        self.extended = bool(app.settings.get("extended_subdomains", True))
        self.services: dict[str, catalog.Service] = {}
        self.custom: list[str] = []
        self.have = {h.lower() for h in session.get("hostnames", []) if isinstance(h, str)}
        b = self.body
        label(b, t, "Add to block", "h1").pack(anchor="w")
        label(b, t, "Added to the running session for the rest of it. Windows asks once for administrator approval "
              "when you press Add and apply.", "small", "muted", wraplength=660, justify="left").pack(anchor="w", pady=(0, 12))
        r = row(b)
        self.search = Field(r, t, placeholder="YouTube, Discord, reddit.com ...", icon=ICON["search"])
        self.search.pack(side="left", fill="x", expand=True)
        self.search.bind_return(self.find)
        Button(r, t, "Find", self.find, kind="primary", padx=16).pack(side="left", padx=(8, 0))
        self.results = tk.Frame(b, bg=t["bg"])
        self.results.pack(fill="x")
        ar = row(b, pady=(10, 0))
        self.add_host = Field(ar, t, placeholder="Or add a hostname, e.g. news.example.com")
        self.add_host.pack(side="left", fill="x", expand=True)
        self.add_host.bind_return(self.add_custom_host)
        Button(ar, t, "Add", self.add_custom_host, kind="standard", padx=16).pack(side="left", padx=(8, 0))
        label(b, t, "Selected", "small", "muted").pack(anchor="w", pady=(12, 4))
        self.chips = tk.Frame(b, bg=t["bg"])
        self.chips.pack(fill="x")
        hr = row(b, pady=(10, 4))
        self.count = label(hr, t, "", "small", "muted")
        self.count.pack(side="left")
        lp = ListPanel(b, t, height=6, selectmode="browse")
        lp.pack(fill="both", expand=True)
        self.list = lp.list
        self.btn_cancel, self.btn_apply = self.buttons([("Cancel", self.destroy, "standard"),
                                                        ("Add and apply", self.apply, "primary")])
        self.search.focus()
        self.refresh()

    # ---- building the list ----
    def new_hostnames(self) -> list[str]:
        out: list[str] = []
        for s in self.services.values():
            for h in catalog.hostnames_for(s, self.extended):
                if h.lower() not in self.have and h not in out:
                    out.append(h)
        for h in self.custom:
            if h.lower() not in self.have and h not in out:
                out.append(h)
        return out

    def already(self) -> int:
        n = 0
        for s in self.services.values():
            n += sum(1 for h in catalog.hostnames_for(s, self.extended) if h.lower() in self.have)
        return n + sum(1 for h in self.custom if h.lower() in self.have)

    def payload(self) -> dict:
        """What goes to the helper: new hostnames plus the services and their processes."""
        procs: list[str] = []
        for s in self.services.values():
            for p in s.processes:
                if p.lower() not in {x.lower() for x in procs}:
                    procs.append(p)
        return {"id": self.session.get("id"), "hostnames": self.new_hostnames(),
                "services": [k for k in self.services if k not in self.session.get("services", [])],
                "processes": procs}

    def refresh(self):
        t = self.theme
        for w in self.chips.winfo_children():
            w.destroy()
        if not self.services and not self.custom:
            label(self.chips, t, "Nothing selected yet.", "small", "faint").pack(anchor="w")
        for key, s in list(self.services.items()):
            Chip(self.chips, t, s.name, on_remove=lambda k=key: self.remove_service(k)).pack(side="left", padx=(0, 6), pady=2)
        if self.custom:
            Chip(self.chips, t, f"Custom  {len(self.custom)}", on_remove=self.clear_custom, accent=True)\
                .pack(side="left", padx=(0, 6), pady=2)
        hosts = self.new_hostnames()
        self.list.delete(0, "end")
        for h in hosts:
            self.list.insert("end", h)
        skipped = self.already()
        self.count.configure(text=f"New hostnames to block ({len(hosts)})"
                             + (f", {skipped} already blocked by this session" if skipped else ""))
        self.btn_apply.set_enabled(bool(hosts) or any(k not in self.session.get("services", []) for k in self.services))

    def _clear_results(self):
        for w in self.results.winfo_children():
            w.destroy()

    def find(self):
        t = self.theme
        q = self.search.value().strip()
        self._clear_results()
        if not q:
            return
        hits = catalog.search(q)[:5]
        box = Surface(self.results, t, pad=12, fill=t["surface2"], outline=t["stroke"], radius=t.ctrl_radius)
        box.pack(fill="x", pady=(8, 0))
        bb = box.body
        for s in hits:
            r = row(bb, pady=(0, 4))
            Button(r, t, "Add", lambda s=s: self.add_service(s), kind="primary", font="small", padx=12, pady=3)\
                .pack(side="left")
            label(r, t, s.name, "bold").pack(side="left", padx=(10, 8))
            label(r, t, f"{s.category}, {len(catalog.hostnames_for(s, self.extended))} hostnames", "small", "muted")\
                .pack(side="left")
        generic = catalog.generic_hostnames(q, self.extended)
        if generic:
            r = row(bb, pady=(6 if hits else 0, 0))
            Button(r, t, "Add", lambda: self.add_generic(generic), kind="standard", font="small", padx=12, pady=3)\
                .pack(side="left")
            label(r, t, f"Block \"{aiblock.clean_text(q, 60)}\" as a website", "bold").pack(side="left", padx=(10, 8))
            label(r, t, f"{len(generic)} guessed hostnames", "small", "muted").pack(side="left")
        if not hits and not generic:
            label(bb, t, "No match. Try another name, or add a hostname below.", "small", "muted").pack(anchor="w")
        Button(bb, t, "Close", self._clear_results, kind="link", font="small", padx=0, pady=2).pack(anchor="e")

    def add_service(self, s: catalog.Service):
        self.services[s.key] = s
        self.search.clear()
        self._clear_results()
        self.refresh()

    def add_generic(self, hosts: list[str]):
        for h in hosts:
            if h not in self.custom:
                self.custom.append(h)
        self.search.clear()
        self._clear_results()
        self.refresh()

    def remove_service(self, key: str):
        self.services.pop(key, None)
        self.refresh()

    def clear_custom(self):
        self.custom.clear()
        self.refresh()

    def add_custom_host(self):
        raw = self.add_host.value()
        n = hostsfile.normalise_host(raw)
        if not n:
            messagebox.showwarning(APP_NAME, f"\"{aiblock.clean_text(raw, 80)}\" is not a valid hostname.", parent=self)
            return
        if n in hostsfile.PROTECTED:
            messagebox.showwarning(APP_NAME, f"{n} is on the protected list because blocking it breaks Windows or "
                                   "sign-in. It will not be added.", parent=self)
            return
        if n not in self.custom:
            self.custom.append(n)
        self.add_host.clear()
        self.refresh()

    # ---- applying ----
    def apply(self):
        payload = self.payload()
        if not payload["hostnames"] and not payload["services"]:
            return
        current = state.load_session()
        if not state.session_is_active(current) or current.get("id") != self.session.get("id"):
            messagebox.showinfo(APP_NAME, "The session has ended, so there is nothing to add to.", parent=self)
            self.destroy()
            return
        paths.ensure_dirs()
        ext_path = paths.STATE_DIR / "extend.json"
        ext_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self.btn_apply.set_enabled(False)
        app = self.app

        def done(res):
            try:
                ext_path.unlink()
            except OSError:
                pass
            if res.get("ok"):
                s = state.load_session()
                if s:
                    app.resume_watcher(s)
                n = res.get("added", 0)
                if res.get("paused"):
                    msg = f"{n} hostname(s) recorded. They are blocked when the current free window ends."
                else:
                    msg = f"Now blocking {res.get('total', 0)} hostnames ({n} new)."
                warn = res.get("warnings") or []
                if warn:
                    msg += "\n\nNot added:\n" + "\n".join(f"  {w}" for w in warn[:8])
                app.set_status(f"Blocking {res.get('total', 0)} hostnames", "active")
                app.toast(APP_NAME, f"{n} hostname(s) added to the session.")
                if self.winfo_exists():
                    self.destroy()
                if warn:
                    messagebox.showinfo(APP_NAME, msg, parent=app)
            else:
                if self.winfo_exists():
                    self.btn_apply.set_enabled(True)
                if not res.get("cancelled"):
                    messagebox.showerror(APP_NAME, "Could not add to the session:\n" + res.get("error", "unknown error"),
                                         parent=self if self.winfo_exists() else app)
            app.pages["dashboard"].refresh()
            if self.on_done:
                self.on_done(res)

        app.run_elevated(["extend", str(ext_path)], done, "Adding to the running session")


class AIBlockDialog(ModalDialog):
    """Type a service name, get an AI-suggested block list (domains, apps, files), review every entry, then save
    it as a named list that the Block dropdown, the builder and presets treat like a built-in service."""

    KIND_TITLE = {"domain": "Domains", "app": "Apps to close", "file": "Files to hide"}
    KIND_PROMPT = {"domain": "Hostname, e.g. cdn.example.com", "app": "Executable name, e.g. Example.exe",
                   "file": "Path, e.g. {Desktop}\\Example.lnk"}

    def __init__(self, app: App, on_use=None, demo: bool = False):
        super().__init__(app, "AI Block", 840, 780)
        t = self.theme
        b = self.body
        self.on_use = on_use
        self.result: aiblock.Result | None = None
        self.key: str | None = None
        self._busy = False
        self._dots = 0
        hdr = row(b)
        label(hdr, t, "AI Block", "h1").pack(side="left")
        self.saved_btn = Button(hdr, t, "Saved lists", self._saved_menu, kind="standard", font="small", padx=10, pady=4,
                                icon=ICON["list"])
        self.saved_btn.pack(side="right")
        label(b, t, "Type an app or service. The AI suggests the domains, desktop apps and files that belong to it. "
                    "Nothing is blocked until you have reviewed the list and added it.", "small", "muted",
              wraplength=780, justify="left").pack(anchor="w", pady=(2, 12))
        qr = row(b)
        self.query = Field(qr, t, placeholder="What do you want to block?  e.g. Discord, Steam, TikTok",
                           icon=ICON["search"])
        self.query.pack(side="left", fill="x", expand=True)
        self.query.bind_return(self.generate)
        self.gen_btn = Button(qr, t, "Generate", self.generate, kind="primary", padx=18, icon=ICON["ai"])
        self.gen_btn.pack(side="left", padx=(8, 0))
        self.status = label(b, t, "", "small", "muted", wraplength=780, justify="left")
        self.status.pack(anchor="w", pady=(6, 0))
        self.scroll = Scroll(b, t)
        self.scroll.pack(fill="both", expand=True, pady=(8, 0))
        nr = row(b, pady=(10, 0))
        label(nr, t, "Save as", "small", "muted").pack(side="left")
        self.name_var = tk.StringVar()
        self.name_field = Field(nr, t, textvariable=self.name_var, placeholder="Name of the list")
        self.name_field.pack(side="left", fill="x", expand=True, padx=(8, 0))
        _c, self.btn_save, self.btn_use = self.buttons([("Cancel", self.destroy, "standard"),
                                                       ("Save list", self.save, "standard"),
                                                       ("Save and use", self.use, "primary")])
        self.btn_save.set_enabled(False)
        self.btn_use.set_enabled(False)
        self.query.focus()
        self._hint()
        if demo:  # offline data, used by the GUI tests and captures
            self.query.set("Discord")
            self._show(aiblock.fallback("Discord"))

    # ---- generation -------------------------------------------------------
    def _hint(self):
        cfg = self.app.llm_config()
        if not cfg.api_key:
            self._set_status("No API key is set yet (Settings > AI Block), so lists come from the built-in catalog "
                             "of 62 services. Add a key to cover anything else.", "muted")

    def _set_status(self, text: str, kind: str = "muted"):
        if self.winfo_exists():
            self.status.configure(text=text, fg=self.theme[kind])

    def generate(self):
        if self._busy:
            return
        try:
            q = aiblock.sanitize_query(self.query.value())
        except aiblock.AIBlockError as e:
            self._set_status(str(e), "warn")
            return
        cfg = self.app.llm_config()
        self._busy = True
        self.gen_btn.set_enabled(False)
        self._dots = 0
        self._pulse(q)

        def work():
            try:
                res, err = aiblock.generate(q, cfg), None
            except (llm.LLMError, aiblock.AIBlockError) as e:
                res, err = aiblock.fallback(q), e
            except Exception as e:  # never let a surprise take the dialog down
                res, err = aiblock.fallback(q), aiblock.AIBlockError(f"Unexpected error: {type(e).__name__}")
            try:
                self.after(0, lambda: self._done(res, err))
            except (RuntimeError, tk.TclError):
                pass  # dialog closed while generating
        threading.Thread(target=work, daemon=True).start()

    def _pulse(self, q: str):
        if not self._busy or not self.winfo_exists():
            return
        self._dots = (self._dots + 1) % 4
        self._set_status(f"Asking the AI about \"{q}\"" + "." * self._dots, "muted")
        self.after(400, lambda: self._pulse(q))

    def _done(self, res: aiblock.Result, err):
        if not self.winfo_exists():
            return
        self._busy = False
        self.gen_btn.set_enabled(True)
        if err is None:
            self._set_status(f"AI suggestions for \"{res.service}\". AI-suggested, please review.", "muted")
        else:
            msg = getattr(err, "message", str(err))
            if getattr(err, "kind", "") == "no_key":
                msg = "No API key is set (Settings > AI Block)."
            self._set_status(f"{msg} Showing the offline list instead.", "warn")
        self._show(res)

    # ---- rendering --------------------------------------------------------
    def _show(self, res: aiblock.Result):
        t = self.theme
        self.result = res
        for w in self.scroll.body.winfo_children():
            w.destroy()
        f = self.scroll.body
        if not self.name_var.get().strip() or self.key is None:
            self.name_var.set(f"{res.service} (AI)")
        notes = ["AI-suggested, please review. Untick anything that looks wrong, Edit an entry to fix it, "
                 "or Flag it so it is remembered as a bad suggestion."] + list(res.notes)
        infobar(f, t, " ".join(notes), "info", wrap=680).pack(fill="x", padx=2, pady=(0, 10))
        for kind in ("domain", "app", "file"):
            self._section(f, kind, res.of(kind))
        self.btn_save.set_enabled(True)
        self.btn_use.set_enabled(True)
        self.scroll.scroll_top()

    def _section(self, parent, kind: str, items: list[aiblock.Item]):
        t = self.theme
        s = Surface(parent, t, pad=16)
        s.pack(fill="x", padx=2, pady=(0, 10))
        body = s.body
        hr = row(body)
        n_sel = sum(1 for i in items if i.checked and i.usable)
        label(hr, t, f"{self.KIND_TITLE[kind]}", "h3").pack(side="left")
        label(hr, t, f"{n_sel} of {len(items)} selected", "small", "muted").pack(side="left", padx=(10, 0))
        Button(hr, t, "None", lambda k=kind: self._select_all(k, False), kind="subtle", font="small", padx=8, pady=2)\
            .pack(side="right")
        Button(hr, t, "All", lambda k=kind: self._select_all(k, True), kind="subtle", font="small", padx=8, pady=2)\
            .pack(side="right", padx=(0, 4))
        if not items:
            label(body, t, "Nothing suggested." if kind != "file" else
                  "No files suggested. Add shortcuts or installers below if you want them hidden too.",
                  "small", "faint").pack(anchor="w", pady=(8, 0))
        if kind == "domain":
            for g, title in aiblock.GROUPS:
                grp = [i for i in items if i.group == g]
                if not grp:
                    continue
                label(body, t, title, "small", "faint").pack(anchor="w", pady=(10, 2))
                for it in grp:
                    self._item_row(body, it)
        else:
            tk.Frame(body, bg=t["surface"], height=6).pack()
            for it in items:
                self._item_row(body, it)
        ar = row(body, pady=(10, 0))
        var = tk.StringVar()
        fld = Field(ar, t, textvariable=var, placeholder=self.KIND_PROMPT[kind], font="small")
        fld.pack(side="left", fill="x", expand=True)
        fld.bind_return(lambda k=kind, v=var: self._add_own(k, v.get()))
        Button(ar, t, "Add", lambda k=kind, v=var: self._add_own(k, v.get()), kind="standard", font="small", padx=10,
               pady=3).pack(side="left", padx=(8, 0))

    def _item_row(self, parent, item: aiblock.Item):
        t = self.theme
        r = tk.Frame(parent, bg=t["surface"])
        r.pack(fill="x", pady=3)
        r.columnconfigure(1, weight=1)
        var = tk.BooleanVar(value=item.checked and item.usable)
        var.trace_add("write", lambda *_, it=item, v=var: setattr(it, "checked", bool(v.get())))
        ck = Check(r, t, var)
        ck.grid(row=0, column=0, rowspan=2, sticky="n", pady=(1, 0))
        if not item.usable:
            ck.set_enabled(False)
        line = tk.Frame(r, bg=t["surface"])
        line.grid(row=0, column=1, sticky="ew", padx=(10, 0))
        colour = "text" if item.usable and not item.flagged else "faint"
        label(line, t, item.value, "body", colour).pack(side="left")
        tag, tag_colour = {"broad": ("may affect other sites", "warn"), "protected": ("protected", "danger"),
                           "invalid": ("invalid", "danger"), "refused": ("refused", "danger"),
                           "missing": ("not found on this PC", "faint"), "duplicate": ("duplicate", "faint")
                           }.get(item.status, ("", "faint"))
        if item.flagged:
            tag, tag_colour = "flagged as wrong", "danger"
        if tag:
            label(line, t, tag, "small", tag_colour).pack(side="left", padx=(10, 0))
        if item.kind == "app" and item.group in aiblock.ROLES:
            label(line, t, aiblock.ROLES[item.group], "small", "faint").pack(side="left", padx=(10, 0))
        label(line, t, f"{int(round(item.confidence * 100))}%", "small", "faint").pack(side="left", padx=(10, 0))
        Button(line, t, "Unflag" if item.flagged else "Flag", lambda it=item: self._flag(it), kind="link", font="small",
               padx=4, pady=0).pack(side="right")
        Button(line, t, "Edit", lambda it=item: self._edit(it), kind="link", font="small", padx=4, pady=0)\
            .pack(side="right")
        text = " ".join(x for x in (item.reason, item.note) if x)
        if text:
            label(r, t, text, "small", "muted", wraplength=640, justify="left").grid(row=1, column=1, sticky="w",
                                                                                     padx=(10, 0))

    # ---- editing ----------------------------------------------------------
    def _select_all(self, kind: str, on: bool):
        if not self.result:
            return
        for it in self.result.of(kind):
            if it.usable and not it.flagged:
                it.checked = on
        self._show(self.result)

    def _revalidate(self, item: aiblock.Item, value: str) -> bool:
        """Apply a user-typed value to an item with the same normalisation as model output."""
        if item.kind == "domain":
            host = aiblock.normalise_domain(value)
            if not host:
                messagebox.showwarning(APP_NAME, f"\"{value}\" is not a valid hostname.", parent=self)
                return False
            item.value = host
            item.status, item.note = aiblock.domain_status(host, llm.provider_host(self.app.llm_config()))
        elif item.kind == "app":
            exe = aiblock.normalise_app(value)
            if not exe:
                messagebox.showwarning(APP_NAME, f"\"{value}\" is not a plain .exe file name.", parent=self)
                return False
            item.value = exe
            item.status, item.note = aiblock.app_status(exe)
        else:
            path = aiblock.expand_file(value)
            if not path:
                messagebox.showwarning(APP_NAME, "Use an absolute path or one starting with {Desktop}, "
                                       "{PublicDesktop}, {Downloads} or {Documents}.", parent=self)
                return False
            item.value = path
            item.status, item.note = aiblock.file_status(path)
        if item.status == "protected":
            messagebox.showwarning(APP_NAME, f"{item.value} cannot be blocked: {item.note}", parent=self)
        item.checked = item.status == "ok"
        item.original = value
        return True

    def _edit(self, item: aiblock.Item):
        v = simple_prompt(self.app, "Edit entry", self.KIND_PROMPT[item.kind], initial=item.original or item.value,
                          ok_text="Apply")
        if v and self._revalidate(item, v):
            item.reason = "Edited by you."
            item.confidence = 1.0
            self._show(self.result)

    def _add_own(self, kind: str, value: str):
        if not self.result or not value.strip():
            return
        item = aiblock.Item(kind, value.strip(), "main" if kind == "domain" else ("app" if kind == "app" else ""),
                            "Added by you.", 1.0)
        if not self._revalidate(item, value.strip()):
            return
        if any(i.kind == kind and i.value.lower() == item.value.lower() for i in self.result.items):
            messagebox.showinfo(APP_NAME, f"{item.value} is already in the list.", parent=self)
            return
        self.result.items.append(item)
        self._show(self.result)

    def _flag(self, item: aiblock.Item):
        item.flagged = not item.flagged
        if item.flagged:
            item.checked = False
            state.log(f"AI Block: user flagged suggestion {item.kind} {item.value!r} for {self.result.service!r}")
        self._show(self.result)

    # ---- saved lists ------------------------------------------------------
    def _saved_menu(self):
        lists = state.load_ai_blocks()
        ordered = sorted(lists.items(), key=lambda kv: str(kv[1].get("name", "")))
        items: list[tuple] = [("No saved lists yet", None)] if not lists else []
        items += [(f"Open: {aiblock.clean_text(rec.get('name', key), 60)}", lambda k=key: self._load(k))
                  for key, rec in ordered]
        if lists:
            items.append((None, None))
            items += [(f"Delete: {aiblock.clean_text(rec.get('name', key), 60)}", lambda k=key: self._delete(k))
                      for key, rec in ordered]
        popup_menu(self, self.theme, items, self.saved_btn.winfo_rootx(),
                   self.saved_btn.winfo_rooty() + self.saved_btn.winfo_height() + 4)

    def _load(self, key: str):
        rec = state.load_ai_blocks().get(key)
        if not rec:
            return
        self.key = key
        self.name_var.set(rec.get("name", key))
        self.query.set(rec.get("query", ""))
        res = aiblock.record_to_result(rec)
        self._set_status(f"Saved list \"{rec.get('name', key)}\" (last updated {rec.get('updated', '?')[:10]}). "
                         "Press Generate to refresh it with new suggestions, or edit it here.", "muted")
        self._show(res)

    def _delete(self, key: str):
        lists = state.load_ai_blocks()
        rec = lists.get(key)
        if rec and messagebox.askyesno(APP_NAME, f"Delete the AI list \"{rec.get('name', key)}\"?", parent=self):
            lists.pop(key, None)
            state.save_ai_blocks(lists)
            if self.key == key:
                self.key = None
            self._set_status("List deleted.", "muted")

    def save(self) -> str | None:
        if not self.result:
            return None
        name = self.name_var.get().strip() or f"{self.result.service} (AI)"
        if name == self.name_field.placeholder:
            name = f"{self.result.service} (AI)"
        lists = state.load_ai_blocks()
        key = self.key or aiblock.slug(name)
        if self.key is None and key in lists and not messagebox.askyesno(
                APP_NAME, f"A list called \"{lists[key].get('name', key)}\" exists. Replace it?", parent=self):
            return None
        rec = aiblock.to_record(self.result, name, lists.get(key))
        if not (rec["domains"] or rec["apps"] or rec["files"]):
            messagebox.showwarning(APP_NAME, "Select at least one entry first.", parent=self)
            return None
        lists[key] = rec
        state.save_ai_blocks(lists)
        self.key = key
        self._set_status(f"Saved as \"{name}\": {len(rec['domains'])} domains, {len(rec['apps'])} apps, "
                         f"{len(rec['files'])} files. It now appears in the Block dropdown.", "muted")
        return key

    def use(self):
        key = self.save()
        if not key:
            return
        self.destroy()
        if self.on_use:
            self.on_use(catalog.AI_PREFIX + key)


# ====================================================================== pages
class Page(tk.Frame):
    def __init__(self, parent, app: App):
        super().__init__(parent, bg=app.theme["bg"])
        self.app = app
        self.theme = app.theme

    def header(self, parent, title: str, subtitle: str = "", back: bool = False) -> tk.Frame:
        h = tk.Frame(parent, bg=self.theme["bg"], padx=40, pady=22)
        h.pack(fill="x")
        if back:
            Button(h, self.theme, "Focus Session", lambda: self.app.show("dashboard"), kind="subtle", font="small",
                   padx=8, pady=3, icon=ICON["back"]).pack(anchor="w", pady=(0, 6))
        label(h, self.theme, title, "h1").pack(anchor="w")
        h.subtitle = label(h, self.theme, subtitle, "small", "muted", wraplength=760, justify="left")
        if subtitle:
            h.subtitle.pack(anchor="w", pady=(2, 0))
        # wrap the subtitle to the page, never wider (a narrow window must not clip it)
        h.bind("<Configure>", lambda e: h.subtitle.configure(wraplength=max(300, min(760, e.width - 80))))
        return h

    def tiles(self, parent, items: list[tuple], padx: int = 40) -> tk.Frame:
        """Stat tiles. Items are (title, value) or (title, value, glyph)."""
        t = self.theme
        r = tk.Frame(parent, bg=t["bg"], padx=padx)
        r.pack(fill="x")
        for i, it in enumerate(items):
            title, val = it[0], it[1]
            glyph = it[2] if len(it) > 2 else None
            s = Surface(r, t, pad=14)
            s.pack(side="left", fill="x", expand=True, padx=(0, 12 if i < len(items) - 1 else 0))
            top = tk.Frame(s.body, bg=t["surface"])
            top.pack(fill="x")
            if glyph:
                tk.Label(top, text=glyph, bg=t["surface"], fg=t["accent"], font=t.fonts["icon"]).pack(side="left",
                                                                                                        padx=(0, 8))
            label(top, t, title, "small", "muted").pack(side="left")
            label(s.body, t, val, "stat").pack(anchor="w", pady=(2, 0))
        return r


# ------------------------------------------------------------------ dashboard
class DashboardPage(Page):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.view: tk.Frame | None = None
        self._timer = None
        self._last_phase = None
        self._overdue_since = None
        self._frac = 0.0

    def refresh(self):
        if self._timer:
            self.after_cancel(self._timer)
            self._timer = None
        if self.view:
            self.view.destroy()
        self._items = None
        self._goal_item = None
        self.hero = None
        self.ring = None
        self._hero_hover = {}
        self._hoverables = {}
        session = state.load_session()
        if state.session_is_active(session):
            self.view = self._session_view(session)
            self.app.set_status("Session active", "active")
            sounds.play(self.app.settings.get("focus_sound", "off"))
        else:
            self.view = self._idle_view()
            self.app.set_status("Idle", "idle")
            sounds.stop()
        self.view.pack(fill="both", expand=True)

    # ---- idle -------------------------------------------------------------
    LENGTHS = [("25", "25 min"), ("50", "50 min"), ("60", "1 h"), ("90", "90 min"), ("120", "2 h"), ("240", "4 h"),
               ("custom", "Custom")]

    def _idle_view(self) -> tk.Frame:
        t = self.theme
        sc = Scroll(self, t)
        f = sc.body
        self._stats = state.stats(state.load_history())

        # quick-start state (the hero chips and ring read these)
        self.q_preset = tk.StringVar(value="Social media")
        self.q_minutes = tk.IntVar(value=int(self.app.settings.get("default_duration_min", 60)))
        self.q_len = tk.StringVar(value=str(self.q_minutes.get()))
        if self.q_len.get() not in [o[0] for o in self.LENGTHS]:
            self.q_len.set("custom")

        def seg_changed(*_):
            v = self.q_len.get()
            if v != "custom":
                self.q_minutes.set(int(v))
            self._hero_refresh()

        def step_changed(*_):
            v = str(self.q_minutes.get())
            self.q_len.set(v if v in [o[0] for o in self.LENGTHS] else "custom")
            self._hero_refresh()
        self.q_len.trace_add("write", seg_changed)
        self.q_minutes.trace_add("write", step_changed)
        self.q_preset.trace_add("write", lambda *_: self._hero_refresh())
        # Pomodoro preset "work/break" or "off"; notification blocking; focus sound (a saved preference)
        s = self.app.settings
        self.q_pomo = tk.StringVar(value=f"{s.get('pomodoro_work_min', 25)}/{s.get('pomodoro_break_min', 5)}"
                                   if s.get("pomodoro_default") else "off")
        self.q_notif = tk.BooleanVar(value=bool(s.get("block_notifications_default", False)))
        self.q_pomo.trace_add("write", lambda *_: self._hero_refresh())
        self.q_notif.trace_add("write", lambda *_: self._hero_refresh())

        self.hero = Hero(f, t, height=540, seed=11, radius=18, on_layout=self._layout_hero)
        self.hero.pack(fill="x", padx=24, pady=(24, 0))
        self.q_goal = Field(self.hero, t, placeholder="What will you work on?", icon=ICON["search"])
        self._hero_hover: dict[str, float] = {}
        self.bind("<Configure>", self._fit_hero)

        # progress card: this week's bars on the left, two headline stats on the right
        card = Surface(f, t, pad=20)
        card.pack(fill="x", padx=24, pady=(16, 24))
        body = card.body
        body.columnconfigure(0, weight=3, uniform="prog")
        body.columnconfigure(2, weight=1, uniform="prog")
        body.columnconfigure(4, weight=1, uniform="prog")
        left = tk.Frame(body, bg=t["surface"])
        left.grid(row=0, column=0, sticky="nsew")
        hl = row(left)
        label(hl, t, "Your Progress", "h3").pack(side="left")
        tk.Label(hl, text=ICON["trend"], bg=t["surface"], fg=t["accent"], font=t.fonts["icon"]).pack(side="left", padx=(8, 0))
        self.week_chart = tk.Canvas(left, height=118, bg=t["surface"], highlightthickness=0)
        self.week_chart.pack(fill="x", pady=(8, 0))
        self.week_chart.bind("<Configure>", lambda e: self._draw_week())
        st = self._stats
        week = self._week_values()
        rate = f"{round(100 * st['completed'] / st['sessions'])}%" if st["sessions"] else "--"
        hrs = sum(week) / 3600
        this_week = f"{hrs:.1f} hrs" if hrs >= 1 else f"{sum(week) // 60} min"
        facts = [(ICON["target"], rate, "Focus Rate"), (ICON["clock"], this_week, "This Week")]
        for i, (glyph, val, cap) in enumerate(facts):
            tk.Frame(body, bg=t["border"], width=1).grid(row=0, column=1 + 2 * i, sticky="ns", padx=18)
            cell = tk.Frame(body, bg=t["surface"])
            cell.grid(row=0, column=2 + 2 * i, sticky="nsew")
            tk.Label(cell, text=glyph, bg=t["surface"], fg=t["accent"], font=t.fonts["icon_l"]).pack(anchor="w", pady=(16, 10))
            label(cell, t, val, "stat").pack(anchor="w")
            label(cell, t, cap, "small", "muted").pack(anchor="w", pady=(2, 0))
        return sc

    def _fit_hero(self, e=None):
        """Size the hero to the window so the progress card stays visible: 470 .. 620 px."""
        hero = getattr(self, "hero", None)
        if not isinstance(hero, Hero) or not hero.winfo_exists():
            return
        avail = self.winfo_height()
        if avail < 50:
            return
        floor = getattr(self, "_hero_min", 520)
        target = max(floor, min(max(640, floor), avail - 260))
        if abs(int(hero["height"]) - target) > 4:
            hero.configure(height=target)

    # ---- hero (idle) ------------------------------------------------------
    def _week_values(self) -> list[int]:
        per_day = self._stats["per_day"]
        monday = datetime.now().date() - timedelta(days=datetime.now().weekday())
        return [per_day.get((monday + timedelta(days=i)).strftime("%Y-%m-%d"), 0) for i in range(7)]

    def _draw_week(self):
        t = self.theme
        c = self.week_chart
        c.delete("all")
        w = max(c.winfo_width(), 200)
        h = 118
        vals = self._week_values()
        mx = max(vals + [1800])
        slot = w / 7
        bw = min(30, slot * 0.55)
        today = datetime.now().weekday()
        base = h - 24  # shared baseline for every bar
        for i, v in enumerate(vals):
            cx = slot * i + slot / 2
            bh = max(8, (v / mx) * (h - 44))
            x0, x1 = cx - bw / 2, cx + bw / 2
            if v:
                gradient_shape(c, x0, base - bh, x1, base, 6, t["grad2"], t["grad1"], vertical=True)
            else:
                rounded(c, x0, base - bh, x1, base, 6, fill=t["surface3"], outline="")
            c.create_text(cx, h - 8, text=("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[i],
                          fill=t["text"] if i == today else t["muted"], font=t.fonts["small"])

    def _length_label(self) -> str:
        v = self.q_len.get()
        for key, text in self.LENGTHS:
            if key == v and key != "custom":
                return text
        return fmt_duration(int(self.q_minutes.get()) * 60)

    def _preset_label(self) -> str:
        sel = self.q_preset.get()
        if sel.startswith(catalog.AI_PREFIX):
            s = catalog.get(sel)
            name = s.name if s else "AI list (deleted)"
            return name if len(name) <= 22 else name[:20] + "..."
        return sel[6:] if sel.startswith("saved:") else _short(sel)

    def _open_ai_block(self, demo: bool = False):
        def use(key: str):
            self.q_preset.set(key)
            self._hero_refresh()
        AIBlockDialog(self.app, on_use=use, demo=demo)

    def _layout_hero(self, w: int, h: int):
        """Draw the hero overlay: streak badge, title, goal box, ring, start button, option chips."""
        t = self.theme
        hero = self.hero
        hero.delete("fg")
        cx = w / 2
        self._hoverables = {}
        self._chip_pos: dict[str, tuple[float, float]] = {}

        # streak badge (top right); compact (star + days) when the full badge would touch the title
        streak = self._stats["streak_days"]
        days = f"{streak} day{'s' if streak != 1 else ''}"
        title_w = t.fonts["hero"].measure("Ready to Focus?")
        bw_badge = max(196, t.fonts["small"].measure("Productivity streak") + 70)
        compact = cx + title_w / 2 + 16 > w - bw_badge - 20
        if compact:
            bw_badge = t.fonts["bold"].measure(days) + 76
        bx1, by1, bx2, by2 = w - bw_badge - 20, 18, w - 20, 70
        hero.pill(bx1, by1, bx2, by2, 0.10)
        hero.create_text(bx1 + 24, (by1 + by2) / 2, text=ICON["star"], fill="#ffcf5a", font=t.fonts["icon_l"], tags="fg")
        dy = (by1 + by2) / 2 if compact else by1 + 37
        if not compact:
            hero.create_text(bx1 + 48, by1 + 16, text="Productivity streak", anchor="w", fill=t["hero_muted"],
                             font=t.fonts["small"], tags="fg")
        hero.create_text(bx1 + 48, dy, text=days, anchor="w", fill=t["hero_text"], font=t.fonts["bold"], tags="fg")
        hero.create_text(bx1 + 48 + t.fonts["bold"].measure(days) + 6, dy, text=ICON["bolt"], anchor="w",
                         fill="#ffb454", font=t.fonts["icon_s"], tags="fg")

        # title, subtitle, goal box: spaced on the type scale (display 24 pt, subtitle 11 pt)
        top = 24
        ty = top + t.fonts["hero"].metrics("linespace") / 2
        hero.create_text(cx, ty, text="Ready to Focus?", fill=t["hero_text"], font=t.fonts["hero"], tags="fg")
        sy = ty + t.fonts["hero"].metrics("linespace") / 2 + 10 + t.fonts["hero_sub"].metrics("linespace") / 2
        hero.create_text(cx, sy, text="Block distractions, boost productivity.", fill=t["hero_muted"],
                         font=t.fonts["hero_sub"], tags="fg")
        gy = sy + t.fonts["hero_sub"].metrics("linespace") / 2 + 16 + self.q_goal.h / 2
        gw = min(420, w - 80)
        self.q_goal.configure(bg=hero.sky_color_at(gy))
        if not getattr(self, "_goal_item", None) or not hero.type(self._goal_item):
            self._goal_item = hero.create_window(cx, gy, window=self.q_goal, width=gw, anchor="center", tags="goal")
        else:
            hero.coords(self._goal_item, cx, gy)
            hero.itemconfigure(self._goal_item, width=gw)
        hero.tag_raise("goal")

        # chip rows: session options (dropdowns), then what and how long to block. Rows wrap when the hero is
        # narrow, so nothing is ever cut off; everything is laid out bottom-up and the ring takes what is left.
        pomo = self.q_pomo.get()
        snd = self.app.settings.get("focus_sound", "off")
        row1 = [("pomo", ICON["stopwatch"], "Pomodoro" + ("" if pomo == "off" else f": {pomo.replace('/', ' / ')}"),
                 self._menu_pomo, True),
                ("notif", ICON["bell_off"] if self.q_notif.get() else ICON["bell"],
                 "Block Notifications" if self.q_notif.get() else "Notifications on", self._menu_notif, True),
                ("sound", ICON["music"], "Focus Sounds" + ("" if snd == "off" else f": {sounds.label(snd)}"),
                 self._menu_sound, True),
                ("custom", ICON["edit"], "Customize", lambda e=None: self._open_builder("once"), False)]
        row2 = [("block", ICON["globe"], f"Block: {self._preset_label()}", self._menu_block, True),
                ("len", ICON["clock"], f"Length: {self._length_label()}", self._menu_length, True),
                ("weekly", ICON["calendar"], "Weekly schedule", lambda e=None: self._open_builder("weekly"), False),
                ("long", ICON["stopwatch"], "Long-term", lambda e=None: self._open_builder("long"), False),
                ("ai", ICON["ai"], "AI Block", lambda e=None: self._open_ai_block(), False)]
        # body text (10 pt) when it fits in two rows; one step down the scale (9 pt) when that saves a row
        font = t.fonts["body"]
        lines = [(ln, 0.10) for ln in self._wrap_chips(row1, w - 48, font)]
        lines += [(ln, 0.07) for ln in self._wrap_chips(row2, w - 48, font)]
        if len(lines) > 2:
            small = t.fonts["small"]
            alt = [(ln, 0.10) for ln in self._wrap_chips(row1, w - 48, small)]
            alt += [(ln, 0.07) for ln in self._wrap_chips(row2, w - 48, small)]
            if len(alt) < len(lines):
                font, lines = small, alt
        ch, row_gap = 36, 10
        bh = 46
        # the hero must hold the title block, a ring of at least 64 px radius, the button and the chip rows;
        # when it does not (narrow window, many rows) ask for the height it needs and lay out again
        need = int(gy + self.q_goal.h / 2 + 18 + 2 * (64 + 8) + 18 + bh + 8 + len(lines) * (ch + row_gap) + 12)
        self._hero_min = max(520, need)
        if h < need - 2:
            hero.after_idle(lambda: hero.winfo_exists() and hero.configure(height=need))
        y = h - 22
        for ln, alpha in reversed(lines):
            self._chip_row(hero, ln, cx, y - ch / 2, ch, font, alpha)
            y -= ch + row_gap

        # start button
        by2 = y - 8
        by1 = by2 - bh
        bw = 260
        x1, x2 = cx - bw / 2, cx + bw / 2
        glines = gradient_shape(hero, x1, by1, x2, by2, bh / 2, t["grad1"], t["grad2"], tags=("fg", "start"))
        iw = t.fonts["icon"].measure(ICON["play"])
        tw = t.fonts["cta"].measure("Start Focus Session")
        sx = cx - (iw + 12 + tw) / 2
        hero.create_text(sx, (by1 + by2) / 2, text=ICON["play"], anchor="w", fill="#ffffff", font=t.fonts["icon"],
                         tags=("fg", "start"))
        hero.create_text(sx + iw + 12, (by1 + by2) / 2, text="Start Focus Session", anchor="w", fill="#ffffff",
                         font=t.fonts["cta"], tags=("fg", "start"))
        self._bind_hover(hero, "start", self._gradient_painter(hero, glines, t["grad1"], t["grad2"]),
                         lambda e: self._quick_start())

        # ring: fills the space between the goal box and the button, on its own dark disc so the mountains
        # never show through the time
        ring_top, ring_bot = gy + self.q_goal.h / 2 + 18, by1 - 18
        rr = max(56, min(104, (ring_bot - ring_top) / 2 - 8))
        rcx, rcy = cx, (ring_top + ring_bot) / 2
        self._draw_ring_block(hero, rcx, rcy, rr, 1.0, fmt_clock(int(self.q_minutes.get()) * 60), "Focus Time",
                              t["grad1"], t["grad2"], tags="fg")

    def _wrap_chips(self, chips: list[tuple], max_w: float, font) -> list[list[tuple]]:
        """Greedy line wrap of chips so a row never runs past the hero edge."""
        lines: list[list[tuple]] = [[]]
        used = 0.0
        for chip in chips:
            cw = self._chip_width(chip, font)
            if lines[-1] and used + 8 + cw > max_w:
                lines.append([])
                used = 0.0
            lines[-1].append(chip)
            used += (8 if len(lines[-1]) > 1 else 0) + cw
        return lines

    @staticmethod
    def _chip_width(chip: tuple, font) -> float:
        _k, _g, text, _c, menu = chip
        return font.measure(text) + 54 + (16 if menu else 0)

    def _draw_ring_block(self, hero: Hero, rcx: float, rcy: float, rr: float, frac: float, time_text: str,
                         sub_text: str, c1: str, c2: str, tags) -> tuple[int, int, float]:
        """Disc + gradient ring + time and caption, with the text block vertically centred on visual glyph heights
        so the caption never touches the digits. Returns (time_item, sub_item, inner_radius)."""
        t = self.theme
        rw = 12 if rr > 72 else 9
        inner = rr - rw / 2 - 3
        disc = blend(t["bg"], hero.sky_color_at(rcy), 0.45)
        hero.create_oval(rcx - inner, rcy - inner, rcx + inner, rcy + inner, fill=disc, outline="", tags=tags)
        glow = blend(hero.sky_color_at(rcy), c1, 0.35)
        gradient_ring(hero, rcx, rcy, rr, rw, frac, c1, c2, track=blend(hero.sky_color_at(rcy), "#ffffff", 0.16),
                      glow=glow, tags=tags)
        tf = t.timer_font(time_text, inner * 2 * 0.84)
        lf = t.fonts["body"] if inner >= 64 else t.fonts["small"]
        ty, sy = self._ring_text_y(hero, tf, lf, rcy)
        ti = hero.create_text(rcx, ty, text=time_text, fill=t["hero_text"], font=tf, tags=tags)
        si = hero.create_text(rcx, sy, text=sub_text, fill=t["hero_muted"], font=lf, tags=tags)
        return ti, si, inner

    @staticmethod
    def _ring_text_y(hero: Hero, tf, lf, rcy: float) -> tuple[float, float]:
        """Centres of the time and caption items: digits are ~0.72 em tall, so centre that visual block."""
        px = hero.winfo_fpixels("1p")
        th = 0.72 * int(tf.cget("size")) * px
        lh = 0.72 * int(lf.cget("size")) * px
        gap = 8
        block = th + gap + lh
        top = rcy - block / 2
        return top + th / 2 - 0.04 * th, top + th + gap + lh / 2

    def _chip_row(self, hero: Hero, chips: list[tuple], cx: float, cy: float, ch: int, font, alpha: float):
        t = self.theme
        gap = 8
        widths = [self._chip_width(c, font) for c in chips]
        x = cx - (sum(widths) + gap * (len(chips) - 1)) / 2
        for (key, glyph, text, cmd, menu), cw in zip(chips, widths):
            tag = f"chip_{key}"
            pill = hero.pill(x, cy - ch / 2, x + cw, cy + ch / 2, alpha, tags=("fg", tag))
            hero.create_text(x + 18, cy, text=glyph, fill=t["hero_text"], font=t.fonts["icon"], tags=("fg", tag))
            hero.create_text(x + 36, cy, text=text, anchor="w", fill=t["hero_text"], font=font, tags=("fg", tag))
            if menu:
                hero.create_text(x + cw - 15, cy, text=ICON["down"], fill=t["hero_muted"], font=t.fonts["icon_s"],
                                 tags=("fg", tag))
            self._chip_pos[key] = (x, cy + ch / 2)
            self._bind_hover(hero, tag, self._pill_painter(hero, pill, hero.sky_color_at(cy), lo=alpha, hi=alpha + 0.10),
                             cmd, key=key)
            x += cw + gap

    # ---- hover effects (recolour in place: never delete the item under the pointer) ----------------
    @staticmethod
    def _pill_painter(hero: Hero, item: int, base: str, tint: str = "#ffffff", lo: float = 0.10, hi: float = 0.20):
        """level 0 = rest, 1 = hover, below 0 = pressed (darker)."""
        def paint(level: float):
            if level < 0:
                fill = blend(blend(base, tint, lo), "#000000", -level * 0.35)
                hero.itemconfigure(item, fill=fill, outline=blend(base, tint, lo + 0.12))
                return
            a = lo + (hi - lo) * level
            hero.itemconfigure(item, fill=blend(base, tint, a), outline=blend(base, tint, a + 0.12))
        return paint

    @staticmethod
    def _gradient_painter(hero: Hero, lines: list[int], c1: str, c2: str):
        n = max(1, len(lines) - 1)

        def paint(level: float):
            tint = "#ffffff" if level >= 0 else "#000000"
            amt = 0.12 * level if level >= 0 else -0.18 * level
            for i, item in enumerate(lines):
                hero.itemconfigure(item, fill=blend(blend(c1, c2, i / n), tint, amt))
        return paint

    def _bind_hover(self, hero: Hero, tag: str, paint, click, key: str | None = None):
        key = key or tag
        self._hoverables[key] = paint

        def press(e):
            tok = self._hero_hover.setdefault("_tok_" + key, [])
            tok[:] = []  # cancel a running hover animation (animate() checks the same list)
            paint(-1.0)

        def release(e):
            paint(self._hero_hover.get(key, 0.0))
            box = hero.bbox(tag)
            if box and box[0] <= e.x <= box[2] and box[1] <= e.y <= box[3]:
                click(e)
        hero.tag_bind(tag, "<ButtonPress-1>", press)
        hero.tag_bind(tag, "<ButtonRelease-1>", release)
        hero.tag_bind(tag, "<Enter>", lambda e, k=key: (self._hover(k, 1.0), hero.configure(cursor="hand2")))
        hero.tag_bind(tag, "<Leave>", lambda e, k=key: (self._hover(k, 0.0), hero.configure(cursor="arrow")))

    def _hover(self, key: str, target: float):
        paint = self._hoverables.get(key)
        if not paint or self._hero_hover.get("_target_" + key) == target:
            return
        self._hero_hover["_target_" + key] = target
        start = self._hero_hover.get(key, 0.0)
        tok = self._hero_hover.setdefault("_tok_" + key, [])

        def step(f):
            level = start + (target - start) * f
            self._hero_hover[key] = level
            try:
                paint(level)
            except tk.TclError:
                pass
        animate(self, 110, step, token=tok)

    def _popup(self, key: str, items: list[tuple[str, object]]):
        """Dropdown under a hero chip."""
        x, y = self._chip_pos.get(key, (0, 0))
        hero = self.hero if isinstance(self.hero, Hero) else self.ring
        popup_menu(self, self.theme, items, hero.winfo_rootx() + x, hero.winfo_rooty() + y + 6)

    def _menu_block(self, _e=None):
        items: list[tuple] = [(k, lambda k=k: self.q_preset.set(k)) for k in catalog.PRESETS]
        saved = state.load_presets()
        if saved:
            items.append((None, None))
            items += [(f"Saved preset: {n}", lambda n=n: self.q_preset.set("saved:" + n)) for n in saved]
        ai = state.load_ai_blocks()
        items.append((None, None))
        items += [(f"AI list: {rec.get('name', k)}", lambda k=k: self.q_preset.set(catalog.AI_PREFIX + k))
                  for k, rec in sorted(ai.items(), key=lambda kv: kv[1].get("name", ""))]
        items.append(("AI Block: build a new list...", lambda: self._open_ai_block()))
        self._popup("block", items)

    def _menu_length(self, _e=None):
        items: list[tuple] = [(text, lambda k=key: self.q_len.set(k)) for key, text in self.LENGTHS if key != "custom"]
        items.append((None, None))
        items.append(("Custom length...", self._custom_length))
        self._popup("len", items)

    def _custom_length(self):
        v = simple_prompt(self.app, "Custom length", "Minutes (5 to 1440)")
        if v and v.strip().isdigit():
            self.q_minutes.set(max(5, min(1440, int(v.strip()))))

    def _menu_pomo(self, _e=None):
        items: list[tuple] = [("Off", lambda: self.q_pomo.set("off")), (None, None)]
        items += [(f"{w} min work / {b} min break", lambda w=w, b=b: self.q_pomo.set(f"{w}/{b}"))
                  for w, b in ((25, 5), (50, 10), (90, 15))]
        self._popup("pomo", items)

    def _menu_notif(self, _e=None):
        self._popup("notif", [("Leave Windows notifications on", lambda: self.q_notif.set(False)),
                              ("Block Windows notifications during the session", lambda: self.q_notif.set(True))])

    def _menu_sound(self, _e=None):
        def pick(kind):
            self.app.settings["focus_sound"] = kind
            state.save_settings(self.app.settings)
            self._hero_refresh()
            if state.session_is_active(state.load_session()):
                sounds.play(kind)
            else:  # short preview so the choice can be heard
                sounds.play(kind)
                self.after(4000, lambda: sounds.stop() if not state.session_is_active(state.load_session()) else None)
        items: list[tuple] = [(label_, lambda k=k: pick(k)) for k, label_ in sounds.SOUNDS]
        items.insert(1, (None, None))
        self._popup("sound", items)

    def _open_builder(self, mode: str):
        """Jump to the builder with the Long-term or Weekly schedule tab already selected."""
        b = self.app.pages["builder"]
        if self.q_goal.value():
            b.goal_var.set(self.q_goal.value())
        b.mode_var.set(mode)
        b._mode_changed()
        self.app.show("builder")
        if mode != "once":
            self.app.after(260, lambda: b.scroll.scroll_to(0.5))

    def _quick_start(self):
        app = self.app
        plan = Plan(app.settings)
        plan.goal = self.q_goal.value()
        sel = self.q_preset.get()
        if sel.startswith("saved:"):
            p = state.load_presets().get(sel[6:])
            if p:
                plan.load_preset(p)
                plan.goal = self.q_goal.value() or plan.goal
        elif sel.startswith(catalog.AI_PREFIX):
            s = catalog.get(sel)
            if not s:
                messagebox.showwarning(APP_NAME, "That AI list no longer exists. Pick another block list.", parent=app)
                self.q_preset.set("Social media")
                return
            plan.services[sel] = s
            add_ai_files(plan, sel)
        else:
            for k in catalog.PRESETS.get(sel, []):
                s = catalog.get(k)
                if s:
                    plan.services[k] = s
        plan.restore_at = datetime.now() + timedelta(minutes=max(1, int(self.q_minutes.get())))
        pomo = self.q_pomo.get()
        plan.options["pomodoro"] = pomo != "off"
        if pomo != "off":
            w, b = pomo.split("/")
            plan.options["pomodoro_work_min"], plan.options["pomodoro_break_min"] = int(w), int(b)
        plan.options["block_notifications"] = bool(self.q_notif.get())
        app.plan = plan
        app.pages["builder"].load_plan(plan)
        app.show("builder")
        app.after(240, app.pages["builder"].review)

    # ---- active session ---------------------------------------------------
    def _session_view(self, session: dict) -> tk.Frame:
        t = self.theme
        sc = Scroll(self, t)
        f = sc.body
        self._texts = {"time": "", "sub": "remaining", "pomo": "", "subtitle": "", "title": "Focus session in progress"}
        self._items: dict[str, int] = {}
        self._hero_hover = {}
        self._last_paused = bool(session.get("paused"))
        self._frac = 0.0
        self.ring = Hero(f, t, height=470, seed=5, radius=18, on_layout=self._layout_session)
        self.ring.pack(fill="x", padx=24, pady=(24, 0))

        right = tk.Frame(f, bg=t["bg"], padx=24)
        right.pack(fill="x")
        right.columnconfigure(0, weight=1)
        right.columnconfigure(1, weight=1)

        goal = Surface(right, t, pad=18)
        goal.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(18, 0))
        label(goal.body, t, "Your goal", "small", "muted").pack(anchor="w")
        autowrap(label(goal.body, t, session.get("goal") or "(no goal set)", "h2", wraplength=800, justify="left"),
                 goal.body, 8, 1200).pack(anchor="w", pady=(4, 0))

        stats = tk.Frame(right, bg=t["bg"])
        stats.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        stats.columnconfigure((0, 1, 2, 3), weight=1, uniform="stat")
        self.stat_vals = {}
        for i, (key, title, glyph) in enumerate((("hosts", "Hosts blocked", ICON["globe"]),
                                                 ("files", "Files hidden", ICON["hide"]),
                                                 ("kills", "Apps closed", ICON["cross"]),
                                                 ("fixes", "Tamper fixes", ICON["shield"]))):
            s = Surface(stats, t, pad=14)
            s.grid(row=0, column=i, sticky="ew", padx=(0, 10) if i < 3 else 0)
            top = tk.Frame(s.body, bg=t["surface"])
            top.pack(fill="x")
            tk.Label(top, text=glyph, bg=t["surface"], fg=t["accent"], font=t.fonts["icon"]).pack(side="left", padx=(0, 8))
            label(top, t, title, "small", "muted", wraplength=96, justify="left").pack(side="left")
            self.stat_vals[key] = label(s.body, t, "0", "stat")
            self.stat_vals[key].pack(anchor="w")
        self.stat_vals["hosts"].configure(text=str(len(session.get("hostnames", []))))
        self.stat_vals["files"].configure(text=str(len(session.get("hidden_files", []))))

        notes = Surface(right, t, pad=18)
        notes.grid(row=2, column=0, sticky="nsew", pady=(12, 0), padx=(0, 6))
        label(notes.body, t, "Scratchpad", "small", "muted").pack(anchor="w")
        label(notes.body, t, "Park stray thoughts here instead of opening a tab. Saved with the session.", "tiny",
              "faint", wraplength=420, justify="left").pack(anchor="w")
        ta = TextArea(notes.body, t, height=5)
        ta.pack(fill="x", pady=(8, 0))
        self.notes = ta.text
        self.notes.insert("1.0", session.get("notes", ""))
        self.notes.bind("<KeyRelease>", self._notes_changed)
        self._notes_job = None

        details = Surface(right, t, pad=18)
        details.grid(row=2, column=1, sticky="nsew", pady=(12, 0), padx=(6, 0))
        label(details.body, t, "Details", "small", "muted").pack(anchor="w", pady=(0, 6))
        tk.Frame(right, bg=t["bg"], height=30).grid(row=3, column=0)
        svc = ", ".join(catalog.get(k).name for k in session.get("services", []) if catalog.get(k)) or "custom list"
        procs = ", ".join(session.get("processes", [])) or "none"
        o = session.get("options", {})
        prot = []
        prot.append("restore task " + ("registered" if session.get("task_registered") else "NOT registered"))
        prot.append("enforcement " + ("every minute" if session.get("enforce_task_registered") else "NOT registered"))
        if o.get("doh_off", True):
            prot.append("browser DoH off")
        if o.get("lock_files"):
            prot.append("files locked")
        sched = session.get("schedule")
        plan_line = ""
        if schedule.is_weekly(sched):
            plan_line = (f"\nWeekly schedule: {schedule.describe(sched)} Ends for good "
                         f"{state.parse_iso(session['restore_at']).strftime('%a %d %b %Y, %H:%M')}.")
        txt = (f"Services: {svc}\nApps being closed: {procs}\nProtection: {', '.join(prot)}{plan_line}"
               f"\nHosts backup: {session.get('hosts_backup') or 'none'}")
        label(details.body, t, txt, "tiny", "muted", wraplength=400, justify="left").pack(anchor="w")
        br = row(details.body, pady=(10, 0))
        Button(br, t, "Add to block", self._add_block, kind="primary", font="small", padx=10, pady=4,
               icon=ICON["globe"]).pack(side="left")
        Button(br, t, "Apps to close", self._edit_apps, kind="standard", font="small", padx=10, pady=4,
               icon=ICON["cross"]).pack(side="left", padx=(8, 0))

        self.restore_btn = None
        self._overdue_since = None
        self._last_phase = None
        self._target_frac = self._progress(session)
        self._anim_gen = getattr(self, "_anim_gen", 0) + 1
        self._animate_ring(self._anim_gen)
        self._tick()
        return sc

    @staticmethod
    def session_apps(session: dict) -> tuple[list[str], list[str]]:
        """(apps added by hand, apps that come with the session's services) for a session record."""
        svc = []
        for k in session.get("services", []):
            s = catalog.get(k)
            if s:
                svc.extend(p for p in s.processes if p.lower() not in {x.lower() for x in svc})
        low = {p.lower() for p in svc}
        own = [p for p in session.get("processes", []) if isinstance(p, str) and p.lower() not in low]
        return clean_apps(own), svc

    def _add_block(self):
        """Add services or hostnames to the running session through the elevated helper."""
        session = state.load_session()
        if not state.session_is_active(session):
            return
        AddBlockDialog(self.app, session)

    def _edit_apps(self):
        """Add or remove hand-picked apps while the session runs. Only the GUI closes apps, so this needs no
        elevation: the session record is updated, the watcher restarted, and the list remembered for next time."""
        session = state.load_session()
        if not state.session_is_active(session):
            return
        own, svc = self.session_apps(session)

        def save(new_own: list[str]):
            s = state.load_session()
            if not state.session_is_active(s):
                return
            seen: set[str] = set()
            merged = []
            for p in svc + new_own:
                if p.lower() not in seen:
                    seen.add(p.lower())
                    merged.append(p)
            s["processes"] = merged
            s.setdefault("options", {})["process_watch"] = True
            state.save_session(s)
            self.app.settings["remembered_apps"] = list(new_own)
            state.save_settings(self.app.settings)
            self.app.resume_watcher(s)
            self.app.set_status(f"Closing {len(merged)} app(s)", "active")
            self.refresh()

        AppsDialog(self.app, own, svc, save)

    def _layout_session(self, w: int, h: int):
        """Overlay of the session hero: title, status line, ring with countdown, two pill buttons."""
        t = self.theme
        hero = self.ring
        hero.delete("fg")
        cx = w / 2
        self._hoverables = {}
        ty = 28 + t.fonts["hero"].metrics("linespace") / 2
        self._items["title"] = hero.create_text(cx, ty, text=self._texts["title"], fill=t["hero_text"],
                                                font=t.fonts["hero"], tags="fg")
        sy = ty + t.fonts["hero"].metrics("linespace") / 2 + 8
        self._items["subtitle"] = hero.create_text(cx, sy, text=self._texts["subtitle"], anchor="n", fill=t["hero_muted"],
                                                   font=t.fonts["hero_sub"], width=w - 120, justify="center", tags="fg")
        # ring block: two subtitle lines fit above it, pomodoro line and the pills below
        y1, y2 = h - 58, h - 18
        ring_top = sy + 2 * t.fonts["hero_sub"].metrics("linespace") + 14
        ring_bot = y1 - 56
        rr = max(60, min(100, (ring_bot - ring_top) / 2 - 6))
        rcy = (ring_top + ring_bot) / 2
        self._ring_geo = (cx, rcy, rr)
        paused = getattr(self, "_last_paused", False)
        c1, c2 = (blend(t["success"], "#1b7a46", 0.3), t["success"]) if paused else (t["grad1"], t["grad2"])
        self._items["time"], self._items["sub"], self._ring_inner = self._draw_ring_block(
            hero, cx, rcy, rr, 0.0, self._texts["time"] or "00:00", self._texts["sub"], c1, c2, tags="fg")
        hero.itemconfigure(self._items["time"], text=self._texts["time"])
        self._items["pomo"] = hero.create_text(cx, y1 - 26, text=self._texts["pomo"], fill=t["grad2"],
                                               font=t.fonts["body"], tags="fg")
        # buttons: hide, end early, and the focus-sound dropdown
        base = hero.sky_color_at((y1 + y2) / 2)
        snd = self.app.settings.get("focus_sound", "off")
        snd_text = "Focus Sounds" if snd == "off" else f"Sounds: {sounds.label(snd)}"
        sw = t.fonts["bold"].measure(snd_text) + 74
        total = 138 + 10 + 138 + 10 + sw
        x = cx - total / 2
        specs = [("hide", "Hide window", x, x + 138, self.app.iconify, False, None),
                 ("end", "End early", x + 148, x + 286, self._end_early, True, None),
                 ("sound", snd_text, x + 296, x + 296 + sw, self._menu_sound, False, ICON["music"])]
        self._chip_pos = {}
        for key, text, x1, x2, cmd, danger, glyph in specs:
            tag = f"btn_{key}"
            if danger:
                tint, lo, hi, fg = t["danger"], 0.22, 0.34, blend(t["danger"], "#ffffff", 0.35)
            else:
                tint, lo, hi, fg = "#ffffff", 0.10, 0.20, t["hero_text"]
            item = rounded(hero, x1, y1, x2, y2, (y2 - y1) / 2, fill=blend(base, tint, lo),
                           outline=blend(base, tint, lo + 0.12), tags=("fg", tag))
            if glyph:
                hero.create_text(x1 + 20, (y1 + y2) / 2, text=glyph, fill=fg, font=t.fonts["icon"], tags=("fg", tag))
                hero.create_text(x1 + 38, (y1 + y2) / 2, text=text, anchor="w", fill=fg, font=t.fonts["bold"],
                                 tags=("fg", tag))
                hero.create_text(x2 - 16, (y1 + y2) / 2, text=ICON["down"], fill=t["hero_muted"],
                                 font=t.fonts["icon_s"], tags=("fg", tag))
                self._chip_pos[key] = (x1, y2)
            else:
                hero.create_text((x1 + x2) / 2, (y1 + y2) / 2, text=text, fill=fg, font=t.fonts["bold"],
                                 tags=("fg", tag))
            self._bind_hover(hero, tag, self._pill_painter(hero, item, base, tint, lo, hi), lambda e, c=cmd: c(),
                             key=key)
        self._draw_ring()

    def _hero_refresh(self):
        """Redraw whichever hero overlay this view uses (idle quick start or active session)."""
        if getattr(self, "_items", None) is not None and isinstance(getattr(self, "ring", None), Hero):
            hero, fn = self.ring, self._layout_session
        elif isinstance(getattr(self, "hero", None), Hero):
            hero, fn = self.hero, self._layout_hero
        else:
            return
        if hero.winfo_exists() and hero.winfo_width() > 10:
            fn(hero.winfo_width(), hero.winfo_height())

    def _set_item(self, key: str, text: str, **kw):
        self._texts[key] = text
        item = self._items.get(key)
        if item and self.ring.winfo_exists():
            self.ring.itemconfigure(item, text=text, **kw)

    def _timer_font(self, text: str):
        inner = getattr(self, "_ring_inner", 80)
        return self.theme.timer_font(text, inner * 2 * 0.84)

    def _set_timer(self, text: str):
        """Big countdown; shrinks for day-long values so it stays inside the ring, re-centring the text block."""
        tf = self._timer_font(text)
        self._set_item("time", text, font=tf)
        hero = getattr(self, "ring", None)
        if isinstance(hero, Hero) and hero.winfo_exists() and self._items.get("time") and self._items.get("sub"):
            cx, cy, _r = self._ring_geo
            lf = self.theme.fonts["body"] if getattr(self, "_ring_inner", 80) >= 64 else self.theme.fonts["small"]
            ty, sy = self._ring_text_y(hero, tf, lf, cy)
            hero.coords(self._items["time"], cx, ty)
            hero.coords(self._items["sub"], cx, sy)

    def _progress(self, session: dict) -> float:
        """Ring fill: progress through the current phase for weekly schedules, else through the session."""
        sched = session.get("schedule")
        now = datetime.now()
        if schedule.is_weekly(sched):
            nxt = schedule.next_change(sched, now) or state.parse_iso(session["restore_at"])
            prev = schedule.prev_change(sched, now) or state.parse_iso(session.get("started") or session["created"])
            started = state.parse_iso(session.get("started") or session["created"])
            prev = max(prev, started)
            total = (nxt - prev).total_seconds()
            return min(1.0, max(0.0, (now - prev).total_seconds() / total)) if total > 0 else 1.0
        total = state.planned_duration_seconds(session)
        return 1 - state.seconds_remaining(session) / total if total else 1

    def _animate_ring(self, gen: int):
        if gen != self._anim_gen or not self.winfo_exists() or not getattr(self, "ring", None) \
                or not self.ring.winfo_exists():
            return
        diff = self._target_frac - self._frac
        if abs(diff) > 0.0005:
            self._frac += diff * 0.12
            self._draw_ring()
            self.after(30, lambda: self._animate_ring(gen))
        else:
            self._frac = self._target_frac
            self._draw_ring()
            self.after(500, lambda: self._animate_ring(gen))

    def _draw_ring(self):
        t = self.theme
        hero = self.ring
        if not isinstance(hero, Hero) or not getattr(self, "_ring_geo", None):
            return
        hero.delete("arc")
        cx, cy, r = self._ring_geo
        paused = getattr(self, "_last_paused", False)
        c1, c2 = (blend(t["success"], "#1b7a46", 0.3), t["success"]) if paused else (t["grad1"], t["grad2"])
        glow = blend(hero.sky_color_at(cy), c1, 0.3)
        gradient_ring(hero, cx, cy, r, 12 if r > 72 else 9, self._frac, c1, c2,
                      track=blend(hero.sky_color_at(cy), "#ffffff", 0.16), glow=glow, tags=("fg", "arc"))
        for key in ("time", "sub"):
            if self._items.get(key):
                hero.tag_raise(self._items[key])

    def _notes_changed(self, _e=None):
        if self._notes_job:
            self.after_cancel(self._notes_job)
        self._notes_job = self.after(800, self._save_notes)

    def _save_notes(self):
        s = state.load_session()
        if state.session_is_active(s):
            s["notes"] = self.notes.get("1.0", "end-1c")
            state.save_session(s)

    def _tick(self):
        t = self.theme
        session = state.load_session()
        if not state.session_is_active(session):
            self.app.stop_watcher()
            self.app.toast(APP_NAME, "Focus session complete. Everything is restored.")
            self.refresh()
            self.app.pages["history"].refresh()
            return
        remaining = state.seconds_remaining(session)
        total = state.planned_duration_seconds(session)
        self._target_frac = self._progress(session)
        ends = state.parse_iso(session["restore_at"])
        sched = session.get("schedule")
        paused = bool(session.get("paused"))
        if schedule.is_weekly(sched):
            nxt = schedule.next_change(sched) or ends
            phase_left = max(0, int((nxt - datetime.now()).total_seconds()))
            self._set_timer(fmt_clock(min(phase_left, remaining)))
            self._set_item("sub", "free window, blocks return in" if paused else "until free time")
            self._set_item("subtitle", f"{schedule.phase_text(sched)} Schedule ends {ends.strftime('%a %d %b at %H:%M')}. "
                                       "Windows switches the blocks even if you close this window.")
            self._set_item("title", "Free window" if paused else "Focus session in progress")
            if paused != self._last_paused:
                self._last_paused = paused
                self.app.resume_watcher(session)
                self.app.toast(APP_NAME, "Free window started. Blocks are lifted." if paused
                               else "Free window over. Blocks are back on.")
                self.refresh()
                return
        else:
            self._set_timer(fmt_clock(remaining))
            self._set_item("subtitle", f"Ends {ends.strftime('%a %d %b at %H:%M')}. Windows enforces the blocks even if "
                                       "you close this window.")
        self.stat_vals["hosts"].configure(text="0" if paused else str(len(session.get("hostnames", []))))
        self.stat_vals["files"].configure(text=str(len(session.get("hidden_files", []))))
        self.stat_vals["kills"].configure(text=str(session.get("kills", 0)))
        self.stat_vals["fixes"].configure(text=str(len(session.get("enforce_fixes", []))))

        opts = session.get("options", {})
        if opts.get("pomodoro"):
            w = int(opts.get("pomodoro_work_min", 25)) * 60
            b = int(opts.get("pomodoro_break_min", 5)) * 60
            elapsed = total - remaining
            pos = elapsed % (w + b)
            phase = "work" if pos < w else "break"
            left = (w - pos) if phase == "work" else (w + b - pos)
            cycle = elapsed // (w + b) + 1
            self._set_item("pomo", f"Pomodoro {cycle}   {'Focus' if phase == 'work' else 'Break'}   {fmt_clock(left)}",
                           fill=t["grad2"] if phase == "work" else t["success"])
            if self._last_phase and phase != self._last_phase:
                self.app.toast(APP_NAME, "Break time. Stand up, look away from the screen." if phase == "break"
                               else "Break over. Back to it.")
            self._last_phase = phase
        else:
            self._set_item("pomo", "")

        if remaining <= 0:
            if self._overdue_since is None:
                self._overdue_since = datetime.now()
                self._set_item("sub", "restoring")
            elif (datetime.now() - self._overdue_since).total_seconds() > 90 and not self.restore_btn:
                self._set_item("sub", "scheduled task is late")
                self.restore_btn = Button(self.view.body, t, "Restore now (admin prompt)",
                                          lambda: self.app.restore_now("manual"), kind="primary")
                self.restore_btn.pack(pady=10)
        self._timer = self.after(1000, self._tick)

    def _end_early(self):
        session = state.load_session()
        if not state.session_is_active(session):
            self.refresh()
            return
        EndEarlyDialog(self.app, session, lambda: self.app.restore_now("early"))


def _short(preset: str) -> str:
    return {"Social media": "Social", "Video & streaming": "Video", "News & feeds": "News",
            "Deep work (everything)": "Everything"}.get(preset, preset)


def add_ai_files(plan: Plan, key: str) -> int:
    """Add the files of a saved AI list to a plan: existing ones that pass the path guard, nothing else."""
    n = 0
    for f in catalog.ai_files(key):
        if f in plan.files or guard.forbidden_reason(f) or not Path(f).exists():
            continue
        plan.files.append(f)
        n += 1
    return n


# -------------------------------------------------------------------- builder
class BuilderPage(Page):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.plan = app.plan
        t = self.theme
        self.scroll = Scroll(self, t)
        self.scroll.pack(fill="both", expand=True)
        body = self.scroll.body
        self.header(body, "Customize session", "Choose what to block and hide, and when it all comes back.", back=True)

        def card(glyph: str, title: str, sub: str = "") -> tk.Frame:
            s = Surface(body, t, pad=20)
            s.pack(fill="x", padx=40, pady=(0, 12))
            top = row(s.body)
            badge = tk.Canvas(top, width=32, height=32, bg=t["surface"], highlightthickness=0)
            badge.pack(side="left", anchor="n")
            rounded(badge, 0.5, 0.5, 31.5, 31.5, 6, fill=t["accent_soft"], outline=t["accent_soft"])
            badge.create_text(16, 16, text=glyph, fill=t["accent"], font=t.fonts["icon"])
            tb = title_block(top, t, title, sub, wrap=700)
            tb.pack(side="left", padx=(12, 0))
            return s.body

        # --- goal
        c = card(ICON["check"], "What are you working on?", "Shown on the session screen and saved in your history.")
        self.goal_var = tk.StringVar()
        Field(c, t, textvariable=self.goal_var, placeholder="e.g. Finish the report draft").pack(fill="x", pady=(14, 0))

        # --- block
        c = card(ICON["globe"], "Block distractions",
                 "Type a service or website, press Find, then review the hostnames before anything is blocked.")
        r = row(c, pady=(14, 0))
        self.search = Field(r, t, placeholder="YouTube, Discord, reddit.com ...", icon=ICON["search"])
        self.search.pack(side="left", fill="x", expand=True)
        self.search.bind_return(self.find)
        Button(r, t, "Find", self.find, kind="primary", padx=18).pack(side="left", padx=(8, 0))
        Button(r, t, "AI Block", self.ai_block, kind="standard", padx=14, icon=ICON["ai"]).pack(side="left", padx=(8, 0))
        self.results = tk.Frame(c, bg=t["surface"])
        self.results.pack(fill="x")

        pr = row(c, pady=(12, 0))
        label(pr, t, "Presets", "small", "muted").grid(row=0, column=0, sticky="w", padx=(0, 10))
        for i, name in enumerate(catalog.PRESETS):
            Button(pr, t, name, lambda n=name: self.add_preset(n), kind="standard", font="small", padx=10, pady=4,
                   icon=ICON["plus"]).grid(row=i // 3, column=1 + i % 3, sticky="w", padx=(0, 6), pady=2)

        label(c, t, "Selected", "small", "muted").pack(anchor="w", pady=(14, 4))
        self.chips = tk.Frame(c, bg=t["surface"])
        self.chips.pack(fill="x")

        hr = row(c, pady=(14, 4))
        self.host_count = label(hr, t, "Hostnames to block (0)", "small", "muted")
        self.host_count.pack(side="left")
        Button(hr, t, "Remove selected", self.remove_hosts, kind="standard", font="small", padx=10, pady=4).pack(side="right")
        Button(hr, t, "Undo removals", self.undo_removals, kind="subtle", font="small", padx=10, pady=4)\
            .pack(side="right", padx=(0, 6))
        lp = ListPanel(c, t, height=8)
        lp.pack(fill="x")
        self.host_list = lp.list
        ar = row(c, pady=(10, 0))
        self.add_host = Field(ar, t, placeholder="Add a hostname manually, e.g. news.example.com")
        self.add_host.pack(side="left", fill="x", expand=True)
        self.add_host.bind_return(self.add_custom_host)
        Button(ar, t, "Add", self.add_custom_host, kind="standard", padx=18).pack(side="left", padx=(8, 0))

        # --- apps
        c = card(ICON["stop"], "Apps to close",
                 "Desktop programs that are closed every 2 seconds while this window is open. Services you block "
                 "bring their own apps; add any other program here by name, from the list of running programs, or "
                 "by browsing to its exe. Apps you add are remembered for your next session.")
        xr = row(c, pady=(14, 0))
        self.add_app = Field(xr, t, placeholder="App name or exe, e.g. Spotify or Spotify.exe")
        self.add_app.pack(side="left", fill="x", expand=True)
        self.add_app.bind_return(self.add_app_name)
        Button(xr, t, "Add", self.add_app_name, kind="standard", padx=18).pack(side="left", padx=(8, 0))
        self.btn_running = Button(xr, t, "Running now", self.running_apps, kind="standard", padx=12, icon=ICON["list"])
        self.btn_running.pack(side="left", padx=(8, 0))
        Button(xr, t, "Browse", self.browse_app, kind="standard", padx=12, icon=ICON["folder"]).pack(side="left",
                                                                                                        padx=(8, 0))
        ac = row(c, pady=(12, 4))
        self.app_count = label(ac, t, "Apps to close (0)", "small", "muted")
        self.app_count.pack(side="left")
        Button(ac, t, "Remove selected", self.remove_apps, kind="subtle", font="small", padx=10, pady=4).pack(side="right")
        lp = ListPanel(c, t, height=4)
        lp.pack(fill="x")
        self.app_list = lp.list

        # --- files
        c = card(ICON["hide"], "Hide files until the session ends",
                 "Only the Hidden and System attributes change. Files are never moved, copied or modified, and reappear "
                 "exactly as they were. Paths inside Windows, Program Files, AppData or your profile root are refused.")
        fr = row(c, pady=(14, 6))
        Button(fr, t, "Add files", self.add_files, kind="standard", font="small", padx=10, pady=4).pack(side="left")
        Button(fr, t, "Add folder", self.add_folder, kind="standard", font="small", padx=10, pady=4).pack(side="left", padx=6)
        Button(fr, t, "Everything on the Desktop", self.add_desktop, kind="standard", font="small", padx=10, pady=4)\
            .pack(side="left")
        Button(fr, t, "Remove selected", self.remove_files, kind="subtle", font="small", padx=10, pady=4).pack(side="right")
        lp = ListPanel(c, t, height=5)
        lp.pack(fill="x")
        self.file_list = lp.list

        # --- time
        c = card(ICON["clock"], "When should everything come back?",
                 "Timer: a few hours. Long-term: days to months. Weekly schedule: blocked all week and lifted only in "
                 "your free windows, for example Friday afternoon and the weekend. Windows scheduled tasks do the "
                 "switching whether or not the app is running.")
        mr = row(c, pady=(12, 6))
        self.mode_var = tk.StringVar(value="once")
        Segmented(mr, t, [("once", "Timer"), ("long", "Long-term"), ("weekly", "Weekly schedule")], self.mode_var,
                  command=self._mode_changed).pack(side="left")
        self.time_box = tk.Frame(c, bg=t["surface"])
        self.time_box.pack(fill="x")
        tb = self.time_box

        # timer quick picks
        self.f_timer = tk.Frame(tb, bg=t["surface"])
        self.quick_var = tk.StringVar(value="")
        Segmented(self.f_timer, t, [("25", "+25 min"), ("50", "+50 min"), ("60", "+1 h"), ("90", "+90 min"),
                                    ("120", "+2 h"), ("240", "+4 h"), ("480", "+8 h"), ("u12", "Until 12:00"),
                                    ("u17", "Until 17:00"), ("tom", "Tomorrow 08:00")], self.quick_var,
                  command=self._quick_time, font="small", padx=10).pack(side="left", pady=(0, 8))

        # long-term quick picks
        self.f_long = tk.Frame(tb, bg=t["surface"])
        self.long_var = tk.StringVar(value="")
        Segmented(self.f_long, t, [("2", "2 days"), ("3", "3 days"), ("7", "1 week"), ("14", "2 weeks"),
                                   ("30", "1 month"), ("61", "2 months"), ("91", "3 months")], self.long_var,
                  command=self._long_pick, font="small", padx=10).pack(side="left", pady=(0, 8))

        # shared exact date and time (timer and long-term)
        self.f_datetime = tk.Frame(tb, bg=t["surface"])
        dr = self.f_datetime
        label(dr, t, "Date", "small", "muted").pack(side="left")
        self.date_var = tk.StringVar()
        Field(dr, t, textvariable=self.date_var, width=124).pack(side="left", padx=(8, 18))
        label(dr, t, "Time", "small", "muted").pack(side="left")
        self.time_var = tk.StringVar()
        Field(dr, t, textvariable=self.time_var, width=84).pack(side="left", padx=(8, 18))
        self.ends_label = label(dr, t, "", "body")
        self.ends_label.pack(side="left")
        self.date_var.trace_add("write", lambda *_: self._time_changed())
        self.time_var.trace_add("write", lambda *_: self._time_changed())
        self.f_long_note = infobar(tb, t, "A long-term block stays on across reboots and sign-ins until the end date. "
                                   "Ending early always needs the cooling-off delay, plus the phrase in Strict mode.",
                                   "warning", wrap=700)

        # weekly planner
        self.f_weekly = tk.Frame(tb, bg=t["surface"])
        self._build_planner(self.f_weekly)
        self._mode_changed()

        # --- options
        c = card(ICON["settings"], "Options")
        og = tk.Frame(c, bg=t["surface"])
        og.pack(fill="x", pady=(14, 0))
        self.opt_vars: dict[str, tk.Variable] = {}
        opts = [
            ("strict", "Strict mode", "Ending early also requires typing a confirmation phrase."),
            ("doh_off", "Turn off browser DNS-over-HTTPS for the session",
             "Firefox, Chrome, Edge and Brave can otherwise resolve names around the hosts file. Set through browser "
             "policies, recorded and restored exactly. Takes effect when a browser restarts."),
            ("lock_files", "Strict hide: also block reading the hidden files",
             "Adds one read-deny permission entry per file so it cannot be opened even if found. Removed at restore. "
             "OneDrive may report sync errors for locked files until then."),
            ("process_watch", "Close distracting desktop apps",
             "Discord, Steam, Roblox and so on are closed while this window is open."),
            ("close_browsers", "Close all browsers when the session starts",
             "Open tabs can keep a connection alive. Closing them makes the block and the DoH policy take effect at once."),
            ("pomodoro", "Pomodoro reminders",
             "Notifies you when to take a break and when to resume. Blocks stay on during breaks."),
            ("block_notifications", "Block Windows notifications",
             "Turns the Windows notifications switch off for the session and puts it back exactly at restore. "
             "This also silences this app's own reminders."),
        ]
        for i, (key, title, desc) in enumerate(opts):
            v = tk.BooleanVar(value=bool(self.plan.options.get(key)))
            self.opt_vars[key] = v
            Toggle(og, t, v).grid(row=i, column=0, sticky="nw", pady=6)
            tf = tk.Frame(og, bg=t["surface"])
            tf.grid(row=i, column=1, sticky="w", padx=(12, 0), pady=4)
            label(tf, t, title, "body").pack(anchor="w")
            autowrap(label(tf, t, desc, "small", "muted", wraplength=700, justify="left"), tf.master, 84, 700)\
                .pack(anchor="w")
        nr = row(c, pady=(14, 0))
        self.delay_var = tk.IntVar(value=self.plan.options["end_delay_seconds"])
        self.pw_var = tk.IntVar(value=self.plan.options["pomodoro_work_min"])
        self.pb_var = tk.IntVar(value=self.plan.options["pomodoro_break_min"])
        for i, (txt, var, lo, hi, stp, suf) in enumerate((("End-early delay", self.delay_var, 0, 600, 5, " s"),
                                                          ("Pomodoro work", self.pw_var, 5, 120, 5, " min"),
                                                          ("Pomodoro break", self.pb_var, 1, 60, 1, " min"))):
            label(nr, t, txt, "small", "muted").grid(row=0, column=i * 2, sticky="w", padx=(0 if i == 0 else 24, 8))
            Stepper(nr, t, var, lo, hi, stp, suf).grid(row=0, column=i * 2 + 1, sticky="w")

        # --- footer
        fb = tk.Frame(body, bg=t["bg"], padx=40)
        fb.pack(fill="x", pady=(4, 30))
        Button(fb, t, "Review and start", self.review, kind="primary", padx=18, pady=7, icon=ICON["arrow"]).pack(side="right")
        Button(fb, t, "Save as preset", self.save_preset, kind="standard", padx=16, pady=7).pack(side="right", padx=(0, 8))
        Button(fb, t, "Clear", self.clear, kind="subtle", padx=16, pady=7).pack(side="left")

        self.load_plan(self.plan)

    # ---- weekly planner ---------------------------------------------------
    def _build_planner(self, parent: tk.Frame):
        t = self.theme
        top = row(parent, pady=(0, 10))
        label(top, t, "Repeat for", "small", "muted").pack(side="left")
        self.weeks_var = tk.IntVar(value=1)
        Stepper(top, t, self.weeks_var, 1, schedule.MAX_WEEKS, 1, " wk", width=110).pack(side="left", padx=(8, 14))
        self.weeks_label = label(top, t, "", "body")
        self.weeks_label.pack(side="left")
        Button(top, t, "Reset to default", self._reset_planner, kind="subtle", font="small", padx=10, pady=4)\
            .pack(side="right")
        self.weeks_var.trace_add("write", lambda *_: self._planner_changed())

        grid = tk.Frame(parent, bg=t["surface"])
        grid.pack(fill="x")
        self.day_mode: list[tk.StringVar] = []
        self.day_hour: list[tk.IntVar] = []
        self.day_steppers: list[Stepper] = []
        self.day_hint: list[tk.Label] = []
        for d, name in enumerate(schedule.DAY_NAMES):
            label(grid, t, name, "body", width=11, anchor="w").grid(row=d, column=0, sticky="w", pady=3)
            mv = tk.StringVar(value="block")
            hv = tk.IntVar(value=15)
            self.day_mode.append(mv)
            self.day_hour.append(hv)
            Segmented(grid, t, [("block", "Blocked"), ("after", "Free from"), ("free", "Free all day")], mv,
                      command=self._planner_changed, font="small", padx=10, height=30).grid(row=d, column=1,
                                                                                            sticky="w", pady=3)
            st = Stepper(grid, t, hv, 0, 23, 1, ":00", width=108)
            st.grid(row=d, column=2, sticky="w", padx=(12, 0), pady=3)
            self.day_steppers.append(st)
            hv.trace_add("write", lambda *_: self._planner_changed())
            hint = label(grid, t, "", "small", "muted")
            hint.grid(row=d, column=3, sticky="w", padx=(14, 0))
            self.day_hint.append(hint)

        br = row(parent, pady=(10, 0))
        self.planner_summary = label(br, t, "", "small", "muted", wraplength=720, justify="left", anchor="w")
        self.planner_summary.pack(side="left", fill="x", expand=True, anchor="w")
        infobar(parent, t, "Blocks lift within a minute of a free window starting and return when it ends. Hidden "
                "files reappear during free windows and are hidden again afterwards. The whole schedule is restored "
                "for good when the last week ends.", "info", wrap=700).pack(fill="x", pady=(10, 0))
        self._set_planner(schedule.default_weekly())

    def _set_planner(self, sched: dict):
        sched = schedule.normalize(sched)
        if not schedule.is_weekly(sched):
            sched = schedule.default_weekly()
        self.weeks_var.set(int(sched.get("weeks", 1)))
        for d in range(7):
            win = sched["free"].get(str(d))
            if not win:
                self.day_mode[d].set("block")
            elif win[0] == 0 and win[1] >= schedule.MINUTES_PER_DAY:
                self.day_mode[d].set("free")
            else:
                self.day_mode[d].set("after")
                self.day_hour[d].set(max(0, min(23, win[0] // 60)))
        self._planner_changed()

    def _planner_schedule(self) -> dict:
        free: dict[str, list[int]] = {}
        for d in range(7):
            m = self.day_mode[d].get()
            if m == "free":
                free[str(d)] = [0, schedule.MINUTES_PER_DAY]
            elif m == "after":
                try:
                    h = max(0, min(23, int(self.day_hour[d].get())))
                except (tk.TclError, ValueError):
                    h = 15
                free[str(d)] = [h * 60, schedule.MINUTES_PER_DAY]
        try:
            weeks = max(1, min(schedule.MAX_WEEKS, int(self.weeks_var.get())))
        except (tk.TclError, ValueError):
            weeks = 1
        return {"kind": "weekly", "weeks": weeks, "free": free}

    def _planner_changed(self):
        if not getattr(self, "planner_summary", None):
            return
        sched = self._planner_schedule()
        for d in range(7):
            m = self.day_mode[d].get()
            self.day_steppers[d].set_enabled(m == "after")
            self.day_hint[d].configure(text={"block": "blocked all day", "free": "nothing blocked",
                                             "after": f"blocked until {self.day_hour[d].get():02d}:00"}.get(m, ""))
        text = schedule.describe(sched)
        if not schedule.has_blocked_time(sched):
            self.planner_summary.configure(text=text + " Add at least one blocked day.", fg=self.theme["danger"])
        else:
            self.planner_summary.configure(text=text, fg=self.theme["muted"])
        end = datetime.now() + timedelta(weeks=sched["weeks"])
        self.weeks_label.configure(text=f"Ends for good {end.strftime('%a %d %b %Y, %H:%M')}")

    def _reset_planner(self):
        self._set_planner(schedule.default_weekly(self.weeks_var.get() if self.weeks_var.get() else 1))

    def _mode_changed(self):
        mode = self.mode_var.get()
        for w in (self.f_timer, self.f_long, self.f_datetime, self.f_long_note, self.f_weekly):
            w.pack_forget()
        if mode == "weekly":
            self.f_weekly.pack(fill="x")
            self._planner_changed()
        elif mode == "long":
            self.f_long.pack(fill="x")
            self.f_datetime.pack(fill="x")
            self.f_long_note.pack(fill="x", pady=(10, 0))
        else:
            self.f_timer.pack(fill="x")
            self.f_datetime.pack(fill="x")

    def _long_pick(self):
        v = self.long_var.get()
        if v.isdigit():
            dt = (datetime.now() + timedelta(days=int(v))).replace(minute=0, second=0, microsecond=0)
            self.date_var.set(dt.strftime("%Y-%m-%d"))
            self.time_var.set(dt.strftime("%H:%M"))

    # ---- plan <-> widgets --------------------------------------------------
    def load_plan(self, plan: Plan):
        self.plan = plan
        self.goal_var.set(plan.goal)
        self.date_var.set(plan.restore_at.strftime("%Y-%m-%d"))
        self.time_var.set(plan.restore_at.strftime("%H:%M"))
        if plan.is_weekly():
            self.mode_var.set("weekly")
            self._set_planner(plan.schedule)
        else:
            self.mode_var.set("long" if plan.schedule.get("kind") == "long" else "once")
        self._mode_changed()
        for k, v in self.opt_vars.items():
            v.set(bool(plan.options.get(k)))
        self.delay_var.set(plan.options["end_delay_seconds"])
        self.pw_var.set(plan.options["pomodoro_work_min"])
        self.pb_var.set(plan.options["pomodoro_break_min"])
        self.refresh_lists()
        self.scroll.scroll_top()

    def sync_plan(self) -> bool:
        p = self.plan
        p.goal = self.goal_var.get().strip()
        if p.goal == "e.g. Finish the report draft":
            p.goal = ""
        for k, v in self.opt_vars.items():
            p.options[k] = bool(v.get())
        try:
            p.options["end_delay_seconds"] = max(0, int(self.delay_var.get()))
            p.options["pomodoro_work_min"] = max(1, int(self.pw_var.get()))
            p.options["pomodoro_break_min"] = max(1, int(self.pb_var.get()))
        except (tk.TclError, ValueError):
            messagebox.showerror(APP_NAME, "Delay and Pomodoro values must be whole numbers.", parent=self.app)
            return False
        mode = self.mode_var.get()
        if mode == "weekly":
            sched = self._planner_schedule()
            if not schedule.has_blocked_time(sched):
                messagebox.showerror(APP_NAME, "The weekly schedule has no blocked time. Mark at least one day as "
                                     "Blocked or Free from.", parent=self.app)
                return False
            p.schedule = sched
            p.restore_at = (datetime.now() + timedelta(weeks=sched["weeks"])).replace(second=0, microsecond=0)
            return True
        dt = self._parse_time()
        if not dt:
            messagebox.showerror(APP_NAME, "Enter the date as YYYY-MM-DD and the time as HH:MM.", parent=self.app)
            return False
        if dt <= datetime.now() + timedelta(seconds=30):
            messagebox.showerror(APP_NAME, "The restore time must be at least a minute in the future.", parent=self.app)
            return False
        if dt > datetime.now() + timedelta(days=366):
            messagebox.showerror(APP_NAME, "The end must be within one year.", parent=self.app)
            return False
        if mode == "once" and dt > datetime.now() + timedelta(days=14):
            if not messagebox.askyesno(APP_NAME, "That is more than two weeks away. Use the Long-term option for blocks "
                                       "like this, or continue anyway?", parent=self.app):
                return False
        p.schedule = {"kind": mode}
        p.restore_at = dt
        return True

    def refresh(self):
        self.plan.extended = bool(self.app.settings.get("extended_subdomains", True))
        self.refresh_lists()

    def refresh_lists(self):
        t = self.theme
        for w in self.chips.winfo_children():
            w.destroy()
        if not self.plan.services and not self.plan.custom:
            label(self.chips, t, "Nothing selected yet.", "small", "faint").pack(anchor="w")
        for key, s in list(self.plan.services.items()):
            Chip(self.chips, t, f"{s.name}  {len(catalog.hostnames_for(s, self.plan.extended))}",
                 on_remove=lambda k=key: self.remove_service(k)).pack(side="left", padx=(0, 6), pady=2)
        if self.plan.custom:
            Chip(self.chips, t, f"Custom  {len(self.plan.custom)}", on_remove=self.clear_custom, accent=True)\
                .pack(side="left", padx=(0, 6), pady=2)
        hosts = self.plan.hostnames()
        self.host_list.delete(0, "end")
        for h in hosts:
            self.host_list.insert("end", h)
        extra = f", {len(self.plan.removed)} removed by you" if self.plan.removed else ""
        self.host_count.configure(text=f"Hostnames to block ({len(hosts)}{extra})")
        self.file_list.delete(0, "end")
        for f in self.plan.files:
            self.file_list.insert("end", f)
        svc_apps = [p for p in self.plan.processes() if p.lower() not in {a.lower() for a in self.plan.apps}]
        self.app_list.delete(0, "end")
        for a in self.plan.apps:
            self.app_list.insert("end", a)
        for p in svc_apps:
            self.app_list.insert("end", f"{p}   (from a blocked service)")
        n = len(self.plan.processes())
        extra = f", {len(self.plan.apps)} added by you" if self.plan.apps else ""
        self.app_count.configure(text=f"Apps to close ({n}{extra})")
        self._time_changed()

    # ---- services -----------------------------------------------------------
    def find(self):
        t = self.theme
        q = self.search.value().strip()
        self._clear_results()
        if not q:
            return
        hits = catalog.search(q)[:6]
        box = Surface(self.results, t, pad=12, fill=t["surface2"], outline=t["stroke"], radius=t.ctrl_radius)
        box.pack(fill="x", pady=(10, 0))
        b = box.body
        if hits:
            label(b, t, "Matches", "small", "muted").pack(anchor="w", pady=(0, 4))
            for s in hits:
                n = len(catalog.hostnames_for(s, self.plan.extended))
                r = row(b, pady=(0, 4))
                Button(r, t, "Add", lambda s=s: self.add_service(s), kind="primary", font="small", padx=12, pady=3)\
                    .pack(side="left")
                label(r, t, s.name, "bold").pack(side="left", padx=(10, 8))
                label(r, t, f"{s.category}, {n} hostnames"
                      + (f", closes {', '.join(s.processes[:2])}" if s.processes else ""), "small", "muted")\
                    .pack(side="left")
        generic = catalog.generic_hostnames(q, self.plan.extended)
        if generic:
            r = row(b, pady=(6 if hits else 0, 0))
            Button(r, t, "Add", lambda: self.add_generic(generic), kind="standard", font="small", padx=12, pady=3)\
                .pack(side="left")
            label(r, t, f"Block \"{q}\" as a website", "bold").pack(side="left", padx=(10, 8))
            label(r, t, f"{len(generic)} guessed hostnames, e.g. {', '.join(generic[:2])}", "small", "muted",
                  wraplength=260, justify="left").pack(side="left")
        r = row(b, pady=(6, 0))
        Button(r, t, "AI Block", lambda: self.ai_block(q), kind="standard", font="small", padx=12, pady=3,
               icon=ICON["ai"]).pack(side="left")
        label(r, t, f"Let the AI build a full list for \"{q}\"", "bold").pack(side="left", padx=(10, 8))
        label(r, t, "domains, CDNs, login hosts, desktop apps and shortcuts, reviewed before use", "small", "muted",
              wraplength=260, justify="left").pack(side="left")
        Button(b, t, "Close", self._clear_results, kind="link", font="small", padx=0, pady=2).pack(anchor="e")

    def ai_block(self, query: str = ""):
        d = AIBlockDialog(self.app, on_use=self.add_ai_list)
        if query:
            d.query.set(query)

    def add_ai_list(self, key: str):
        s = catalog.get(key)
        if not s:
            return
        self.plan.services[key] = s
        add_ai_files(self.plan, key)
        self._clear_results()
        self.refresh_lists()

    def _clear_results(self):
        for w in self.results.winfo_children():
            w.destroy()

    def add_service(self, s: catalog.Service):
        self.plan.services[s.key] = s
        self.search.clear()
        self._clear_results()
        self.refresh_lists()

    def add_generic(self, hosts: list[str]):
        for h in hosts:
            if h not in self.plan.custom:
                self.plan.custom.append(h)
        self.search.clear()
        self._clear_results()
        self.refresh_lists()

    def add_preset(self, name: str):
        for k in catalog.PRESETS.get(name, []):
            s = catalog.get(k)
            if s:
                self.plan.services[k] = s
        self.refresh_lists()

    def remove_service(self, key: str):
        self.plan.services.pop(key, None)
        self.refresh_lists()

    def clear_custom(self):
        self.plan.custom.clear()
        self.refresh_lists()

    def add_custom_host(self):
        raw = self.add_host.value()
        n = hostsfile.normalise_host(raw)
        if not n:
            messagebox.showwarning(APP_NAME, f"\"{raw}\" is not a valid hostname.", parent=self.app)
            return
        if n in hostsfile.PROTECTED:
            messagebox.showwarning(APP_NAME, f"{n} is on the protected list because blocking it breaks Windows or sign-in. "
                                   "It will not be added.", parent=self.app)
            return
        if n not in self.plan.custom:
            self.plan.custom.append(n)
        self.plan.removed.discard(n)
        self.add_host.clear()
        self.refresh_lists()

    def remove_hosts(self):
        for i in self.host_list.curselection():
            self.plan.removed.add(self.host_list.get(i))
        self.refresh_lists()

    def undo_removals(self):
        self.plan.removed.clear()
        self.refresh_lists()

    # ---- apps -------------------------------------------------------------
    def _remember_apps(self):
        """The apps added by hand are the starting point of every later plan."""
        self.app.settings["remembered_apps"] = list(self.plan.apps)
        state.save_settings(self.app.settings)

    def _add_app(self, raw: str, interactive: bool = True) -> bool:
        exe = validate_app(self.app, raw, interactive)
        if not exe:
            return False
        added = self.plan.add_app(exe)
        self._remember_apps()
        self.refresh_lists()
        return added

    def add_app_name(self):
        raw = self.add_app.value().strip()
        if not raw:
            return
        if self._add_app(raw) or normalise_app_input(raw):
            self.add_app.clear()

    def browse_app(self):
        p = filedialog.askopenfilename(parent=self.app, title="Program to close during the session",
                                       filetypes=[("Programs", "*.exe"), ("All files", "*.*")])
        if p:
            self._add_app(p)

    def running_apps(self):
        """Pick from the programs running right now (system processes and the already added ones left out)."""
        running_apps_menu(self.app, self.btn_running, self.plan.processes(), self._add_app)

    def remove_apps(self):
        sel = {self.app_list.get(i).split("   (")[0] for i in self.app_list.curselection()}
        own = {a for a in self.plan.apps if a in sel}
        if len(sel) > len(own):
            messagebox.showinfo(APP_NAME, "Apps that come from a blocked service are removed together with that "
                                "service (the chips above).", parent=self.app)
        if own:
            self.plan.remove_apps(own)
            self._remember_apps()
            self.refresh_lists()

    # ---- files ------------------------------------------------------------
    def _add_paths(self, ps):
        refused = []
        for p in ps:
            p = str(Path(p))
            if not p or p in self.plan.files or not Path(p).exists():
                continue
            why = guard.forbidden_reason(p)
            if why:
                refused.append(f"{p}\n    it {why}")
                continue
            self.plan.files.append(p)
        self.refresh_lists()
        if refused:
            messagebox.showwarning(APP_NAME, "These were not added because hiding them could break Windows or this "
                                   "app:\n\n" + "\n".join(refused), parent=self.app)

    def add_files(self):
        self._add_paths(filedialog.askopenfilenames(parent=self.app, title="Files to hide"))

    def add_folder(self):
        d = filedialog.askdirectory(parent=self.app, title="Folder to hide")
        if d:
            self._add_paths([d])

    def add_desktop(self):
        d = desktop_dir()
        items = [str(p) for p in d.iterdir() if p.name.lower() != "desktop.ini"]
        if not items:
            messagebox.showinfo(APP_NAME, "The Desktop is already empty.", parent=self.app)
            return
        self._add_paths(items)

    def remove_files(self):
        sel = {self.file_list.get(i) for i in self.file_list.curselection()}
        self.plan.files = [f for f in self.plan.files if f not in sel]
        self.refresh_lists()

    # ---- time -------------------------------------------------------------
    def _quick_time(self):
        v = self.quick_var.get()
        if v == "u12":
            self.set_until(12)
        elif v == "u17":
            self.set_until(17)
        elif v == "tom":
            self.set_until(-8)
        elif v.isdigit():
            self.set_in(int(v))

    def set_in(self, minutes: int):
        dt = datetime.now() + timedelta(minutes=minutes)
        self.date_var.set(dt.strftime("%Y-%m-%d"))
        self.time_var.set(dt.strftime("%H:%M"))

    def set_until(self, hour: int):
        now = datetime.now()
        if hour < 0:
            dt = (now + timedelta(days=1)).replace(hour=-hour, minute=0, second=0, microsecond=0)
        else:
            dt = now.replace(hour=hour, minute=0, second=0, microsecond=0)
            if dt <= now:
                dt += timedelta(days=1)
        self.date_var.set(dt.strftime("%Y-%m-%d"))
        self.time_var.set(dt.strftime("%H:%M"))

    def _parse_time(self) -> datetime | None:
        try:
            return datetime.strptime(f"{self.date_var.get().strip()} {self.time_var.get().strip()}", "%Y-%m-%d %H:%M")
        except ValueError:
            return None

    def _time_changed(self):
        dt = self._parse_time()
        if not dt:
            self.ends_label.configure(text="Invalid date or time", fg=self.theme["danger"])
            return
        delta = int((dt - datetime.now()).total_seconds())
        if delta <= 0:
            self.ends_label.configure(text="That time has already passed", fg=self.theme["danger"])
        else:
            self.ends_label.configure(text=f"Ends {dt.strftime('%a %d %b, %H:%M')}, in {fmt_duration(delta)}",
                                      fg=self.theme["text"])

    # ---- actions ----------------------------------------------------------
    def clear(self):
        self.app.plan = Plan(self.app.settings)
        self.load_plan(self.app.plan)

    def save_preset(self):
        if not self.sync_plan():
            return
        name = simple_prompt(self.app, "Save preset", "Preset name")
        if not name:
            return
        presets = state.load_presets()
        presets[name] = self.plan.to_preset()
        state.save_presets(presets)
        self.app.set_status(f"Preset \"{name}\" saved", "idle")

    def review(self):
        if state.session_is_active(state.load_session()):
            messagebox.showinfo(APP_NAME, "A session is already running. End it before starting another.", parent=self.app)
            self.app.show("dashboard")
            return
        if not self.sync_plan():
            return
        if not self.plan.hostnames() and not self.plan.files and not self.plan.processes():
            messagebox.showwarning(APP_NAME, "Add at least one service, hostname, app or file first.", parent=self.app)
            return
        ReviewDialog(self.app, self.plan, self.start)

    def start(self):
        p = self.plan
        session = state.new_session(p.goal, p.restore_at, p.hostnames(),
                                    p.processes() if p.options["process_watch"] else [], p.files, list(p.services),
                                    dict(p.options), dict(p.schedule))
        paths.ensure_dirs()
        plan_path = paths.STATE_DIR / "plan.json"
        plan_path.write_text(json.dumps(session, indent=2), encoding="utf-8")

        def done(res):
            if res.get("ok"):
                if p.options.get("close_browsers") and not res.get("paused"):
                    procwatch.kill_many(procwatch.BROWSERS)
                s = state.load_session()
                if s:
                    self.app.resume_watcher(s)
                warn = res.get("warnings") or []
                if res.get("paused"):
                    nxt = schedule.next_change(p.schedule)
                    msg = ("Weekly schedule started inside a free window. Nothing is blocked until "
                           + (nxt.strftime("%A %H:%M") if nxt else "the next blocked period") + ".")
                else:
                    msg = f"Blocking {res.get('hostnames', 0)} hostnames, hid {res.get('hidden', 0)} item(s)."
                if res.get("locked"):
                    msg += f" Locked {res['locked']}."
                if not res.get("task"):
                    warn.append("The scheduled restore task could not be registered. Keep this app open, or use "
                                "Settings > Emergency restore when you finish.")
                hint = guard.install_dir_problem()
                if hint and not self.app.settings.get("install_dir_warned"):
                    # said once; the integrity check under Settings repeats it on request
                    warn.append(hint)
                    self.app.settings["install_dir_warned"] = True
                    state.save_settings(self.app.settings)
                if warn:
                    messagebox.showwarning(APP_NAME, msg + "\n\nWarnings:\n- " + "\n- ".join(warn), parent=self.app)
                self.app.toast(APP_NAME, "Focus session started. " + msg)
                self.app.plan = Plan(self.app.settings)
                self.load_plan(self.app.plan)
                self.app.show("dashboard")
            elif res.get("cancelled"):
                self.app.set_status("Start cancelled", "idle")
            else:
                messagebox.showerror(APP_NAME, "Could not start the session:\n" + res.get("error", "unknown error"),
                                     parent=self.app)

        self.app.run_elevated(["apply", str(plan_path)], done, "Applying blocks and hiding files")


# -------------------------------------------------------------------- presets
class PresetsPage(Page):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        self.scroll = Scroll(self, self.theme)
        self.scroll.pack(fill="both", expand=True)
        self.header(self.scroll.body, "Presets", "Saved combinations of services, files and options.", back=True)
        self.listwrap = tk.Frame(self.scroll.body, bg=self.theme["bg"], padx=40)
        self.listwrap.pack(fill="both", expand=True)

    def refresh(self):
        t = self.theme
        for w in self.listwrap.winfo_children():
            w.destroy()
        locked = state.session_is_active(state.load_session())
        if locked:
            infobar(self.listwrap, t, "Presets are read-only while a session is active, so the session you agreed to "
                    "cannot be changed from underneath you.", "info").pack(fill="x", pady=(0, 12))
        presets = state.load_presets()
        if not presets:
            s = Surface(self.listwrap, t, pad=20)
            s.pack(fill="x")
            title_block(s.body, t, "No presets yet",
                        "Build a session, then press \"Save as preset\" at the bottom of the builder.").pack(anchor="w")
        for name, p in presets.items():
            s = Surface(self.listwrap, t, pad=18)
            s.pack(fill="x", pady=(0, 10))
            top = row(s.body)
            label(top, t, name, "h3").pack(side="left")
            b1 = Button(top, t, "Delete", lambda n=name: self.delete(n), kind="danger_soft", font="small", padx=12, pady=4)
            b1.pack(side="right")
            b2 = Button(top, t, "Load into builder", lambda pp=p: self.load(pp), kind="primary", font="small", padx=12,
                        pady=4)
            b2.pack(side="right", padx=(0, 8))
            b1.set_enabled(not locked)
            b2.set_enabled(not locked)
            svcs = ", ".join(catalog.get(k).name for k in p.get("services", []) if catalog.get(k)) or "none"
            autowrap(label(s.body, t, f"Services: {svcs}", "small", "muted", wraplength=820, justify="left"), s.body, 8)\
                .pack(anchor="w", pady=(6, 0))
            o = p.get("options", {})
            sched = schedule.normalize(p.get("schedule"))
            if schedule.is_weekly(sched):
                length = f"weekly schedule for {sched['weeks']} week{'s' if sched['weeks'] != 1 else ''}"
            else:
                length = f"length {fmt_duration(int(p.get('duration_min', 60)) * 60)}"
            label(s.body, t, f"Custom hostnames {len(p.get('custom', []))}, files {len(p.get('files', []))}, "
                  f"{length}, strict {'yes' if o.get('strict') else 'no'}, "
                  f"locked files {'yes' if o.get('lock_files') else 'no'}", "small", "muted").pack(anchor="w")
            if schedule.is_weekly(sched):
                autowrap(label(s.body, t, schedule.describe(sched), "small", "muted", wraplength=820, justify="left"),
                         s.body, 8).pack(anchor="w")

    def load(self, p: dict):
        plan = Plan(self.app.settings)
        plan.load_preset(p)
        self.app.plan = plan
        self.app.pages["builder"].load_plan(plan)
        self.app.show("builder")

    def delete(self, name: str):
        if messagebox.askyesno(APP_NAME, f"Delete preset \"{name}\"?", parent=self.app):
            presets = state.load_presets()
            presets.pop(name, None)
            state.save_presets(presets)
            self.refresh()


# -------------------------------------------------------------------- history
class HistoryPage(Page):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        t = self.theme
        self.header(self, "Analytics", "Every session, how far you got, and your focus time over the last two weeks.")
        self.cards = tk.Frame(self, bg=t["bg"])
        self.cards.pack(fill="x")
        chart = Surface(self, t, pad=18)
        chart.pack(fill="x", padx=40, pady=(14, 0))
        label(chart.body, t, "Last 14 days", "h3").pack(anchor="w")
        self.chart = tk.Canvas(chart.body, height=120, bg=t["surface"], highlightthickness=0)
        self.chart.pack(fill="x", pady=(8, 0))
        self.chart.bind("<Configure>", lambda e: self._draw_chart(self._per_day))
        self._per_day: dict[str, int] = {}
        tw = Surface(self, t, pad=2, fill=t["surface2"], outline=t["stroke"], radius=t.ctrl_radius)
        tw.pack(fill="both", expand=True, padx=40, pady=(14, 30))
        cols = ("date", "goal", "planned", "actual", "result", "blocked", "hidden")
        self.tree = ttk.Treeview(tw.body, columns=cols, show="headings")
        for c, txt, w in (("date", "Started", 140), ("goal", "Goal", 320), ("planned", "Planned", 90),
                          ("actual", "Actual", 90), ("result", "Result", 110), ("blocked", "Hosts", 70), ("hidden", "Files", 70)):
            self.tree.heading(c, text=txt)
            self.tree.column(c, width=w, anchor="w", stretch=(c == "goal"))
        self.tree.pack(fill="both", expand=True, padx=8, pady=8)
        tw.configure(height=320)

    def refresh(self):
        hist = state.load_history()
        st = state.stats(hist)
        for w in self.cards.winfo_children():
            w.destroy()
        self.tiles(self.cards, [("Today", fmt_duration(st["today_seconds"]), ICON["clock"]),
                                ("Total focus", fmt_duration(st["total_seconds"]), ICON["stopwatch"]),
                                ("Completed", str(st["completed"]), ICON["check"]),
                                ("Ended early", str(st["early"]), ICON["stop"]),
                                ("Day streak", str(st["streak_days"]), ICON["bolt"])])
        self._per_day = st["per_day"]
        self._draw_chart(self._per_day)
        for i in self.tree.get_children():
            self.tree.delete(i)
        for e in reversed(hist):
            if not e.get("started"):
                continue
            start = state.parse_iso(e["started"])
            end = state.parse_iso(e.get("ended") or e["restore_at"])
            planned = state.planned_duration_seconds(e)
            actual = max(0, int((end - start).total_seconds()))
            result = {"early": "Ended early", "scheduled": "Completed", "manual": "Completed", "startup": "Recovered",
                      "orphan-cleanup": "Cleaned up", "enforce-cleanup": "Cleaned up"}.get(e.get("end_reason"),
                                                                                           e.get("end_reason") or "")
            self.tree.insert("", "end", values=(start.strftime("%Y-%m-%d %H:%M"), e.get("goal") or "",
                                                fmt_duration(planned), fmt_duration(actual), result,
                                                len(e.get("hostnames", [])), len(e.get("hidden_files", []))))

    def _draw_chart(self, per_day: dict[str, int]):
        t = self.theme
        c = self.chart
        c.delete("all")
        w = max(c.winfo_width(), 400)
        h = 120
        days = [(datetime.now().date() - timedelta(days=i)) for i in range(13, -1, -1)]
        vals = [per_day.get(d.strftime("%Y-%m-%d"), 0) for d in days]
        mx = max(vals + [1800])
        bw = (w - 20) / 14
        for i, (d, v) in enumerate(zip(days, vals)):
            x0 = 10 + i * bw + 5
            x1 = x0 + bw - 10
            bh = max(6, (v / mx) * (h - 36))
            if v:
                gradient_shape(c, x0, h - 22 - bh, x1, h - 22, 5, t["grad2"], t["grad1"], vertical=True)
            else:
                rounded(c, x0, h - 22 - bh, x1, h - 22, 3, fill=t["surface3"], outline="")
            c.create_text((x0 + x1) / 2, h - 9, text=d.strftime("%d"), fill=t["muted"], font=t.fonts["tiny"])
            if v:
                c.create_text((x0 + x1) / 2, h - 30 - bh, text=fmt_duration(v).split(" ")[0]
                              + ("h" if v >= 3600 else ""), fill=t["muted"], font=t.fonts["tiny"])


# ----------------------------------------------------------------------- help
class HelpPage(Page):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        t = self.theme
        sc = Scroll(self, t)
        sc.pack(fill="both", expand=True)
        f = sc.body
        self.header(f, "Help", "How DeepFocus AI works, how it keeps your PC safe, and where to look if something is off.")

        def card(glyph: str, title: str, body: str) -> tk.Frame:
            s = Surface(f, t, pad=20)
            s.pack(fill="x", padx=40, pady=(0, 12))
            hdr = row(s.body)
            tk.Label(hdr, text=glyph, bg=t["surface"], fg=t["accent"], font=t.fonts["icon_l"]).pack(side="left")
            label(hdr, t, title, "h3").pack(side="left", padx=(10, 0))
            autowrap(label(s.body, t, body, "small", "muted", wraplength=780, justify="left"), s.body, 8, 780)\
                .pack(anchor="w", pady=(8, 0))
            return s.body

        card(ICON["play"], "Starting a session",
             "Focus session: type a goal, pick what to block and for how long, press Start. New session: the full "
             "builder with services, hostnames, files to hide, the three length modes (Timer, Long-term, Weekly "
             "schedule) and all options. Every domain and file is listed for review before anything changes, and "
             "the Review screen explains exactly why the single administrator prompt is needed.")
        card(ICON["ai"], "AI Block",
             "Type the name of an app or service (Discord, Steam, TikTok) and the AI proposes a complete block "
             "list: the main domains, API and content hosts, sign-in and download domains, the desktop app with its "
             "launcher and updater processes, and shortcuts or installers to hide. Every entry is shown with a "
             "reason and a confidence; shared infrastructure that could affect other sites starts unchecked, and "
             "protected Windows, sign-in and AI-provider names are refused outright. Nothing is blocked until you "
             "review the list and save it. Saved lists appear in the Block dropdown and work with timers, long-term "
             "blocks and weekly schedules like any built-in service. Only the name you type is sent to the AI; the "
             "key lives in the Windows Credential Manager. Without a key, the built-in catalog is used instead.")
        card(ICON["chat"], "Assistant",
             "A chat with your own AI, using the provider and key from Settings > AI Block. Ask it to plan your "
             "afternoon, break a task into Pomodoro chunks, talk through what keeps distracting you, or explain a "
             "feature. Replies stream in and the conversation stays on this PC. By default the assistant also sees a "
             "short summary of the current session, your focus stats and the names of your saved lists (never file "
             "paths or hostnames); turn that off in Settings. Enter sends, Shift+Enter adds a line, Ctrl+7 opens it.")
        card(ICON["globe"], "Changing a running session",
             "The session screen's Details card has two buttons. Add to block takes more services or hostnames: "
             "pick them like in the builder, see exactly which ones are new, then Windows asks once for "
             "administrator approval and the hosts file is rewritten (during a free window the names are recorded "
             "and blocked when the window ends). Apps to close adds or removes programs that are closed while this "
             "window is open; no approval is needed and the apps you add are remembered for your next session. "
             "Nothing can be removed from a running block: that is the point.")
        card(ICON["calendar"], "Long-term blocks and weekly schedules",
             "Long-term blocks run for days to months. A weekly schedule stays on all week and lifts only in the "
             "free windows you choose per weekday (the default is Friday from 15:00 plus Saturday and Sunday). "
             "Windows switches the blocks off and on within a minute, whether or not this app is open, and hidden "
             "files come back during free windows. The schedule ends for good after the chosen number of weeks.")
        card(ICON["shield"], "How it keeps you safe",
             "Two Windows scheduled tasks run independently of this window: one restores everything at the end "
             "time, the other checks every minute that the block is still in place, undoes tampering, and restores "
             "as a backup. A crash, a reboot or closing the app can never leave anything blocked or hidden. Hidden "
             "files are never moved or modified, the hosts file is backed up before each change and written "
             "atomically, and paths inside Windows, Program Files, AppData or your profile root, or reached through "
             "a symbolic link or junction, are refused. The elevated helper re-checks every hostname, path and "
             "option in the plan itself and refuses a state folder that has been replaced by a link. Settings and "
             "presets are read-only while a session runs, and ending early always needs the cooling-off delay. "
             "AI features send only what you type (plus the optional session summary), talk to the provider over "
             "https only, cap response sizes, and keep the key in the Windows Credential Manager.")
        b = card(ICON["warning"], "If something looks wrong",
                 "Settings > Check status reports the hosts block, both scheduled tasks and the session record. "
                 "Emergency restore removes every DeepFocus block, unlocks and unhides all recorded files, restores "
                 "the browser DoH policies and deletes the tasks. From outside the app, run EmergencyRestore.cmd in "
                 "the app folder, or \"DeepFocus.exe helper restore\" from an elevated prompt.")
        br = row(b, pady=(12, 0))
        Button(br, t, "Emergency restore", lambda: app.pages["settings"].emergency(), kind="danger_soft", padx=14, pady=6,
               icon=ICON["warning"]).pack(side="left")
        Button(br, t, "Open log folder", lambda: open_folder(paths.STATE_DIR), kind="standard", padx=14, pady=6,
               icon=ICON["folder"]).pack(side="left", padx=(8, 0))
        card(ICON["info"], "Shortcuts and details",
             "Ctrl+1 to Ctrl+7 switch pages. Launching the app again brings the open window forward. "
             f"State lives in {paths.STATE_DIR}. Version {__version__}.")
        tk.Frame(f, bg=t["bg"], height=20).pack()


# ------------------------------------------------------------------ assistant
class ChatPage(Page):
    """Chat with the user's own AI (the provider and key from Settings > AI Block). Replies stream in; the
    conversation is kept on disk; optional context about the current session goes into the system prompt."""

    SYSTEM = ("You are the assistant built into DeepFocus AI, a Windows focus app. The app blocks distracting "
              "websites and desktop apps for timed sessions, long-term blocks (days to months) and weekly schedules "
              "with free windows; it hides chosen files for the session, can turn off Windows notifications, plays "
              "focus sounds, and has an AI Block feature that builds block lists for any service name. Everything is "
              "restored automatically by Windows scheduled tasks. Help the user plan work, stay focused, think through "
              "distractions and use the app well. Be concise and concrete: short paragraphs or simple bullet lines, "
              "no markdown headings or tables. You cannot change settings or start sessions yourself; when something "
              "needs the app, name the exact button or page (for example the Start Focus Session button, the Weekly "
              "schedule chip, Settings > AI Block).")
    QUICK = ["Help me plan the next two hours",
             "I keep getting distracted. What should I block?",
             "Break what I'm working on into 25-minute chunks",
             "How do weekly schedules work in this app?"]
    HISTORY_TURNS = 24  # messages sent to the model

    def __init__(self, parent, app):
        super().__init__(parent, app)
        t = self.theme
        hdr = self.header(self, "Assistant", "Chat with your own AI about focus, planning and this app. It uses the "
                                             "provider and key from Settings > AI Block.")
        hdr.pack_configure(pady=(22, 10))
        self.scroll = Scroll(self, t)
        self.scroll.canvas.configure(height=140)  # ask for little; expand gives it the rest, the input bar always fits
        self.scroll.pack(fill="both", expand=True, padx=(40, 24))
        bar = tk.Frame(self, bg=t["bg"], padx=40, pady=12)
        bar.pack(fill="x", side="bottom")
        self.box = TextArea(bar, t, height=3)
        self.box.pack(side="left", fill="x", expand=True)
        self.box.text.bind("<Return>", self._enter)
        self.box.text.bind("<Shift-Return>", lambda e: None)
        self.box.text.bind("<Control-Return>", self._enter)
        btns = tk.Frame(bar, bg=t["bg"])
        btns.pack(side="left", padx=(10, 0), anchor="n")
        self.send_btn = Button(btns, t, "Send", self.send, kind="primary", padx=16, pady=7, icon=ICON["send"])
        self.send_btn.pack(fill="x")
        self.stop_btn = Button(btns, t, "Stop", self.stop, kind="standard", padx=16, pady=5, font="small")
        self.new_btn = Button(btns, t, "New chat", self.new_chat, kind="subtle", padx=10, pady=5, font="small")
        self.new_btn.pack(fill="x", pady=(6, 0))
        self.status = label(self, t, "", "tiny", "faint", wraplength=800, justify="left")
        self.status.pack(side="bottom", anchor="w", padx=40, pady=(0, 8))
        self.messages: list[dict] = state.load_chat()
        self._busy = False
        self._cancel: threading.Event | None = None
        self._pending: list[str] = []
        self._flush_job = None
        self._live_label: tk.Label | None = None
        self._resize_job = None
        self._width = 0
        self.bind("<Configure>", self._resized)
        self._render_all()

    def _resized(self, e):
        """Bubbles and the status line wrap to the page width; re-lay out after a resize settles."""
        if e.widget is not self or e.width == self._width:
            return
        self._width = e.width
        self.status.configure(wraplength=max(300, e.width - 80))
        if self._resize_job:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(150, self._rerender_after_resize)

    def _rerender_after_resize(self):
        self._resize_job = None
        if not self._busy and self.winfo_exists():
            self._render_all()

    def _wrap(self) -> int:
        w = self.winfo_width()
        return max(260, int((w - 64) * 0.72)) if w > 100 else 560

    # ---- rendering --------------------------------------------------------
    def refresh(self):
        cfg = self.app.llm_config()
        p = llm.PROVIDERS.get(cfg.provider, llm.PROVIDERS["anthropic"])
        if cfg.api_key:
            self.status.configure(text=f"{p['label']}, model {cfg.resolved_model()}. "
                                       + ("Session context is shared with the assistant." if
                                          self.app.settings.get("chat_share_context", True) else
                                          "Session context is not shared (Settings).") + " Enter sends, Shift+Enter "
                                       "for a new line. Conversations stay on this PC.", fg=self.theme["faint"])
        else:
            self.status.configure(text="No API key yet. Add one under Settings > AI Block to chat with your AI.",
                                  fg=self.theme["warn"])
        self.after(100, lambda: self.box.text.focus_set())

    def _render_all(self):
        for w in self.scroll.body.winfo_children():
            w.destroy()
        t = self.theme
        f = self.scroll.body
        if not self.messages:
            s = Surface(f, t, pad=20)
            s.pack(fill="x", pady=(4, 12))
            hl = row(s.body)
            tk.Label(hl, text=ICON["chat"], bg=t["surface"], fg=t["accent"], font=t.fonts["icon_l"]).pack(side="left")
            label(hl, t, "Ask anything about focus, planning or this app", "h3").pack(side="left", padx=(10, 0))
            label(s.body, t, "Replies come from the AI provider you set up under Settings > AI Block. Only your "
                             "messages and, if enabled, a short summary of the current session and your stats are "
                             "sent. Try one of these to start:", "small", "muted", wraplength=self._wrap() + 80,
                  justify="left").pack(anchor="w", pady=(8, 10))
            for q in self.QUICK:
                Button(s.body, t, q, lambda q=q: self.send(q), kind="standard", font="small", padx=12, pady=5)\
                    .pack(anchor="w", pady=2)
        for m in self.messages:
            self._bubble(m["role"], m["content"])
        self._scroll_bottom()

    def _bubble(self, role: str, text: str) -> tk.Label:
        t = self.theme
        text = aiblock.clean_display(text)
        wrap = tk.Frame(self.scroll.body, bg=t["bg"])
        wrap.pack(fill="x", pady=4)
        wl = self._wrap()
        if role == "user":
            s = Surface(wrap, t, pad=12, fill=t["accent_soft"], outline=blend(t["accent_soft"], t["accent"], 0.3),
                        radius=14, fit=True)
            s.pack(side="right", padx=(40, 8))
            lbl = label(s.body, t, text, "body", wraplength=wl - 60, justify="left")
            lbl.pack(anchor="w")
        else:
            s = Surface(wrap, t, pad=14, radius=14, fit=True)
            s.pack(side="left", padx=(0, 40))
            hl = row(s.body)
            tk.Label(hl, text=ICON["ai"], bg=t["surface"], fg=t["accent"], font=t.fonts["icon_s"]).pack(side="left")
            label(hl, t, "Assistant", "tiny", "faint").pack(side="left", padx=(6, 0))
            lbl = label(s.body, t, text, "body", wraplength=wl, justify="left")
            lbl.pack(anchor="w", pady=(4, 0))
        return lbl

    def _note(self, text: str, kind: str = "warn"):
        autowrap(label(self.scroll.body, self.theme, text, "small", kind, wraplength=700, justify="left"),
                 self.scroll.body, 40, 700).pack(anchor="w", pady=4)
        self._scroll_bottom()

    def _scroll_bottom(self):
        self.after_idle(lambda: self.scroll.winfo_exists() and self.scroll.scroll_to(1.0))

    # ---- sending ----------------------------------------------------------
    def _enter(self, _e=None):
        self.send()
        return "break"

    def _system_prompt(self) -> str:
        if self.app.settings.get("chat_share_context", True):
            return self.SYSTEM + "\n\nCurrent state of the app:\n" + self.app.chat_context()
        return self.SYSTEM

    def send(self, text: str | None = None):
        if self._busy:
            return
        text = aiblock.clean_display(text if text is not None else self.box.text.get("1.0", "end-1c"),
                                     llm.MAX_MESSAGE_CHARS + 1).strip()
        if not text:
            return
        if len(text) > llm.MAX_MESSAGE_CHARS:
            self._note(f"That message is too long (over {llm.MAX_MESSAGE_CHARS:,} characters). Shorten it first.")
            return
        cfg = self.app.llm_config()
        if not cfg.api_key:
            self._note("No API key is set. Add one under Settings > AI Block, then send again.")
            return
        problem = llm.validate_base_url(cfg.base_url)
        if problem:
            self._note(problem + " Fix the endpoint under Settings > AI Block.")
            return
        if not self.messages:
            self._render_all_clear()
        self.box.text.delete("1.0", "end")
        self.messages.append({"role": "user", "content": text, "time": state.now_iso()})
        self.messages = self.messages[-state.CHAT_KEEP:]
        state.save_chat(self.messages)
        self._bubble("user", text)
        self._live_label = self._bubble("assistant", "...")
        self._pending = []
        self._busy = True
        self._cancel = llm.Cancel()
        self.send_btn.set_enabled(False)
        self.stop_btn.pack(fill="x", pady=(6, 0), before=self.new_btn)
        self._scroll_bottom()
        history = [{"role": m["role"], "content": m["content"]} for m in self.messages[-self.HISTORY_TURNS:]]
        system = self._system_prompt()
        cancel = self._cancel

        def work():
            try:
                reply, err = llm.chat_stream(cfg, system, history, self._on_text, cancel), None
            except llm.LLMError as e:
                reply, err = "".join(self._pending_all), e
            except Exception as e:  # never let a surprise take the page down
                reply, err = "".join(self._pending_all), llm.LLMError("server", f"Unexpected error: {type(e).__name__}")
            try:
                self.after(0, lambda: self._done(reply, err, cancel))
            except (RuntimeError, tk.TclError):
                pass  # the window was closed while the reply was streaming
        self._pending_all: list[str] = []
        threading.Thread(target=work, daemon=True).start()

    def _render_all_clear(self):
        for w in self.scroll.body.winfo_children():
            w.destroy()

    def _on_text(self, fragment: str):
        # worker thread: queue the fragment, let the UI thread flush it in batches
        self._pending.append(fragment)
        self._pending_all.append(fragment)
        if self._flush_job is None:
            try:
                self._flush_job = self.after(60, self._flush)
            except (RuntimeError, tk.TclError):
                pass

    def _flush(self):
        self._flush_job = None
        if not self._live_label or not self._live_label.winfo_exists():
            return
        self._live_label.configure(text=aiblock.clean_display("".join(self._pending_all)))
        self._scroll_bottom()

    def _done(self, reply: str, err, cancel):
        if not self.winfo_exists():
            return
        if self._flush_job:
            self.after_cancel(self._flush_job)
            self._flush_job = None
        self._busy = False
        self.send_btn.set_enabled(True)
        self.stop_btn.pack_forget()
        reply = aiblock.clean_display(reply or "").strip()
        if self._live_label and self._live_label.winfo_exists():
            if reply:
                self._live_label.configure(text=reply + (" [stopped]" if cancel.is_set() else ""))
            else:
                self._live_label.master.master.master.destroy()  # the empty bubble (label > body > Surface > wrap)
        if reply:
            self.messages.append({"role": "assistant", "content": reply, "time": state.now_iso()})
            self.messages = self.messages[-state.CHAT_KEEP:]
            state.save_chat(self.messages)
        if err is not None:
            msg = err.message if isinstance(err, llm.LLMError) else str(err)
            self._note(msg if reply else f"{msg} Your message was not answered; send it again.")
        self._scroll_bottom()
        self.box.text.focus_set()

    def stop(self):
        if self._cancel:
            self._cancel.set()

    def new_chat(self):
        if self._busy:
            self.stop()
        self.messages = []
        state.save_chat([])
        self._render_all()


# ------------------------------------------------------------------- settings
class SettingsPage(Page):
    def __init__(self, parent, app):
        super().__init__(parent, app)
        t = self.theme
        self.scroll = Scroll(self, t)
        self.scroll.pack(fill="both", expand=True)
        body = self.scroll.body
        self.header(body, "Settings")
        s = app.settings
        self.lockable: list = []

        self.lock_bar = infobar(body, t, "A session is active. Defaults that affect how a session can be ended are "
                                "read-only until it finishes. Emergency restore stays available.", "info", wrap=760)
        # packed/unpacked in refresh()

        self._cards: list[Surface] = []

        def card(title: str, sub: str = "") -> tk.Frame:
            sf = Surface(body, t, pad=20)
            sf.pack(fill="x", padx=40, pady=(0, 12))
            title_block(sf.body, t, title, sub).pack(anchor="w")
            self._cards.append(sf)
            return sf.body

        c = card("Appearance")
        ar = row(c, pady=(12, 0))
        label(ar, t, "App theme", "body").pack(side="left")
        self.theme_var = tk.StringVar(value=s.get("theme", "system"))
        Segmented(ar, t, [("system", "Use Windows setting"), ("light", "Light"), ("dark", "Dark")], self.theme_var,
                  command=lambda: app.after(150, lambda: app.apply_theme(self.theme_var.get()))).pack(side="right")
        divider(c, t, pady=(10, 10))
        ar2 = row(c)
        self.accent_var = tk.BooleanVar(value=bool(s.get("system_accent", False)))
        label(ar2, t, "Use the Windows accent colour", "body").pack(side="left")
        Toggle(ar2, t, self.accent_var, command=self._accent_changed).pack(side="right")

        c = card("Defaults for new sessions")
        g = tk.Frame(c, bg=t["surface"])
        g.pack(fill="x", pady=(12, 0))
        self.vars: dict[str, tk.Variable] = {}
        toggles = [
            ("extended_subdomains", "Extended subdomain coverage",
             "Also block api., cdn., static., login. and similar subdomains of each service.", False),
            ("ipv6", "Add IPv6 entries", "Writes a ::1 line next to each 0.0.0.0 line.", False),
            ("doh_off_default", "Turn off browser DNS-over-HTTPS during sessions",
             "Stops Firefox, Chrome, Edge and Brave from resolving names around the hosts file.", True),
            ("lock_files_default", "Strict hide by default (deny reading hidden files)", "", True),
            ("strict_default", "Strict mode on by default", "", True),
            ("process_watch_default", "Close distracting desktop apps by default", "", False),
            ("close_browsers_default", "Close browsers at session start by default", "", False),
            ("pomodoro_default", "Pomodoro reminders on by default", "", False),
            ("block_notifications_default", "Block Windows notifications during sessions by default",
             "The Windows notifications switch is turned off for the session and restored afterwards.", True),
            ("notifications", "Windows notifications", "Session start, end, and Pomodoro phases.", False),
        ]
        for i, (key, title, desc, lock) in enumerate(toggles):
            v = tk.BooleanVar(value=bool(s.get(key)))
            self.vars[key] = v
            tg = Toggle(g, t, v, command=self.save)
            tg.grid(row=i, column=0, sticky="nw", pady=6)
            if lock:
                self.lockable.append(tg)
            tf = tk.Frame(g, bg=t["surface"])
            tf.grid(row=i, column=1, sticky="w", padx=(12, 0), pady=4)
            label(tf, t, title, "body").pack(anchor="w")
            if desc:
                autowrap(label(tf, t, desc, "small", "muted", wraplength=700, justify="left"), tf.master, 84, 700)\
                .pack(anchor="w")
        nr = tk.Frame(c, bg=t["surface"])
        nr.pack(fill="x", pady=(14, 0))
        for i, (key, txt, lo, hi, stp, suf, lock) in enumerate((
                ("end_delay_seconds", "End-early delay", 0, 600, 5, " s", True),
                ("default_duration_min", "Default length", 5, 1440, 5, " min", False),
                ("pomodoro_work_min", "Pomodoro work", 5, 120, 5, " min", False),
                ("pomodoro_break_min", "Pomodoro break", 1, 60, 1, " min", False))):
            v = tk.IntVar(value=int(s.get(key)))
            self.vars[key] = v
            label(nr, t, txt, "small", "muted").grid(row=i // 2, column=(i % 2) * 2, sticky="w",
                                                     padx=(0 if i % 2 == 0 else 28, 8), pady=4)
            sp = Stepper(nr, t, v, lo, hi, stp, suf, command=self.save)
            sp.grid(row=i // 2, column=(i % 2) * 2 + 1, sticky="w", pady=4)
            if lock:
                self.lockable.append(sp)

        c = card("AI Block", "AI Block asks a language model for the domains, apps and files of a service you name. "
                             "Only the name you type is sent. The key is stored in the Windows Credential Manager, "
                             "never in a file.")
        pr = row(c, pady=(12, 0))
        label(pr, t, "Provider", "body").pack(side="left")
        self.ai_provider = tk.StringVar(value=s.get("ai_provider", "anthropic"))
        Segmented(pr, t, [(k, v["label"]) for k, v in llm.PROVIDERS.items()], self.ai_provider,
                  command=self._ai_provider_changed).pack(side="right")
        g2 = tk.Frame(c, bg=t["surface"])
        g2.pack(fill="x", pady=(8, 0))
        g2.columnconfigure(1, weight=1)
        label(g2, t, "Model", "small", "muted").grid(row=0, column=0, sticky="w", padx=(0, 10), pady=4)
        self.ai_model = tk.StringVar(value=s.get("ai_model", ""))
        self.ai_model_field = Field(g2, t, textvariable=self.ai_model, placeholder="")
        self.ai_model_field.grid(row=0, column=1, sticky="ew", pady=4)
        label(g2, t, "Endpoint", "small", "muted").grid(row=1, column=0, sticky="w", padx=(0, 10), pady=4)
        self.ai_base = tk.StringVar(value=s.get("ai_base_url", ""))
        self.ai_base_field = Field(g2, t, textvariable=self.ai_base, placeholder="")
        self.ai_base_field.grid(row=1, column=1, sticky="ew", pady=4)
        label(g2, t, "API key", "small", "muted").grid(row=2, column=0, sticky="w", padx=(0, 10), pady=4)
        self.ai_key = tk.StringVar()
        self.ai_key_field = Field(g2, t, textvariable=self.ai_key, placeholder="")
        self.ai_key_field.entry.configure(show="•")
        self.ai_key_field.grid(row=2, column=1, sticky="ew", pady=4)
        kr = row(c, pady=(10, 0))
        Button(kr, t, "Save key", self._ai_save_key, kind="primary", font="small", padx=12, pady=4, icon=ICON["key"])\
            .pack(side="left")
        Button(kr, t, "Remove key", self._ai_remove_key, kind="standard", font="small", padx=12, pady=4)\
            .pack(side="left", padx=6)
        Button(kr, t, "Test connection", self._ai_test, kind="standard", font="small", padx=12, pady=4,
               icon=ICON["refresh"]).pack(side="left")
        self.ai_status = autowrap(label(c, t, "", "small", "muted", wraplength=760, justify="left"), c, 8, 760)
        self.ai_status.pack(anchor="w", pady=(8, 0))
        divider(c, t, pady=(12, 10))
        cr = row(c)
        self.chat_ctx_var = tk.BooleanVar(value=bool(s.get("chat_share_context", True)))
        Toggle(cr, t, self.chat_ctx_var, command=self._chat_ctx_changed).pack(side="right", anchor="n")  # first: never squeezed out
        ct = tk.Frame(cr, bg=t["surface"])
        ct.pack(side="left", fill="x", expand=True)
        label(ct, t, "Assistant: share the current session and your stats", "body").pack(anchor="w")
        autowrap(label(ct, t, "Adds the active session (goal, what is blocked, time left), today's focus time and the "
                              "names of your saved lists to the assistant's instructions. Never file paths or "
                              "hostnames.", "small", "muted", wraplength=640, justify="left"), cr, 70, 640)\
            .pack(anchor="w")
        self.ai_model.trace_add("write", lambda *_: self._ai_fields_changed())
        self.ai_base.trace_add("write", lambda *_: self._ai_fields_changed())
        self._ai_refresh_hints()

        c = card("Safety and recovery")
        label(c, t, f"State folder  {paths.STATE_DIR}\nHosts file  {paths.HOSTS_PATH}\n"
              f"Scheduled tasks  {paths.TASK_NAME}, {paths.ENFORCE_TASK_NAME}",
              "small", "muted", justify="left").pack(anchor="w", pady=(10, 10))
        self.integrity = autowrap(label(c, t, "", "small", "muted", wraplength=800, justify="left"), c, 8, 800)
        self.integrity.pack(anchor="w", pady=(0, 10))
        r = row(c)
        Button(r, t, "Check status", self.check, kind="standard", font="small", padx=12, pady=4, icon=ICON["refresh"])\
            .pack(side="left")
        Button(r, t, "Open state folder", lambda: open_folder(paths.STATE_DIR), kind="standard", font="small", padx=12,
               pady=4).pack(side="left", padx=6)
        Button(r, t, "Open hosts backups", lambda: open_folder(paths.BACKUP_DIR), kind="standard", font="small", padx=12,
               pady=4).pack(side="left")
        Button(r, t, "Emergency restore now", self.emergency, kind="danger_soft", font="small", padx=12, pady=4)\
            .pack(side="right")
        label(c, t,
              "Emergency restore removes any DeepFocus block from the hosts file, unlocks and unhides every recorded "
              "file, puts browser DoH policies back, deletes both scheduled tasks and archives the session. It is safe "
              "to run at any time, even with no session. It asks for administrator permission.",
              "tiny", "faint", wraplength=800, justify="left").pack(anchor="w", pady=(10, 0))
        autowrap(c.winfo_children()[-1], c, 8, 800)

        c = card("What is protected, and what is not")
        label(c, t,
              "The hosts block is re-applied within a minute if it is edited by hand, and both scheduled tasks are "
              "re-created if deleted while a session is active. Anyone with administrator rights can still stop a "
              "session: this is a focus aid for you, not a parental control.\n"
              "Paths inside Windows, Program Files, ProgramData, AppData, the profile root, the Desktop folder itself "
              "and this app are refused, so a mistake can never hide what the system or the restore depends on.\n"
              "Hidden files stay visible to anyone who turns off \"Hide protected operating system files\" in Explorer "
              "unless Strict hide is on. Services that stream from random subdomains may keep an open stream alive.",
              "small", "muted", wraplength=800, justify="left").pack(anchor="w", pady=(10, 0))
        autowrap(c.winfo_children()[-1], c, 8, 800)
        tk.Frame(body, bg=t["bg"], height=16).pack()

    def refresh(self):
        active = state.session_is_active(state.load_session())
        for w in self.lockable:
            w.set_enabled(not active)
        if active:
            self.lock_bar.pack(fill="x", padx=40, pady=(0, 12), before=self._cards[0])
        else:
            self.lock_bar.pack_forget()
        self.check()

    def _accent_changed(self):
        self.app.settings["system_accent"] = bool(self.accent_var.get())
        state.save_settings(self.app.settings)
        self.app.after(150, lambda: self.app.rebuild_theme("settings"))

    # ---- AI Block settings --------------------------------------------------
    def _ai_refresh_hints(self):
        p = llm.PROVIDERS.get(self.ai_provider.get(), llm.PROVIDERS["anthropic"])
        for fld, hint in ((self.ai_model_field, f"default: {p['default_model']}"),
                          (self.ai_base_field, f"default: {p['base_url']}"),
                          (self.ai_key_field, p["key_hint"])):
            fld.placeholder = hint
            if not fld.value():
                fld.set("")
        try:
            has = credstore.has(self.ai_provider.get())
        except Exception:
            has = False
        self.ai_status.configure(text=(f"A key for {p['label']} is stored in the Windows Credential Manager." if has
                                       else f"No key stored for {p['label']} yet. AI Block falls back to the "
                                            "built-in catalog until one is saved."), fg=self.theme["muted"])

    def _chat_ctx_changed(self):
        self.app.settings["chat_share_context"] = bool(self.chat_ctx_var.get())
        state.save_settings(self.app.settings)

    def _ai_provider_changed(self):
        self.app.settings["ai_provider"] = self.ai_provider.get()
        state.save_settings(self.app.settings)
        self._ai_refresh_hints()

    def _ai_fields_changed(self):
        self.app.settings["ai_model"] = self.ai_model_field.value().strip()[:120]
        base = self.ai_base_field.value().strip()[:300]
        self.app.settings["ai_base_url"] = base
        state.save_settings(self.app.settings)
        problem = llm.validate_base_url(base)
        if problem:
            self.ai_status.configure(text=problem, fg=self.theme["warn"])

    def _ai_save_key(self):
        key = self.ai_key_field.value().strip()
        if not key:
            self.ai_status.configure(text="Paste the API key into the field first.", fg=self.theme["warn"])
            return
        try:
            credstore.save(self.ai_provider.get(), key)
        except OSError as e:
            self.ai_status.configure(text=f"Windows refused to store the key: {e}", fg=self.theme["danger"])
            return
        self.ai_key_field.set("")
        self._ai_refresh_hints()
        self.ai_status.configure(text="Key saved to the Windows Credential Manager (Control Panel > Credential "
                                      "Manager > Windows Credentials > DeepFocus AI).", fg=self.theme["muted"])

    def _ai_remove_key(self):
        try:
            removed = credstore.delete(self.ai_provider.get())
        except Exception:
            removed = False
        self._ai_refresh_hints()
        if removed:
            self.ai_status.configure(text="Key removed.", fg=self.theme["muted"])

    def _ai_test(self):
        cfg = self.app.llm_config()
        typed = self.ai_key_field.value().strip()
        if typed:
            cfg.api_key = typed  # test what is in the box before saving it
        if not cfg.api_key:
            self.ai_status.configure(text="No key to test. Paste one and press Test connection.", fg=self.theme["warn"])
            return
        problem = llm.validate_base_url(cfg.base_url)
        if problem:
            self.ai_status.configure(text=problem, fg=self.theme["warn"])
            return
        self.ai_status.configure(text="Testing the connection...", fg=self.theme["faint"])

        def work():
            try:
                msg, kind = llm.test_connection(cfg), "muted"
            except llm.LLMError as e:
                msg, kind = e.message, "warn"
            except Exception as e:
                msg, kind = f"Unexpected error: {type(e).__name__}", "danger"
            try:
                self.after(0, lambda: self.winfo_exists() and self.ai_status.configure(text=msg, fg=self.theme[kind]))
            except (RuntimeError, tk.TclError):
                pass
        threading.Thread(target=work, daemon=True).start()

    def save(self):
        for k, v in self.vars.items():
            try:
                self.app.settings[k] = v.get()
            except tk.TclError:
                pass
        state.save_settings(self.app.settings)

    def check(self):
        """Query the two scheduled tasks on a worker thread so the page never stalls, then report."""
        self.integrity.configure(text="Checking...", fg=self.theme["faint"])

        def exists(name):
            try:
                return subprocess.run([str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
                                           / "schtasks.exe"), "/Query", "/TN", name], capture_output=True,
                                      creationflags=0x08000000, timeout=10).returncode == 0
            except Exception:
                return False

        def work():
            task, enf = exists(paths.TASK_NAME), exists(paths.ENFORCE_TASK_NAME)
            try:
                self.after(0, lambda: self._report(task, enf))
            except (RuntimeError, tk.TclError):
                pass
        threading.Thread(target=work, daemon=True).start()

    def _report(self, task: bool, enf: bool):
        if not self.winfo_exists():
            return
        session = state.load_session()
        text = hostsfile.read_hosts(paths.HOSTS_PATH)
        block = hostsfile.has_block(text)
        if state.session_is_active(session):
            fixes = len(session.get("enforce_fixes", []))
            paused = bool(session.get("paused"))
            sched = session.get("schedule")
            kind_txt = "weekly schedule, currently a free window" if paused else (
                "weekly schedule, currently blocking" if schedule.is_weekly(sched) else "single block")
            hosts_ok = (not block) if paused else block
            msg = (f"Active session {session['id']} ({kind_txt}) until {session['restore_at'].replace('T', ' ')}. "
                   f"Hosts block present: {'yes' if block else 'no'}{'' if hosts_ok else ' (unexpected)'}. Restore task: "
                   f"{'yes' if task else 'NO (unexpected)'}. Enforcement task: {'yes' if enf else 'NO (unexpected)'}. "
                   f"Files hidden: {len(session.get('hidden_files', []))}. Tamper fixes so far: {fixes}. "
                   f"Schedule switches so far: {len(session.get('schedule_log', []))}.")
            kind = "muted" if (hosts_ok and task and enf) else "warn"
        else:
            problems = []
            if block:
                problems.append("a DeepFocus block is still in the hosts file")
            if task:
                problems.append("the restore task still exists")
            if enf:
                problems.append("the enforcement task still exists")
            msg = "No active session. " + ("Everything is clean." if not problems else
                                           "Leftovers found: " + "; ".join(problems) + ". Use Emergency restore.")
            kind = "muted" if not problems else "warn"
        if guard.install_dir_problem():
            msg += (" Note: the app folder is writable by your account, so the elevated scheduled tasks could run a "
                    "swapped executable. See 'Where to put it' in the README.")
        self.integrity.configure(text=msg, fg=self.theme[kind])

    def emergency(self):
        if messagebox.askyesno(APP_NAME, "Remove every DeepFocus block, unlock and unhide all recorded files, restore "
                               "browser DoH policies and delete the scheduled tasks now?\n\n"
                               "Windows will ask for administrator permission.", parent=self.app):
            self.app.restore_now("manual", on_done=lambda r: self.check())


def main() -> None:
    paths.ensure_dirs()
    if not single_instance():
        return
    App().mainloop()


if __name__ == "__main__":
    main()
