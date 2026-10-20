"""Reminders, timers, alarms and scheduled commands (one sleeping scheduler thread)."""
from __future__ import annotations

from datetime import datetime

from ..registry import Result, rule, tool
from ..timeparse import extract_when
from ._util import human_duration


def _when_text(due: datetime, now: datetime, repeat) -> str:
    clock = due.strftime("%I:%M %p").lstrip("0")
    if repeat:
        kind = repeat["kind"]
        if kind == "interval":
            return f"every {human_duration(repeat['seconds'])}"
        return {"daily": f"every day at {clock}", "weekdays": f"every weekday at {clock}", "weekly": f"every week at {clock}"}[kind]
    delta = (due.date() - now.date()).days
    if delta == 0 and (due - now).total_seconds() < 3600:
        return f"in {human_duration((due - now).total_seconds())}"
    return f"{'today' if delta == 0 else 'tomorrow' if delta == 1 else due.strftime('%d %b')} at {clock}"


def _schedule(ctx, text: str, kind: str, label_default: str) -> Result:
    parsed = extract_when(text, ctx.now())
    if parsed is None:
        examples = {"reminder": "remind me to call mom in 20 minutes", "timer": "timer for 10 minutes", "alarm": "alarm at 6 am"}
        return Result(False, f"When should I do that? For example: {examples[kind]}.")
    task, due, repeat = parsed
    task = task or label_default
    rid = ctx.scheduler.add(due.timestamp(), task, kind=kind, repeat=repeat)
    when = _when_text(due, ctx.now(), repeat)
    undo = {"tool": "cancel_reminder_id", "args": {"rid": rid}, "label": f"cancelled the {kind}"}
    if kind == "timer":
        return Result(True, f"Timer set for {human_duration((due - ctx.now()).total_seconds())}.", undo=undo)
    if kind == "alarm":
        return Result(True, f"Alarm set for {when}.", undo=undo)
    return Result(True, f"Okay, I'll remind you to {task} {when}.", undo=undo)


@tool("set_reminder", desc="set a reminder with a time", args='{"text": "call mom in 20 minutes"}')
def set_reminder(ctx, text):
    return _schedule(ctx, text, "reminder", "do that")


@tool("set_timer", desc="start a countdown timer", args='{"text": "10 minutes"}')
def set_timer(ctx, text):
    return _schedule(ctx, "in " + text.strip(), "timer", "Timer")


@tool("set_alarm", desc="set an alarm", args='{"text": "6 am"}')
def set_alarm(ctx, text):
    body = text.strip()
    return _schedule(ctx, body if body.lower().startswith(("at ", "tomorrow", "every")) else "at " + body, "alarm", "Alarm")


@tool("cancel_reminder_id", desc="cancel one reminder by id")
def cancel_reminder_id(ctx, rid):
    n = ctx.scheduler.cancel(rid=int(rid))
    return Result(bool(n), "Cancelled." if n else "That reminder is already gone.")


@tool("list_reminders", desc="list reminders, timers or alarms", args='{"kind": "reminder|timer|alarm"}')
def list_reminders(ctx, kind=None):
    kinds = {"reminders": "reminder", "timers": "timer", "alarms": "alarm"}
    kind = kinds.get(kind, kind)
    items = [i for i in ctx.store.list_reminders(kind) if i["kind"] in ("reminder", "timer", "alarm")]
    if not items:
        return Result(True, "You have nothing scheduled.")
    now = ctx.now()
    parts = [f"{i['text'] or i['kind']} {_when_text(datetime.fromtimestamp(i['due']), now, i['repeat'])}" for i in items[:6]]
    return Result(True, f"You have {len(items)}: " + "; ".join(parts) + ".", data=items)


@tool("cancel_reminders", desc="cancel all reminders, timers or alarms", args='{"kind": "reminder|timer|alarm"}')
def cancel_reminders(ctx, kind=None):
    kinds = {"reminders": "reminder", "timers": "timer", "alarms": "alarm", "reminder": "reminder", "timer": "timer", "alarm": "alarm"}
    n = ctx.scheduler.cancel(kind=kinds.get(kind or ""))
    return Result(True, f"Cancelled {n}." if n else "There was nothing to cancel.")


@tool("schedule_command", desc="run a command or routine later or repeatedly", args='{"command": "work mode", "when": "every day at 9"}')
def schedule_command(ctx, command, when):
    parsed = extract_when(when, ctx.now())
    if parsed is None:
        return Result(False, "I didn't catch when. For example: run work mode every day at 9 am.")
    _, due, repeat = parsed
    ctx.scheduler.add(due.timestamp(), command, kind="command", command=command, repeat=repeat)
    return Result(True, f"Okay, I'll run '{command}' {_when_text(due, ctx.now(), repeat)}.")


def fire(ctx, item: dict) -> None:
    """Called by the scheduler thread for each due item."""
    kind, text = item["kind"], item.get("text") or ""
    if item.get("command"):
        result = ctx.run_command(item["command"])
        if kind == "command":
            ctx.notify("Cozy", result.get("message", ""))
        return
    late = item.get("late_by", 0) > 300
    prefix = "(Missed) " if late else ""
    title = {"timer": "Timer finished", "alarm": "Alarm"}.get(kind, "Reminder")
    ctx.notify(title, prefix + (text if kind == "reminder" else title), speak=True)


rule(r"^remind\s+me\s+(?:to\s+|about\s+|that\s+)?(?P<text>.+)$", "set_reminder", priority=20)
rule(r"^(?:set\s+(?:a\s+)?)?timer(?:\s+for)?\s+(?P<text>.+)$", "set_timer", priority=20)
rule(r"^(?:set\s+(?:an?\s+)?)?alarm(?:\s+(?:for|at))?\s+(?P<text>.+)$", "set_alarm", priority=20)
rule(r"^(?:list|show|what are)\s+(?:my\s+)?(?P<kind>reminders|timers|alarms)$", "list_reminders", priority=20)
rule(r"^what(?:'s| is)\s+(?:on\s+)?(?:my\s+)?(?:schedule|reminders)$", "list_reminders", priority=20)
rule(r"^(?:cancel|delete|clear)\s+(?:all\s+)?(?:my\s+)?(?P<kind>reminders|timers|alarms|reminder|timer|alarm)$", "cancel_reminders", priority=20)
rule(r"^(?:schedule|run)\s+(?P<command>.+?)\s+(?P<when>(?:every|at|in|tomorrow)\b.+)$", "schedule_command", priority=30)
