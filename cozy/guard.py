"""Keeps secrets out of memory, the clipboard helpers and the action log."""
from __future__ import annotations

import re

_SENSITIVE = re.compile(
    r"\b(pass\s?word|passcode|pin|otp|cvv|card\s+number|credit\s+card|debit\s+card|secret|api[\s_-]?key|token|"
    r"aadhaar|aadhar|ssn|social\s+security|pan\s+number|private\s+key|seed\s+phrase|recovery\s+code)\b", re.I)
_LONG_NUMBER = re.compile(r"\b(?:\d[ -]?){12,}\b")

REFUSAL = "That looks like a password, key or card number, so I won't store or repeat it."


def is_sensitive(*texts: str) -> bool:
    return any(t and (_SENSITIVE.search(t) or _LONG_NUMBER.search(t)) for t in texts)
