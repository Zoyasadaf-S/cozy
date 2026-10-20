import json
import subprocess
import webbrowser
import sys
import pytest

from cozy.winops import WinOps


def say(env, text, **kw):
    return env.disp.handle(text, **kw)


def ok(env, text):
    r = say(env, text)
    assert r["status"] == "done", r
    return r


def last_action(env):
    return env.bridge.calls[-1][0]


# ----------------------------------------------------------------------------- system
def test_exact_volume_and_range_errors(env):
    assert "40 percent" in ok(env, "set volume to 40 percent")["message"] and env.rec.endpoint.level == 0.4
    for text in ("set volume to 101", "set volume to -1"):
        r = say(env, text)
        assert r["status"] == "error" and "between 0 and 100" in r["message"]
    assert env.rec.endpoint.level == 0.4


def test_system_volume_never_touches_the_video_and_video_volume_never_touches_windows(env):
    ok(env, "set video volume to 25")
    assert last_action(env) == {"type": "media", "action": "set_volume", "value": 25} and env.rec.endpoint.level == 0.5
    n = len(env.bridge.calls)
    ok(env, "volume up")
    assert env.rec.endpoint.level == 0.6 and len(env.bridge.calls) == n
    assert say(env, "set video volume to 101")["status"] == "error"


def test_volume_falls_back_to_media_keys_without_pycaw(env):
    env.win._endpoint_factory = lambda: (_ for _ in ()).throw(ImportError("pycaw"))
    ok(env, "volume down")
    assert env.rec.keys.count((0xAE, 0)) == 5
    ok(env, "mute")
    assert (0xAD, 0) in env.rec.keys


def test_brightness_uses_powershell_with_a_valid_percentage(env):
    env.rec.outputs[("powershell",)] = type("P", (), {"returncode": 0, "stdout": "70\n", "stderr": ""})()
    ok(env, "set brightness to 55")
    script = env.rec.runs[-1][0][-1]
    assert "WmiSetBrightness(1,55)" in script
    assert say(env, "set brightness to 150")["status"] == "error"
    ok(env, "undo")
    assert "WmiSetBrightness(1,70)" in env.rec.runs[-1][0][-1]


def test_wifi_and_sleep_are_approval_gated_and_screenshot_sends_the_right_keys(env):
    r = say(env, "turn off wifi")
    assert r["status"] == "approval_required" and not env.rec.runs
    ok(env, "yes")
    assert env.rec.runs[-1][0] == ["netsh", "interface", "set", "interface", "name=Wi-Fi", "admin=disabled"]
    ok(env, "take a screenshot")
    assert env.rec.keys[-4:] == [(0x5B, 0), (0x2C, 0), (0x2C, 2), (0x5B, 2)]
    ok(env, "snap window to the right")
    assert (0x27, 0) in env.rec.keys


def test_windows_only_features_explain_themselves_on_other_platforms(env):
    env.win.platform = "linux"
    r = say(env, "lock the laptop")
    assert r["status"] == "error" and "Windows" in r["message"]


def test_open_folder_app_and_unknown_app(env):
    (env.home / "Downloads").mkdir()
    ok(env, "open downloads")
    assert env.rec.started[-1].endswith("Downloads")
    r = say(env, "open Archives folder")
    assert r["status"] == "error" and "Tell me its location" in r["message"]
    start = env.tmp / "appdata" / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    start.mkdir(parents=True)
    (start / "Notepad.lnk").write_text("x")
    ok(env, "open notepad")
    assert env.rec.started[-1].endswith("Notepad.lnk")
    r = ok(env, "open zzzunknownapp")
    assert "searched Google" in r["message"] and "google.com/search?q=zzzunknownapp" in last_action(env)["url"]


def test_copy_that_puts_the_last_answer_on_the_clipboard_without_shell_interpolation(env):
    ok(env, "what time is it")
    ok(env, "copy that")
    args, kw = env.rec.runs[-1]
    assert args[-1] == "Set-Clipboard -Value $env:COZY_CLIP" and kw["env"]["COZY_CLIP"].startswith("It's")


