"""Small provider-agnostic client for one job: ask a model for JSON that matches a schema.

Only the standard library is used (urllib), because the app ships without third-party packages. Two wire
formats are supported: the Claude Messages API (default) and the OpenAI-compatible chat completions shape that
many hosted and local servers speak. Errors are reduced to a handful of kinds with messages fit for the UI.
"""
from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

ANTHROPIC_VERSION = "2026-10-21"

PROVIDERS = {
    "anthropic": {"label": "Claude API", "base_url": "https://api.anthropic.com", "default_model": "claude-opus-5-5",
                  "key_hint": "sk-ant-...", "host": "api.anthropic.com"},
    "openai": {"label": "OpenAI-compatible", "base_url": "https://api.openai.com/v1", "default_model": "gpt-4o-mini",
               "key_hint": "sk-...", "host": "api.openai.com"},
}

# hostnames the configured provider needs; the AI Block feature refuses to block them
PROVIDER_HOSTS = {"api.anthropic.com", "anthropic.com", "claude.ai", "api.openai.com", "openai.com",
                  "openrouter.ai", "generativelanguage.googleapis.com", "api.groq.com", "api.mistral.ai",
                  "api.together.xyz", "api.deepseek.com"}


MAX_RESPONSE_BYTES = 2_000_000   # a JSON answer larger than this is refused
MAX_STREAM_CHARS = 120_000       # a streamed reply is cut here
MAX_LINE_BYTES = 1_000_000       # one server-sent event line
MAX_MESSAGE_CHARS = 8_000        # one chat message
MAX_HISTORY_CHARS = 48_000       # all chat history sent in one request


def validate_base_url(url: str) -> str | None:
    """Why an endpoint override must not be used, or None. Keys only travel over https, except to this machine."""
    from urllib.parse import urlparse
    u = url.strip()
    if not u:
        return None
    try:
        p = urlparse(u)
    except ValueError:
        return "The endpoint is not a valid URL."
    if p.scheme not in ("http", "https") or not p.hostname:
        return "The endpoint must start with https://."
    if p.scheme == "http" and p.hostname not in ("localhost", "127.0.0.1", "::1"):
        return "The endpoint must use https, so the API key is never sent in the clear (http is allowed only for " \
               "localhost)."
    if p.username or p.password:
        return "The endpoint must not contain credentials."
    return None


class Cancel:
    """A cancel flag for a streaming reply that also closes the socket, so Stop takes effect at once even
    when the server is silent. Duck-compatible with threading.Event (is_set/set)."""

    def __init__(self):
        import threading
        self._ev = threading.Event()
        self._resp = None

    def attach(self, resp) -> None:
        self._resp = resp
        if self._ev.is_set():
            self._close()

    def _close(self) -> None:
        try:
            if self._resp is not None:
                self._resp.close()
        except Exception:
            pass

    def set(self) -> None:
        self._ev.set()
        self._close()

    def is_set(self) -> bool:
        return self._ev.is_set()


class LLMError(Exception):
    """kind: no_key | network | timeout | auth | rate_limit | server | bad_request | bad_response | refused"""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message


@dataclass
class Config:
    provider: str = "anthropic"
    model: str = ""
    api_key: str = ""
    base_url: str = ""
    timeout: float = 45.0
    retries: int = 2

    def resolved_model(self) -> str:
        return self.model.strip() or PROVIDERS.get(self.provider, PROVIDERS["anthropic"])["default_model"]

    def resolved_base(self) -> str:
        base = self.base_url.strip() or PROVIDERS.get(self.provider, PROVIDERS["anthropic"])["base_url"]
        return base.rstrip("/")


def provider_host(cfg: Config) -> str | None:
    """Hostname of the endpoint in use (so it can be protected from blocking)."""
    try:
        from urllib.parse import urlparse
        return urlparse(cfg.resolved_base()).hostname
    except ValueError:
        return None


