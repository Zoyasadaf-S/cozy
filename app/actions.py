"""Turn spoken/typed text into safe, structured actions.

Supports: open a known website or app, search Google, play/search YouTube,
and 'remember ...' notes. Anything else is returned as 'chat' (handled by the
LLM). Actions that change things (calls, messages, delete...) need approval.
"""
from __future__ import annotations

import re
import webbrowser
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Callable
from urllib.parse import quote_plus

WEBSITES = {
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",
    "gmail": "https://mail.google.com",
    "calendar": "https://calendar.google.com",
    "chatgpt": "https://chatgpt.com",
    "github": "https://github.com",
    "whatsapp": "https://web.whatsapp.com",
}
WEBSITE_NAMES = {"youtube": "YouTube", "gmail": "Gmail", "github": "GitHub", "whatsapp": "WhatsApp", "chatgpt": "ChatGPT"}

RISKY_WORDS = ("call", "message", "send", "delete", "remove", "shutdown", "format")

# How speech-to-text often writes "cozy".
COZY_HEARD = {
    "cozy", "cosy", "cozzy", "cozi", "cosi", "cozie", "cosie", "cozee", "cozey",
    "cosey", "kozy", "kosy", "cousy", "causing", "cozying", "cosying", "cozzie",
    "cozie's", "cozy's", "cosy's", "kozhi", "kozi", "cozhi", "kosi",
}

BROWSER_RE = re.compile(
    r"\s+(?:in|on|using|with|via)\s+(?:the\s+)?"
    r"(google chrome|chrome|microsoft edge|edge|mozilla firefox|firefox|brave)"
    r"(?:\s+browser)?\s*$",
    re.IGNORECASE,
)

BROWSER_NAMES = {
    "google chrome": "chrome",
    "chrome": "chrome",
    "microsoft edge": "edge",
    "edge": "edge",
    "mozilla firefox": "firefox",
    "firefox": "firefox",
    "brave": "brave",
}

MEDIA_ACTIONS = {
    "pause", "play", "toggle", "next", "previous", "mute", "unmute",
    "volume_up", "volume_down", "set_volume", "fullscreen", "forward",
    "rewind", "skip_ad", "video_volume_up", "video_volume_down",
    "video_mute", "video_unmute",
}

_THIS = r"(?: (?:the|this|that))?"

MEDIA_RULES = [
    (r"^skip (?:the )?ads?$", "skip_ad"),
    (rf"^(?:pause|stop){_THIS}(?: (?:video|song|music|playback|youtube|it))?$", "pause"),
    (rf"^(?:resume|continue|unpause){_THIS}(?: (?:video|song|music|playback|it))?$", "play"),
    (rf"^play{_THIS}(?: (?:video|song|music|it))?$", "play"),
    (rf"^(?:next|skip){_THIS}(?: (?:video|song|track|one))?$", "next"),
    (r"^(?:previous|prev|go back|play (?:the )?previous)(?: (?:video|song|track|one))?$", "previous"),
    (rf"^mute{_THIS}(?: (?:video|song|music|sound|audio|it))?$", "mute"),
    (rf"^unmute{_THIS}(?: (?:video|song|music|sound|audio|it))?$", "unmute"),
    (
        r"^(?:(?:turn |make )?(?:the )?(?:volume|sound) up|"
        r"(?:increase|raise|turn up)(?: the)? (?:volume|sound)|louder)$",
        "volume_up",
    ),
    (
        r"^(?:(?:turn |make )?(?:the )?(?:volume|sound) down|"
        r"(?:decrease|lower|reduce|turn down)(?: the)? (?:volume|sound)|quieter)$",
        "volume_down",
    ),
    (r"^(?:make )?(?:it |the video )?full ?screen$|^exit full ?screen$", "fullscreen"),
    (r"^(?:forward|fast forward|skip forward)(?: \d+ seconds?)?$", "forward"),
    (r"^(?:rewind|backward)(?: \d+ seconds?)?$", "rewind"),
]

WAKE_FIRST = {"ok", "okay", "o.k.", "hey"}
COMMAND_VERBS = {
    "open", "launch", "start", "play", "watch", "search", "google",
    "find", "remember", "go", "turn",
}