# ----------------------------------------------------------------------------- web
def test_open_sites_new_tab_and_browser_choice(env):
    ok(env, "open youtube")
    assert env.bridge.calls[-1] == ({"type": "open_url", "url": "https://www.youtube.com", "new_tab": False}, True)
    ok(env, "open github.com in a new tab")
    assert last_action(env)["new_tab"] is True
    r = say(env, "open github in edge")
    assert r["status"] == "error" and "Edge" in r["message"]             # no Edge installed in the sandbox


def test_extension_down_falls_back_to_a_normal_browser_tab(env, monkeypatch):
    opened = []
    monkeypatch.setattr(webbrowser, "open_new_tab", lambda u: opened.append(u) or True)
    env.bridge.connected = False
    r = ok(env, "open github")
    assert opened == ["https://github.com"] and "extension" in r["message"]
    r = ok(env, "play kalyani")
    assert "automatic playback needs the Cozy extension" in r["message"] and "kalyani" in opened[-1]


def test_tab_commands_and_page_reading(env):
    ok(env, "close this tab")
    ok(env, "go back")
    ok(env, "scroll down")
    ok(env, "switch to github tab")
    assert [c[0].get("op") for c in env.bridge.calls] == ["close", "back", "scroll", "switch"]
    assert env.bridge.calls[2][0]["direction"] == "down" and env.bridge.calls[3][0]["query"] == "github"
    env.bridge.results["tab"] = {"ok": True, "data": [{"title": "Inbox", "host": "mail.google.com"}, {"title": "GitHub", "host": "github.com"}]}
    assert "2 tabs" in ok(env, "list my tabs")["message"]
    text = " ".join(f"Sentence number {i} talks about solar panels and renewable energy storage in detail." for i in range(12))
    env.bridge.results["page_text"] = {"ok": True, "data": {"title": "Solar", "text": text}}
    summary = ok(env, "summarize this page")
    assert summary["message"].startswith("Solar:") and "solar panels" in summary["message"]
    assert len(ok(env, "read this page aloud")["speak"]) > 200
    env.bridge.results["page_text"] = {"ok": False, "detail": "Enable page reading in the extension options."}
    assert "Enable page reading" in say(env, "summarize this page")["message"]


# ----------------------------------------------------------------------------- media
def test_seek_and_relative_skip_messages_and_payloads(env):
    r = ok(env, "forward that video to 1min")
    assert last_action(env) == {"type": "media", "action": "seek_to", "value": 60} and "1:00" in r["message"]
    assert env.bridge.calls[-1][1] is False                              # media is never retried
    r = ok(env, "forward")
    assert last_action(env) == {"type": "media", "action": "forward"} and "10 seconds" in r["message"]
    ok(env, "rewind 15 seconds")
    assert last_action(env)["value"] == 15


def test_media_failures_are_explained(env):
    env.bridge.results["media"] = {"ok": False, "detail": "Open a YouTube tab first (no YouTube tab found)"}
    assert "Open a YouTube video first" in say(env, "pause")["message"]
    env.bridge.results["media"] = {"ok": False, "detail": "The seek failed"}
    assert "The seek failed" in say(env, "forward")["message"]


def test_youtube_play_payload_and_failure(env):
    ok(env, "play tenu leke mai jaunga")
    a, retry = env.bridge.calls[-1]
    assert a["type"] == "play_youtube" and "tenu+leke+mai+jaunga" in a["url"] and retry is True
    env.bridge.results["play_youtube"] = {"ok": False, "detail": "YouTube did not expose a playable search result."}
    r = say(env, "play kalyani")
    assert r["status"] == "error" and "playable search result" in r["message"]


