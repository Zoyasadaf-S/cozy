"""Tools and parse rules register themselves here. Plugins use the same two decorators."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple


@dataclass
class Result:
    ok: bool = True
    message: str = ""
    data: Any = None
    undo: Optional[dict] = None      # {"tool": ..., "args": {...}, "label": ...}
    speak: Optional[str] = None      # text to read aloud when it differs from `message`


@dataclass
class Tool:
    name: str
    fn: Callable
    desc: str = ""
    risky: bool = False              # needs a spoken/tapped "yes" first
    net: bool = False                # uses the internet (blocked in offline mode)
    args: str = ""                   # argument hint shown to the optional local LLM


@dataclass
class Rule:
    pattern: "re.Pattern[str]"
    tool: str
    static: dict
    conv: dict
    priority: int


TOOLS: Dict[str, Tool] = {}
RULES: List[Rule] = []


def tool(name: str, *, desc: str = "", risky: bool = False, net: bool = False, args: str = ""):
    def deco(fn):
        first_line = (fn.__doc__ or "").strip().split("\n")[0]
        TOOLS[name] = Tool(name, fn, desc or first_line, risky, net, args)
        return fn
    return deco


def rule(pattern: str, tool_name: str, *, static: Optional[dict] = None,
         conv: Optional[dict] = None, priority: int = 50) -> None:
    """Lower priority number = checked first. 10 plugins, 20 specific, 50 normal, 90 catch-all."""
    RULES.append(Rule(re.compile(pattern, re.I), tool_name, static or {}, conv or {}, priority))
    RULES.sort(key=lambda r: r.priority)


def unregister(tool_name: str) -> None:
    TOOLS.pop(tool_name, None)
    RULES[:] = [r for r in RULES if r.tool != tool_name]


def match_rules(text: str) -> Optional[Tuple[str, dict]]:
    for r in RULES:
        m = r.pattern.match(text)
        if not m:
            continue
        args = dict(r.static)
        bad = False
        for key, value in m.groupdict().items():
            if value is None:
                continue
            value = value.strip()
            if key in r.conv:
                try:
                    value = r.conv[key](value)
                except (ValueError, TypeError):
                    bad = True
                    break
            args[key] = value
        if not bad:
            return r.tool, args
    return None
