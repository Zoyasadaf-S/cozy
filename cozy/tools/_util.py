from __future__ import annotations

import re
from urllib.parse import quote_plus, urlparse

_CLOCK = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2})$")
_UNITS = re.compile(r"^(?:(\d+)\s*(?:hours?|hrs?|h)\s*)?(?:(\d+)\s*(?:minutes?|mins?|m)\s*)?(?:(\d+)\s*(?:seconds?|secs?|s))?$", re.I)
_DUR = re.compile(r"^(\d+)\s*(seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h)$", re.I)


def parse_timestamp(text: str) -> int:
    """'1:30' -> 90, '1min' -> 60, '2 minutes 10 seconds' -> 130. Raises ValueError otherwise."""
    t = text.strip().lower()
    m = _CLOCK.match(t)
    if m:
        h, mi, s = int(m.group(1) or 0), int(m.group(2)), int(m.group(3))
        if s >= 60 or (m.group(1) and mi >= 60):
            raise ValueError("bad clock time")
        total = h * 3600 + mi * 60 + s
    elif t.isdigit():
        total = int(t)
    else:
        m = _UNITS.match(t)
        if not m or not any(m.groups()):
            raise ValueError("bad timestamp")
        total = int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + int(m.group(3) or 0)
    if not 0 <= total <= 86400:
        raise ValueError("out of range")
    return total


def parse_duration(text: str) -> int:
    """'30 seconds' -> 30, '2 minutes' -> 120 (at least 1 second)."""
    m = _DUR.match(text.strip())
    if not m:
        raise ValueError("bad duration")
    n, unit = int(m.group(1)), m.group(2).lower()
    seconds = n * (3600 if unit.startswith("h") else 60 if unit.startswith("m") else 1)
    if not 1 <= seconds <= 86400:
        raise ValueError("out of range")
    return seconds


def human_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds} second{'s' if seconds != 1 else ''}"
    if seconds < 3600:
        m, s = divmod(seconds, 60)
        return f"{m} minute{'s' if m != 1 else ''}" + (f" {s} seconds" if s else "")
    h, rem = divmod(seconds, 3600)
    m = rem // 60
    return f"{h} hour{'s' if h != 1 else ''}" + (f" {m} minutes" if m else "")


def mmss(seconds: int) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def host_of(url: str) -> str:
    return (urlparse(url).hostname or url).removeprefix("www.")


def q(text: str) -> str:
    return quote_plus(text.strip())
