"""Facts, custom macros/routines, profiles with time-of-day defaults, and settings."""
from __future__ import annotations

import re

from .. import guard
from ..parser import split_steps
from ..registry import Result, match_rules, rule, tool

_SETTING_LABELS = {"offline": "Offline mode", "speak_on_laptop": "Speaking replies on the laptop"}


# ----------------------------------------------------------------------------- facts
@tool("remember_fact", desc="remember a personal fact", args='{"key": "email", "value": "..."}')
def remember_fact(ctx, key, value):
    if guard.is_sensitive(key, value):
        return Result(False, guard.REFUSAL)
    ctx.store.kv_set("fact:" + key.lower().strip(), value.strip())
    return Result(True, f"Got it. Your {key.strip()} is {value.strip()}.")


@tool("remember_note", desc="remember a free-form note", args='{"text": str}')
def remember_note(ctx, text):
    if guard.is_sensitive(text):
        return Result(False, guard.REFUSAL)
    ctx.store.kv_set(f"note:{int(ctx.clock() * 1000)}", text.strip())
    return Result(True, "Got it, I'll remember that.")


@tool("recall_fact", desc="tell the user a fact they saved", args='{"key": str}')
def recall_fact(ctx, key):
    value = ctx.store.kv_get("fact:" + key.lower().strip())
    if value is None:
        return Result(False, f"I don't know your {key.strip()} yet. Say 'remember my {key.strip()} is ...'.")
    if guard.is_sensitive(key, str(value)):
        return Result(False, "That looks sensitive, so I won't read it out.")
    return Result(True, f"Your {key.strip()} is {value}.")


@tool("forget_fact", desc="forget a saved fact", args='{"key": str}')
def forget_fact(ctx, key):
    gone = ctx.store.kv_delete("fact:" + key.lower().strip())
    return Result(gone, f"Forgot your {key.strip()}." if gone else f"I didn't have a {key.strip()} saved.")


@tool("copy_fact", desc="copy a saved fact to the clipboard", args='{"key": str}')
def copy_fact(ctx, key):
    value = ctx.store.kv_get("fact:" + key.lower().strip())
    if value is None:
        return Result(False, f"I don't have your {key.strip()} saved.")
    if guard.is_sensitive(key, str(value)):
        return Result(False, guard.REFUSAL)
    ctx.win.clipboard_set(str(value))
    return Result(True, f"Copied your {key.strip()}.")


# ----------------------------------------------------------------------------- macros / routines
def _split_macro_steps(text: str):
    out = []
    for chunk in re.split(r"\s*(?:;|,|\band then\b|\bthen\b)\s*", text):
        out.extend(split_steps(chunk.strip()))
    return [s for s in out if s]


@tool("teach_macro", desc="teach Cozy a custom phrase that runs several commands", args='{"phrase": "work mode", "steps": "open github then play lofi"}')
def teach_macro(ctx, phrase, steps):
    phrase = phrase.strip().lower()
    parts = _split_macro_steps(steps)
    if not parts:
        return Result(False, "Tell me what the phrase should do.")
    if len(phrase) < 3 or phrase in {p.lower() for p in parts}:
        return Result(False, "That phrase would loop on itself. Pick a different name.")
    unknown = [p for p in parts if match_rules(p) is None and ctx.store.kv_get("macro:" + p.lower()) is None]
    if unknown:
        return Result(False, f"I don't know how to do '{unknown[0]}', so I didn't save it.")
    ctx.store.kv_set("macro:" + phrase, parts)
    return Result(True, f"Learned '{phrase}': {', then '.join(parts)}.")


@tool("forget_macro", desc="forget a custom phrase", args='{"phrase": str}')
def forget_macro(ctx, phrase):
    gone = ctx.store.kv_delete("macro:" + phrase.strip().lower())
    return Result(gone, f"Forgot '{phrase.strip()}'." if gone else f"I don't have a phrase called '{phrase.strip()}'.")


@tool("list_macros", desc="list custom phrases")
def list_macros(ctx):
    macros = ctx.store.kv_all("macro:")
    if not macros:
        return Result(True, "You haven't taught me any phrases yet. Try: teach work mode: open github then play lofi.")
    return Result(True, "I know: " + "; ".join(f"{k} ({len(v)} steps)" for k, v in macros.items()) + ".", data=macros)


# ----------------------------------------------------------------------------- profiles and time-of-day defaults
@tool("create_profile", desc="create a named profile", args='{"name": str}')
def create_profile(ctx, name):
    key = "profile:" + name.strip().lower()
    if ctx.store.kv_get(key) is not None:
        return Result(False, f"There is already a profile called {name.strip()}.")
    ctx.store.kv_set(key, {"defaults": {}, "auto_defaults": False})
    return Result(True, f"Created the {name.strip()} profile.")


@tool("switch_profile", desc="switch to another profile", args='{"name": str}')
def switch_profile(ctx, name):
    key = name.strip().lower()
    if key != "default" and ctx.store.kv_get("profile:" + key) is None:
        return Result(False, f"I don't have a profile called {name.strip()}. Say 'create profile {name.strip()}'.")
    ctx.set_setting("profile", key)
    return Result(True, f"Switched to the {key} profile.")


