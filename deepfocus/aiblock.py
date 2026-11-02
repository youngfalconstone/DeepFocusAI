"""AI Block: turn the name of an app or service into a reviewable block list.

Flow: `generate(query, cfg)` sends only the sanitised service name to the model (see `llm`), asks for JSON in a
fixed schema, validates and normalises every entry, and classifies each one: protected names are rejected,
broad shared infrastructure is flagged and left unchecked, files are expanded from placeholders and checked
against the same path guard the rest of the app uses. `fallback(query)` builds the same shape from the built-in
catalog when the AI is unavailable. Model output is data: it is never executed, never put in a command line,
and only reaches the hosts file or the file hider after the user reviews it and through the existing validators.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import catalog, guard, hostsfile, llm, procwatch

GROUPS = [("main", "Main site"), ("api_cdn", "API and content delivery"), ("login", "Login and sign-in"),
          ("downloads", "Downloads and updates")]
GROUP_LABEL = dict(GROUPS)
ROLES = {"app": "desktop app", "launcher": "launcher", "helper": "helper process", "updater": "updater"}

MAX_QUERY = 60
MAX_DOMAINS, MAX_APPS, MAX_FILES = 80, 30, 30

# Blocking anything under these would break Windows, sign-in, certificates or the AI provider itself.
PROTECTED_SUFFIXES = (
    "microsoft.com", "microsoftonline.com", "windowsupdate.com", "windows.com", "live.com", "msftconnecttest.com",
    "msftncsi.com", "office.com", "office.net", "azure.com", "azure.net", "xboxlive.com", "msauth.net", "msocdn.com",
    "digicert.com", "verisign.com", "globalsign.com", "letsencrypt.org", "sectigo.com", "entrust.net",
    "anthropic.com", "claude.ai", "openai.com", "localhost", "local", "localdomain", "home.arpa", "in-addr.arpa",
)
# Shared infrastructure: blocking it takes unrelated sites down with it. Flagged and unchecked by default.
BROAD_SUFFIXES = (
    "cloudfront.net", "akamaihd.net", "akamaized.net", "akamai.net", "akamaiedge.net", "edgekey.net", "edgesuite.net",
    "cloudflare.net", "cloudflare.com", "fastly.net", "fastlylb.net", "googleapis.com", "gstatic.com",
    "googleusercontent.com", "amazonaws.com", "azureedge.net", "azurefd.net", "windows.net", "msecnd.net",
    "llnwd.net", "cdn77.org", "jsdelivr.net", "unpkg.com", "cloudflare-dns.com", "doubleclick.net",
    "googlesyndication.com", "googletagmanager.com", "google-analytics.com", "akstat.io", "trafficmanager.net",
)
BROAD_EXACT = {"google.com", "www.google.com", "amazon.com", "www.amazon.com", "apple.com", "www.apple.com",
               "facebook.com", "www.facebook.com", "cdn.jsdelivr.net", "fonts.googleapis.com", "fonts.gstatic.com"}
# Known service-specific hosts under broad suffixes that are fine to block.
BROAD_EXCEPTIONS = {"youtubei.googleapis.com", "yt3.ggpht.com"}

PROTECTED_PROCESSES = {
    "system", "explorer.exe", "svchost.exe", "csrss.exe", "winlogon.exe", "dwm.exe", "lsass.exe", "services.exe",
    "smss.exe", "wininit.exe", "taskhostw.exe", "runtimebroker.exe", "searchhost.exe", "sihost.exe", "ctfmon.exe",
    "fontdrvhost.exe", "conhost.exe", "powershell.exe", "pwsh.exe", "cmd.exe", "deepfocus.exe", "python.exe",
    "pythonw.exe", "msmpeng.exe", "securityhealthservice.exe", "audiodg.exe", "startmenuexperiencehost.exe",
    "shellexperiencehost.exe", "textinputhost.exe", "applicationframehost.exe", "spoolsv.exe", "wmiprvse.exe",
    "dllhost.exe", "schtasks.exe", "taskkill.exe", "tasklist.exe", "rundll32.exe", "userinit.exe", "logonui.exe",
    "lockapp.exe", "wuauclt.exe", "tiworker.exe", "trustedinstaller.exe", "msiexec.exe", "ntoskrnl.exe", "registry",
}
_EXE_RE = re.compile(r"^[A-Za-z0-9 _.\-+()!&~]{1,80}\.exe$", re.IGNORECASE)
_IP_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
# control characters, bidi overrides/isolates and zero-width marks: none of them belong in a hostname, a file
# name, a label or a menu entry, and the bidi ones can make text read backwards in the UI
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f​-‏ -‮⁠-⁤⁦-⁩﻿]")


def clean_text(text, limit: int = 200) -> str:
    """Model or user text made safe for labels and files: no control/bidi characters, trimmed, length-capped."""
    if not isinstance(text, str):
        return ""
    return _CONTROL_RE.sub("", text).strip()[:limit]

PLACEHOLDERS = ("{Desktop}", "{PublicDesktop}", "{Downloads}", "{Documents}", "{UserProfile}")


class AIBlockError(Exception):
    pass


@dataclass
class Item:
    kind: str          # domain | app | file
    value: str         # normalised hostname, exe name, or expanded absolute path
    group: str = ""    # domain group key, or app role
    reason: str = ""
    confidence: float = 0.5
    checked: bool = True
    status: str = "ok"  # ok | broad | protected | invalid | refused | missing | duplicate
    note: str = ""
    flagged: bool = False
    original: str = ""  # what the model wrote (for files: the template)

    @property
    def usable(self) -> bool:
        return self.status in ("ok", "broad", "missing") and self.kind in ("domain", "app", "file")


@dataclass
class Result:
    query: str
    service: str
    known: bool
    source: str  # ai | offline | saved
    items: list[Item] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def of(self, kind: str) -> list[Item]:
        return [i for i in self.items if i.kind == kind]

    def selected(self, kind: str) -> list[str]:
        return [i.value for i in self.items if i.kind == kind and i.checked and i.usable]


# ----------------------------------------------------------------------------- schema and prompt
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["service", "known", "domains", "apps", "files"],
    "properties": {
        "service": {"type": "string", "description": "Canonical name of the service, or the input if unknown"},
        "known": {"type": "boolean", "description": "true only if you recognise the service and know its domains"},
        "domains": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["host", "group", "reason", "confidence"],
                "properties": {
                    "host": {"type": "string", "description": "Hostname; use *.example.com for a whole domain"},
                    "group": {"type": "string", "enum": ["main", "api_cdn", "login", "downloads"]},
                    "reason": {"type": "string"},
                    "confidence": {"type": "number"},
                },
            },
        },
        "apps": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["exe", "role", "locations", "reason", "confidence"],
                "properties": {
                    "exe": {"type": "string", "description": "Windows executable file name, e.g. Discord.exe"},
                    "role": {"type": "string", "enum": ["app", "launcher", "helper", "updater"]},
                    "locations": {"type": "array", "items": {"type": "string"},
                                  "description": "Typical install folders, for display only"},
                    "reason": {"type": "string"},
                    "confidence": {"type": "number"},
                },
            },
        },
        "files": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["path", "reason", "confidence"],
                "properties": {
                    "path": {"type": "string",
                             "description": "Path using one of the placeholders {Desktop} {PublicDesktop} "
                                            "{Downloads} {Documents}"},
                    "reason": {"type": "string"},
                    "confidence": {"type": "number"},
                },
            },
        },
    },
}

SYSTEM_PROMPT = """You help a personal focus app on Windows build a block list for one app or online service.
Given only the service name, return JSON matching the schema.

