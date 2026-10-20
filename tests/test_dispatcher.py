import pytest

from cozy.llm import LocalLLM


def say(env, text, **kw):
    return env.disp.handle(text, **kw)


def test_dangerous_actions_ask_first_and_only_run_after_yes(env):
    r = say(env, "shut down")
    assert r["status"] == "approval_required" and "Are you sure" in r["message"]
    assert not env.rec.runs                                        # nothing happened yet
    done = say(env, "yes")
    assert done["status"] == "done"
    assert env.rec.runs[-1][0][:2] == ["shutdown", "/s"]


def test_no_cancels_and_other_commands_abandon_the_question(env):
    say(env, "restart the laptop")
    assert say(env, "no")["message"] == "Okay, cancelled."
    assert not env.rec.runs
    say(env, "shut down")
    say(env, "what time is it")                                    # abandons the pending question
    assert say(env, "yes")["status"] != "done" or not env.rec.runs


def test_approval_endpoint_path_and_expiry(env):
    aid = say(env, "shut down")["approval_id"]
    env.clock.advance(120)
    assert say_approve(env, aid, True)["status"] == "error"
    aid = say(env, "shut down")["approval_id"]
    assert say_approve(env, aid, True)["status"] == "done"


def say_approve(env, aid, approved):
    return env.disp.approve(aid, approved)


def test_scheduled_commands_can_never_run_risky_tools(env):
    r = env.disp.handle("shut down", source="scheduled")
    assert r["status"] == "error" and not env.rec.runs


def test_volume_change_can_be_undone(env):
    env.rec.endpoint.level = 0.30
    say(env, "set volume to 80")
    assert env.rec.endpoint.level == 0.8
    r = say(env, "undo that")
    assert r["status"] == "done" and env.rec.endpoint.level == 0.3


def test_pause_undo_resumes_and_empty_undo_is_honest(env):
    assert say(env, "undo")["status"] == "error"
    say(env, "pause")
    say(env, "undo")
    assert [c[0]["action"] for c in env.bridge.calls] == ["pause", "play"]


def test_repeat_and_play_it_again_reuse_the_last_action(env):
    say(env, "play kalyani song")
    say(env, "play it again")
    assert [c[0]["query"] for c in env.bridge.calls] == ["kalyani song", "kalyani song"]
    say(env, "set volume to 20")
    say(env, "again")
    assert env.rec.endpoint.level == 0.2


def test_multi_step_runs_every_step_in_order(env):
    r = say(env, "set volume to 30 and then play lofi")
    assert r["status"] == "done" and len(r["steps"]) == 2
    assert env.rec.endpoint.level == 0.3 and env.bridge.calls[0][0]["query"] == "lofi"


def test_multi_step_stops_at_an_approval_question(env):
    r = say(env, "pause then shut down then play lofi")
    assert r["status"] == "approval_required"
    assert all(c[0]["type"] == "media" for c in env.bridge.calls) and len(env.bridge.calls) == 1


def test_teach_macro_and_run_it(env):
    r = say(env, "teach work mode: open github then set volume to 20")
    assert r["status"] == "done", r
    out = say(env, "work mode")
    assert out["status"] == "done" and len(out["steps"]) == 2
    assert env.bridge.calls[0][0]["url"] == "https://github.com" and env.rec.endpoint.level == 0.2
    assert say(env, "run work mode")["status"] == "done"
    assert "work mode" in say(env, "list my routines")["message"]
    assert say(env, "forget the phrase work mode")["status"] == "done"
    assert say(env, "work mode")["status"] != "done"


def test_macros_refuse_unknown_steps_and_self_loops(env):
    assert say(env, "teach party: dance wildly")["status"] == "error"
    assert say(env, "teach pause: pause")["status"] == "error"


def test_low_confidence_speech_asks_before_acting(env):
    r = say(env, "set volume to 100", confidence=0.3)
    assert r["status"] == "confirm" and env.rec.endpoint.level == 0.5
    assert say(env, "yes")["status"] == "done" and env.rec.endpoint.level == 1.0
    assert say(env, "set volume to 10", confidence=0.9)["status"] == "done"


def test_offline_mode_blocks_internet_tools_but_not_local_ones(env):
    say(env, "turn on offline mode")
    r = say(env, "define serendipity")
    assert r["status"] == "error" and "Offline mode" in r["message"] and "urls" not in env.fetched
    assert say(env, "what is 6 times 7")["message"].startswith("6 times 7 is 42")
    say(env, "offline mode off")
    env.fetched["routes"] = {"dictionaryapi": [{"meanings": [{"partOfSpeech": "noun", "definitions": [{"definition": "a happy accident"}]}]}]}
    assert "a happy accident" in say(env, "define serendipity")["message"]


def test_extension_down_gives_a_helpful_message(env):
    env.bridge.connected = False
    r = say(env, "pause")
    assert r["status"] == "error" and "Chrome extension" in r["message"]


def test_unknown_commands_are_honest_and_a_crashing_tool_does_not_crash_cozy(env, monkeypatch):
    assert say(env, "tell me a story about dragons")["status"] == "unknown"
    from cozy.registry import TOOLS
    monkeypatch.setitem(TOOLS, "tell_time", type(TOOLS["tell_time"])("tell_time", lambda ctx: 1 / 0))
    r = say(env, "what time is it")
    assert r["status"] == "error" and "went wrong" in r["message"]


def test_action_log_records_commands_and_redacts_secrets(env):
    say(env, "set volume to 40")
    say(env, "remember my password is hunter22")
    rows = env.store.history()
    assert any(r["text"] == "set volume to 40" and r["ok"] for r in rows)
    assert all("hunter22" not in (r["text"] + r["message"]) for r in rows)
    assert "set volume to 40" in say(env, "what did you do today")["message"]


def test_llm_router_chooses_tools_but_cannot_invent_them(env):
    answers = iter(['{"tool":"tell_time","args":{}}', '{"tool":"launch_missiles","args":{}}',
                    '{"tool":"chat","args":{"reply":"Hello!"}}', "not json"])
    llm = LocalLLM("http://x", "m", post=lambda url, payload, t: {"message": {"content": next(answers)}})
    env.ctx.llm = llm
    assert say(env, "could you tell the hour")["message"].startswith("It's")
    assert say(env, "do the thing")["status"] == "unknown"
    assert say(env, "how are you")["message"] == "Hello!"
    assert say(env, "gibberish zzz")["status"] == "unknown"


def test_llm_outage_is_remembered_so_commands_stay_fast(env):
    calls = []

    def boom(url, payload, t):
        calls.append(1)
        raise OSError("refused")
    env.ctx.llm = LocalLLM("http://x", "m", post=boom, clock=env.clock)
    say(env, "blah blah one")
    say(env, "blah blah two")
    assert len(calls) == 1                                            # second command skipped the dead model
    env.clock.advance(61)
    say(env, "blah blah three")
    assert len(calls) == 2
