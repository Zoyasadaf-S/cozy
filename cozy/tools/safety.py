"""Diagnostics, history, undo and help."""
from __future__ import annotations

import time
from datetime import datetime

from .. import __version__
from ..registry import TOOLS, Result, rule, tool


@tool("undo", desc="undo the last reversible change (volume, brightness, mute, pause, reminders)")
def undo(ctx):
    return ctx.undo_fn()


@tool("what_did_you_do", desc="read back today's actions")
def what_did_you_do(ctx):
    midnight = datetime.fromtimestamp(ctx.clock()).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    rows = [r for r in ctx.store.history(40, since=midnight) if r["tool"] != "what_did_you_do"]
    if not rows:
        return Result(True, "I haven't done anything yet today.")
    recent = "; ".join(f"{r['text']} ({'ok' if r['ok'] else 'failed'})" for r in rows[:5])
    return Result(True, f"Today I handled {len(rows)} request{'s' if len(rows) != 1 else ''}. Latest: {recent}.", data=rows)


def status_snapshot(ctx) -> dict:
    llm = ctx.llm
    return {
        "version": __version__,
        "extension_connected": ctx.bridge.available(),
        "extension_version": ctx.bridge.version,
        "pending_actions": ctx.bridge.pending(),
        "offline": ctx.offline,
        "llm_available": bool(llm and llm.available()),
        "profile": ctx.profile_name(),
        "scheduled": len(ctx.store.list_reminders()),
        "tools": len(TOOLS),
        "plugins": list(getattr(ctx, "plugins_loaded", [])),
        "uptime_s": int(ctx.clock() - ctx.started),
        "live_clients": ctx.events.subscribers,
    }


@tool("status", desc="report whether Cozy and the Chrome extension are healthy")
def status(ctx):
    s = status_snapshot(ctx)
    ext = f"connected (v{s['extension_version']})" if s["extension_connected"] and s["extension_version"] else \
          "connected" if s["extension_connected"] else "NOT connected"
    text = (f"Cozy {s['version']} is running. Chrome extension: {ext}. Offline mode {'on' if s['offline'] else 'off'}. "
            f"Local language model: {'ready' if s['llm_available'] else 'not running'}. {s['scheduled']} scheduled item(s).")
    return Result(True, text, data=s)


@tool("help", desc="say what Cozy can do")
def help_tool(ctx):
    return Result(True, "I can open sites, apps and folders; play and control YouTube; change volume and brightness; set reminders, "
                        "timers and alarms; run your git and test commands; find files; answer quick questions; and run routines you teach me. "
                        "Try: play lofi, set volume to 40, remind me to stretch in 20 minutes, or what changed today.")


rule(r"^undo(?:\s+(?:that|it|the last (?:thing|action)))?$|^take that back$", "undo", priority=10)
rule(r"^what\s+did\s+you\s+do(?:\s+today)?$|^(?:show|read)\s+(?:my\s+)?(?:action\s+)?(?:history|log)$", "what_did_you_do", priority=20)
rule(r"^(?:status|diagnostics|are you connected|is the extension connected|how are you doing|health check)$", "status", priority=20)
rule(r"^(?:help|what can you do)$", "help", priority=20)
