"""Generate assets/deepfocus.ico (and a 256px PNG) with no third-party libraries.
Design: rounded blue square, white focus ring with a gap, solid centre dot."""
from __future__ import annotations

import math
import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "assets"
OUT_DIR.mkdir(exist_ok=True)

BG = (0x5B, 0x8D, 0xFF)
BG2 = (0x3B, 0x6F, 0xF0)
FG = (0xFF, 0xFF, 0xFF)


def render(size: int, ss: int = 4) -> bytes:
    """Return RGBA bytes for a size x size icon, supersampled ss x ss."""
    n = size * ss
    px = bytearray(n * n * 4)
    r_corner = n * 0.22
    cx = cy = n / 2
    ring_r = n * 0.30
    ring_w = n * 0.085
    dot_r = n * 0.10
    gap_a0, gap_a1 = math.radians(300), math.radians(345)  # gap in the ring (top-right)
    for y in range(n):
        for x in range(n):
            # rounded square mask
            dx = max(abs(x + 0.5 - cx) - (n / 2 - r_corner), 0)
            dy = max(abs(y + 0.5 - cy) - (n / 2 - r_corner), 0)
            inside = (dx * dx + dy * dy) <= r_corner * r_corner
            i = (y * n + x) * 4
            if not inside:
                continue
            t = (x + y) / (2 * n)
            col = tuple(round(BG[k] + (BG2[k] - BG[k]) * t) for k in range(3))
            d = math.hypot(x + 0.5 - cx, y + 0.5 - cy)
            ang = math.atan2(-(y + 0.5 - cy), x + 0.5 - cx) % (2 * math.pi)
            if abs(d - ring_r) <= ring_w / 2 and not (gap_a0 <= ang <= gap_a1):
                col = FG
            elif d <= dot_r:
                col = FG
            px[i:i + 4] = bytes((*col, 255))
    # downsample
    out = bytearray(size * size * 4)
    for y in range(size):
        for x in range(size):
            acc = [0, 0, 0, 0]
            for sy in range(ss):
                for sx in range(ss):
                    j = ((y * ss + sy) * n + (x * ss + sx)) * 4
                    for k in range(4):
                        acc[k] += px[j + k]
            o = (y * size + x) * 4
            a = acc[3] / (ss * ss)
            if a > 0:
                out[o:o + 3] = bytes(round(acc[k] / (ss * ss) * 255 / a) if a else 0 for k in range(3))
            out[o + 3] = round(a)
    return bytes(out)


def png(size: int, rgba: bytes) -> bytes:
    raw = b"".join(b"\x00" + rgba[y * size * 4:(y + 1) * size * 4] for y in range(size))

    def chunk(tag: bytes, data: bytes) -> bytes:
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def ico(images: list[tuple[int, bytes]]) -> bytes:
    head = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, data = b"", b""
    for size, blob in images:
        s = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", s, s, 0, 0, 1, 32, len(blob), offset + len(data))
        data += blob
    return head + entries + data


def main():
    sizes = [16, 24, 32, 48, 64, 128, 256]
    images = []
    for s in sizes:
        rgba = render(s, ss=4 if s <= 64 else 2)
        images.append((s, png(s, rgba)))
        if s == 256:
            (OUT_DIR / "deepfocus.png").write_bytes(images[-1][1])
    (OUT_DIR / "deepfocus.ico").write_bytes(ico(images))
    print("wrote", OUT_DIR / "deepfocus.ico")


if __name__ == "__main__":
    sys.exit(main())