def _post(url: str, headers: dict, body: dict, timeout: float) -> tuple[int, str]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": "application/json",
                                                                         "User-Agent": "DeepFocusAI/1.4", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise LLMError("bad_response", "The AI service sent an answer that is far too large.")
            return resp.status, raw.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        try:
            text = e.read(MAX_RESPONSE_BYTES).decode("utf-8", errors="replace")
        except Exception:
            text = ""
        return e.code, text
    except socket.timeout:
        raise LLMError("timeout", "The AI service did not answer in time. Try again in a moment.")
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        if isinstance(reason, socket.timeout) or "timed out" in str(reason).lower():
            raise LLMError("timeout", "The AI service did not answer in time. Try again in a moment.")
        raise LLMError("network", "No connection to the AI service. Check your internet connection "
                                  "(and that the provider's hostname is not blocked).")
    except (OSError, ValueError) as e:
        raise LLMError("network", f"Could not reach the AI service: {e}")


def _error_detail(text: str) -> str:
    try:
        j = json.loads(text)
        err = j.get("error", j)
        if isinstance(err, dict):
            return str(err.get("message") or err.get("type") or "")[:200]
        return str(err)[:200]
    except (ValueError, AttributeError):
        return text[:200]


def _raise_for_status(status: int, text: str) -> None:
    if status in (401, 403):
        raise LLMError("auth", "The API key was rejected. Check the key in Settings.")
    if status == 429:
        raise LLMError("rate_limit", "The AI service is rate-limiting requests. Wait a minute and try again.")
    if status == 404:
        raise LLMError("bad_request", "The model or endpoint was not found. Check the model name in Settings. "
                                      + _error_detail(text))
    if 400 <= status < 500:
        raise LLMError("bad_request", f"The AI service rejected the request ({status}). {_error_detail(text)}")
    if status >= 500:
        raise LLMError("server", f"The AI service had a problem ({status}). Try again shortly.")


def _check_cfg(cfg: Config) -> None:
    if not cfg.api_key.strip():
        raise LLMError("no_key", "No API key is set. Add one under Settings > AI Block.")
    problem = validate_base_url(cfg.base_url)
    if problem:
        raise LLMError("bad_request", problem + " Fix the endpoint under Settings > AI Block.")


def complete_json(cfg: Config, system: str, user: str, schema: dict) -> dict:
    """Return the parsed JSON object the model produced for `user`, matching `schema`."""
    _check_cfg(cfg)
    if cfg.provider == "openai":
        return _openai(cfg, system, user, schema)
    return _anthropic(cfg, system, user, schema)


def _with_retries(cfg: Config, fn):
    delay = 1.5
    last: LLMError | None = None
    for attempt in range(cfg.retries + 1):
        try:
            return fn()
        except LLMError as e:
            last = e
            if e.kind not in ("network", "timeout", "rate_limit", "server") or attempt == cfg.retries:
                raise
            time.sleep(delay)
            delay *= 2
    raise last  # pragma: no cover


def _anthropic(cfg: Config, system: str, user: str, schema: dict) -> dict:
    url = cfg.resolved_base() + "/v1/messages"
    headers = {"x-api-key": cfg.api_key.strip(), "anthropic-version": ANTHROPIC_VERSION}
    body = {
        "model": cfg.resolved_model(),
        "max_tokens": 4096,
        "system": system,
        "messages": [{"role": "user", "content": user}],
        "output_config": {"format": {"type": "json_schema", "schema": schema}, "effort": "low"},
    }

    def call():
        status, text = _post(url, headers, body, cfg.timeout)
        _raise_for_status(status, text)
        try:
            j = json.loads(text)
        except ValueError:
            raise LLMError("bad_response", "The AI service returned something that is not JSON.")
        if j.get("stop_reason") == "refusal":
            raise LLMError("refused", "The model declined this request.")
        parts = [b.get("text", "") for b in j.get("content", []) if isinstance(b, dict) and b.get("type") == "text"]
        return _parse_json("".join(parts), truncated=j.get("stop_reason") == "max_tokens")
    return _with_retries(cfg, call)


