"""Pure functions for building and removing the DeepFocus block in a hosts file.

The block is bracketed by two marker comments so it can always be removed
exactly, even if the session record is lost.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable

BEGIN = "# >>> DeepFocus AI session block - do not edit between these markers >>>"
END = "# <<< DeepFocus AI session block <<<"

_HOST_RE = re.compile(r"^(?=.{1,253}$)(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))+$")

# Names that must never be sent to 0.0.0.0 or the machine stops working.
PROTECTED = {
    "localhost", "localhost.localdomain", "broadcasthost",
    "microsoft.com", "www.microsoft.com", "login.microsoftonline.com", "windowsupdate.com",
    "update.microsoft.com", "login.live.com", "account.microsoft.com",
    "google.com", "www.google.com", "accounts.google.com", "cloudflare.com",
    "anthropic.com", "claude.ai",
}


def normalise_host(h: str) -> str | None:
    h = h.strip().lower().rstrip(".")
    for prefix in ("http://", "https://"):
        if h.startswith(prefix):
            h = h[len(prefix):]
    h = h.split("/")[0].split(":")[0]
    if h.startswith("*."):
        h = h[2:]
    if not h or not _HOST_RE.match(h):
        return None
    return h


def strip_block(text: str) -> tuple[str, int]:
    """Remove every DeepFocus block. Returns (new_text, lines_removed)."""
    out, removed, inside = [], 0, False
    for line in text.splitlines():
        s = line.strip()
        if s == BEGIN:
            inside = True
            removed += 1
            continue
        if s == END:
            inside = False
            removed += 1
            continue
        if inside:
            removed += 1
            continue
        out.append(line)
    while out and out[-1].strip() == "":
        out.pop()
    return ("\n".join(out) + "\n") if out else "", removed


def build_block(hostnames: Iterable[str], session_id: str, restore_at: str, ipv6: bool = True) -> str:
    lines = ["", BEGIN, f"# session {session_id} - restores automatically at {restore_at}"]
    seen = set()
    for h in hostnames:
        n = normalise_host(h)
        if not n or n in seen or n in PROTECTED:
            continue
        seen.add(n)
        lines.append(f"0.0.0.0 {n}")
        if ipv6:
            lines.append(f"::1 {n}")
    lines.append(END)
    return "\n".join(lines) + "\n"


def apply_block(text: str, hostnames: Iterable[str], session_id: str, restore_at: str, ipv6: bool = True) -> str:
    base, _ = strip_block(text)
    return base + build_block(hostnames, session_id, restore_at, ipv6)


def has_block(text: str) -> bool:
    return BEGIN in text


def extract_block(text: str) -> str:
    """The current DeepFocus block (markers included, normalised line endings), or ''."""
    lines = text.replace("\r\n", "\n").split("\n")
    out, inside = [], False
    for line in lines:
        s = line.strip()
        if s == BEGIN:
            inside = True
        if inside:
            out.append(s)
        if s == END:
            break
    return "\n".join(out)


def block_matches(text: str, hostnames: Iterable[str], session_id: str, restore_at: str, ipv6: bool = True) -> bool:
    """True if the block in `text` is exactly what we would write now (nothing removed or edited)."""
    want = build_block(hostnames, session_id, restore_at, ipv6).strip()
    return extract_block(text) == want


def read_hosts(path: Path) -> str:
    """The hosts file as text. Bytes that are not UTF-8 survive as surrogates so write_hosts puts them back."""
    try:
        return path.read_bytes().decode("utf-8", errors="surrogateescape")
    except FileNotFoundError:
        return ""


def write_hosts(path: Path, text: str) -> None:
    """Write the hosts file: CRLF line endings, original bytes preserved, and atomically (temp file in the same
    folder, then replace) so a crash can never leave a half-written hosts file."""
    data = text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8", errors="surrogateescape")
    # Some tools mark hosts read-only. Clear the bit for the write and put it back.
    readonly = False
    try:
        from . import filehide
        a = filehide.get_attrs(str(path))
        if a is not None and a & filehide.READONLY:
            filehide.set_attrs(str(path), a & ~filehide.READONLY)
            readonly = True
    except OSError:
        pass
    try:
        tmp = path.with_name(path.name + ".deepfocus.tmp")
        try:
            with open(tmp, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except OSError:
            # replace can be refused (permissions, locks): fall back to writing in place
            try:
                tmp.unlink()
            except OSError:
                pass
            with open(path, "wb") as f:
                f.write(data)
    finally:
        if readonly:
            try:
                a = filehide.get_attrs(str(path))
                if a is not None:
                    filehide.set_attrs(str(path), a | filehide.READONLY)
            except OSError:
                pass
