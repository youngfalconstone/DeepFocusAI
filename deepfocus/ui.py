"""Theme tokens and the widget kit the app is built from, styled after Windows 11.

Plain tkinter, drawn on Canvas where needed. Follows the Windows app theme
(light/dark) and the Windows accent colour, uses Segoe UI Variable for text
and Segoe Fluent Icons for symbols (monochrome icon-font glyphs, never emoji),
and mirrors WinUI control shapes: 4 px control radius, 8 px card radius,
1 px strokes, 32 px control height, accent underline selector bars, 40x20
toggle switches with a growing knob, and entrance transitions that slide up.
"""
from __future__ import annotations

import tkinter as tk
import winreg
from tkinter import font as tkfont
from typing import Callable

# ------------------------------------------------------------------ system ---
def system_theme() -> str:
    """'light' or 'dark' from the Windows "Choose your mode" setting."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            val, _ = winreg.QueryValueEx(k, "AppsUseLightTheme")
            return "light" if int(val) else "dark"
    except OSError:
        return "dark"


def system_accent_palette() -> list[str] | None:
    """The 8-colour Windows accent palette (light3 .. dark3), or None."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer\Accent") as k:
            raw, _ = winreg.QueryValueEx(k, "AccentPalette")
        if len(raw) >= 32:
            return ["#%02x%02x%02x" % (raw[i], raw[i + 1], raw[i + 2]) for i in range(0, 32, 4)]
    except OSError:
        pass
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\DWM") as k:
            val, _ = winreg.QueryValueEx(k, "AccentColor")
        c = "#%02x%02x%02x" % (val & 0xFF, (val >> 8) & 0xFF, (val >> 16) & 0xFF)
        return [blend(c, "#ffffff", t) for t in (0.6, 0.4, 0.2)] + [c] + \
               [blend(c, "#000000", t) for t in (0.2, 0.4, 0.6)] + [c]
    except OSError:
        return None