domains: the primary domain plus everything the service needs to work: web app, subdomains, API, content delivery
and media hosts, login/auth, telemetry, update and download hosts. Use "*.example.com" to cover a whole domain.
Group each entry: main (website/app), api_cdn (API, CDN, media, telemetry), login (sign-in), downloads (installers,
updates). Never include domains of unrelated companies, generic shared CDNs (cloudfront.net, akamai, cloudflare,
googleapis.com, amazonaws.com) unless the host is dedicated to this service, Microsoft/Windows domains, or the
domains of AI providers.

apps: Windows executable names (ending in .exe) of the desktop app, its launcher, helper and updater processes,
with typical install folders in locations. Never list Windows system processes or web browsers.

files: shortcuts, installers and user-level launcher files the user might have, written with placeholders only:
{Desktop}\\Name.lnk, {PublicDesktop}\\Name.lnk, {Downloads}\\NameSetup.exe. Never use Program Files, Windows,
AppData or any other real path.

confidence is 0 to 1. If you do not recognise the service, set known=false and only suggest the obvious
name.com pattern with low confidence. Reply with JSON only."""


def sanitize_query(q: str) -> str:
    q = _CONTROL_RE.sub("", str(q or "")).strip()
    q = re.sub(r"\s+", " ", q)
    if not q:
        raise AIBlockError("Type the name of an app or service first.")
    return q[:MAX_QUERY]


_clean_str = clean_text
_DISPLAY_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‏ -‮⁠-⁤⁦-⁩﻿]")


def clean_display(text, limit: int = 20000) -> str:
    """Multi-line text (chat replies) made safe for labels: keeps newlines and tabs, drops the rest of the
    control and bidi characters, caps the length."""
    if not isinstance(text, str):
        return ""
    out = _DISPLAY_RE.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    return out if len(out) <= limit else out[:limit] + " [...]"


def _clean_conf(v) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.5
    if f != f:  # NaN
        return 0.5
    return max(0.0, min(1.0, f))


def validate_response(data) -> dict:
    """Check the model's JSON against the schema shape; coerce what can be coerced, refuse the rest."""
    if not isinstance(data, dict):
        raise AIBlockError("The model returned the wrong shape of data.")
    for key in ("domains", "apps", "files"):
        if key not in data or not isinstance(data[key], list):
            raise AIBlockError(f"The model's answer is missing the '{key}' list.")
    out = {"service": _clean_str(data.get("service"), 80), "known": bool(data.get("known", True)),
           "domains": [], "apps": [], "files": []}
    groups = {g for g, _ in GROUPS}
    for d in data["domains"][:MAX_DOMAINS]:
        if not isinstance(d, dict) or not isinstance(d.get("host"), str):
            continue
        out["domains"].append({"host": _clean_str(d["host"], 253),
                               "group": d.get("group") if d.get("group") in groups else "main",
                               "reason": _clean_str(d.get("reason")), "confidence": _clean_conf(d.get("confidence"))})
    for a in data["apps"][:MAX_APPS]:
        if not isinstance(a, dict) or not isinstance(a.get("exe"), str):
            continue
        locs = a.get("locations") if isinstance(a.get("locations"), list) else []
        out["apps"].append({"exe": _clean_str(a["exe"], 120), "role": a.get("role") if a.get("role") in ROLES else "app",
                            "locations": [_clean_str(x, 160) for x in locs[:4] if isinstance(x, str)],
                            "reason": _clean_str(a.get("reason")), "confidence": _clean_conf(a.get("confidence"))})
    for f in data["files"][:MAX_FILES]:
        if not isinstance(f, dict) or not isinstance(f.get("path"), str):
            continue
        out["files"].append({"path": _clean_str(f["path"], 260), "reason": _clean_str(f.get("reason")),
                             "confidence": _clean_conf(f.get("confidence"))})
    return out