@dataclass
class Action:
    kind: str  # open_website | open_named | youtube_play | media | remember | greet | reply | chat
    target: str = ""
    needs_approval: bool = False
    text: str = ""
    label: str = ""
    query: str = ""
    browser: str = ""
    value: int | None = None  # Absolute controls such as set_volume.


def _is_cozy(word: str) -> bool:
    w = word.lower().strip(",.:;!?-")
    return w in COZY_HEARD or SequenceMatcher(None, w, "cozy").ratio() >= 0.8


def strip_wake_word(text: str, wake_word: str = "ok cozy") -> str:
    """Remove 'ok cozy', tolerating common speech-to-text mishearings."""
    parts = text.strip().split(None, 2)

    if len(parts) >= 2 and parts[0].lower().strip(",.") in WAKE_FIRST:
        if _is_cozy(parts[1]):
            return parts[2].strip() if len(parts) == 3 else ""

        if len(parts) == 3:
            w1 = parts[1].lower().strip(",.:;!?-")
            first = parts[2].split()[0].lower().strip(",.:;!?-")
            if w1 not in COMMAND_VERBS and first in COMMAND_VERBS:
                return parts[2].strip()

    if parts and _is_cozy(parts[0]):
        return " ".join(parts[1:]).strip()

    return text.strip()


def has_wake_word(text: str) -> bool:
    return strip_wake_word(text) != text.strip()


def _youtube_search(q: str) -> Action:
    q = q.strip().strip(".!?")
    return Action(
        "open_website",
        f"https://www.youtube.com/results?search_query={quote_plus(q)}",
        label=f"Searching YouTube for {q}",
        query=q,
    )


def _youtube_play(q: str) -> Action:
    """Play the top result. An empty query means the last searched item."""
    q = q.strip().strip(".!?")
    return Action(
        "youtube_play",
        q,
        query=q,
        label=f"Playing {q} on YouTube" if q else "Playing on YouTube",
    )


def _google_search(q: str) -> Action:
    q = q.strip().strip(".!?")
    return Action(
        "open_website",
        f"https://www.google.com/search?q={quote_plus(q)}",
        label=f"Searching Google for {q}",
    )


FILLER_RE = re.compile(
    r"^(?:(?:now|please|can you|could you|just|then|also|and|hey|ok|okay)\s+)+",
    re.IGNORECASE,
)


def parse_command(text: str, wake_word: str = "ok cozy") -> Action:
    cleaned = FILLER_RE.sub("", strip_wake_word(text, wake_word)).strip()
    browser = ""

    bm = BROWSER_RE.search(cleaned)
    if bm:
        browser = BROWSER_NAMES[bm.group(1).lower()]
        cleaned = cleaned[:bm.start()].strip()

    action = _parse_core(cleaned)
    action.browser = browser
    return action