def test_speed_now_playing_lyrics_usual_and_queue(env):
    env.bridge.results["media"] = {"ok": True, "data": {"speed": 1.5}}
    assert "1.5x" in ok(env, "play at 1.5x")["message"]
    assert say(env, "play at 3x")["status"] == "error"
    env.bridge.results["now_playing"] = {"ok": True, "data": {"title": "Kesariya", "channel": "Sony Music"}}
    assert 'Now playing "Kesariya" by Sony Music' in ok(env, "what song is this")["message"]
    ok(env, "lyrics")
    assert "Kesariya+lyrics" in last_action(env)["url"]
    for _ in range(3):
        ok(env, "play tum hi ho")
    ok(env, "play kesariya")
    assert "tum hi ho" in ok(env, "play my usual music")["message"]
    ok(env, "add song one to queue")
    ok(env, "queue song two")
    assert "song one" in ok(env, "show queue")["message"]
    ok(env, "play next in queue")
    assert last_action(env)["query"] == "song one"
    ok(env, "clear queue")
    assert say(env, "play next in queue")["status"] == "error"
    ok(env, "play lofi on youtube music")
    assert "music.youtube.com/search?q=lofi" in last_action(env)["url"]


def test_sleep_timer_pauses_later_and_can_be_cancelled(env):
    assert "20 minutes" in ok(env, "pause in 20 minutes")["message"]
    env.clock.advance(19 * 60)
    env.ctx.scheduler.run_due()
    assert not env.bridge.calls
    env.clock.advance(120)
    env.ctx.scheduler.run_due()
    assert last_action(env)["action"] == "pause"
    ok(env, "pause in 5 minutes")
    ok(env, "cancel the sleep timer")
    env.clock.advance(600)
    env.ctx.scheduler.run_due()
    assert len([c for c in env.bridge.calls if c[0].get("action") == "pause"]) == 1


# ----------------------------------------------------------------------------- reminders
def test_reminder_fires_notifies_and_is_removed(env):
    sub = env.ctx.events.subscribe()
    assert "call mom" in ok(env, "remind me to call mom in 20 minutes")["message"]
    assert "call mom" in ok(env, "list my reminders")["message"]
    env.clock.advance(19 * 60)
    assert env.ctx.scheduler.run_due() == 0
    env.clock.advance(120)
    assert env.ctx.scheduler.run_due() == 1
    event = sub.get_nowait()
    assert event["kind"] == "notify" and event["payload"]["message"] == "call mom" and event["payload"]["speak"] is True
    assert env.store.list_reminders() == []
    assert env.rec.popens and "COZY_MSG" in env.rec.popens[-1][1]["env"]    # Windows balloon notification


def test_repeating_reminders_reschedule_and_late_ones_are_marked(env):
    ok(env, "remind me to stretch every 30 minutes")
    sub = env.ctx.events.subscribe()
    env.clock.advance(3 * 3600)                                            # laptop was asleep for 3 hours
    assert env.ctx.scheduler.run_due() == 1
    assert sub.get_nowait()["payload"]["message"].startswith("(Missed)")
    nxt = env.store.list_reminders()[0]
    assert nxt["due"] > env.clock() and nxt["repeat"]["kind"] == "interval"


def test_timers_alarms_listing_cancelling_and_undo(env):
    assert "Timer set for 10 minutes" in ok(env, "timer for 10 minutes")["message"]
    assert "Alarm set" in ok(env, "alarm at 6 am")["message"]
    assert "You have 2" in ok(env, "what's on my schedule")["message"]
    assert "You have nothing scheduled" in ok(env, "show my reminders")["message"]      # that one lists reminders only
    ok(env, "cancel all alarms")
    assert [r["kind"] for r in env.store.list_reminders()] == ["timer"]
    r = say(env, "undo")                                  # the alarm it would cancel is already gone: say so, don't pretend
    assert r["status"] == "error" and "already gone" in r["message"]
    ok(env, "remind me to read in 5 minutes")
    assert len(env.store.list_reminders()) == 2
    ok(env, "undo")
    assert [x["kind"] for x in env.store.list_reminders()] == ["timer"]
    r = say(env, "remind me to buy milk")                 # no time given: ask, never guess
    assert r["status"] == "error" and "When should I" in r["message"]