def _openai(cfg: Config, system: str, user: str, schema: dict) -> dict:
    url = cfg.resolved_base() + "/chat/completions"
    headers = {"Authorization": "Bearer " + cfg.api_key.strip()}
    body = {
        "model": cfg.resolved_model(),
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "response_format": {"type": "json_schema", "json_schema": {"name": "block_list", "schema": schema,
                                                                   "strict": True}},
    }

    def call():
        status, text = _post(url, headers, body, cfg.timeout)
        _raise_for_status(status, text)
        try:
            j = json.loads(text)
            msg = j["choices"][0]["message"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise LLMError("bad_response", "The AI service returned an unexpected answer.")
        if msg.get("refusal"):
            raise LLMError("refused", "The model declined this request.")
        content = msg.get("content") or ""
        return _parse_json(content, truncated=j["choices"][0].get("finish_reason") == "length")
    return _with_retries(cfg, call)


def _parse_json(text: str, truncated: bool = False) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        data = json.loads(text)
    except ValueError:
        if truncated:
            raise LLMError("bad_response", "The answer was cut off before it was complete. Try again.")
        raise LLMError("bad_response", "The model did not return valid JSON.")
    if not isinstance(data, dict):
        raise LLMError("bad_response", "The model returned the wrong shape of data.")
    return data


# ----------------------------------------------------------------------------- chat (streaming)
def _open_stream(url: str, headers: dict, body: dict, timeout: float):
    """Open a streaming POST; HTTP errors become LLMErrors before any token is read."""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json", "Accept": "text/event-stream",
                                          "User-Agent": "DeepFocusAI/1.5", **headers})
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        try:
            text = e.read().decode("utf-8", errors="replace")
        except Exception:
            text = ""
        _raise_for_status(e.code, text)
        raise LLMError("server", f"The AI service answered with status {e.code}.")
    except socket.timeout:
        raise LLMError("timeout", "The AI service did not answer in time. Try again in a moment.")
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        if isinstance(reason, socket.timeout) or "timed out" in str(reason).lower():
            raise LLMError("timeout", "The AI service did not answer in time. Try again in a moment.")
        raise LLMError("network", "No connection to the AI service. Check your internet connection "
                                  "(and that the provider's hostname is not blocked).")
    except (OSError, ValueError) as e:
        raise LLMError("network", f"Could not reach the AI service: {e}")


def _sse(resp):
    """Yield (event, data) pairs from a server-sent-events body."""
    event = None
    try:
        while True:
            raw = resp.readline(MAX_LINE_BYTES + 1)
            if not raw:
                break
            if len(raw) > MAX_LINE_BYTES:
                raise LLMError("bad_response", "The AI service sent an event that is far too large.")
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line:
                event = None
                continue
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                yield event, line[5:].strip()
    except socket.timeout:
        raise LLMError("timeout", "The AI service stopped answering mid-reply.")
    except (OSError, ValueError) as e:  # includes http.client.IncompleteRead, and the socket closed by Stop
        raise LLMError("bad_response", f"The connection dropped before the reply was complete ({type(e).__name__}).")


def trim_history(messages: list[dict], max_chars: int = MAX_HISTORY_CHARS) -> list[dict]:
    """Drop the oldest messages until the history fits the character budget; always keeps the last message and
    starts the result on a user turn, as the APIs require."""
    kept: list[dict] = []
    total = 0
    for m in reversed(messages):
        n = len(m["content"])
        if kept and total + n > max_chars:
            break
        kept.append(m)
        total += n
    kept.reverse()
    while kept and kept[0]["role"] != "user":
        kept.pop(0)
    return kept or messages[-1:]


