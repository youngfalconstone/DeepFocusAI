"""Render a page of the app to a PNG even when other windows cover it (PrintWindow).
Usage: python -m tests.capture <page> <out.png>"""
from __future__ import annotations

import ctypes
import os
import struct
import sys
import zlib
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tkinter as tk  # noqa: E402

from deepfocus import catalog  # noqa: E402
from deepfocus.app import App  # noqa: E402

page = sys.argv[1]
out = Path(sys.argv[2])

if page in ("session", "session_weekly", "session_free"):
    # Fake an active session in an isolated state folder (DEEPFOCUS_HOME must be set by the caller).
    from datetime import datetime, timedelta

    from deepfocus import schedule, state

    weekly = page != "session"
    sched = None
    if weekly:
        d = datetime.now().weekday()
        # session_free: today is a free day; session_weekly: free from tomorrow 15:00 plus the day after
        free = {str(d): [0, 1440]} if page == "session_free" else {str((d + 1) % 7): [900, 1440],
                                                                    str((d + 2) % 7): [0, 1440]}
        sched = {"kind": "weekly", "weeks": 4, "free": free}
    s = state.new_session("Write the integration tests for the billing service",
                          datetime.now() + (timedelta(weeks=4) if weekly else timedelta(minutes=38)),
                          ["youtube.com"] * 61 + ["discord.com"] * 109, ["Discord.exe"],
                          [], ["youtube", "discord"],
                          {"pomodoro": not weekly, "pomodoro_work_min": 25, "pomodoro_break_min": 5,
                           "process_watch": True, "end_delay_seconds": 30}, sched)
    s["status"] = "active"
    s["started"] = (datetime.now() - timedelta(minutes=22)).strftime(state.ISO)
    s["paused"] = page == "session_free"
    s["hidden_files"] = [] if s["paused"] else [{"path": "C:\\Users\\me\\Desktop\\games.lnk", "attrs": 32}] * 3
    s["kills"] = 2
    s["task_registered"] = True
    s["enforce_task_registered"] = True
    s["hosts_backup"] = "C:\\Users\\me\\.deepfocus\\hosts_backups\\hosts_20261101_120000.bak"
    s["notes"] = "Remember to ask about the refund edge case."
    state.save_session(s)
    page = "dashboard"

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
GA_ROOT = 2
PW_RENDERFULLCONTENT = 0x2


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


def png_write(path: Path, w: int, h: int, bgra: bytes) -> None:
    raw = bytearray()
    stride = w * 4
    for y in range(h):
        raw.append(0)
        row = bgra[y * stride:(y + 1) * stride]
        for x in range(0, stride, 4):
            raw += bytes((row[x + 2], row[x + 1], row[x]))

    def chunk(tag: bytes, data: bytes) -> bytes:
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b"")
    path.write_bytes(png)


def capture(hwnd: int, path: Path) -> None:
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    hdc = user32.GetDC(0)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bmp)
    user32.PrintWindow(hwnd, mem, PW_RENDERFULLCONTENT)
    bi = BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.biWidth, bi.biHeight, bi.biPlanes, bi.biBitCount = w, -h, 1, 32
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)
    png_write(path, w, h, buf.raw)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(0, hdc)


app = App()
_geom = os.environ.get("DEEPFOCUS_CAP_GEOM", "1160x780+40+40")  # "max" = maximized
if _geom == "max":
    app.state("zoomed")
else:
    app.geometry(_geom)


def setup():
    global page
    if page in ("builder", "builder_weekly", "builder_long"):
        b = app.pages["builder"]
        b.goal_var.set("Finish the quarterly report draft")
        b.add_service(catalog.get("youtube"))
        b.add_service(catalog.get("discord"))
        b.add_service(catalog.get("reddit"))
        b.search.set("twit"); b.find()
        if page == "builder_weekly":
            b.mode_var.set("weekly"); b._mode_changed(); b.weeks_var.set(4)
            app.after(300, lambda: b.scroll.scroll_to(0.52))
        elif page == "builder_long":
            b.mode_var.set("long"); b._mode_changed(); b.long_var.set("14"); b._long_pick()
            app.after(300, lambda: b.scroll.scroll_to(0.55))
        page = "builder"
    elif page == "review":
        b = app.pages["builder"]
        b.goal_var.set("Finish the quarterly report draft")
        b.add_service(catalog.get("youtube")); b.add_service(catalog.get("discord"))
        app.after(600, lambda: b.review())
        page = "builder"
    elif page == "aiblock":
        app.after(600, lambda: app.pages["dashboard"]._open_ai_block(demo=True))
        page = "dashboard"
    elif page == "chat":
        from deepfocus import state
        state.save_chat([
            {"role": "user", "content": "I have two hours before my meeting. How should I use them?", "time": ""},
            {"role": "assistant", "content": "Start a 50-minute session on the report draft with the Social preset "
             "blocked, take a 10-minute break away from the screen, then a second 50-minute block for the slides. "
             "Leave the last 10 minutes to write down where you stopped so the restart after the meeting is easy.",
             "time": ""},
            {"role": "user", "content": "What should I block?", "time": ""},
            {"role": "assistant", "content": "Your history shows the early-ended sessions all had Discord and YouTube "
             "open. Use the Social preset plus your saved Discord (AI) list, and turn on Block Notifications so the "
             "pings do not pull you back.", "time": ""}])
        app.show("chat")
    elif page == "settings_ai":
        app.after(400, lambda: app.pages["settings"].scroll.scroll_to(0.42))
        page = "settings"
    app.show(page)
    app.after(1500, shoot)


def shoot():
    # capture the topmost Toplevel (a dialog) when one is open, else the main window
    tops = [w for w in app.winfo_children() if isinstance(w, tk.Toplevel) and w.winfo_viewable()]
    target = tops[-1] if tops else app
    hwnd = user32.GetAncestor(target.winfo_id(), GA_ROOT)
    capture(hwnd, out)
    print("saved", out)
    app.destroy()


app.after(700, setup)
app.mainloop()
