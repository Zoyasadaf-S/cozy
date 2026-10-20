"""Text clean-up shared by every rule: wake phrase, fillers, Hinglish, multi-step splitting."""
from __future__ import annotations

import re
from typing import List

_WAKE = re.compile(r"^(?:ok|okay|hey)\s+cozy\b[\s,.:;!?-]*", re.I)
_FILLER = re.compile(r"^(?:(?:now|please|can you|could you|just|hey|ok|okay|then|also)\s+)+", re.I)
_TRAIL = re.compile(r"\s+please$", re.I)

_HINGLISH = [
    (re.compile(r"^youtube\s+(?:ko\s+|pe\s+)?(?:kholo|khol\s+do|open\s+karo)$", re.I), "open youtube"),
    (re.compile(r"^youtube\s+pe\s+(.+?)\s+chalao$", re.I), r"play \1 on youtube"),
    (re.compile(r"^(.+?)\s+(?:alag\s+tab\s+(?:mein\s+)?)(?:ko\s+)?(?:kholo|khol\s+do)$", re.I), r"open \1 in a new tab"),
    (re.compile(r"^(.+?)\s+(?:ko\s+)?(?:kholo|khol\s+do)$", re.I), r"open \1"),
    (re.compile(r"^is(?:ko|se)\s+pause\s+karo$", re.I), "pause"),
    (re.compile(r"^video\s+ki\s+(?:volume|awaz)\s+(?:thoda\s+)?(?:kam|down)\s+karo$", re.I), "decrease video volume"),
    (re.compile(r"^(?:volume|awaz)\s+(?:thoda\s+)?(?:kam|down)\s+karo$", re.I), "decrease volume"),
    (re.compile(r"^(?:volume|awaz)\s+(?:thoda\s+)?(?:badhao|zyada|up)\s+karo$", re.I), "increase volume"),
]

_NO_SPLIT = re.compile(r"^(?:teach|when i say|if i say|remember|remind|note|commit|translate|define|add|save|snippet|"
                       r"schedule|set an? |create|timer|alarm|run .+ (?:every|at|in) )", re.I)
_STEP_SPLIT = re.compile(
    r"\s+(?:and then|after that)\s+|\s*,\s*(?:and\s+)?then\s+|\s+then\s+|"
    r"\s+and\s+(?=(?:set|turn|open|play|pause|mute|unmute|volume|increase|decrease|lower|raise|lock|search|"
    r"remind|start|stop|close|switch|go|run|show|resume|skip|forward|rewind)\b)", re.I)


def normalize(text: str, wake_phrases=("ok cozy", "okay cozy")) -> str:
    t = " ".join((text or "").split())
    m = _WAKE.match(t)
    if m:
        t = t[m.end():]
    else:
        for w in wake_phrases:
            if t.lower().startswith(w.lower()) and (len(t) == len(w) or not t[len(w)].isalnum()):
                t = t[len(w):].lstrip(" ,.:;!?-")
                break
    t = _FILLER.sub("", t)
    t = _TRAIL.sub("", t).strip().rstrip(".!?").strip()
    t = re.sub(r"\bthr(?=\s+volume\b)", "the", t, flags=re.I)   # common speech-to-text slip
    for rx, repl in _HINGLISH:
        if rx.match(t):
            return rx.sub(repl, t)
    return t


def split_steps(text: str) -> List[str]:
    if _NO_SPLIT.match(text):
        return [text]
    parts = [p.strip(" ,.") for p in _STEP_SPLIT.split(text)]
    return [p for p in parts if p] or [text]
