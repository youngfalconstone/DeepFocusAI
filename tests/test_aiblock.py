"""AI Block tests: schema validation, normalisation, protected and broad lists, files, offline fallback, the
provider client against a local mock server (both wire formats, errors, retries), the credential store, and
saved-list round trips. Run: python -m tests.test_aiblock"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["DEEPFOCUS_HOME"] = tempfile.mkdtemp(prefix="deepfocus_ai_test_")

from deepfocus import aiblock, catalog, credstore, llm, state  # noqa: E402


def test_sanitize():
    assert aiblock.sanitize_query("  Dis\x00cord\n ") == "Discord"
    assert aiblock.clean_text("a‮b​c﻿") == "abc", "bidi and zero-width characters removed"
    assert aiblock.clean_display("line1\r\nline2\x07⁦x") == "line1\nline2x"
    assert aiblock.clean_display("x" * 30, 10).startswith("x" * 10) and aiblock.clean_display("x" * 30, 10).endswith("[...]")
    assert aiblock.to_record(aiblock.fallback("Discord"), "‮Evil" + "n" * 100)["name"].startswith("Eviln")
    assert len(aiblock.to_record(aiblock.fallback("Discord"), "n" * 100)["name"]) == 60
    # endpoint overrides: https only, http just for this machine
    v = llm.validate_base_url
    assert v("") is None and v("https://api.example.com/v1") is None and v("http://localhost:8080/v1") is None
    assert v("http://127.0.0.1:1234") is None
    assert v("http://api.example.com") and v("ftp://x") and v("https://user:pw@host") and v("nonsense") and v("https://")
    # history trimming keeps the newest turns and starts on a user message
    hist = [{"role": "user", "content": "a" * 30000}, {"role": "assistant", "content": "b" * 30000},
            {"role": "user", "content": "c"}]
    t = llm.trim_history(hist, 40000)
    assert t == hist[2:], [len(m["content"]) for m in t]
    t = llm.trim_history([{"role": "assistant", "content": "x"}, {"role": "user", "content": "y"}], 10)
    assert t == [{"role": "user", "content": "y"}]
    assert len(aiblock.sanitize_query("x" * 500)) == aiblock.MAX_QUERY
    try:
        aiblock.sanitize_query("   ")
        assert False, "empty accepted"
    except aiblock.AIBlockError:
        pass
    print("sanitize ok")


def test_schema_validation():
    for bad in (None, [], "x", {"domains": []}, {"domains": {}, "apps": [], "files": []},
                {"domains": [], "apps": "no", "files": []}):
        try:
            aiblock.validate_response(bad)
            assert False, f"accepted {bad!r}"
        except aiblock.AIBlockError:
            pass
    v = aiblock.validate_response({
        "service": "X\x01", "known": "yes",
        "domains": [{"host": "A.com", "group": "weird", "reason": 5, "confidence": "high"},
                    {"host": 12}, "junk", {"host": "b.com", "group": "login", "reason": "r", "confidence": 7}],
        "apps": [{"exe": "X.exe", "role": "nope", "locations": "C:\\x", "reason": "", "confidence": -1}],
        "files": [{"path": "{Desktop}\\X.lnk", "reason": "s", "confidence": float("nan")}] + [{"path": "p"}] * 100,
    })
    assert v["service"] == "X" and v["known"] is True
    assert [d["host"] for d in v["domains"]] == ["A.com", "b.com"]
    assert v["domains"][0]["group"] == "main" and v["domains"][0]["reason"] == "" and v["domains"][0]["confidence"] == 0.5
    assert v["domains"][1]["confidence"] == 1.0
    assert v["apps"][0]["role"] == "app" and v["apps"][0]["locations"] == [] and v["apps"][0]["confidence"] == 0.0
    assert v["files"][0]["confidence"] == 0.5 and len(v["files"]) == aiblock.MAX_FILES
    print("schema validation ok")


def test_domain_normalisation():
    n = aiblock.normalise_domain
    assert n("HTTPS://WWW.Example.COM/path?x=1") == "www.example.com"
    assert n("*.cdn.example.com") == "cdn.example.com"
    assert n("example.com.") == "example.com"
    assert n("example.com:8443") == "example.com"
    assert n("192.168.1.1") is None and n("8.8.8.8") is None
    assert n("[2001:db8::1]") is None and n("2001:db8::1") is None
    assert n("localhost") is None and n("intranet") is None
    assert n("bad host.com") is None and n("") is None and n("-x.com") is None
    # protected and broad classification
    assert aiblock.domain_status("localhost.localdomain")[0] == "protected"
    assert aiblock.domain_status("login.microsoftonline.com")[0] == "protected"
    assert aiblock.domain_status("sls.update.microsoft.com")[0] == "protected"
    assert aiblock.domain_status("ocsp.digicert.com")[0] == "protected"
    assert aiblock.domain_status("api.anthropic.com")[0] == "protected"
    assert aiblock.domain_status("api.example.com", "api.example.com")[0] == "protected", "provider endpoint"
    assert aiblock.domain_status("d1234.cloudfront.net")[0] == "broad"
    assert aiblock.domain_status("fonts.googleapis.com")[0] == "broad"
    assert aiblock.domain_status("youtubei.googleapis.com")[0] == "ok", "known service-specific host"
    assert aiblock.domain_status("amazon.com")[0] == "broad" and aiblock.domain_status("primevideo.com")[0] == "ok"
    assert aiblock.domain_status("discord.com")[0] == "ok"
    print("domain normalisation ok")


def test_apps_and_files():
    assert aiblock.normalise_app("C:\\Program Files\\Discord\\Discord.exe") == "Discord.exe"
    assert aiblock.normalise_app("Update.exe") == "Update.exe"
    assert aiblock.normalise_app("discord") is None and aiblock.normalise_app("x.exe; del *") is None
    assert aiblock.normalise_app("..\\..\\evil.exe") == "evil.exe"
    assert aiblock.app_status("explorer.exe")[0] == "protected" and aiblock.app_status("SvcHost.EXE")[0] == "protected"
    assert aiblock.app_status("DeepFocus.exe")[0] == "protected"
    assert aiblock.app_status("chrome.exe")[0] == "broad" and aiblock.app_status("Steam.exe")[0] == "ok"
    desk = str(aiblock._placeholder_roots()["{Desktop}"])
    assert aiblock.expand_file("{Desktop}\\Discord.lnk") == desk + "\\Discord.lnk"
    assert aiblock.expand_file("{desktop}/Discord.lnk") == desk + "\\Discord.lnk"
    assert aiblock.expand_file("{Desktop}\\..\\secret") is None
    assert aiblock.expand_file("Discord.lnk") is None and aiblock.expand_file("{AppData}\\x") is None
    assert aiblock.expand_file("D:\\Games\\launcher.exe") == "D:\\Games\\launcher.exe"
    assert aiblock.file_status(os.path.join(os.environ["SystemRoot"], "notepad.exe"))[0] == "refused"
    assert aiblock.file_status(desk + "\\surely-not-there-12345.lnk")[0] == "missing"
    tmp = Path(tempfile.gettempdir()).parent.parent / "Desktop"  # real desktop may not exist in CI; use home file
    f = Path.home() / "deepfocus_ai_test_file.txt"
    f.write_text("x")
    try:
        assert aiblock.file_status(str(f))[0] == "ok"
    finally:
        f.unlink()
    print("apps and files ok")


def test_build_result_and_record():
    data = {"service": "Discord", "known": True,
            "domains": [{"host": "discord.com", "group": "main", "reason": "site", "confidence": 0.95},
                        {"host": "DISCORD.com", "group": "main", "reason": "dup", "confidence": 0.9},
                        {"host": "*.discordapp.net", "group": "api_cdn", "reason": "cdn", "confidence": 0.9},
                        {"host": "cdn.cloudflare.net", "group": "api_cdn", "reason": "cdn", "confidence": 0.6},
                        {"host": "login.live.com", "group": "login", "reason": "oops", "confidence": 0.4},
                        {"host": "10.0.0.1", "group": "main", "reason": "ip", "confidence": 0.4},
                        {"host": "maybe.discord.gg", "group": "main", "reason": "low", "confidence": 0.1}],
            "apps": [{"exe": "Discord.exe", "role": "app", "locations": ["%LocalAppData%\\Discord"], "reason": "app",
                      "confidence": 0.95},
                     {"exe": "explorer.exe", "role": "helper", "locations": [], "reason": "bad", "confidence": 0.5},
                     {"exe": "chrome.exe", "role": "app", "locations": [], "reason": "browser", "confidence": 0.5}],
            "files": [{"path": "{Desktop}\\Discord.lnk", "reason": "shortcut", "confidence": 0.5},
                      {"path": "C:\\Windows\\System32\\cmd.exe", "reason": "bad", "confidence": 0.5},
                      {"path": "random", "reason": "bad", "confidence": 0.5}]}
    res = aiblock.build_result("discord", data, "ai", provider_host="api.anthropic.com")
    by = {i.value: i for i in res.items}
    assert [i.value for i in res.of("domain")] == ["discord.com", "discordapp.net", "cdn.cloudflare.net",
                                                   "login.live.com", "10.0.0.1", "maybe.discord.gg"]
    assert by["discord.com"].checked and by["discordapp.net"].checked
    assert by["cdn.cloudflare.net"].status == "broad" and not by["cdn.cloudflare.net"].checked
    assert by["login.live.com"].status == "protected" and not by["login.live.com"].usable
    assert by["10.0.0.1"].status == "invalid"
    assert not by["maybe.discord.gg"].checked, "low confidence starts unchecked"
    assert by["Discord.exe"].checked and "Usually in" in by["Discord.exe"].reason
    assert by["explorer.exe"].status == "protected" and by["chrome.exe"].status == "broad"
    files = res.of("file")
    assert files[0].status == "missing" and not files[0].checked
    assert files[1].status == "refused" and files[2].status == "invalid"
    assert any("may affect other sites" in n for n in res.notes)
    # saved record keeps only checked usable entries; flagged entries remembered
    by["maybe.discord.gg"].flagged = True
    rec = aiblock.to_record(res, "Discord (AI)")
    assert [d["host"] for d in rec["domains"]] == ["discord.com", "discordapp.net"]
    assert [a["exe"] for a in rec["apps"]] == ["Discord.exe"] and rec["files"] == []
    assert rec["flagged"] == ["maybe.discord.gg"] and rec["name"] == "Discord (AI)"
    back = aiblock.record_to_result(rec)
    assert back.selected("domain") == ["discord.com", "discordapp.net"] and back.source == "saved"
    # the catalog exposes a saved list as a service, with apex roots expanded
    lists = state.load_ai_blocks()
    lists["discord-ai"] = rec
    state.save_ai_blocks(lists)
    svc = catalog.get("ai:discord-ai")
    assert svc and svc.name == "Discord (AI)" and svc.processes == ["Discord.exe"]
    hosts = catalog.hostnames_for(svc, False)
    assert "discord.com" in hosts and "www.discord.com" in hosts and "discordapp.net" in hosts
    assert catalog.get("ai:nope") is None and catalog.ai_files("ai:discord-ai") == []
    assert aiblock.slug("Discord (AI)") == "discord-ai"
    print("build result and record ok")


def test_fallback():
    r = aiblock.fallback("Discord")
    assert r.source == "offline" and r.known and "discord.com" in r.selected("domain")
    assert "Discord.exe" in r.selected("app")
    assert all(i.status == "missing" or i.status == "ok" for i in r.of("file"))
    r2 = aiblock.fallback("Frobnicatorz")
    assert not r2.known and "frobnicatorz.com" in [i.value for i in r2.of("domain")]
    assert all(i.checked for i in r2.of("domain") if i.status == "ok") and "guessed" in r2.notes[0]
    r3 = aiblock.fallback("tik tok")  # misspelt / spaced: still resolves through the catalog search
    assert r3.known and any("tiktok" in i.value for i in r3.of("domain"))
    print("fallback ok")


# ----------------------------------------------------------------------------- mock provider server
ANSWER = {"service": "Steam", "known": True,
          "domains": [{"host": "steampowered.com", "group": "main", "reason": "store", "confidence": 0.95},
                      {"host": "steamcommunity.com", "group": "main", "reason": "community", "confidence": 0.95},
                      {"host": "api.steampowered.com", "group": "api_cdn", "reason": "api", "confidence": 0.9},
                      {"host": "steamcontent.com", "group": "downloads", "reason": "game downloads", "confidence": 0.8},
                      {"host": "login.steampowered.com", "group": "login", "reason": "sign-in", "confidence": 0.9}],
          "apps": [{"exe": "steam.exe", "role": "launcher", "locations": ["C:\\Program Files (x86)\\Steam"],
                    "reason": "launcher", "confidence": 0.95},
                   {"exe": "steamwebhelper.exe", "role": "helper", "locations": [], "reason": "helper", "confidence": 0.8}],
          "files": [{"path": "{Desktop}\\Steam.lnk", "reason": "shortcut", "confidence": 0.6}]}
MODE = {"seq": [], "requests": []}


CHAT_PARTS = ["Block ", "Discord, ", "then start a 50-minute session."]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _stream(self, action):
        """Server-sent events in the Claude or OpenAI shape; action 'refusal' ends with a refusal."""
        lines = []
        if self.path.endswith("/v1/messages"):
            lines.append('event: message_start\ndata: {"type":"message_start"}\n\n')
            for p in CHAT_PARTS:
                lines.append('event: content_block_delta\ndata: ' + json.dumps(
                    {"type": "content_block_delta", "delta": {"type": "text_delta", "text": p}}) + "\n\n")
            stop = "refusal" if action == "refusal" else "end_turn"
            lines.append('event: message_delta\ndata: ' + json.dumps({"type": "message_delta",
                                                                       "delta": {"stop_reason": stop}}) + "\n\n")
            lines.append('event: message_stop\ndata: {"type":"message_stop"}\n\n')
        else:
            for p in CHAT_PARTS:
                lines.append("data: " + json.dumps({"choices": [{"delta": {"content": p}}]}) + "\n\n")
            if action == "refusal":
                lines.append("data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "content_filter"}]}) + "\n\n")
            lines.append("data: [DONE]\n\n")
        out = "".join(lines).encode()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        MODE["requests"].append((self.path, {k.lower(): v for k, v in self.headers.items()}, body))
        action = MODE["seq"].pop(0) if MODE["seq"] else "ok"
        if action == "sleep":
            time.sleep(1.5)
            action = "ok"
        if action == "huge":
            out = b'{"content":[{"type":"text","text":"' + b"x" * (llm.MAX_RESPONSE_BYTES + 100) + b'"}]}'
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                pass
            return
        if body.get("stream") and not isinstance(action, int):
            return self._stream(action)
        if isinstance(action, int):
            self.send_response(action)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": {"type": "err", "message": f"status {action}"}}).encode())
            return
        if self.path.endswith("/v1/messages"):
            text = json.dumps(ANSWER) if action == "ok" else ("{not json" if action == "badjson" else "")
            payload = {"content": [{"type": "text", "text": text}], "stop_reason": "refusal" if action == "refusal"
                       else "end_turn"}
        else:
            text = json.dumps(ANSWER) if action == "ok" else "{not json"
            payload = {"choices": [{"message": {"content": text}, "finish_reason": "stop"}]}
        out = json.dumps(payload).encode()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass  # the timeout test hangs up before the answer arrives


def test_llm_client():
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{port}"
        cfg = llm.Config(provider="anthropic", api_key="k", base_url=base, timeout=5, retries=1)
        # no key
        try:
            llm.complete_json(llm.Config(provider="anthropic", api_key=""), "s", "u", aiblock.SCHEMA)
            assert False
        except llm.LLMError as e:
            assert e.kind == "no_key"
        # happy path, Claude wire format: only the name goes out, schema and headers present
        MODE["seq"] = ["ok"]
        MODE["requests"].clear()
        res = aiblock.generate("Steam", cfg)
        path, headers, body = MODE["requests"][0]
        assert path == "/v1/messages" and headers.get("x-api-key") == "k" and "anthropic-version" in headers, headers
        assert body["messages"] == [{"role": "user", "content": "Service name: Steam"}]
        assert body["output_config"]["format"]["schema"] == aiblock.SCHEMA and body["model"] == "claude-opus-5-5"
        assert res.source == "ai" and res.selected("domain")[:2] == ["steampowered.com", "steamcommunity.com"]
        assert res.selected("app") == ["steam.exe", "steamwebhelper.exe"]
        assert res.of("file")[0].status in ("missing", "ok")
        # OpenAI-compatible wire format
        cfg_o = llm.Config(provider="openai", api_key="k2", base_url=base + "/v1", timeout=5, retries=0, model="m")
        MODE["seq"] = ["ok"]
        MODE["requests"].clear()
        res = aiblock.generate("Steam", cfg_o)
        path, headers, body = MODE["requests"][0]
        assert path == "/v1/chat/completions" and headers.get("authorization") == "Bearer k2"
        assert body["response_format"]["json_schema"]["schema"] == aiblock.SCHEMA and body["model"] == "m"
        assert res.selected("domain")
        # auth error: no retry
        MODE["seq"] = [401]
        MODE["requests"].clear()
        try:
            aiblock.generate("Steam", cfg)
            assert False
        except llm.LLMError as e:
            assert e.kind == "auth" and len(MODE["requests"]) == 1
        # rate limit then success: retried once
        MODE["seq"] = [429, "ok"]
        MODE["requests"].clear()
        assert aiblock.generate("Steam", cfg).selected("domain") and len(MODE["requests"]) == 2
        # server error twice: gives up after retries
        MODE["seq"] = [500, 500]
        MODE["requests"].clear()
        try:
            aiblock.generate("Steam", cfg)
            assert False
        except llm.LLMError as e:
            assert e.kind == "server" and len(MODE["requests"]) == 2
        # bad JSON and refusal
        MODE["seq"] = ["badjson"]
        try:
            aiblock.generate("Steam", cfg)
            assert False
        except llm.LLMError as e:
            assert e.kind == "bad_response"
        MODE["seq"] = ["refusal"]
        try:
            aiblock.generate("Steam", cfg)
            assert False
        except llm.LLMError as e:
            assert e.kind == "refused"
        # timeout
        MODE["seq"] = ["sleep"]
        try:
            aiblock.generate("Steam", llm.Config(provider="anthropic", api_key="k", base_url=base, timeout=0.3, retries=0))
            assert False
        except llm.LLMError as e:
            assert e.kind == "timeout"
        # no server at all
        try:
            aiblock.generate("Steam", llm.Config(provider="anthropic", api_key="k", base_url="http://127.0.0.1:9",
                                                 timeout=2, retries=0))
            assert False
        except llm.LLMError as e:
            assert e.kind in ("network", "timeout")
        assert llm.provider_host(cfg) == "127.0.0.1"
        # --- streaming chat, both wire formats
        import threading as _th
        history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"},
                   {"role": "user", "content": "What should I block?"}]
        got: list[str] = []
        MODE["seq"] = ["ok"]
        MODE["requests"].clear()
        reply = llm.chat_stream(cfg, "SYS", history, got.append)
        assert reply == "".join(CHAT_PARTS) and got == CHAT_PARTS, (reply, got)
        path, headers, body = MODE["requests"][0]
        assert path == "/v1/messages" and body["stream"] is True and body["system"] == "SYS"
        assert body["messages"] == history and headers.get("accept") == "text/event-stream"
        got.clear()
        MODE["seq"] = ["ok"]
        MODE["requests"].clear()
        reply = llm.chat_stream(cfg_o, "SYS", history, got.append)
        assert reply == "".join(CHAT_PARTS) and got == CHAT_PARTS
        path, headers, body = MODE["requests"][0]
        assert path == "/v1/chat/completions" and body["messages"][0] == {"role": "system", "content": "SYS"}
        # refusal, auth error, cancel, empty history, no key
        MODE["seq"] = ["refusal"]
        try:
            llm.chat_stream(cfg, "SYS", history, got.append)
            assert False
        except llm.LLMError as e:
            assert e.kind == "refused"
        MODE["seq"] = [401]
        try:
            llm.chat_stream(cfg, "SYS", history, got.append)
            assert False
        except llm.LLMError as e:
            assert e.kind == "auth"
        ev = _th.Event()
        ev.set()
        MODE["seq"] = ["ok"]
        assert llm.chat_stream(cfg, "SYS", history, got.append, cancel=ev) == ""
        for bad in ([], [{"role": "assistant", "content": "x"}], [{"role": "user", "content": "  "}]):
            try:
                llm.chat_stream(cfg, "SYS", bad, got.append)
                assert False
            except llm.LLMError as e:
                assert e.kind == "bad_request"
        try:
            llm.chat_stream(llm.Config(api_key=""), "SYS", history, got.append)
            assert False
        except llm.LLMError as e:
            assert e.kind == "no_key"
        # an endpoint that would leak the key over plain http is refused before any request
        MODE["requests"].clear()
        try:
            llm.chat_stream(llm.Config(api_key="k", base_url="http://example.com"), "SYS", history, got.append)
            assert False
        except llm.LLMError as e:
            assert e.kind == "bad_request" and not MODE["requests"]
        # oversized JSON answer, oversized chat message
        MODE["seq"] = ["huge"]
        try:
            aiblock.generate("Steam", cfg)
            assert False
        except llm.LLMError as e:
            assert e.kind == "bad_response" and "too large" in e.message
        try:
            llm.chat_stream(cfg, "SYS", [{"role": "user", "content": "x" * (llm.MAX_MESSAGE_CHARS + 1)}], got.append)
            assert False
        except llm.LLMError as e:
            assert e.kind == "bad_request" and "too long" in e.message
        # Cancel closes the socket as soon as it is attached
        c = llm.Cancel()
        c.set()
        closed = []

        class FakeResp:
            def close(self):
                closed.append(True)
        c.attach(FakeResp())
        assert closed and c.is_set()
        MODE["seq"] = ["ok"]
        assert llm.chat_stream(cfg, "SYS", history, got.append, cancel=c) == ""
        # chat history on disk: validated and capped
        state.save_chat([{"role": "user", "content": f"m{i}", "time": ""} for i in range(80)] + [{"junk": 1}])
        saved = state.load_chat()
        assert len(saved) == state.CHAT_KEEP and saved[-1]["content"] == "m79" and all("role" in m for m in saved)
        state.save_chat([])
        assert state.load_chat() == []
    finally:
        srv.shutdown()
    print("llm client ok")


def test_credstore():
    name = "test-suite"
    credstore.delete(name)
    assert credstore.load(name) is None and not credstore.has(name)
    credstore.save(name, "sk-test-secret-\u00e9\u4e2d")
    assert credstore.load(name) == "sk-test-secret-\u00e9\u4e2d" and credstore.has(name)
    credstore.save(name, "second")
    assert credstore.load(name) == "second"
    assert credstore.delete(name) and credstore.load(name) is None
    assert not credstore.delete(name)
    print("credstore ok")


if __name__ == "__main__":
    test_sanitize()
    test_schema_validation()
    test_domain_normalisation()
    test_apps_and_files()
    test_build_result_and_record()
    test_fallback()
    test_llm_client()
    test_credstore()
    print("ALL OK")