def test_scheduled_routines_run_commands_and_never_risky_ones(env):
    ok(env, "teach morning mode: set volume to 25")
    assert "every day" in ok(env, "schedule morning mode every day at 10 am")["message"]
    env.clock.advance(3600 + 60)
    env.ctx.scheduler.run_due()
    assert env.rec.endpoint.level == 0.25
    assert len(env.store.list_reminders()) == 1                             # re-armed for tomorrow
    env.store.kv_set("macro:bad", ["shut down"])
    env.ctx.scheduler.add(env.clock() + 1, "bad", kind="command", command="bad")
    env.clock.advance(5)
    env.ctx.scheduler.run_due()
    assert not any(r[0][:1] == ["shutdown"] for r in env.rec.runs)


# ----------------------------------------------------------------------------- information
def test_calculator_units_and_safety(env):
    assert "is 42" in ok(env, "what is 6 times 7")["message"]
    assert "is 54" in ok(env, "what is 12 percent of 450")["message"]
    assert "is 12" in ok(env, "calculate square root of 144")["message"]
    assert say(env, "calculate __import__('os').system('x')")["status"] == "error"
    assert say(env, "calculate 9 ** 9999")["status"] == "error"
    assert "3.107" in ok(env, "5 km to miles")["message"]
    assert "98.6" in ok(env, "37 celsius to fahrenheit")["message"]
    assert say(env, "5 km to kilograms")["status"] == "error"


def test_currency_weather_news_and_briefing_use_the_internet_only_through_the_cache(env):
    env.fetched["routes"] = {
        "frankfurter": {"rates": {"INR": 83.5}},
        "geocoding": {"results": [{"name": "Pune", "latitude": 18.5, "longitude": 73.8}]},
        "open-meteo.com/v1/forecast": {"current": {"temperature_2m": 27.4, "weather_code": 2},
                                       "daily": {"temperature_2m_max": [31], "temperature_2m_min": [22], "precipitation_probability_max": [40]}},
        "news.google.com": b"<rss><channel><item><title>Big story - Paper</title></item><item><title>Second story - Wire</title></item></channel></rss>",
    }
    assert "8,350.00 INR" in ok(env, "100 usd to inr")["message"]
    assert "Which city" in say(env, "what's the weather")["message"]
    ok(env, "set my city to pune")
    w = ok(env, "weather")["message"]
    assert "In Pune it's 27°C with partly cloudy" in w and "rain chance 40%" in w
    before = len(env.fetched["urls"])
    ok(env, "weather")
    assert len(env.fetched["urls"]) == before                               # served from the cache
    assert "1. Big story." in ok(env, "latest news")["message"]
    ok(env, "remind me to stretch in 2 hours")
    brief = ok(env, "good morning")["message"]
    assert "Monday, 19 October" in brief and "Pune" in brief and "stretch" in brief and "Big story" in brief
    say(env, "turn on offline mode")
    assert "Pune" not in ok(env, "good morning")["message"]                 # briefing degrades gracefully offline


def test_translate_prefers_the_local_model_then_google_translate(env):
    from cozy.llm import LocalLLM
    env.ctx.llm = LocalLLM("http://x", "m", post=lambda u, p, t: {"message": {"content": "नमस्ते"}})
    assert ok(env, "translate hello to hindi")["message"] == "नमस्ते"
    env.ctx.llm = None
    ok(env, "translate good night to kannada")
    assert "tl=kn" in last_action(env)["url"]
    assert say(env, "translate hello to klingon")["status"] == "error"


# ----------------------------------------------------------------------------- personal data and safety
def test_facts_clipboard_and_the_sensitive_data_guard(env):
    ok(env, "remember my email is zoya@example.com")
    assert "zoya@example.com" in ok(env, "what is my email")["message"]
    ok(env, "copy my email")
    assert env.rec.runs[-1][1]["env"]["COZY_CLIP"] == "zoya@example.com"
    for text in ("remember my password is hunter2", "remember my card number is 4111 1111 1111 1111", "remember my wifi pin is 4821"):
        assert say(env, text)["status"] == "error", text
    env.store.kv_set("fact:bank password", "x")
    assert say(env, "copy my bank password")["status"] == "error"
    assert say(env, "what is my bank password")["status"] == "error"
    ok(env, "forget my email")
    assert say(env, "what is my email")["status"] == "error"