@tool("set_band_default", desc="set a default volume for a time of day", args='{"band": "morning|afternoon|evening|night", "percent": 30}')
def set_band_default(ctx, band, percent):
    percent = int(percent)
    if not 0 <= percent <= 100:
        return Result(False, "Volume must be between 0 and 100 percent.")
    prof = ctx.profile()
    prof.setdefault("defaults", {}).setdefault(band, {})["volume"] = percent
    prof["auto_defaults"] = True
    ctx.save_profile(prof)
    return Result(True, f"In the {band} I'll start with the volume at {percent} percent.")


@tool("auto_defaults", desc="turn time-of-day defaults on or off", args='{"state": "on|off"}')
def auto_defaults(ctx, state):
    prof = ctx.profile()
    prof["auto_defaults"] = state == "on"
    ctx.save_profile(prof)
    return Result(True, f"Time-of-day defaults are {state}.")


# ----------------------------------------------------------------------------- settings
@tool("set_flag", desc="turn offline mode or laptop speech on or off", args='{"name": "offline|speak_on_laptop", "value": true}')
def set_flag(ctx, name, value):
    if name not in _SETTING_LABELS:
        return Result(False, "I don't have a setting with that name.")
    ctx.set_setting(name, bool(value))
    extra = " Internet lookups are paused; everything on this laptop still works." if name == "offline" and value else ""
    return Result(True, f"{_SETTING_LABELS[name]} is {'on' if value else 'off'}.{extra}")


@tool("set_city", desc="set the city used for weather", args='{"city": str}')
def set_city(ctx, city):
    ctx.set_setting("city", city.strip().title())
    return Result(True, f"Okay, I'll use {city.strip().title()} for the weather.")


_on = lambda v: v.lower() in ("on", "enable", "enabled")  # noqa: E731
rule(r"^remember\s+(?:that\s+)?my\s+(?P<key>[\w' -]+?)\s+(?:is|are)\s+(?P<value>.+)$", "remember_fact", priority=20)
rule(r"^remember\s+(?:that\s+)?(?P<text>.+)$", "remember_note", priority=40)
rule(r"^(?:what(?:'s| is)|tell me|what are)\s+my\s+(?P<key>[\w' -]+)$", "recall_fact", priority=40)
rule(r"^forget\s+my\s+(?P<key>[\w' -]+)$", "forget_fact", priority=20)
rule(r"^copy\s+my\s+(?P<key>[\w' -]+)$", "copy_fact", priority=20)
rule(r"^(?:when i say|if i say|teach(?:\s+cozy)?(?:\s+the phrase)?)\s+(?P<phrase>[^:,]+?)\s*[:,]\s*(?:then\s+|do\s+)?(?P<steps>.+)$", "teach_macro", priority=10)
rule(r"^forget\s+(?:the\s+)?(?:phrase|macro|routine)\s+(?P<phrase>.+)$", "forget_macro", priority=10)
rule(r"^(?:list|show|what are)\s+(?:my\s+)?(?:macros|routines|phrases|custom phrases)$", "list_macros", priority=10)
rule(r"^(?:run|start)\s+(?:the\s+)?(?:routine\s+)?(?P<phrase>.+?)(?:\s+routine)?$", "run_macro", priority=95)
rule(r"^create\s+(?:a\s+)?profile\s+(?:called\s+|named\s+)?(?P<name>.+)$", "create_profile", priority=20)
rule(r"^(?:switch|change)\s+to\s+(?:the\s+)?(?P<name>.+?)\s+profile$", "switch_profile", priority=20)
rule(r"^set\s+(?:the\s+)?(?P<band>morning|afternoon|evening|night)\s+volume\s+to\s+(?P<percent>\d+)\s*(?:%|percent)?$",
     "set_band_default", conv={"band": str.lower, "percent": int}, priority=20)
rule(r"^turn\s+(?P<state>on|off)\s+(?:time of day|time-of-day)\s+defaults$", "auto_defaults", conv={"state": str.lower}, priority=20)
rule(r"^turn\s+(?P<v>on|off)\s+offline\s+mode$|^offline\s+mode\s+(?P<v2>on|off)$", "set_flag_offline", priority=20)
rule(r"^turn\s+(?P<v>on|off)\s+(?:speaking|voice replies|laptop voice)$", "set_flag_speak", priority=20)
rule(r"^(?:set\s+)?(?:my\s+)?(?:city|location)\s+(?:to|is)\s+(?P<city>.+)$", "set_city", priority=20)


# `run <phrase>` only fires for phrases the user actually taught (so "run tests" etc. aren't hijacked).
@tool("run_macro", desc="run a custom phrase", args='{"phrase": str}')
def run_macro(ctx, phrase):
    steps = ctx.store.kv_get("macro:" + phrase.strip().lower())
    if not steps:
        return Result(False, f"I don't have a routine called '{phrase.strip()}'.")
    out = ctx.dispatch(phrase.strip())
    return Result(out["status"] == "done", out.get("message", "Done."))


# on/off flag rules need the value mapped; tiny wrappers keep the rule table simple
@tool("set_flag_offline", desc="turn offline mode on or off")
def set_flag_offline(ctx, v=None, v2=None):
    return set_flag(ctx, "offline", _on(v or v2 or "off"))


@tool("set_flag_speak", desc="turn laptop speech on or off")
def set_flag_speak(ctx, v):
    return set_flag(ctx, "speak_on_laptop", _on(v))
