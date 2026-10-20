"""Optional local-LLM fallback for phrases no rule understands. The model only *chooses* a tool and
its arguments as JSON; Cozy validates and performs the action, so it can never claim to have done
something it didn't."""
from __future__ import annotations

import json
import re
from typing import Optional

from .llm import LLMUnavailable
from .registry import TOOLS

SKIP = {"undo", "copy_that", "status", "what_did_you_do", "approve"}


def _prompt() -> str:
    lines = [f"- {t.name}: {t.desc}" + (f" args: {t.args}" if t.args else "")
             for t in sorted(TOOLS.values(), key=lambda t: t.name) if t.name not in SKIP]
    return ("You are Cozy, a voice assistant on the user's laptop. Reply with ONE JSON object only: "
            '{"tool": "<name>", "args": {...}}. Use a tool whenever the user asks you to DO something. '
            'If no tool fits, use {"tool":"chat","args":{"reply":"<one short sentence>"}} and never claim you did '
            "something.\nTools:\n" + "\n".join(lines))


def route(ctx, text: str) -> Optional[dict]:
    llm = ctx.llm
    if llm is None or not llm.available():
        return None
    try:
        raw = llm.chat([{"role": "system", "content": _prompt()}, {"role": "user", "content": text}], json_mode=True)
    except LLMUnavailable:
        return None
    return parse_decision(raw)


def parse_decision(raw: str) -> Optional[dict]:
    data = None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        m = re.search(r"\{.*\}", raw or "", re.S)
        if m:
            try:
                data = json.loads(m.group(0))
            except ValueError:
                data = None
    if not isinstance(data, dict):
        return None
    name = str(data.get("tool", "")).strip().lower()
    args = data.get("args") if isinstance(data.get("args"), dict) else {}
    if name == "chat":
        reply = str(args.get("reply") or "").strip()
        return {"tool": "chat", "args": {"reply": reply or "I'm not sure how to help with that."}}
    if name in TOOLS and name not in SKIP:
        return {"tool": name, "args": args}
    return None