def test_profiles_and_time_of_day_defaults(env):
    ok(env, "create profile study")
    ok(env, "switch to study profile")
    ok(env, "set morning volume to 30")
    ok(env, "play lofi")                                                    # 9 am on the test clock => morning
    assert env.rec.endpoint.level == 0.3
    env.clock.advance(9 * 3600)                                             # 6 pm => evening
    ok(env, "set evening volume to 55")
    ok(env, "play jazz")
    assert env.rec.endpoint.level == 0.55
    ok(env, "switch to default profile")
    ok(env, "set volume to 90")
    ok(env, "play rock")
    assert env.rec.endpoint.level == 0.9
    assert say(env, "switch to nowhere profile")["status"] == "error"


def test_status_and_help_and_history(env):
    r = ok(env, "status")
    assert "Chrome extension: connected" in r["message"] and r["data"]["tools"] > 50
    env.bridge.connected = False
    assert "NOT connected" in say(env, "diagnostics")["message"]
    assert "reminders" in ok(env, "what can you do")["message"]


# ----------------------------------------------------------------------------- developer
@pytest.fixture
def gitenv(env, tmp_path):
    env.win = WinOps(run=subprocess.run, popen=env.rec.popen, platform="win32", home=env.home, env={},
                     endpoint=lambda: env.rec.endpoint, keybd=env.rec.keybd, startfile=env.rec.startfile)
    env.ctx.win = env.win
    repo = tmp_path / "chalkmind"
    repo.mkdir()
    def git(*a):
        subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)
    git("init", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "Tester")
    (repo / "app.py").write_text("print('hi')\n")
    git("add", "-A")
    git("commit", "-m", "first commit")
    env.repo, env.git = repo, git
    return env


def test_project_git_workflow(gitenv):
    env = gitenv
    assert say(env, "add project chalkmind at /nonexistent")["status"] == "error"
    ok(env, f"add project ChalkMind at {env.repo}")
    ok(env, "open my chalkmind project")
    assert env.ctx.setting("current_project") == "chalkmind"
    assert "clean on branch main" in ok(env, "git status")["message"]
    (env.repo / "app.py").write_text("print('changed')\n")
    (env.repo / "new.txt").write_text("x")
    assert "1 changed and 1 new" in ok(env, "git status")["message"]
    assert "1 commit" in ok(env, "what changed today")["message"] and "2 uncommitted" in ok(env, "what changed today")["message"]
    r = say(env, "commit with message add notes")
    assert r["status"] == "approval_required"
    out = subprocess.run(["git", "-C", str(env.repo), "log", "--oneline"], capture_output=True, text=True).stdout
    assert "add notes" not in out                                           # nothing committed before "yes"
    ok(env, "yes")
    out = subprocess.run(["git", "-C", str(env.repo), "log", "--oneline"], capture_output=True, text=True).stdout
    assert "add notes" in out
    say(env, "push")
    r = say(env, "yes")
    assert r["status"] == "error" and "Push failed" in r["message"]         # no remote configured


def test_github_shortcuts_and_ci_fallback(gitenv):
    env = gitenv
    ok(env, f"add project chalkmind at {env.repo}")
    ok(env, "open my chalkmind project")
    env.git("remote", "add", "origin", "git@github.com:zoya/chalkmind.git")
    ok(env, "open issues")
    assert last_action(env)["url"] == "https://github.com/zoya/chalkmind/issues"
    ok(env, "open my prs")
    assert last_action(env)["url"] == "https://github.com/pulls"
    ok(env, "ci status")                                                    # gh isn't installed here -> Actions page
    assert last_action(env)["url"].endswith("/zoya/chalkmind/actions")


