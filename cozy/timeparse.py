"""Understand "in 20 minutes", "tomorrow at 7:30 am", "every day at 9" inside a sentence."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Optional, Tuple

UNIT_SECONDS = {"second": 1, "sec": 1, "minute": 60, "min": 60, "hour": 3600, "hr": 3600, "day": 86400, "week": 604800}
WORD_NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "ten": 10,
            "fifteen": 15, "twenty": 20, "thirty": 30, "half a": 0.5, "half an": 0.5}

_DUR = re.compile(r"\b(?:in|for)\s+(?P<n>\d+(?:\.\d+)?|half an?|an?|one|two|three|four|five|ten|fifteen|twenty|thirty)\s*"
                  r"(?P<u>seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|weeks?)\b", re.I)
_AT = re.compile(r"\b(?:(?P<day>today|tomorrow|tonight)\s+)?at\s+(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>[ap]\.?m\.?)?(?!\w)", re.I)
_AMPM = re.compile(r"\b(?:(?P<day>today|tomorrow|tonight)\s+)?(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>[ap]\.?m\.?)(?!\w)", re.I)
_EVERY = re.compile(r"\bevery\s+(?:(?P<n>\d+)\s*(?P<u>minutes?|mins?|hours?|hrs?)|(?P<word>day|weekday|week|morning|evening|night))\b", re.I)
_TOMORROW = re.compile(r"\btomorrow\b", re.I)
_DEFAULT_TIMES = {"morning": (8, 0), "evening": (18, 0), "night": (21, 0)}


def _unit(u: str) -> int:
    u = u.lower().rstrip("s")
    return UNIT_SECONDS[u]


def _hm(h: str, m: Optional[str], ap: Optional[str], day: Optional[str]) -> Optional[Tuple[int, int]]:
    hour, minute = int(h), int(m or 0)
    if minute > 59:
        return None
    if ap:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if ap.lower().startswith("p") else 0)
    else:
        if hour > 23:
            return None
        if day and day.lower() == "tonight" and hour < 12:
            hour += 12
    return hour, minute


def extract_when(text: str, now: datetime) -> Optional[Tuple[str, datetime, Optional[dict]]]:
    """Return (text without the time words, first due time, repeat rule) or None."""
    spans, repeat, due = [], None, None
    default_hm = None

    em = _EVERY.search(text)
    if em:
        spans.append(em.span())
        if em.group("n"):
            repeat = {"kind": "interval", "seconds": int(em.group("n")) * _unit(em.group("u"))}
        else:
            word = em.group("word").lower()
            repeat = {"kind": {"weekday": "weekdays", "week": "weekly"}.get(word, "daily")}
            default_hm = _DEFAULT_TIMES.get(word, (9, 0))

    clock = None
    for rx in (_AT, _AMPM):
        cm = rx.search(text)
        if cm and not any(s <= cm.start() < e for s, e in spans):
            hm = _hm(cm.group("h"), cm.group("m"), cm.group("ap"), cm.group("day"))
            if hm:
                clock = (cm, hm)
                spans.append(cm.span())
                break

    dm = _DUR.search(text)
    if dm and clock is None:
        raw = dm.group("n").lower()
        n = float(raw) if raw[0].isdigit() else WORD_NUM[raw]
        due = now + timedelta(seconds=n * _unit(dm.group("u")))
        spans.append(dm.span())

    if clock:
        cm, (h, m) = clock
        day = (cm.group("day") or "").lower()
        due = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if day == "tomorrow":
            due += timedelta(days=1)
        elif due <= now:
            due += timedelta(days=1)
    elif due is None and _TOMORROW.search(text):
        tm = _TOMORROW.search(text)
        spans.append(tm.span())
        due = (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    elif due is None and repeat:
        if repeat["kind"] == "interval":
            due = now + timedelta(seconds=repeat["seconds"])
        else:
            h, m = default_hm or (9, 0)
            due = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if due <= now:
                due += timedelta(days=1)

    if due is None:
        return None
    rest = text
    for s, e in sorted(spans, reverse=True):
        rest = rest[:s] + " " + rest[e:]
    rest = re.sub(r"\s+", " ", rest).strip(" ,.;:-")
    return rest, due, repeat


def next_due(repeat: dict, last_due: datetime, now: datetime) -> datetime:
    """The next occurrence strictly after `now` (missed ones are skipped, not replayed)."""
    kind = repeat["kind"]
    due = last_due
    for _ in range(100000):
        if kind == "interval":
            due += timedelta(seconds=max(60, int(repeat["seconds"])))
        elif kind == "weekly":
            due += timedelta(days=7)
        else:
            due += timedelta(days=1)
            if kind == "weekdays":
                while due.weekday() >= 5:
                    due += timedelta(days=1)
        if due > now:
            return due
    return now + timedelta(days=1)
