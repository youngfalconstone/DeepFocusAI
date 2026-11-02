"""Focus sounds: looping ambient noise generated on first use, played with winsound.

No audio files ship with the app. Each sound is synthesised once (8 seconds, 22.05 kHz, mono,
16-bit) into the state folder and then looped with the Windows multimedia API, which the
standard library exposes as `winsound`. Playback is per process: it stops when the window
closes, which is the intended behaviour for a focus aid.
"""
from __future__ import annotations

import math
import random
import struct
import wave

from . import paths

SOUNDS = [("off", "Off"), ("rain", "Rain"), ("brown", "Brown noise"), ("white", "White noise"), ("hum", "Deep hum")]
RATE = 22050
SECONDS = 8
_current = "off"


def _samples(kind: str) -> list[int]:
    rnd = random.Random(1234)
    n = RATE * SECONDS
    out: list[int] = []
    if kind == "white":
        for _ in range(n):
            out.append(int(rnd.uniform(-1, 1) * 9000))
    elif kind == "brown":
        v = 0.0
        for _ in range(n):
            v += rnd.uniform(-1, 1) * 0.02
            v = max(-1.0, min(1.0, v * 0.998))
            out.append(int(v * 24000))
    elif kind == "rain":
        # low-passed noise with slow amplitude drift and sparse "drops"
        lp = 0.0
        for i in range(n):
            lp += (rnd.uniform(-1, 1) - lp) * 0.25
            drift = 0.75 + 0.25 * math.sin(i / RATE * 0.9) * math.sin(i / RATE * 0.37 + 1.3)
            v = lp * 0.6 * drift
            if rnd.random() < 0.0015:
                v += rnd.uniform(-0.5, 0.5)
            out.append(int(max(-1.0, min(1.0, v)) * 14000))
    else:  # hum: two soft low tones with gentle beating
        for i in range(n):
            t = i / RATE
            v = 0.5 * math.sin(2 * math.pi * 55 * t) + 0.35 * math.sin(2 * math.pi * 82.4 * t) \
                + 0.15 * math.sin(2 * math.pi * 110 * t)
            v *= 0.8 + 0.2 * math.sin(2 * math.pi * 0.1 * t)
            out.append(int(v * 9000))
    # seamless loop: cross-fade the last 0.25 s into the first 0.25 s
    f = RATE // 4
    for i in range(f):
        a = i / f
        out[n - f + i] = int(out[n - f + i] * (1 - a) + out[i] * a)
    return out


def file_for(kind: str):
    d = paths.STATE_DIR / "sounds"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{kind}.wav"
    if not p.exists():
        data = _samples(kind)
        with wave.open(str(p), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(struct.pack(f"<{len(data)}h", *data))
    return p


def play(kind: str) -> None:
    """Start looping `kind` (or stop for 'off'). Safe to call repeatedly."""
    global _current
    import winsound
    if kind == _current:
        return
    winsound.PlaySound(None, winsound.SND_PURGE)
    _current = "off"
    if kind and kind != "off":
        try:
            winsound.PlaySound(str(file_for(kind)), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_LOOP)
            _current = kind
        except (RuntimeError, OSError):
            _current = "off"


def stop() -> None:
    play("off")


def current() -> str:
    return _current


def label(kind: str) -> str:
    return dict(SOUNDS).get(kind, "Off")