def test_run_tests_and_dev_server_lifecycle(gitenv):
    env = gitenv
    ok(env, f"add project chalkmind at {env.repo}")
    cfg = env.store.kv_get("project:chalkmind")
    cfg.update(
        test=f'"{sys.executable}" -c "print(\'3 passed in 0.1s\')"',
        run="npm run dev",
        url="http://localhost:5173",
        editor=sys.executable,
    )
    env.store.kv_set("project:chalkmind", cfg)
    r = ok(env, "run the tests")
    assert r["message"] == "Chalkmind tests passed: 3 passed in 0.1s."
    cfg["test"] = f'"{sys.executable}" -c "import sys; print(\'1 failed, 2 passed\'); sys.exit(1)"'
    env.store.kv_set("project:chalkmind", cfg)
    r = say(env, "run the tests")
    assert r["status"] == "error" and "1 failed, 2 passed" in r["message"]
    ok(env, "open my chalkmind project")
    spawned = [p[0] for p in env.rec.popens]
    assert any(a == "npm run dev" for a in spawned) and any(isinstance(a, list) and a[1] == str(env.repo) for a in spawned)
    ok(env, "open my chalkmind project")
    assert [p[0] for p in env.rec.popens].count("npm run dev") == 1          # not started twice
    env.clock.advance(6)
    env.ctx.scheduler.run_due()
    assert last_action(env)["url"] == "http://localhost:5173"
    ok(env, "stop my chalkmind project")
    assert env.ctx.dev_servers == {}


def test_snippets_paste_through_the_clipboard(env):
    ok(env, "save snippet fastapi boilerplate: from fastapi import FastAPI")
    ok(env, "paste my fastapi boilerplate")
    assert env.rec.runs[-1][1]["env"]["COZY_CLIP"] == "from fastapi import FastAPI"
    assert env.rec.keys[-2:] == [(0x56, 2), (0x11, 2)] or (0x56, 0) in env.rec.keys
    assert say(env, "save snippet creds: token is abc123")["status"] == "error"
    assert say(env, "paste my nothing")["status"] == "error"
    assert "fastapi boilerplate" in ok(env, "list snippets")["message"]
    assert "random-one-question" in (ok(env, "random leetcode problem") and last_action(env)["url"])
    ok(env, "give me a random leetcode medium problem")
    assert "difficulty=MEDIUM" in last_action(env)["url"]


def test_file_search_and_open(env):
    docs = env.home / "Documents"
    (docs / "sub").mkdir(parents=True)
    (docs / "Resume_2026.pdf").write_text("x")
    (docs / "sub" / "resume_old.docx").write_text("x")
    (docs / "node_modules").mkdir()
    (docs / "node_modules" / "resume.pdf").write_text("x")
    (env.home / "Downloads").mkdir()
    (env.home / "Downloads" / "notes.txt").write_text("x")
    r = ok(env, "find my resume pdf")
    assert "Resume_2026.pdf" in r["message"] and len(r["data"]) == 1
    assert "2" in ok(env, "find resume")["message"]
    ok(env, "open my resume pdf file")
    assert env.rec.started[-1].endswith("Resume_2026.pdf")
    assert say(env, "find my passport scan")["status"] == "error"


def test_plugins_load_and_a_broken_one_is_skipped(env, tmp_path):
    from cozy.plugins import load_plugins
    from cozy.registry import unregister
    plugins = tmp_path / "pl"
    plugins.mkdir()
    (plugins / "good.py").write_text(
        "from cozy.registry import Result, rule, tool\n"
        "@tool('shout', desc='shout text')\n"
        "def shout(ctx, text): return Result(True, text.upper() + '!')\n"
        "rule(r'^shout (?P<text>.+)$', 'shout', priority=10)\n")
    (plugins / "broken.py").write_text("raise RuntimeError('boom')\n")
    (plugins / "_private.py").write_text("raise RuntimeError('never loaded')\n")
    try:
        assert load_plugins(plugins) == ["good"]
        assert ok(env, "shout hello")["message"] == "HELLO!"
        assert load_plugins(tmp_path / "missing") == []
    finally:
        unregister("shout")