# ----------------------------------------------------------------------------- normalisation and status
def normalise_domain(raw: str) -> str | None:
    """Lower-case hostname without scheme, path, port or wildcard; None for IPs, localhost and junk."""
    h = hostsfile.normalise_host(raw)
    if not h:
        return None
    if _IP_RE.match(h) or all(p.isdigit() for p in h.split(".")):
        return None
    if "." not in h:  # IPv6 literals never survive normalise_host (no dots), nor do bare words
        return None
    return h


def _under(host: str, suffix: str) -> bool:
    return host == suffix or host.endswith("." + suffix)


def domain_status(host: str, provider_host: str | None = None) -> tuple[str, str]:
    if host in hostsfile.PROTECTED:
        return "protected", "On the protected list: blocking it breaks Windows or sign-in."
    if provider_host and _under(host, provider_host):
        return "protected", "This is your AI provider's endpoint; blocking it would break AI Block itself."
    if host in llm.PROVIDER_HOSTS:
        return "protected", "An AI provider endpoint. Not blocked, so the AI features keep working."
    for s in PROTECTED_SUFFIXES:
        if _under(host, s):
            return "protected", f"Part of {s}: Windows, sign-in, certificates or the AI provider depend on it."
    if host in BROAD_EXCEPTIONS:
        return "ok", ""
    if host in BROAD_EXACT:
        return "broad", "May affect other sites: this is a company-wide domain, not just this service."
    for s in BROAD_SUFFIXES:
        if _under(host, s):
            return "broad", f"May affect other sites: {s} is shared infrastructure used by many services."
    return "ok", ""


