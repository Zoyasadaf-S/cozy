import json
import threading
import time

import pytest

from cozy.config import Settings, get_settings, load_token
from cozy.llm import LLMUnavailable, LocalLLM
from cozy.netio import Http, OfflineError
from cozy.scheduler import Scheduler
from cozy.store import Store, redact
from cozy.winops import OpFailed, Unsupported, WinOps, canonical_folder


def test_store_kv_prefix_history_and_play_counts():
    s = Store(":memory:")
    s.kv_set("fact:a_b", 1)
    s.kv_set("fact:aXb", 2)
    s.kv_set("macro:x", [1])
    assert s.kv_all("fact:a_") == {"b": 1}                              # '_' is not a wildcard
    assert s.kv_get("nope", "d") == "d" and s.kv_delete("fact:a_b") and not s.kv_delete("fact:a_b")
    for i in range(3):
        s.log_action(f"cmd {i}", "t", True, "m")
    assert [h["text"] for h in s.history(2)] == ["cmd 2", "cmd 1"]
    s.record_play("Lofi")
    s.record_play("lofi")
    s.record_play("jazz")
    assert s.top_plays(1) == [{"query": "lofi", "count": 2}]


def test_redaction_removes_secrets_and_long_numbers():
    assert "hunter2" not in redact("my password is hunter2")
    assert "4111111111111111" not in redact("card 4111111111111111")
    assert redact("set volume to 40") == "set volume to 40"


def test_store_uses_wal_to_keep_disk_writes_low(tmp_path):
    s = Store(tmp_path / "x.db")
    assert s._db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert s._db.execute("PRAGMA synchronous").fetchone()[0] == 1       # NORMAL


def test_scheduler_thread_sleeps_when_idle_and_wakes_for_new_work():
    store = Store(":memory:")
    calls = {"next_due": 0}
    real = store.next_due
    store.next_due = lambda: (calls.__setitem__("next_due", calls["next_due"] + 1), real())[1]
    fired = []
    sch = Scheduler(store, fired.append)
    sch.start()
    time.sleep(0.6)
    assert calls["next_due"] <= 2                                       # idle: no polling at all
    sch.add(time.time() + 0.2, "later")
    deadline = time.time() + 3
    while not fired and time.time() < deadline:
        time.sleep(0.05)
    sch.stop()
    assert fired and fired[0]["text"] == "later"


def test_scheduler_survives_a_failing_item():
    store = Store(":memory:")
    seen = []

    def fire(item):
        seen.append(item["text"])
        if item["text"] == "boom":
            raise RuntimeError("x")
    sch = Scheduler(store, fire, clock=lambda: 1000.0)
    store.add_reminder(10, "reminder", "boom", None, None)
    store.add_reminder(20, "reminder", "ok", None, None)
    assert sch.run_due() == 2 and seen == ["boom", "ok"]


def test_http_cache_offline_switch_and_timeouts():
    now = [0.0]
    fetches = []
    offline = [False]
    http = Http(lambda: offline[0], fetcher=lambda u, t: (fetches.append(u), b'{"a": 1}')[1], clock=lambda: now[0])
    assert http.get_json("https://x/y", ttl=60) == {"a": 1} and http.get_json("https://x/y", ttl=60) == {"a": 1}
    assert len(fetches) == 1
    now[0] = 61
    http.get_json("https://x/y", ttl=60)
    assert len(fetches) == 2
    offline[0] = True
    with pytest.raises(OfflineError):
        http.get_text("https://x/other")


def test_llm_uses_json_mode_and_reports_outages_once():
    seen = []
    llm = LocalLLM("http://localhost:11434/", "m", post=lambda u, p, t: (seen.append((u, p)), {"message": {"content": "{}"}})[1])
    assert llm.chat([{"role": "user", "content": "hi"}], json_mode=True) == "{}"
    assert seen[0][0] == "http://localhost:11434/api/chat" and seen[0][1]["format"] == "json" and seen[0][1]["stream"] is False
    disabled = LocalLLM("http://x", "m", enabled=lambda: False)
    assert not disabled.available()
    with pytest.raises(LLMUnavailable):
        disabled.chat([])

def test_config_from_environment(monkeypatch, tmp_path):
    monkeypatch.delenv("COZY_HOST", raising=False)
    monkeypatch.setenv("COZY_LAN", "1")
    monkeypatch.setenv("COZY_PORT", "9001")
    monkeypatch.setenv("COZY_DATA", str(tmp_path))
    monkeypatch.setenv("COZY_ALLOWED_HOSTS", "cozy.local, mypc")
    s = get_settings()
    assert s.host == "0.0.0.0" and s.port == 9001 and {"cozy.local", "mypc", "localhost"} <= s.allowed_hosts
    monkeypatch.delenv("COZY_LAN")
    assert get_settings().host == "127.0.0.1"


def test_winops_validation_and_platform_guards(tmp_path):
    ops = WinOps(platform="linux", home=tmp_path)
    for call in (lambda: ops.set_volume(50), ops.lock, lambda: ops.hotkey("win", "d"), lambda: ops.set_brightness(10)):
        with pytest.raises(Unsupported):
            call()
    win = WinOps(platform="win32", home=tmp_path, endpoint=lambda: None)
    for bad in (101, -1, True, "5", 5.5):
        with pytest.raises(OpFailed):
            win.set_volume(bad)
    with pytest.raises(OpFailed):
        win.hotkey("win", "nonsense")
    ops.notify("t", "m")                                                  # silently ignored off Windows
    assert canonical_folder("Screen Shorts folder") == "screenshots" and canonical_folder("my documents") == "documents"
    assert canonical_folder("random") is None


def test_winops_folder_resolution_only_returns_existing_paths(tmp_path):
    (tmp_path / "OneDrive" / "Pictures" / "Screenshots").mkdir(parents=True)
    (tmp_path / "Downloads").mkdir()
    ops = WinOps(platform="win32", home=tmp_path, env={})
    assert ops.folder("downloads") == (tmp_path / "Downloads").resolve()
    assert ops.folder("screen shorts") == (tmp_path / "OneDrive" / "Pictures" / "Screenshots").resolve()
    assert ops.folder("documents") is None and ops.folder("random directory") is None


def test_app_finder_exact_partial_fuzzy_and_rescans_new_installs(tmp_path):
    start = tmp_path / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    start.mkdir(parents=True)
    (start / "Visual Studio Code.lnk").write_text("x")
    (start / "Calculator.lnk").write_text("x")
    ops = WinOps(platform="win32", home=tmp_path, env={"APPDATA": str(tmp_path)})
    assert ops.find_app("calculator").stem == "Calculator"
    assert ops.find_app("studio code").stem == "Visual Studio Code"
    assert ops.find_app("calculater").stem == "Calculator"               # fuzzy
    assert ops.find_app("zzzzz") is None
    (start / "Spotify.lnk").write_text("x")
    assert ops.find_app("spotify").stem == "Spotify"                     # installed after the first scan
    assert ops.find_app("bad&name|calc") is None                         # never used to build a shell command


def test_notifications_pass_text_through_the_environment_not_the_command_line(tmp_path):
    popens = []
    ops = WinOps(platform="win32", home=tmp_path, popen=lambda a, **kw: popens.append((a, kw)), env={})
    ops.notify("Reminder", "'; Remove-Item -Recurse C:\\ ; '")
    args, kw = popens[0]
    assert "Remove-Item" not in " ".join(args) and "Remove-Item" in kw["env"]["COZY_MSG"]
