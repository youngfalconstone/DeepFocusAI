"""Opens the app, visits every page, exercises the builder, then closes. Run: python -m tests.smoke_gui"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Isolate the run from the real machine: own state folder, and a copy of the hosts file with no DeepFocus
# block in it, so a focus session that is live on this PC cannot pop a question box (which would block).
os.environ.setdefault("DEEPFOCUS_HOME", tempfile.mkdtemp(prefix="deepfocus_smoke_"))
if not os.environ.get("DEEPFOCUS_HOSTS"):
    _hosts = Path(os.environ["DEEPFOCUS_HOME"]) / "hosts"
    try:
        shutil.copyfile(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "drivers" / "etc" / "hosts", _hosts)
        _lines = _hosts.read_text(encoding="utf-8", errors="surrogateescape").splitlines(keepends=True)
        _out, _skip = [], False
        for _l in _lines:
            if _l.strip().startswith("# >>> DeepFocus AI session block"):
                _skip = True
            if not _skip:
                _out.append(_l)
            if _l.strip().startswith("# <<< DeepFocus AI session block"):
                _skip = False
        _hosts.write_text("".join(_out), encoding="utf-8", errors="surrogateescape")
    except OSError:
        _hosts.write_text("127.0.0.1 localhost\n", encoding="utf-8")
    os.environ["DEEPFOCUS_HOSTS"] = str(_hosts)

from datetime import datetime  # noqa: E402

from deepfocus import catalog, schedule, state  # noqa: E402
from deepfocus.app import (AddBlockDialog, AIBlockDialog, App, AppsDialog, Plan, ReviewDialog,  # noqa: E402
                           clean_apps, normalise_app_input)

errors: list[str] = []


def step(app: App, fn, delay: int, nxt=None):
    def run():
        try:
            fn()
        except Exception:
            errors.append(traceback.format_exc())
        if nxt:
            nxt()
    app.after(delay, run)


def main():
    app = App()

    def report(exc, val, tb):
        errors.append("".join(traceback.format_exception(exc, val, tb)))

    app.report_callback_exception = report
    b = app.pages["builder"]

    def s6():
        step(app, app.destroy, 500)

    def s5():
        def presets_help():
            app.show("presets")
            app.after(250, lambda: app.show("help"))
        step(app, presets_help, 500, s6)

    def s4b():
        # Assistant page with a fake streaming provider: send, stream, persist, new chat
        from deepfocus import llm
        cp = app.pages["chat"]
        state.save_chat([])
        cp.messages = []
        cp._render_all()
        app.show("chat")
        orig_cfg, orig_stream = app.llm_config, llm.chat_stream
        app.llm_config = lambda: llm.Config(provider="anthropic", api_key="fake")
        seen = {}

        def fake_stream(cfg, system, messages, on_text, cancel=None):
            seen["system"] = system
            seen["messages"] = messages
            for part in ("Block ", "Discord ", "and start a 50-minute session."):
                on_text(part)
            return "Block Discord and start a 50-minute session."
        llm.chat_stream = fake_stream
        cp.send("What should I block?")
        assert cp._busy

        def check():
            try:
                assert not cp._busy, "chat still busy"
                assert cp.messages[-1]["role"] == "assistant" and cp.messages[-1]["content"].startswith("Block Discord")
                assert state.load_chat()[-2]["content"] == "What should I block?"
                assert "DeepFocus" in seen["system"] and "Current state of the app" in seen["system"]
                assert seen["messages"][-1] == {"role": "user", "content": "What should I block?"}
                cp.new_chat()
                assert not cp.messages and state.load_chat() == []
            except Exception:
                errors.append(traceback.format_exc())
            finally:
                app.llm_config, llm.chat_stream = orig_cfg, orig_stream
            s5()
        app.after(700, check)

    def s4():
        step(app, lambda: app.show("settings"), 500, s4b)

    def s3():
        step(app, lambda: app.show("history"), 500, s4)

    def s2():
        def builder_ops():
            app.show("builder")
            b.search.set("youtube"); b.find()
            b.add_service(catalog.get("discord"))
            b.add_preset("Gaming")
            b.add_host.set("news.example.com"); b.add_custom_host()
            b.host_list.selection_set(0); b.remove_hosts()
            b.set_in(90); b.set_until(17)
            b.quick_var.set("120"); b._quick_time()
            assert b.sync_plan(), "sync_plan failed"
            assert "news.example.com" in b.plan.hostnames()
            assert len(b.plan.processes()) > 0
            # apps added by hand: normalised, deduplicated, remembered for the next plan, removable
            app.settings["remembered_apps"] = []
            b.plan.apps = []
            b.add_app.set("Spotify"); b.add_app_name()
            b.add_app.set(r"C:\Games\Launcher\MyGame.exe"); b.add_app_name()
            b.add_app.set("spotify.exe"); b.add_app_name()  # duplicate, ignored
            assert b.plan.apps == ["Spotify.exe", "MyGame.exe"], b.plan.apps
            assert "Spotify.exe" in b.plan.processes() and "MyGame.exe" in b.plan.processes()
            assert app.settings["remembered_apps"] == ["Spotify.exe", "MyGame.exe"]
            assert Plan(app.settings).apps == ["Spotify.exe", "MyGame.exe"], "apps must carry over to a new plan"
            assert b.app_list.get(0) == "Spotify.exe" and b.app_list.get(1) == "MyGame.exe"
            assert b.app_list.size() == len(b.plan.processes()), "own apps first, then the services' apps"
            b.app_list.selection_set(0); b.remove_apps()
            assert b.plan.apps == ["MyGame.exe"] and app.settings["remembered_apps"] == ["MyGame.exe"]
            assert clean_apps(["explorer.exe", "Discord", 5, "Discord.exe", "bad/name?.exe"]) == ["Discord.exe"]
            # the mid-session editor: own apps editable, service apps fixed, Save hands back the own list
            got = {}
            d = AppsDialog(app, ["MyGame.exe"], ["Discord.exe"], lambda own: got.update(own=own))
            d.add.set("Spotify"); d.add_name()
            d.add.set("discord.exe"); d.add_name()   # already there via the service: no duplicate
            assert d.own == ["MyGame.exe", "Spotify.exe"] and d.all_apps() == ["MyGame.exe", "Spotify.exe", "Discord.exe"]
            d.list.selection_set(0); d.remove()
            assert d.own == ["Spotify.exe"]
            d.save()
            assert got["own"] == ["Spotify.exe"]
            fake = {"services": ["discord"], "processes": ["Discord.exe", "DiscordPTB.exe", "Steam.exe"]}
            own, svc = app.pages["dashboard"].session_apps(fake)
            assert own == ["Steam.exe"] and "Discord.exe" in svc
            # "Add to block" dialog: only hostnames the session does not have yet, services and processes carried
            sess = {"id": "abc123", "status": "active", "services": ["discord"],
                    "hostnames": ["discord.com", "www.discord.com", "news.example.com"], "processes": ["Discord.exe"]}
            ab = AddBlockDialog(app, sess)
            assert not ab.btn_apply.enabled
            ab.search.set("reddit"); ab.find()
            ab.add_service(catalog.get("reddit"))
            ab.add_service(catalog.get("discord"))           # already in the session: nothing new from it
            ab.add_host.set("news.example.com"); ab.add_custom_host()   # already blocked
            ab.add_host.set("Fresh.Example.org"); ab.add_custom_host()
            pl = ab.payload()
            assert pl["id"] == "abc123" and "reddit.com" in pl["hostnames"] and "fresh.example.org" in pl["hostnames"]
            assert "discord.com" not in pl["hostnames"] and "news.example.com" not in pl["hostnames"]
            assert pl["services"] == ["reddit"] and ab.already() >= 3 and ab.btn_apply.enabled
            assert ab.list.size() == len(pl["hostnames"])
            ab.destroy()
            assert normalise_app_input("explorer.exe") == "explorer.exe" and normalise_app_input("") is None
            # long-term and weekly modes
            b.mode_var.set("long"); b._mode_changed()
            b.long_var.set("14"); b._long_pick()
            assert b.sync_plan() and b.plan.schedule == {"kind": "long"}
            assert 13 <= (b.plan.restore_at - datetime.now()).days <= 14
            b.mode_var.set("weekly"); b._mode_changed()
            b.weeks_var.set(3)
            b.day_mode[0].set("after"); b.day_hour[0].set(18)
            assert b.sync_plan() and b.plan.is_weekly() and b.plan.schedule["weeks"] == 3
            assert b.plan.schedule["free"]["0"] == [1080, 1440] and b.plan.schedule["free"]["5"] == [0, 1440]
            assert 20 <= (b.plan.restore_at - datetime.now()).days <= 21
            preset = b.plan.to_preset()
            p2 = Plan(app.settings); p2.load_preset(preset)
            assert p2.is_weekly() and p2.schedule == b.plan.schedule
            ReviewDialog(app, b.plan, lambda: None).destroy()
            b._reset_planner()
            assert b._planner_schedule()["free"] == schedule.DEFAULT_FREE
            b.mode_var.set("once"); b._mode_changed()
            d = app.pages["dashboard"]
            d.q_len.set("90"); assert d.q_minutes.get() == 90
            d.q_minutes.set(77); assert d.q_len.get() == "custom"
            d.q_pomo.set("50/10"); d.q_notif.set(True)
            d._fit_hero()
            # AI Block dialog in offline (demo) mode: review, edit, flag, save, use, integrate, clean up
            lists = state.load_ai_blocks(); lists.pop("discord-smoke-ai", None); state.save_ai_blocks(lists)
            dlg = AIBlockDialog(app, on_use=lambda k: setattr(app, "_ai_used", k), demo=True)
            assert dlg.result and dlg.result.of("domain"), "demo list empty"
            doms = dlg.result.of("domain")
            assert doms[0].value == "discord.com"
            doms[1].checked = False  # www.discord.com: untick it and expect it to be left out of the saved list
            dlg._add_own("domain", "Extra.Discord.test")
            assert any(i.value == "extra.discord.test" for i in dlg.result.items)
            apps_ = dlg.result.of("app")
            assert apps_ and apps_[0].value == "Discord.exe"
            dlg._flag(apps_[1]) if len(apps_) > 1 else None
            dlg.name_var.set("Discord smoke (AI)")
            key = dlg.save()
            assert key == "discord-smoke-ai", key
            rec = state.load_ai_blocks()[key]
            hosts = [x["host"] for x in rec["domains"]]
            assert "discord.com" in hosts and doms[1].value not in hosts and "extra.discord.test" in hosts
            dlg.use()
            assert getattr(app, "_ai_used", None) == "ai:discord-smoke-ai"
            s = catalog.get("ai:discord-smoke-ai")
            assert s and "Discord.exe" in s.processes and s.name == "Discord smoke (AI)"
            b.add_ai_list("ai:discord-smoke-ai")
            b.undo_removals()  # an earlier step removed the first listed host (discord.com)
            assert "discord.com" in b.plan.hostnames() and "www.discord.com" in b.plan.hostnames()
            assert "ai:discord-smoke-ai" in b.plan.services
            d.q_preset.set("ai:discord-smoke-ai")
            assert "Discord smoke" in d._preset_label()
            preset = b.plan.to_preset()
            p3 = Plan(app.settings); p3.load_preset(preset)
            assert "ai:discord-smoke-ai" in p3.services
            lists = state.load_ai_blocks(); lists.pop(key); state.save_ai_blocks(lists)
            assert catalog.get("ai:discord-smoke-ai") is None
            b.remove_service("ai:discord-smoke-ai")
            d.q_preset.set("Social media")
        step(app, builder_ops, 500, s3)

    step(app, lambda: app.show("dashboard"), 1200, s2)
    app.mainloop()
    if errors:
        print("GUI ERRORS:\n" + "\n".join(errors))
        sys.exit(1)
    print("GUI OK")


if __name__ == "__main__":
    main()