def chat_stream(cfg: Config, system: str, messages: list[dict], on_text, cancel=None) -> str:
    """Stream a chat reply. `messages` are {"role": "user"|"assistant", "content": str} in order; `on_text` is
    called with each text fragment (from the worker thread); `cancel` is an optional threading.Event. Returns the
    full reply. Not retried: a half-streamed reply must not be duplicated, the user can simply send again."""
    _check_cfg(cfg)
    clean = [{"role": m["role"], "content": str(m["content"])} for m in messages
             if m.get("role") in ("user", "assistant") and str(m.get("content", "")).strip()]
    if not clean or clean[-1]["role"] != "user":
        raise LLMError("bad_request", "Nothing to send.")
    if len(clean[-1]["content"]) > MAX_MESSAGE_CHARS:
        raise LLMError("bad_request", f"That message is too long (over {MAX_MESSAGE_CHARS:,} characters). "
                                      "Shorten it and send again.")
    clean = trim_history(clean)
    out: list[str] = []
    total = 0

    def take(txt: str) -> bool:
        """Deliver a fragment; False once the reply hit the size cap."""
        nonlocal total
        total += len(txt)
        if total > MAX_STREAM_CHARS:
            return False
        out.append(txt)
        on_text(txt)
        return True

    if cfg.provider == "openai":
        url = cfg.resolved_base() + "/chat/completions"
        headers = {"Authorization": "Bearer " + cfg.api_key.strip()}
        body = {"model": cfg.resolved_model(), "stream": True,
                "messages": [{"role": "system", "content": system}] + clean}
        with _open_stream(url, headers, body, cfg.timeout) as resp:
            if hasattr(cancel, "attach"):
                cancel.attach(resp)
            for _event, data in _sse(resp):
                if cancel is not None and cancel.is_set():
                    break
                if data == "[DONE]":
                    break
                try:
                    j = json.loads(data)
                except ValueError:
                    continue
                if j.get("error"):
                    raise LLMError("server", str(j["error"].get("message", "stream error"))[:200])
                choice = (j.get("choices") or [{}])[0]
                if choice.get("finish_reason") == "content_filter":
                    raise LLMError("refused", "The model declined this request.")
                txt = (choice.get("delta") or {}).get("content")
                if txt and not take(txt):
                    break
        return "".join(out)
    url = cfg.resolved_base() + "/v1/messages"
    headers = {"x-api-key": cfg.api_key.strip(), "anthropic-version": ANTHROPIC_VERSION}
    body = {"model": cfg.resolved_model(), "max_tokens": 4096, "stream": True, "system": system, "messages": clean}
    with _open_stream(url, headers, body, cfg.timeout) as resp:
        if hasattr(cancel, "attach"):
            cancel.attach(resp)
        for _event, data in _sse(resp):
            if cancel is not None and cancel.is_set():
                break
            try:
                j = json.loads(data)
            except ValueError:
                continue
            t = j.get("type")
            if t == "content_block_delta" and (j.get("delta") or {}).get("type") == "text_delta":
                txt = j["delta"].get("text", "")
                if txt and not take(txt):
                    break
            elif t == "message_delta" and (j.get("delta") or {}).get("stop_reason") == "refusal":
                raise LLMError("refused", "The model declined this request.")
            elif t == "error":
                raise LLMError("server", str((j.get("error") or {}).get("message", "stream error"))[:200])
    return "".join(out)


def test_connection(cfg: Config) -> str:
    """Tiny round trip used by the Settings 'Test' button. Returns a short success message."""
    schema = {"type": "object", "additionalProperties": False, "required": ["ok"],
              "properties": {"ok": {"type": "boolean"}}}
    data = complete_json(cfg, "Reply with JSON only.", "Return {\"ok\": true}.", schema)
    if data.get("ok") is True:
        return f"Connected. Model {cfg.resolved_model()} answered."
    return "Connected, but the model gave an unexpected answer."