def normalise_app(raw: str) -> str | None:
    s = _clean_str(raw, 120)
    s = s.split("\\")[-1].split("/")[-1].strip()
    if not s or not _EXE_RE.match(s):
        return None
    return s


def app_status(exe: str) -> tuple[str, str]:
    low = exe.lower()
    if low in PROTECTED_PROCESSES or low.startswith("deepfocus"):
        return "protected", "A Windows or DeepFocus process. Closing it would break the system or this app."
    if low in procwatch.BROWSERS:
        return "broad", "A web browser: closing it affects everything you do online, not just this service."
    return "ok", ""


def _placeholder_roots() -> dict[str, Path]:
    home = Path.home()
    public = Path(os.environ.get("PUBLIC") or r"C:\Users\Public")
    return {"{Desktop}": guard.desktop_folder(), "{PublicDesktop}": public / "Desktop",
            "{Downloads}": home / "Downloads", "{Documents}": home / "Documents", "{UserProfile}": home}


def expand_file(template: str) -> str | None:
    """Replace a leading placeholder with the real folder. Absolute paths pass through; anything else is None."""
    s = _clean_str(template, 260).replace("/", "\\")
    if not s:
        return None
    for ph, root in _placeholder_roots().items():
        if s.lower().startswith(ph.lower()):
            rest = s[len(ph):].lstrip("\\")
            if not rest or ".." in rest.split("\\"):
                return None
            return str(root / rest)
    if re.match(r"^[A-Za-z]:\\", s) and ".." not in s.split("\\"):
        return s
    return None


def file_status(path: str) -> tuple[str, str]:
    why = guard.forbidden_reason(path)
    if why:
        return "refused", f"Refused: it {why}."
    if not Path(path).exists():
        return "missing", "Not found on this PC. Unchecked; keep it only if you expect the file to appear."
    return "ok", ""


# ----------------------------------------------------------------------------- building results
def build_result(query: str, data: dict, source: str, provider_host: str | None = None) -> Result:
    v = validate_response(data)
    res = Result(query=query, service=v["service"] or query, known=v["known"], source=source)
    seen: set[str] = set()
    for d in v["domains"]:
        host = normalise_domain(d["host"])
        if not host:
            res.items.append(Item("domain", d["host"] or "(empty)", d["group"], d["reason"], d["confidence"],
                                  checked=False, status="invalid", note="Not a valid hostname (IP addresses, "
                                  "localhost and bare words are not accepted).", original=d["host"]))
            continue
        if host in seen:
            continue
        seen.add(host)
        status, note = domain_status(host, provider_host)
        checked = status == "ok" and d["confidence"] >= 0.3
        res.items.append(Item("domain", host, d["group"], d["reason"], d["confidence"], checked, status, note,
                              original=d["host"]))
    seen_apps: set[str] = set()
    for a in v["apps"]:
        exe = normalise_app(a["exe"])
        if not exe:
            res.items.append(Item("app", a["exe"] or "(empty)", a["role"], a["reason"], a["confidence"], False,
                                  "invalid", "Not a plain .exe file name.", original=a["exe"]))
            continue
        if exe.lower() in seen_apps:
            continue
        seen_apps.add(exe.lower())
        status, note = app_status(exe)
        reason = a["reason"]
        if a["locations"]:
            reason = (reason + " " if reason else "") + "Usually in " + ", ".join(a["locations"][:2]) + "."
        res.items.append(Item("app", exe, a["role"], reason, a["confidence"], status == "ok", status, note,
                              original=a["exe"]))
    seen_files: set[str] = set()
    for f in v["files"]:
        path = expand_file(f["path"])
        if not path:
            res.items.append(Item("file", f["path"] or "(empty)", "", f["reason"], f["confidence"], False, "invalid",
                                  "Only {Desktop}, {PublicDesktop}, {Downloads} and {Documents} paths are accepted.",
                                  original=f["path"]))
            continue
        key = os.path.normcase(path)
        if key in seen_files:
            continue
        seen_files.add(key)
        status, note = file_status(path)
        res.items.append(Item("file", path, "", f["reason"], f["confidence"], status == "ok", status, note,
                              original=f["path"]))
    if not res.known:
        res.notes.append("The AI did not recognise this name. The list below is a guess; check the spelling or add "
                         "the hostnames yourself.")
    if any(i.status == "broad" for i in res.items):
        res.notes.append("Entries marked \"may affect other sites\" are shared infrastructure and start unchecked.")
    return res