def blend(c1: str, c2: str, t: float) -> str:
    t = max(0.0, min(1.0, t))
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def luminance(c: str) -> float:
    r, g, b = (int(c[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


# ------------------------------------------------------------------ tokens ---
# "Nebula" palette: deep indigo night with violet accents (light variant is a lavender daylight).
BASE = {
    "dark": {
        "bg": "#0d0b19", "sidebar": "#0a0914", "surface": "#15132a", "surface2": "#1c1a35", "surface3": "#272449",
        "border": "#24213f", "stroke": "#2f2b54", "strong": "#6f6a9c",
        "text": "#f4f2ff", "muted": "#b6b1d8", "faint": "#7b7699",
        "danger": "#ff8aa0", "danger_soft": "#3a1e35", "success": "#74e3a0", "warn": "#ffd166",
        "ring_bg": "#231f44", "knob_off": "#cac5ee",
    },
    "light": {
        "bg": "#f4f2fb", "sidebar": "#ece8f9", "surface": "#fcfbff", "surface2": "#f3f0fc", "surface3": "#e6e1f7",
        "border": "#e2ddf4", "stroke": "#d2ccec", "strong": "#8d88b0",
        "text": "#17132b", "muted": "#5c5680", "faint": "#9a95ba",
        "danger": "#c42b1c", "danger_soft": "#fde7e9", "success": "#0f7b0f", "warn": "#9d5d00",
        "ring_bg": "#e6e1f7", "knob_off": "#5c5680",
    },
}
DEFAULT_ACCENT = {"dark": "#8b5cf6", "light": "#6d4aff"}

# Segoe Fluent Icons code points (fall back to Segoe MDL2 Assets, which shares them).
ICON = {
    "dashboard": "\uE80F", "new": "\uE710", "presets": "\uE8FD", "history": "\uE81C", "settings": "\uE713",
    "check": "\uE73E", "cross": "\uE711", "play": "\uE768", "stop": "\uE71A", "dot": "\uEA3B", "arrow": "\uE72A",
    "chevron": "\uE76C", "minus": "\uE738", "plus": "\uE710", "back": "\uE76B", "up": "\uE70E", "down": "\uE70D",
    "shield": "\uEA18", "lock": "\uE72E", "globe": "\uE774", "folder": "\uE8B7", "clock": "\uE823",
    "info": "\uE946", "warning": "\uE7BA", "search": "\uE721", "hide": "\uED1A", "refresh": "\uE72C",
    "open": "\uE8A7", "help": "\uE897", "star": "\uE735", "bolt": "\uE945", "chart": "\uE9D9",
    "stopwatch": "\uE916", "target": "\uE8A1", "trophy": "\uEB4F", "sparkle": "\uEA61", "calendar": "\uE787",
    "bell": "\uEA8F", "bell_off": "\uE7ED", "music": "\uEC4F", "flame": "\uE9CA", "edit": "\uE70F",
    "trend": "\uE8CB", "tune": "\uE8B2", "mute": "\uE74F", "ai": "\uE82F", "flag": "\uE7C1", "key": "\uE8D7",
    "delete": "\uE74D", "list": "\uEA37", "chat": "\uE8F2", "send": "\uE724", "person": "\uE77B",
}

EASE = lambda t: 1 - (1 - t) ** 3  # ease-out cubic  # noqa: E731

# Point sizes of the type scale. Nothing in the app renders text below `caption`.
TYPE_SCALE = {"caption": 9, "body": 10, "subtitle": 11, "title_s": 12, "title": 14, "stat": 18, "h1": 20,
              "display": 24, "timer": 32, "timer_m": 25, "timer_s": 20}


def rounded(canvas: tk.Canvas, x1: float, y1: float, x2: float, y2: float, r: float, **kw) -> int:
    """Draw a rounded rectangle (as a smoothed polygon) and return its canvas item id."""
    return canvas.create_polygon(_poly(x1, y1, x2, y2, r), smooth=True, splinesteps=10, **kw)


def _poly(x1, y1, x2, y2, r):
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    return [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r,
            x1, y1 + r, x1, y1]


class Theme:
    """Resolved colour tokens plus fonts. `name` is the resolved mode ('dark'/'light')."""

    def __init__(self, mode: str = "system", system_accent: bool = True):
        self.mode = mode
        self.name = system_theme() if mode == "system" else mode
        self.c = dict(BASE[self.name])
        pal = system_accent_palette() if system_accent else None
        if pal:
            # Windows uses a lighter accent on dark backgrounds and a darker one on light.
            accent = pal[1] if self.name == "dark" else pal[4]
            base = pal[3]
        else:
            accent = base = DEFAULT_ACCENT[self.name]
        self.c["accent"] = accent
        self.c["accent_base"] = base
        self.c["accent_hover"] = blend(accent, self.c["bg"], 0.1)
        self.c["accent_press"] = blend(accent, self.c["bg"], 0.2)
        self.c["accent_text"] = "#000000" if luminance(accent) > 0.45 else "#ffffff"
        self.c["accent_soft"] = blend(self.c["surface"], accent, 0.18 if self.name == "dark" else 0.12)
        self.c["sel"] = blend(self.c["surface2"], accent, 0.25)
        self.c["knob_on"] = self.c["accent_text"]
        # gradients (primary buttons, rings, bars) and the hero "night sky" scene
        self.c["grad1"] = blend(accent, "#2b1d8f" if self.name == "dark" else "#3a2bb3", 0.35)
        self.c["grad2"] = blend(accent, "#ffffff", 0.22)
        bg = self.c["bg"]
        if self.name == "dark":
            self.c["sky_top"] = blend(bg, accent, 0.03)
            self.c["sky_mid"] = blend(bg, accent, 0.22)
            self.c["sky_bot"] = blend(bg, accent, 0.42)
            self.c["mount_back"] = blend(bg, accent, 0.34)
            self.c["mount_mid"] = blend(bg, accent, 0.20)
            self.c["mount_front"] = blend(bg, accent, 0.09)
            self.c["hero_text"] = "#ffffff"
            self.c["hero_muted"] = "#d9d4f2"
        else:
            self.c["sky_top"] = blend("#ffffff", accent, 0.62)
            self.c["sky_mid"] = blend("#ffffff", accent, 0.42)
            self.c["sky_bot"] = blend("#ffffff", accent, 0.28)
            self.c["mount_back"] = blend("#ffffff", accent, 0.50)
            self.c["mount_mid"] = blend("#ffffff", accent, 0.62)
            self.c["mount_front"] = blend("#ffffff", accent, 0.76)
            self.c["hero_text"] = "#ffffff"
            self.c["hero_muted"] = "#efebff"
        self.c["snow"] = blend(self.c["mount_back"], "#ffffff", 0.45 if self.name == "dark" else 0.7)
        self.fonts: dict[str, tkfont.Font] = {}
        self.radius = 14
        self.ctrl_radius = 8

    def init_fonts(self, root: tk.Misc) -> None:
        fams = set(tkfont.families(root))
        fam = "Segoe UI Variable Text" if "Segoe UI Variable Text" in fams else "Segoe UI"
        disp = "Segoe UI Variable Display" if "Segoe UI Variable Display" in fams else fam
        small = "Segoe UI Variable Small" if "Segoe UI Variable Small" in fams else fam
        icon = "Segoe Fluent Icons" if "Segoe Fluent Icons" in fams else \
            ("Segoe MDL2 Assets" if "Segoe MDL2 Assets" in fams else "Segoe UI Symbol")
        self.icon_font_ok = icon != "Segoe UI Symbol"
        # Type scale (points; Tk scales points with the Windows display scaling). Minimum 9 pt (Windows
        # "caption"), body 10 pt, then 11 / 12 / 14 / 18 / 20 / 24 for headings and display sizes.
        # Scale ratios are what matters: keep every size on this ladder.
        S = TYPE_SCALE
        self.fonts = {
            "tiny": tkfont.Font(root, family=small, size=S["caption"]),
            "small": tkfont.Font(root, family=small, size=S["caption"]),
            "body": tkfont.Font(root, family=fam, size=S["body"]),
            "bold": tkfont.Font(root, family=fam, size=S["body"], weight="bold"),
            "h3": tkfont.Font(root, family=fam, size=S["subtitle"], weight="bold"),
            "brand": tkfont.Font(root, family=disp, size=S["title_s"], weight="bold"),
            "cta": tkfont.Font(root, family=fam, size=S["title_s"], weight="bold"),
            "hero_sub": tkfont.Font(root, family=fam, size=S["subtitle"]),
            "h2": tkfont.Font(root, family=disp, size=S["title"], weight="bold"),
            "stat": tkfont.Font(root, family=disp, size=S["stat"], weight="bold"),
            "h1": tkfont.Font(root, family=disp, size=S["h1"], weight="bold"),
            "hero": tkfont.Font(root, family=disp, size=S["display"], weight="bold"),
            # timer sizes: the hero picks the largest that fits inside its ring
            "timer": tkfont.Font(root, family=disp, size=S["timer"], weight="bold"),
            "timer_m": tkfont.Font(root, family=disp, size=S["timer_m"], weight="bold"),
            "timer_s": tkfont.Font(root, family=disp, size=S["timer_s"], weight="bold"),
            "timer_xs": tkfont.Font(root, family=disp, size=S["stat"], weight="bold"),
            "icon": tkfont.Font(root, family=icon, size=S["body"]),
            "icon_s": tkfont.Font(root, family=icon, size=8),
            "icon_l": tkfont.Font(root, family=icon, size=S["title"]),
            "icon_xl": tkfont.Font(root, family=icon, size=S["stat"]),
        }

    def timer_font(self, text: str, max_width: float) -> tkfont.Font:
        """Largest timer font whose rendering of `text` fits in `max_width` pixels."""
        for key in ("timer", "timer_m", "timer_s", "timer_xs"):
            f = self.fonts[key]
            if f.measure(text) <= max_width:
                return f
        return self.fonts["timer_xs"]

    def __getitem__(self, k: str) -> str:
        return self.c[k]


def gradient_shape(canvas: tk.Canvas, x1: float, y1: float, x2: float, y2: float, r: float, c1: str, c2: str,
                   vertical: bool = False, tags: str | tuple = "grad", step: int = 1) -> list[int]:
    """Fill a rounded rectangle with a linear gradient (c1 -> c2) by drawing 1 px lines clipped to the
    rounded outline. Horizontal by default (left to right), vertical when asked (top to bottom).
    Returns the line ids in gradient order, so a caller can recolour them in place (hover effects)."""
    w, h = x2 - x1, y2 - y1
    ids: list[int] = []
    if w <= 0 or h <= 0:
        return ids
    r = max(0.0, min(r, w / 2, h / 2))

    def inset(d: float) -> float:
        # how far the rounded corner cuts in, d px from the edge along the long axis
        if d >= r:
            return 0.0
        dx = r - d
        return r - (r * r - dx * dx) ** 0.5
    if not vertical:
        n = int(w)
        for i in range(0, n, step):
            x = x1 + i + 0.5
            d = min(i, n - 1 - i)
            cut = inset(d)
            ids.append(canvas.create_line(x, y1 + cut, x, y2 - cut, fill=blend(c1, c2, i / max(1, n - 1)), width=step,
                                          tags=tags))
    else:
        n = int(h)
        for i in range(0, n, step):
            y = y1 + i + 0.5
            d = min(i, n - 1 - i)
            cut = inset(d)
            ids.append(canvas.create_line(x1 + cut, y, x2 - cut, y, fill=blend(c1, c2, i / max(1, n - 1)), width=step,
                                          tags=tags))
    return ids


def gradient_ring(canvas: tk.Canvas, cx: float, cy: float, radius: float, width: float, frac: float, c1: str, c2: str,
                  track: str, glow: str | None = None, tags: str | tuple = "ring", segments: int = 48) -> None:
    """Progress ring with a colour sweep from c1 to c2 along the arc and a soft glow behind it."""
    x1, y1, x2, y2 = cx - radius, cy - radius, cx + radius, cy + radius
    if glow:
        canvas.create_oval(x1, y1, x2, y2, outline=glow, width=width + 12, tags=tags)
    canvas.create_oval(x1, y1, x2, y2, outline=track, width=width, tags=tags)
    frac = max(0.0, min(1.0, frac))
    if frac <= 0.002:
        return
    total = 359.9 * frac
    n = max(1, int(segments * frac))
    seg = total / n
    for i in range(n):
        start = 90 - i * seg
        canvas.create_arc(x1, y1, x2, y2, start=start, extent=-(seg + (0.8 if i < n - 1 else 0)), style="arc",
                          outline=blend(c1, c2, i / max(1, segments - 1)), width=width, tags=tags)


def animate(widget: tk.Misc, duration_ms: int, step: Callable[[float], None], done: Callable | None = None,
            fps: int = 60, token: list | None = None) -> None:
    """Call step(t) with t eased from 0..1 over duration. `token` (a list) cancels when changed."""
    frames = max(1, int(duration_ms * fps / 1000))
    my = object()
    if token is not None:
        token[:] = [my]

    def tick(i: int):
        if token is not None and (not token or token[0] is not my):
            return
        if not widget.winfo_exists():
            return
        step(EASE(i / frames))
        if i < frames:
            widget.after(int(1000 / fps), tick, i + 1)
        elif done:
            done()

    tick(0)


def animate_synced(widget: tk.Misc, duration_ms: int, step: Callable[[float], None], done: Callable | None = None,
                   token: list | None = None, min_interval_ms: int = 16) -> None:
    """Time-based animation whose next frame waits until the previous one has been painted.

    For animations that MOVE child windows (scrolling, page slides). After `step` moves things, Windows queues
    paint requests; Tk turns them into redraws that run at idle time. The next frame is scheduled through two
    idle hops, so it runs only after those redraws have happened: every frame the user sees is complete, and
    the frame rate adapts to what painting allows (capped at 1000/min_interval_ms). `t` is derived from the
    clock, so the animation always takes `duration_ms` regardless of frame rate."""
    import time
    my = object()
    if token is not None:
        token[:] = [my]
    t0 = time.perf_counter()

    def alive() -> bool:
        return (token is None or (token and token[0] is my)) and widget.winfo_exists()

    def frame():
        if not alive():
            return
        started = time.perf_counter()
        t = min(1.0, (started - t0) * 1000 / max(1, duration_ms))
        step(EASE(t))
        if t >= 1.0:
            if done:
                done()
            return

        def hop2():
            if not alive():
                return
            wait = max(1, int(min_interval_ms - (time.perf_counter() - started) * 1000))
            widget.after(wait, frame)

        def hop1():
            if alive():
                widget.after_idle(hop2)
        widget.after_idle(hop1)

    # the first frame also waits for whatever is still being painted (a wheel notch that restarts the
    # animation must not move content whose previous frame is not on screen yet)
    def first():
        if alive():
            widget.after_idle(lambda: alive() and frame())
    widget.after_idle(first)


def invalidate(widget: tk.Misc, rect: tuple[int, int, int, int] | None = None) -> None:
    """Mark a widget and every window inside it (or just the `rect` part, in widget coordinates) as needing a
    repaint.

    Why: Tk answers WM_PAINT at once (BeginPaint/EndPaint) and does the actual drawing later, at idle time.
    When a widget tree is moved in between (a scrolled frame, a sliding page, a relayout) Windows copies the
    not-yet-drawn pixels along with the window and considers them valid, so nothing ever repaints them: text
    gets duplicated, torn or left behind. Invalidating the moved region right after the move turns every one
    of those copied regions back into a real paint request. A full repaint of a busy page costs ~60 ms, so
    animations invalidate only the band that moved in recently and do a full pass once they settle."""
    try:
        import ctypes
        from ctypes import wintypes
        r = ctypes.byref(wintypes.RECT(*rect)) if rect else None
        ctypes.windll.user32.RedrawWindow(widget.winfo_id(), r, None, 0x0001 | 0x0080)  # INVALIDATE | ALLCHILDREN
    except (AttributeError, OSError, ValueError, tk.TclError):
        pass


def flush_paint(widget: tk.Misc) -> None:
    """Hand every pending WM_PAINT in the subtree to Tk right now.

    Tk's event loop runs idle callbacks even while Windows still holds paint requests for the window (it does
    not treat WM_PAINT as a pending event), so `animate_synced`'s idle hops alone would not wait for them.
    RDW_UPDATENOW dispatches the paints synchronously; Tk turns them into Expose events whose redraws are then
    queued ahead of the next frame."""
    try:
        import ctypes
        ctypes.windll.user32.RedrawWindow(widget.winfo_id(), None, None, 0x0100 | 0x0080)  # UPDATENOW | ALLCHILDREN
    except (AttributeError, OSError, tk.TclError):
        pass


# ------------------------------------------------------------------ surfaces
class Surface(tk.Canvas):
    """Card: 8 px radius, 1 px stroke. Put children in `.body`."""

    def __init__(self, parent, theme: Theme, pad: int = 18, fill: str | None = None, outline: str | None = None,
                 radius: int | None = None, fit: bool = False, **kw):
        self.fit = fit
        kw.setdefault("width", 10)
        kw.setdefault("height", 2 * pad + 24)  # keep the embedded body partly visible so Tk maps it
        super().__init__(parent, bg=parent["bg"], highlightthickness=0, bd=0, **kw)
        self.theme = theme
        self.pad = pad
        self.fill_color = fill or theme["surface"]
        self.outline_color = outline or theme["border"]
        self.radius = radius if radius is not None else theme.radius
        self.body = tk.Frame(self, bg=self.fill_color)
        self._rect = rounded(self, 0, 0, 10, 10, self.radius, fill=self.fill_color, outline=self.outline_color)
        self._win = self.create_window((pad, pad), window=self.body, anchor="nw")
        self.body.bind("<Configure>", self._body_changed)
        self.bind("<Configure>", self._canvas_changed)
        self._h = 2 * pad + 24
        self.after_idle(self._sync)

    def _sync(self):
        if not self.winfo_exists():
            return
        h = self.body.winfo_reqheight() + 2 * self.pad
        if self._h != h:
            self._h = h
            self.configure(height=h)
        if self.fit:
            w = self.body.winfo_reqwidth() + 2 * self.pad
            if self.winfo_reqwidth() != w:
                self.configure(width=w)
        self._redraw()

    def _body_changed(self, e):
        self._sync()

    def _canvas_changed(self, e):
        if not self.fit:
            self.itemconfigure(self._win, width=max(10, e.width - 2 * self.pad))
        self._sync()

    def _redraw(self):
        w, h = self.winfo_width(), max(self._h, self.winfo_height())
        self.coords(self._rect, *_poly(1, 1, w - 1, h - 1, self.radius))

    def set_colors(self, fill: str | None = None, outline: str | None = None):
        if fill:
            self.fill_color = fill
            self.body.configure(bg=fill)
            for c in self.body.winfo_children():
                try:
                    c.configure(bg=fill)
                except tk.TclError:
                    pass
        if outline:
            self.outline_color = outline
        self.itemconfigure(self._rect, fill=self.fill_color, outline=self.outline_color)


class Hero(tk.Canvas):
    """Night-sky scene drawn in code: vertical gradient, stars and three mountain ridges, with optional
    rounded corners. Background items carry the tag "bg"; the owner draws its overlay in `on_layout(w, h)`,
    which runs after every (debounced) redraw, and should tag those items "fg" so it can clear them."""

    def __init__(self, parent, theme: Theme, height: int = 540, seed: int = 7, on_layout: Callable | None = None,
                 stars: int = 110, radius: int = 0):
        super().__init__(parent, bg=parent["bg"], highlightthickness=0, bd=0, height=height)
        self.theme = theme
        self.seed = seed
        self.on_layout = on_layout
        self.stars = stars
        self.radius = radius
        self._job = None
        self._size = (0, 0)
        self.bind("<Configure>", self._on_resize)

    def _on_resize(self, e):
        if (e.width, e.height) == self._size:
            return
        self._size = (e.width, e.height)
        if self._job:
            self.after_cancel(self._job)
        self._job = self.after(30, self.redraw)

    def sky_color_at(self, y: float) -> str:
        t = self.theme
        h = max(1, self.winfo_height())
        f = max(0.0, min(1.0, y / h))
        if f < 0.6:
            return blend(t["sky_top"], t["sky_mid"], f / 0.6)
        return blend(t["sky_mid"], t["sky_bot"], (f - 0.6) / 0.4)

    def _ridge(self, w: int, h: int, base_f: float, amp_f: float, freq: float, seed: int) -> list[float]:
        import math
        import random
        rnd = random.Random(seed)
        ph = [rnd.random() * 6.283 for _ in range(3)]
        pts: list[float] = []
        n = 72
        for i in range(n + 1):
            x = w * i / n
            u = i / n
            s = (0.55 * abs(math.sin(freq * u * 3.1416 + ph[0])) + 0.30 * abs(math.sin(freq * 2.3 * u * 3.1416 + ph[1]))
                 + 0.15 * abs(math.sin(freq * 5.1 * u * 3.1416 + ph[2])))
            y = h * base_f - h * amp_f * s
            pts += [x, y]
        return pts

    def redraw(self):
        self._job = None
        import math
        import random
        t = self.theme
        w, h = self.winfo_width(), self.winfo_height()
        if w < 10 or h < 10:
            return
        self.delete("bg")
        step = 3
        for y in range(0, h, step):
            self.create_rectangle(0, y, w, y + step, fill=self.sky_color_at(y + step / 2), outline="", tags="bg")
        rnd = random.Random(self.seed)
        for _ in range(self.stars):
            x = rnd.random() * w
            y = rnd.random() * h * 0.55
            r = rnd.choice((0.6, 0.8, 1.1, 1.5))
            c = blend(self.sky_color_at(y), "#ffffff", rnd.uniform(0.3, 0.9))
            self.create_oval(x - r, y - r, x + r, y + r, fill=c, outline="", tags="bg")
        layers = ((0.60, 0.17, 1.9, t["mount_back"], t["snow"]), (0.72, 0.15, 2.7, t["mount_mid"], None),
                  (0.84, 0.11, 3.6, t["mount_front"], None))
        for i, (base_f, amp_f, freq, color, snow) in enumerate(layers):
            pts = self._ridge(w, h, base_f, amp_f, freq, self.seed * 10 + i)
            self.create_polygon(pts + [w, h, 0, h], fill=color, outline=color, tags="bg")
            if snow:
                self.create_line(pts, fill=snow, width=1.5, tags="bg")
        # haze where the back ridge meets the sky
        haze_y = int(h * 0.50)
        for k in range(6):
            c = blend(t["mount_back"], t["sky_bot"], 0.5)
            self.create_rectangle(0, haze_y + k * 3, w, haze_y + k * 3 + 3, fill=blend(self.sky_color_at(haze_y), c, 0.12 * (6 - k) / 6),
                                  outline="", tags="bg", stipple="gray25")
        if self.radius:
            self._corner_masks(w, h, self.radius)
        self.tag_lower("bg")
        if self.on_layout:
            self.on_layout(w, h)

    def _corner_masks(self, w: int, h: int, r: int):
        import math
        bg = self.master["bg"]
        for cx, cy, a0 in ((r, r, 90), (w - r, r, 0), (w - r, h - r, 270), (r, h - r, 180)):
            pts = [cx + (r if cx > w / 2 else -r), cy + (r if cy > h / 2 else -r)]
            for k in range(0, 91, 9):
                a = math.radians(a0 + k)
                pts += [cx + r * math.cos(a), cy - r * math.sin(a)]
            self.create_polygon(pts, fill=bg, outline=bg, tags="bg")

    def pill(self, x1: float, y1: float, x2: float, y2: float, alpha: float = 0.10, tags: str | tuple = "fg") -> int:
        """A translucent-looking rounded chip over the sky (blended with the local sky colour)."""
        base = self.sky_color_at((y1 + y2) / 2)
        fill = blend(base, "#ffffff", alpha)
        return rounded(self, x1, y1, x2, y2, (y2 - y1) / 2, fill=fill, outline=blend(base, "#ffffff", alpha + 0.12),
                       tags=tags)


# ------------------------------------------------------------------- buttons
class Button(tk.Canvas):
    """WinUI-style button: 32 px tall, 4 px radius, 1 px stroke.
    kinds: primary (accent), standard, subtle, danger, danger_soft, link."""

    def __init__(self, parent, theme: Theme, text: str, command: Callable | None = None, kind: str = "standard",
                 font: str = "body", padx: int = 14, pady: int = 6, width: int | None = None, radius: int | None = None,
                 icon: str | None = None):
        super().__init__(parent, bg=parent["bg"], highlightthickness=0, bd=0, cursor="hand2", takefocus=1)
        self.theme = theme
        self.kind = "standard" if kind in ("ghost", "soft") else kind
        self.command = command
        self.enabled = True
        self.font = theme.fonts[font]
        self.icon = icon
        self.padx, self.pady = padx, pady
        self.radius = theme.ctrl_radius if radius is None else radius
        self._hover = 0.0
        self._pressed = False
        self._tok: list = []
        self.text = text
        self._w_override = width
        self._rect = rounded(self, 0, 0, 10, 10, self.radius, fill="", outline="")
        self._ico = self.create_text(0, 0, text=icon or "", font=theme.fonts["icon"], anchor="w") if icon else None
        self._txt = self.create_text(0, 0, text=text, font=self.font, anchor="center")
        self._focus = rounded(self, 0, 0, 10, 10, self.radius + 2, fill="", outline="")
        self._size()
        self._paint()
        self.bind("<Enter>", lambda e: self._to(1.0))
        self.bind("<Leave>", lambda e: self._to(0.0))
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<FocusIn>", lambda e: self.itemconfigure(self._focus, outline=theme["text"]))
        self.bind("<FocusOut>", lambda e: self.itemconfigure(self._focus, outline=""))
        self.bind("<Return>", lambda e: self.invoke())
        self.bind("<space>", lambda e: self.invoke())

    def _colors(self) -> tuple[str, str, str, str]:
        """(fill, hover fill, text, stroke)"""
        t = self.theme
        k = self.kind
        if not self.enabled:
            return t["surface2"], t["surface2"], t["faint"], t["border"]
        if k == "primary":
            return t["accent"], t["accent_hover"], t["accent_text"], blend(t["accent"], "#000000", 0.12)
        if k == "danger":
            return t["danger"], blend(t["danger"], t["bg"], 0.1), t["accent_text"] if t.name == "dark" else "#ffffff", \
                blend(t["danger"], "#000000", 0.12)
        if k == "danger_soft":
            return t["danger_soft"], blend(t["danger_soft"], t["danger"], 0.12), t["danger"], \
                blend(t["danger_soft"], t["danger"], 0.25)
        if k == "link":
            return self.master["bg"], self.master["bg"], t["accent"], ""
        if k == "subtle":
            return self.master["bg"], t["surface2"] if self.master["bg"] != t["surface2"] else t["surface3"], t["text"], ""
        # standard
        on_card = self.master["bg"] in (t["surface"], t["surface2"])
        fill = t["surface2"] if on_card else t["surface"]
        return fill, blend(fill, t["text"], 0.06), t["text"], t["stroke"]

    def _size(self):
        iw = (self.theme.fonts["icon"].measure(self.icon) + 8) if self.icon else 0
        w = self._w_override or (self.font.measure(self.text) + 2 * self.padx + iw)
        h = self.font.metrics("linespace") + 2 * self.pady
        self.bw, self.bh = w, h
        self.configure(width=w, height=h)
        self.coords(self._rect, *_poly(0.5, 0.5, w - 0.5, h - 0.5, self.radius))
        self.coords(self._focus, *_poly(1.5, 1.5, w - 1.5, h - 1.5, self.radius))
        if self.icon:
            self.coords(self._ico, self.padx, h / 2)
            self.coords(self._txt, self.padx + iw + (w - 2 * self.padx - iw) / 2, h / 2)
        else:
            self.coords(self._txt, w / 2, h / 2)

    def _paint(self):
        base, hov, fg, stroke = self._colors()
        bg = blend(base, hov, self._hover)
        if self._pressed:
            bg = blend(bg, self.theme["bg"], 0.12)
            fg = blend(fg, bg, 0.25)
        if self.kind == "primary" and self.enabled:
            t = self.theme
            lift = 0.10 * self._hover - (0.12 if self._pressed else 0)
            c1 = blend(t["grad1"], "#ffffff", max(0, lift)) if lift >= 0 else blend(t["grad1"], "#000000", -lift)
            c2 = blend(t["grad2"], "#ffffff", max(0, lift)) if lift >= 0 else blend(t["grad2"], "#000000", -lift)
            key = (self.bw, self.bh)
            ids = getattr(self, "_grad_ids", None)
            if ids and getattr(self, "_grad_key", None) == key:
                # same size: recolour the existing lines in place (cheap, no flicker)
                n = max(1, len(ids) - 1)
                for i, item in enumerate(ids):
                    self.itemconfigure(item, fill=blend(c1, c2, i / n))
            else:
                self.delete("grad")
                self._grad_ids = gradient_shape(self, 0.5, 0.5, self.bw - 0.5, self.bh - 0.5, self.radius, c1, c2)
                self._grad_key = key
                self.itemconfigure(self._rect, fill="", outline="")
                self.tag_raise(self._txt)
                if self.icon:
                    self.tag_raise(self._ico)
                self.tag_raise(self._focus)
            fg = "#ffffff" if not self._pressed else blend("#ffffff", c1, 0.25)
        else:
            if getattr(self, "_grad_ids", None):
                self.delete("grad")
                self._grad_ids = None
            self.itemconfigure(self._rect, fill=bg, outline=stroke or bg)
        self.itemconfigure(self._txt, fill=fg, text=self.text)
        if self.icon:
            self.itemconfigure(self._ico, fill=fg)

    def _to(self, target: float):
        start = self._hover

        def step(t):
            self._hover = start + (target - start) * t
            self._paint()
        animate(self, 120, step, token=self._tok)

    def _press(self, _e):
        if self.enabled:
            self._pressed = True
            self._paint()

    def _release(self, e):
        self._pressed = False
        self._paint()
        if not self.enabled:
            return
        if 0 <= e.x <= self.bw and 0 <= e.y <= self.bh:
            self.invoke()

    def invoke(self):
        if self.enabled and self.command:
            self.command()

    def resize_to(self, w: int):
        """Stretch to a given width (used when a layout manager sizes the button)."""
        if w <= 1 or w == self.bw:
            return
        self.bw = w
        self.coords(self._rect, *_poly(0.5, 0.5, w - 0.5, self.bh - 0.5, self.radius))
        self.coords(self._focus, *_poly(1.5, 1.5, w - 1.5, self.bh - 1.5, self.radius))
        if self.icon:
            iw = self.theme.fonts["icon"].measure(self.icon) + 8
            self.coords(self._txt, self.padx + iw + (w - 2 * self.padx - iw) / 2, self.bh / 2)
        else:
            self.coords(self._txt, w / 2, self.bh / 2)
        self._paint()

    def set_enabled(self, on: bool):
        self.enabled = on
        self.configure(cursor="hand2" if on else "arrow")
        self._paint()

    def set_text(self, text: str):
        self.text = text
        self._size()
        self._paint()

    def set_kind(self, kind: str):
        self.kind = kind
        self._paint()


class IconButton(Button):
    """32x32 square glyph button."""

    def __init__(self, parent, theme, glyph: str, command=None, kind="subtle", size: int = 32, tip: str = ""):
        super().__init__(parent, theme, glyph, command, kind=kind, font="icon", padx=0, pady=0, width=size)
        self.configure(height=size)
        self.bh = size
        self.coords(self._rect, *_poly(0.5, 0.5, size - 0.5, size - 0.5, self.radius))
        self.coords(self._focus, *_poly(1.5, 1.5, size - 1.5, size - 1.5, self.radius))
        self.coords(self._txt, size / 2, size / 2)


# ------------------------------------------------------------------- inputs
class Field(tk.Canvas):
    """WinUI TextBox: 4 px radius, 1 px stroke, stronger bottom edge that turns accent (2 px) on focus."""

    def __init__(self, parent, theme: Theme, textvariable: tk.StringVar | None = None, placeholder: str = "",
                 width: int | None = None, font: str = "body", icon: str | None = None):
        super().__init__(parent, bg=parent["bg"], highlightthickness=0, bd=0)
        self.theme = theme
        self.placeholder = placeholder
        self._ph = False
        self.var = textvariable or tk.StringVar()
        self.icon = icon
        h = theme.fonts[font].metrics("linespace") + 12
        self.h = h
        self.configure(height=h, width=width or 200)
        self._rect = rounded(self, 0, 0, 10, 10, theme.ctrl_radius, fill=theme["surface2"], outline=theme["stroke"])
        self._under = self.create_line(0, 0, 0, 0, fill=theme["strong"], width=1)
        x0 = 11
        if icon:
            self.create_text(11, h / 2, text=icon, font=theme.fonts["icon"], fill=theme["faint"], anchor="w")
            x0 = 34
        self.x0 = x0
        self.entry = tk.Entry(self, textvariable=self.var, relief="flat", bd=0, font=theme.fonts[font],
                              bg=theme["surface2"], fg=theme["text"], insertbackground=theme["text"],
                              highlightthickness=0, disabledbackground=theme["surface2"], disabledforeground=theme["faint"])
        self._win = self.create_window((x0, h // 2), window=self.entry, anchor="w")
        self.bind("<Configure>", self._resize)
        self.entry.bind("<FocusIn>", self._focus_in)
        self.entry.bind("<FocusOut>", self._focus_out)
        if placeholder:
            self._focus_out()

    def _resize(self, e):
        r = self.theme.ctrl_radius
        self.coords(self._rect, *_poly(0.5, 0.5, e.width - 0.5, e.height - 0.5, r))
        self.coords(self._under, r, e.height - 1, e.width - r, e.height - 1)
        self.itemconfigure(self._win, width=max(10, e.width - self.x0 - 10))

    def _focus_in(self, _e=None):
        t = self.theme
        self.itemconfigure(self._under, fill=t["accent"], width=2)
        fill = t["bg"] if t.name == "dark" else "#ffffff"
        self.itemconfigure(self._rect, fill=fill)
        self.entry.configure(bg=fill)
        if self._ph:
            self.entry.delete(0, "end")
            self.entry.configure(fg=t["text"])
            self._ph = False

    def _focus_out(self, _e=None):
        t = self.theme
        self.itemconfigure(self._under, fill=t["strong"], width=1)
        self.itemconfigure(self._rect, fill=t["surface2"])
        self.entry.configure(bg=t["surface2"])
        if self.placeholder and not self.entry.get():
            self.entry.insert(0, self.placeholder)
            self.entry.configure(fg=t["faint"])
            self._ph = True

    def value(self) -> str:
        return "" if self._ph else self.entry.get()

    def set(self, text: str):
        self._ph = False
        self.entry.configure(fg=self.theme["text"])
        self.entry.delete(0, "end")
        self.entry.insert(0, text)
        if not text:
            self._focus_out()

    def clear(self):
        self.set("")

    def bind_return(self, fn: Callable):
        self.entry.bind("<Return>", lambda e: fn())

    def focus(self):
        self.entry.focus_set()

    def set_enabled(self, on: bool):
        self.entry.configure(state="normal" if on else "disabled")


class Toggle(tk.Canvas):
    """WinUI ToggleSwitch: 40x20 track; off = outlined, on = accent; knob grows on hover."""

    def __init__(self, parent, theme: Theme, variable: tk.BooleanVar, command: Callable | None = None):
        super().__init__(parent, width=44, height=24, bg=parent["bg"], highlightthickness=0, cursor="hand2", takefocus=1)
        self.theme = theme
        self.var = variable
        self.command = command
        self.enabled = True
        self._pos = 1.0 if variable.get() else 0.0
        self._hover = 0.0
        self._tok: list = []
        self._tok2: list = []
        self.bind("<Button-1>", self._toggle)
        self.bind("<space>", self._toggle)
        self.bind("<Return>", self._toggle)
        self.bind("<Enter>", lambda e: self._hov(1.0))
        self.bind("<Leave>", lambda e: self._hov(0.0))
        self.var.trace_add("write", lambda *_: self._slide())
        self._draw()

    def _toggle(self, _e=None):
        if not self.enabled:
            return
        self.var.set(not self.var.get())
        if self.command:
            self.command()

    def _hov(self, target):
        start = self._hover

        def step(t):
            self._hover = start + (target - start) * t
            self._draw()
        animate(self, 120, step, token=self._tok2)

    def _slide(self):
        target = 1.0 if self.var.get() else 0.0
        start = self._pos

        def step(t):
            self._pos = start + (target - start) * t
            self._draw()
        animate(self, 160, step, token=self._tok)

    def _draw(self):
        self.delete("all")
        t = self.theme
        x1, y1, x2, y2 = 2, 2, 42, 22
        if not self.enabled:
            track, outline, knob = t["surface3"], t["surface3"], t["faint"]
        else:
            on_track = t["accent"]
            off_fill = blend(self.master["bg"], t["text"], 0.04 + 0.04 * self._hover)
            track = blend(off_fill, on_track, self._pos)
            outline = blend(t["strong"], on_track, self._pos)
            knob = blend(t["knob_off"], t["knob_on"], self._pos)
        rounded(self, x1, y1, x2, y2, 10, fill=track, outline=outline)
        r = 6 + 1 * self._hover
        cx = 11 + 22 * self._pos
        self.create_oval(cx - r, 12 - r, cx + r, 12 + r, fill=knob, outline=knob)

    def set_enabled(self, on: bool):
        self.enabled = on
        self.configure(cursor="hand2" if on else "arrow")
        self._draw()


class Check(tk.Canvas):
    """WinUI CheckBox: 20 px box, accent fill with a check glyph when on."""

    def __init__(self, parent, theme: Theme, variable: tk.BooleanVar, command: Callable | None = None):
        super().__init__(parent, width=22, height=22, bg=parent["bg"], highlightthickness=0, cursor="hand2",
                         takefocus=1)
        self.theme = theme
        self.var = variable
        self.command = command
        self.enabled = True
        self._hover = False
        self.bind("<Button-1>", self._toggle)
        self.bind("<space>", self._toggle)
        self.bind("<Enter>", lambda e: (setattr(self, "_hover", True), self._draw()))
        self.bind("<Leave>", lambda e: (setattr(self, "_hover", False), self._draw()))
        self.var.trace_add("write", lambda *_: self._draw())
        self._draw()

    def _toggle(self, _e=None):
        if not self.enabled:
            return
        self.var.set(not self.var.get())
        if self.command:
            self.command()

    def _draw(self):
        self.delete("all")
        t = self.theme
        on = bool(self.var.get())
        if not self.enabled:
            fill, outline, fg = t["surface3"], t["surface3"], t["faint"]
        elif on:
            fill, outline, fg = t["accent"], t["accent"], t["accent_text"]
        else:
            fill = blend(self.master["bg"], t["text"], 0.06 if self._hover else 0.0)
            outline, fg = t["strong"], ""
        rounded(self, 1.5, 1.5, 20.5, 20.5, 5, fill=fill, outline=outline)
        if on:
            self.create_text(11, 11, text=ICON["check"], font=t.fonts["icon_s"], fill=fg)

    def set_enabled(self, on: bool):
        self.enabled = on
        self.configure(cursor="hand2" if on else "arrow")
        self._draw()


class Segmented(tk.Canvas):
    """WinUI SelectorBar: text items with a sliding accent underline under the selection."""

    def __init__(self, parent, theme: Theme, options: list[tuple[str, str]], variable: tk.StringVar,
                 command: Callable | None = None, font: str = "body", padx: int = 12, height: int = 36):
        super().__init__(parent, bg=parent["bg"], highlightthickness=0, bd=0, cursor="hand2")
        self.theme = theme
        self.options = options
        self.var = variable
        self.command = command
        self.enabled = True
        self.font = theme.fonts[font]
        self.h = height
        self._x: list[tuple[float, float]] = []
        x = 0
        for _v, text in options:
            w = self.font.measure(text) + 2 * padx
            self._x.append((x, x + w))
            x += w + 2
        self.configure(width=x, height=height)
        self._disp = float(self._index())
        self._hover_i = -1
        self._tok: list = []
        self.bind("<Button-1>", self._click)
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda e: self._set_hover(-1))
        self.var.trace_add("write", lambda *_: self._moved())
        self._draw()

    def _index(self) -> int:
        vals = [v for v, _ in self.options]
        return vals.index(self.var.get()) if self.var.get() in vals else 0

    def _at(self, x) -> int:
        for i, (a, b) in enumerate(self._x):
            if a <= x <= b:
                return i
        return -1

    def _motion(self, e):
        self._set_hover(self._at(e.x))

    def _set_hover(self, i):
        if i != self._hover_i:
            self._hover_i = i
            self._draw()

    def _click(self, e):
        if not self.enabled:
            return
        i = self._at(e.x)
        if i >= 0:
            self.var.set(self.options[i][0])
            if self.command:
                self.command()

    def _moved(self):
        start, target = self._disp, float(self._index())

        def step(t):
            self._disp = start + (target - start) * t
            self._draw()
        animate(self, 180, step, token=self._tok)

    def _draw(self):
        self.delete("all")
        t = self.theme
        cur = self._index()
        for i, ((a, b), (_v, text)) in enumerate(zip(self._x, self.options)):
            if i == self._hover_i and self.enabled:
                rounded(self, a, 2, b, self.h - 2, t.ctrl_radius, fill=blend(self.master["bg"], t["text"], 0.05),
                        outline="")
            fg = t["faint"] if not self.enabled else (t["text"] if i == cur else t["muted"])
            self.create_text((a + b) / 2, self.h / 2 - 2, text=text, font=self.font, fill=fg)
        i0 = int(self._disp)
        i1 = min(i0 + 1, len(self._x) - 1)
        f = self._disp - i0
        ca = (self._x[i0][0] + self._x[i0][1]) / 2
        cb = (self._x[i1][0] + self._x[i1][1]) / 2
        c = ca + (cb - ca) * f
        col = t["accent"] if self.enabled else t["faint"]
        rounded(self, c - 8, self.h - 4, c + 8, self.h - 1, 1.5, fill=col, outline=col)

    def set_enabled(self, on: bool):
        self.enabled = on
        self.configure(cursor="hand2" if on else "arrow")
        self._draw()


class Stepper(tk.Canvas):
    """WinUI NumberBox: value box with inline down/up spin buttons."""

    def __init__(self, parent, theme: Theme, variable: tk.IntVar, lo: int, hi: int, step: int = 1, suffix: str = "",
                 command: Callable | None = None, width: int = 132):
        h = theme.fonts["body"].metrics("linespace") + 12
        super().__init__(parent, bg=parent["bg"], highlightthickness=0, bd=0, width=width, height=h)
        self.theme = theme
        self.var, self.lo, self.hi, self.step, self.suffix, self.command = variable, lo, hi, step, suffix, command
        self.enabled = True
        self.w, self.h = width, h
        self._hover = -1  # 0 = down, 1 = up
        t = theme
        r = t.ctrl_radius
        self._rect = rounded(self, 0.5, 0.5, width - 0.5, h - 0.5, r, fill=t["surface2"], outline=t["stroke"])
        self._under = self.create_line(r, h - 1, width - r, h - 1, fill=t["strong"])
        self._txt = self.create_text(11, h / 2, text="", anchor="w", font=t.fonts["body"], fill=t["text"])
        bw = 26
        self._btn_x = [(width - 2 * bw - 4, width - bw - 4), (width - bw - 2, width - 2)]
        self._btn_bg = [rounded(self, a, 3, b, h - 3, 3, fill="", outline="") for a, b in self._btn_x]
        self._btn_g = [self.create_text((a + b) / 2, h / 2, text=g, font=t.fonts["icon_s"], fill=t["muted"])
                       for (a, b), g in zip(self._btn_x, (ICON["down"], ICON["up"]))]
        self.bind("<Button-1>", self._click)
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda e: self._set_hover(-1))
        self.bind("<MouseWheel>", lambda e: self._bump(1 if e.delta > 0 else -1))
        self.var.trace_add("write", lambda *_: self._refresh())
        self._refresh()

    def _which(self, x, y) -> int:
        for i, (a, b) in enumerate(self._btn_x):
            if a <= x <= b and 3 <= y <= self.h - 3:
                return i
        return -1

    def _motion(self, e):
        self._set_hover(self._which(e.x, e.y))

    def _set_hover(self, i):
        self._hover = i
        t = self.theme
        for j, item in enumerate(self._btn_bg):
            col = blend(t["surface2"], t["text"], 0.08) if (j == i and self.enabled) else ""
            self.itemconfigure(item, fill=col, outline=col)
        self.configure(cursor="hand2" if i >= 0 and self.enabled else "arrow")

    def _click(self, e):
        i = self._which(e.x, e.y)
        if i >= 0:
            self._bump(1 if i == 1 else -1)

    def _bump(self, d: int):
        if not self.enabled:
            return
        try:
            v = int(self.var.get())
        except tk.TclError:
            v = self.lo
        self.var.set(max(self.lo, min(self.hi, v + d * self.step)))
        if self.command:
            self.command()

    def _refresh(self):
        try:
            v = int(self.var.get())
        except tk.TclError:
            v = self.lo
        self.itemconfigure(self._txt, text=f"{v}{self.suffix}")

    def set_enabled(self, on: bool):
        self.enabled = on
        t = self.theme
        self.itemconfigure(self._txt, fill=t["text"] if on else t["faint"])
        for g in self._btn_g:
            self.itemconfigure(g, fill=t["muted"] if on else t["faint"])


class Chip(tk.Canvas):
    """Small tag with optional remove glyph (4 px radius, 1 px stroke)."""

    def __init__(self, parent, theme: Theme, text: str, on_remove: Callable | None = None, accent: bool = False):
        super().__init__(parent, bg=parent["bg"], highlightthickness=0, bd=0)
        self.theme = theme
        f = theme.fonts["small"]
        w = f.measure(text) + 20 + (20 if on_remove else 0)
        h = f.metrics("linespace") + 10
        self.configure(width=w, height=h)
        fill = theme["accent_soft"] if accent else theme["surface2"]
        outline = blend(theme["accent_soft"], theme["accent"], 0.3) if accent else theme["stroke"]
        rounded(self, 0.5, 0.5, w - 0.5, h - 0.5, theme.ctrl_radius, fill=fill, outline=outline)
        self.create_text(10, h / 2, text=text, anchor="w", font=f, fill=theme["accent"] if accent else theme["text"])
        if on_remove:
            x = self.create_text(w - 10, h / 2, text=ICON["cross"], anchor="e", font=theme.fonts["icon_s"],
                                 fill=theme["muted"])
            self.tag_bind(x, "<Button-1>", lambda e: on_remove())
            self.tag_bind(x, "<Enter>", lambda e: self.itemconfigure(x, fill=theme["text"]))
            self.tag_bind(x, "<Leave>", lambda e: self.itemconfigure(x, fill=theme["muted"]))
            self.configure(cursor="hand2")


class ListPanel(tk.Canvas):
    """Bordered container around a Listbox (fast for hundreds of rows)."""

    def __init__(self, parent, theme: Theme, height: int = 8, selectmode: str = "extended"):
        super().__init__(parent, bg=parent["bg"], highlightthickness=0, bd=0)
        self.theme = theme
        r = theme.ctrl_radius
        self._rect = rounded(self, 0, 0, 10, 10, r, fill=theme["surface2"], outline=theme["stroke"])
        inner = tk.Frame(self, bg=theme["surface2"])
        self.list = tk.Listbox(inner, height=height, selectmode=selectmode, relief="flat", bd=0,
                               font=theme.fonts["body"], bg=theme["surface2"], fg=theme["text"],
                               selectbackground=theme["sel"], selectforeground=theme["text"], highlightthickness=0,
                               activestyle="none")
        self.sb = ThinScrollbar(inner, theme, self.list.yview, bg=theme["surface2"])
        self.list.configure(yscrollcommand=self.sb.set)
        self.sb.pack(side="right", fill="y", pady=6, padx=(0, 3))
        self.list.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=6)
        self._win = self.create_window((1, 1), window=inner, anchor="nw")
        rows = height * theme.fonts["body"].metrics("linespace") + 12
        self.configure(height=rows + 2)
        self.bind("<Configure>", lambda e: (self.coords(self._rect, *_poly(0.5, 0.5, e.width - 0.5, e.height - 0.5, r)),
                                            self.itemconfigure(self._win, width=e.width - 2, height=e.height - 2)))


class TextArea(tk.Canvas):
    def __init__(self, parent, theme: Theme, height: int = 6):
        super().__init__(parent, bg=parent["bg"], highlightthickness=0, bd=0)
        r = theme.ctrl_radius
        self._rect = rounded(self, 0, 0, 10, 10, r, fill=theme["surface2"], outline=theme["stroke"])
        self._under = self.create_line(0, 0, 0, 0, fill=theme["strong"])
        self.text = tk.Text(self, height=height, relief="flat", bd=0, bg=theme["surface2"], fg=theme["text"],
                            insertbackground=theme["text"], font=theme.fonts["body"], wrap="word", highlightthickness=0,
                            padx=10, pady=8)
        self._win = self.create_window((1, 1), window=self.text, anchor="nw")
        self.configure(height=height * theme.fonts["body"].metrics("linespace") + 18)

        def resize(e):
            self.coords(self._rect, *_poly(0.5, 0.5, e.width - 0.5, e.height - 0.5, r))
            self.coords(self._under, r, e.height - 1, e.width - r, e.height - 1)
            self.itemconfigure(self._win, width=e.width - 2, height=e.height - 3)
        self.bind("<Configure>", resize)
        self.text.bind("<FocusIn>", lambda e: self.itemconfigure(self._under, fill=theme["accent"], width=2))
        self.text.bind("<FocusOut>", lambda e: self.itemconfigure(self._under, fill=theme["strong"], width=1))


# ----------------------------------------------------------------- scrolling
class ThinScrollbar(tk.Canvas):
    """Windows-style thin rail scrollbar; implements the set/command protocol."""

    def __init__(self, parent, theme: Theme, command: Callable, bg: str | None = None, width: int = 6):
        # height=10: a Canvas otherwise asks for 7 cm, which would force every scroll container that tall
        super().__init__(parent, width=width, height=10, bg=bg or parent["bg"], highlightthickness=0, bd=0)
        self.theme = theme
        self.command = command
        self.lo, self.hi = 0.0, 1.0
        self._drag = None
        self._hover = False
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Enter>", lambda e: (setattr(self, "_hover", True), self._draw()))
        self.bind("<Leave>", lambda e: (setattr(self, "_hover", False), self._draw()))
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._motion)
        self.bind("<ButtonRelease-1>", lambda e: setattr(self, "_drag", None))

    def set(self, lo, hi):
        self.lo, self.hi = float(lo), float(hi)
        self._draw()

    def _draw(self):
        self.delete("all")
        h = self.winfo_height()
        w = int(self["width"])
        if self.hi - self.lo >= 0.999:
            return
        y0 = self.lo * h
        y1 = max(y0 + 24, self.hi * h)
        if self._hover or self._drag:
            rounded(self, 0, y0, w, y1, w / 2, fill=self.theme["strong"], outline="")
        else:
            rounded(self, w / 2 - 1, y0, w / 2 + 1, y1, 1, fill=self.theme["strong"], outline="")

    def _press(self, e):
        h = self.winfo_height()
        self._drag = (e.y, self.lo)
        if not (self.lo * h <= e.y <= self.hi * h):
            self.command("moveto", max(0, e.y / h - (self.hi - self.lo) / 2))
            self._drag = (e.y, max(0, e.y / h - (self.hi - self.lo) / 2))

    def _motion(self, e):
        if self._drag:
            y, lo = self._drag
            self.command("moveto", lo + (e.y - y) / max(1, self.winfo_height()))


class Scroll(tk.Frame):
    """Vertical smooth-scrolling container. Put children in `.body`."""

    FPS = 60  # scroll animation frame-rate cap; frames are paint-synchronised (see `animate_synced`)

    def __init__(self, parent, theme: Theme, **kw):
        super().__init__(parent, bg=parent["bg"], **kw)
        self.theme = theme
        self.canvas = tk.Canvas(self, bg=parent["bg"], highlightthickness=0, bd=0)
        self.sb = ThinScrollbar(self, theme, self._yview)
        self.canvas.configure(yscrollcommand=self.sb.set)
        self.sb.pack(side="right", fill="y", padx=(0, 3), pady=8)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body = tk.Frame(self.canvas, bg=parent["bg"])
        self._win = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self._region = None
        self.body.bind("<Configure>", self._body_changed)
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        self.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", self._wheel))
        self.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))
        self._target = None
        self._tok: list = []
        self._settle_job = None

    def _body_changed(self, e):
        # The body moves on every scroll step, which also fires <Configure>; only a size change needs a new
        # scrollregion (configuring it schedules a full canvas redraw, so avoid doing that 60 times a second).
        region = (0, 0, e.width, e.height)
        if region != self._region:
            self._region = region
            self.canvas.configure(scrollregion=region)

    def _yview(self, *args):
        """Scrollbar protocol: move, then repaint what the move could have copied stale (see `invalidate`)."""
        if not self.canvas.winfo_exists():
            return
        y0 = self.canvas.canvasy(0)
        self.canvas.yview(*args)
        self._after_move(y0)

    def _moveto(self, frac: float):
        if not self.canvas.winfo_exists():
            return
        y0 = self.canvas.canvasy(0)
        self.canvas.yview_moveto(frac)
        self._after_move(y0)

    def _after_move(self, y0: float):
        """Stale copies can only sit in content that entered the view during the last few frames (Windows itself
        invalidates what enters now). Repaint that band at the leading edge, and the whole view once scrolling
        settles, so nothing stale survives whatever the frame timing was."""
        c = self.canvas
        dy = c.canvasy(0) - y0
        if dy == 0:
            return
        w, h = c.winfo_width(), c.winfo_height()
        band = int(min(h, abs(dy) * 4 + 48))
        invalidate(c, (0, h - band, w, h) if dy > 0 else (0, 0, w, band))
        flush_paint(c)
        if self._settle_job:
            self.after_cancel(self._settle_job)
        self._settle_job = self.after(120, self._settled)

    def _settled(self):
        self._settle_job = None
        if self.canvas.winfo_exists():
            invalidate(self.canvas)

    def _wheel(self, e):
        # Let NumberBoxes take the wheel when the pointer is over them.
        w = self.winfo_containing(e.x_root, e.y_root)
        if isinstance(w, Stepper):
            return
        lo, hi = self.canvas.yview()
        if hi - lo >= 0.999:
            return
        page = hi - lo
        cur = self._target if self._target is not None else lo
        delta = -(e.delta / 120) * page * 0.35
        self._target = max(0.0, min(1 - page, cur + delta))
        start = lo
        target = self._target

        def step(t):
            self._moveto(start + (target - start) * t)

        def done():
            self._target = None
        animate_synced(self, 220, step, done, token=self._tok, min_interval_ms=1000 // self.FPS)

    def scroll_top(self):
        self._target = None
        self._moveto(0)

    def scroll_to(self, frac: float):
        self._target = None
        self._moveto(frac)


# ------------------------------------------------------------------- helpers
def label(parent, theme: Theme, text: str, font: str = "body", color: str = "text", **kw) -> tk.Label:
    """A themed text label on the parent's background (font and colour are theme token names)."""
    return tk.Label(parent, text=text, bg=parent["bg"], fg=theme[color], font=theme.fonts[font], **kw)


def autowrap(lbl: tk.Label, container: tk.Misc, margin: int = 0, max_w: int = 820) -> tk.Label:
    """Keep a wrapped label no wider than `container` (minus margin), so narrow windows never clip text.
    Bind to a container that fills its row (a card body, a grid frame), never to the label's own parent when
    that parent sizes itself to the label."""
    def fit(e):
        try:
            lbl.configure(wraplength=max(200, min(max_w, e.width - margin)))
        except tk.TclError:
            pass
    container.bind("<Configure>", fit, add="+")
    return lbl


def icon_label(parent, theme: Theme, glyph: str, color: str = "muted", size: str = "icon") -> tk.Label:
    """A single Fluent icon glyph as a label."""
    return tk.Label(parent, text=glyph, bg=parent["bg"], fg=theme[color], font=theme.fonts[size])


def title_block(parent, theme: Theme, title: str, subtitle: str = "", wrap: int = 760) -> tk.Frame:
    """Card heading: bold title with an optional muted subtitle that wraps to the card."""
    f = tk.Frame(parent, bg=parent["bg"])
    label(f, theme, title, "h3").pack(anchor="w")
    if subtitle:
        sub = label(f, theme, subtitle, "small", "muted", wraplength=wrap, justify="left")
        sub.pack(anchor="w", pady=(2, 0))
        autowrap(sub, parent, 60, wrap)  # the parent row fills the card; the block itself sizes to its text
    return f


def row(parent, pady=(0, 0)) -> tk.Frame:
    """A full-width horizontal strip (already packed) to lay widgets out side by side."""
    f = tk.Frame(parent, bg=parent["bg"])
    f.pack(fill="x", pady=pady)
    return f


def divider(parent, theme: Theme, pady=(12, 12)) -> tk.Frame:
    """A 1 px horizontal rule in the border colour (already packed)."""
    f = tk.Frame(parent, bg=theme["border"], height=1)
    f.pack(fill="x", pady=pady)
    return f


def popup_menu(owner: tk.Misc, theme: Theme, items: list[tuple], x: int, y: int) -> tk.Menu:
    """Show a themed popup menu at screen position (x, y). `items` are (label, command) pairs; (None, None) is
    a separator and a label with command None is shown disabled. Labels are clipped to 80 characters so model
    or user text can never produce an unusable menu."""
    m = tk.Menu(owner, tearoff=0, bg=theme["surface2"], fg=theme["text"], activebackground=theme["accent_soft"],
                activeforeground=theme["text"], bd=0, relief="flat", font=theme.fonts["body"], activeborderwidth=0)
    for text, cmd in items:
        if text is None:
            m.add_separator()
        elif cmd is None:
            m.add_command(label=str(text)[:80], state="disabled")
        else:
            m.add_command(label=str(text)[:80], command=cmd)
    m.tk_popup(int(x), int(y))
    return m


def infobar(parent, theme: Theme, text: str, kind: str = "info", wrap: int = 760) -> Surface:
    """WinUI InfoBar: tinted strip with a leading glyph. kind: info, warning, success, danger."""
    col = {"info": theme["accent"], "warning": theme["warn"], "success": theme["success"], "danger": theme["danger"]}[kind]
    glyph = {"info": ICON["info"], "warning": ICON["warning"], "success": ICON["check"], "danger": ICON["warning"]}[kind]
    fill = blend(theme["surface"], col, 0.14)
    s = Surface(parent, theme, pad=12, fill=fill, outline=blend(fill, col, 0.3), radius=theme.ctrl_radius)
    tk.Label(s.body, text=glyph, bg=fill, fg=col, font=theme.fonts["icon"]).pack(side="left", anchor="n", padx=(2, 10))
    tk.Label(s.body, text=text, bg=fill, fg=theme["text"], font=theme.fonts["small"], wraplength=wrap,
             justify="left").pack(side="left", anchor="w", fill="x")
    return s


def fmt_duration(seconds: int) -> str:
    """Human length: '45s', '12 min', '2h 05m', '3d 4h'."""
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h >= 48:
        d, h = divmod(h, 24)
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m:02d}m"
    if m or not s:
        return f"{m} min"
    return f"{s}s"


def fmt_clock(seconds: int) -> str:
    """Countdown text: 'MM:SS', 'H:MM:SS', or 'Nd HH:MM' past two days."""
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h >= 48:
        d, h = divmod(h, 24)
        return f"{d}d {h:02d}:{m:02d}"
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