def _parse_core(cleaned: str) -> Action:
    lower = cleaned.lower().strip()

    if not lower:
        return Action("greet", text="", label="Yes, I'm listening.")

    rm = re.match(r"^remember(?:\s+that)?\s+(.+)$", cleaned, re.IGNORECASE)
    if rm:
        return Action("remember", rm.group(1).strip(), text=cleaned)

    risky = any(re.search(rf"\b{w}\b", lower) for w in RISKY_WORDS)
    if risky:
        return Action("chat", cleaned, needs_approval=True, text=cleaned)

    # Exact system volume percentage.
    # Examples: "set volume to 100", "volume 80%", "increase volume to 60 percent".
    volume_patterns = [
        r"^(?:(?:set|put|make|turn)\s+(?:the\s+)?(?:system\s+)?"
        r"(?:volume|sound)\s+(?:(?:to|at)\s+)?)"
        r"(-?\d+)(?:\s*(?:%|percent|per cent))?$",

        r"^(?:(?:increase|raise)\s+(?:the\s+)?(?:system\s+)?"
        r"(?:volume|sound)\s+to\s+)"
        r"(-?\d+)(?:\s*(?:%|percent|per cent))?$",

        r"^(?:volume|sound)\s+(?:to\s+)?"
        r"(-?\d+)(?:\s*(?:%|percent|per cent))?$",
    ]

    for pattern in volume_patterns:
        vm = re.match(pattern, lower)
        if vm:
            percent = int(vm.group(1))
            if 0 <= percent <= 100:
                return Action("media", "set_volume", text=cleaned, value=percent)
            return Action(
                "reply",
                text="Volume must be between 0 and 100 percent.",
                label="Volume must be between 0 and 100 percent.",
            )

    video_volume = re.match(
        r"^(?:(?:turn|increase|raise|decrease|lower)\s+)?"
        r"(?:the\s+)?(?:video|youtube)(?:\s+video)?\s+volume\s+(up|down)$|"
        r"^(?:increase|raise|decrease|lower)\s+(?:the\s+)?"
        r"(?:video|youtube)(?:\s+video)?\s+volume$",
        lower,
    )
    if video_volume:
        direction = video_volume.group(1)
        if direction is None:
            direction = "up" if lower.startswith(("increase", "raise")) else "down"
        return Action("media", f"video_volume_{direction}", text=cleaned)

    video_mute = re.match(r"^(mute|unmute)\s+(?:the\s+)?(?:video|youtube)(?:\s+video)?$", lower)
    if video_mute:
        return Action("media", f"video_{video_mute.group(1)}", text=cleaned)

    # Media controls for the currently playing media.
    for pattern, media_action in MEDIA_RULES:
        if re.match(pattern, lower):
            return Action("media", media_action, text=cleaned)

    # "Play the first result" means the last YouTube search.
    if re.match(r"^(?:play|watch)\s+(?:the\s+)?(?:first|top|1st)\b", lower):
        return _youtube_play("")

    # "Open YouTube and play X".
    cm = re.match(
        r"^(?:open|go to)\s+youtube(?:\.com)?\s+(?:and|then)\s+"
        r"(play|watch|search(?:\s+for)?|find)\s+(.+)$",
        lower,
    )
    if cm:
        return (
            _youtube_play(cm.group(2))
            if cm.group(1) in ("play", "watch")
            else _youtube_search(cm.group(2))
        )

    # "Play X on YouTube" / "search X in YouTube".
    m = re.match(
        r"^(play|watch|search|find)\s+(.+?)\s+(?:on|in)\s+youtube(?:\.com)?$",
        lower,
    )
    if m:
        return (
            _youtube_play(m.group(2))
            if m.group(1) in ("play", "watch")
            else _youtube_search(m.group(2))
        )

    m = re.match(
        r"^(.+?)\s+(?:song|songs|video|videos)?\s*(?:on|in)\s+youtube(?:\.com)?$",
        lower,
    )
    if m:
        return _youtube_search(m.group(1))

    m = re.match(r"^youtube\s+search\s+(.+)$", lower)
    if m:
        return _youtube_search(m.group(1))

    m = re.match(r"^(?:youtube\s+play|play|watch)\s+(.+)$", lower)
    if m:
        return _youtube_play(m.group(1))

    # Google search.
    m = re.match(r"^(?:search|google)\s+(?:for\s+)?(.+?)(?:\s+(?:on|in)\s+google)?$", lower)
    if m:
        return _google_search(m.group(1))

    m = re.match(r"^(?:open|launch|start|go to)\s+(.+)$", lower)
    if m:
        name = m.group(1).strip().rstrip(".!?")
        name = re.sub(r"^(the|my)\s+", "", name)

        if name in WEBSITES:
            return Action("open_website", WEBSITES[name], text=cleaned, label=f"Opening {WEBSITE_NAMES.get(name, name.title())}")

        if re.match(r"^[\w-]+(\.[\w-]+)+$", name):
            return Action("open_website", f"https://{name}", text=cleaned, label=f"Opening {name}")

        return Action("open_named", name, text=cleaned)

    return Action("chat", cleaned, text=cleaned)


def execute(action: Action, open_url: Callable[[str], object] = webbrowser.open) -> str:
    if action.needs_approval:
        raise PermissionError("This action needs your approval first.")

    if action.kind in ("greet", "reply"):
        return action.label

    if action.kind == "open_website":
        open_url(action.target)
        return action.label or f"Opening {action.target}"

    return "No local action; hand to the language model."