def fallback(query: str) -> Result:
    """Offline list from the built-in catalog (62 services) or the generic name.com expansion."""
    q = sanitize_query(query)
    svc, hosts = catalog.resolve(q, True)
    data = {"service": svc.name if svc else q, "known": bool(svc), "domains": [], "apps": [], "files": []}
    for h in hosts:
        first = h.split(".")[0]
        if first in ("api", "cdn", "static", "media", "gateway", "gql", "usher", "clips") or "cdn" in h:
            group = "api_cdn"
        elif first in ("login", "accounts", "passport", "oauth", "auth"):
            group = "login"
        else:
            group = "main"
        data["domains"].append({"host": h, "group": group, "reason": "From the built-in catalog" if svc else
                                "Guessed from the name", "confidence": 0.9 if svc else 0.3})
    if svc:
        for p in svc.processes:
            data["apps"].append({"exe": p, "role": "app", "locations": [], "reason": "Known desktop app",
                                 "confidence": 0.8})
        for ph in ("{Desktop}", "{PublicDesktop}"):
            data["files"].append({"path": f"{ph}\\{svc.name}.lnk", "reason": "Desktop shortcut", "confidence": 0.4})
    res = build_result(q, data, "offline")
    res.notes.insert(0, "AI unavailable: this list comes from the built-in catalog." if svc else
                     "AI unavailable and this name is not in the built-in catalog: hostnames are guessed.")
    return res


def generate(query: str, cfg: llm.Config) -> Result:
    """Ask the model. Raises llm.LLMError (network, key, rate limit...) or AIBlockError (bad answer)."""
    q = sanitize_query(query)
    data = llm.complete_json(cfg, SYSTEM_PROMPT, f"Service name: {q}", SCHEMA)
    return build_result(q, data, "ai", llm.provider_host(cfg))


# ----------------------------------------------------------------------------- saved lists
def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "list"


def to_record(res: Result, name: str, existing: dict | None = None) -> dict:
    """Saved list: only checked, usable entries, plus flagged ones for the user's own reference."""
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    rec = dict(existing or {})
    rec.update({
        "name": clean_text(name, 60) or clean_text(f"{res.service} (AI)", 60),
        "query": res.query,
        "service": res.service,
        "source": res.source,
        "created": rec.get("created") or now,
        "updated": now,
        "domains": [{"host": i.value, "group": i.group, "reason": i.reason, "confidence": i.confidence}
                    for i in res.items if i.kind == "domain" and i.checked and i.usable],
        "apps": [{"exe": i.value, "role": i.group, "reason": i.reason, "confidence": i.confidence}
                 for i in res.items if i.kind == "app" and i.checked and i.usable],
        "files": [{"path": i.value, "reason": i.reason, "confidence": i.confidence}
                  for i in res.items if i.kind == "file" and i.checked and i.usable],
        "flagged": sorted({i.value for i in res.items if i.flagged} | set(rec.get("flagged", []))),
    })
    return rec


def record_to_result(rec: dict) -> Result:
    """Reopen a saved list for review: every saved entry comes back checked, re-validated against today's rules."""
    data = {"service": rec.get("service") or rec.get("name", ""), "known": True,
            "domains": [{"host": d.get("host", ""), "group": d.get("group", "main"), "reason": d.get("reason", ""),
                         "confidence": d.get("confidence", 0.8)} for d in rec.get("domains", [])],
            "apps": [{"exe": a.get("exe", ""), "role": a.get("role", "app"), "locations": [],
                      "reason": a.get("reason", ""), "confidence": a.get("confidence", 0.8)}
                     for a in rec.get("apps", [])],
            "files": [{"path": f.get("path", ""), "reason": f.get("reason", ""), "confidence": f.get("confidence", 0.8)}
                      for f in rec.get("files", [])]}
    res = build_result(rec.get("query") or rec.get("name", ""), data, "saved")
    for i in res.items:
        if i.status in ("ok", "broad", "missing"):
            i.checked = True
    res.notes = []
    return res
