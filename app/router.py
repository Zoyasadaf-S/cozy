
"""Let the local language model understand free-form requests.

The model picks ONE tool from a short list and fills in its arguments as JSON.
Our code validates the choice and performs the action, so the model can never
claim it did something it didn't. Add a tool = add a line here + a handler.
"""
from __future__ import annotations

import json
import re
from typing import Dict, List
from urllib.parse import urlparse

from .actions import (
    BROWSER_NAMES,
    MEDIA_ACTIONS,
    Action,
    _google_search,
    _youtube_play,
)

TOOLS = {
    "open_app",
    "open_url",
    "web_search",
    "youtube_play",
    "media_control",
    "remember",
    "chat",
}

PROMPT = """You are the brain of Cozy, a voice assistant running on the user's Windows laptop.
Understand what the user wants and reply with ONE JSON object only: {"tool": "<name>", "args": {...}}

Tools:
- open_app: open ANY app, program or folder on the laptop. args: {"name": "<app name>"}
- open_url: open a website. args: {"url": "<full https url>", "browser": "<optional: chrome|edge|firefox|brave>"}
- web_search: search Google. args: {"query": "<text>", "browser": "<optional>"}
- youtube_play: search YouTube and play the top result. args: {"query": "<text>"}
- media_control: control Windows output volume with volume_up, volume_down,
  mute, unmute, or set_volume. For video controls use video_mute,
  video_unmute, video_volume_up, or video_volume_down. Playback actions
  control the YouTube video already open in Chrome.
  args: {"action": "pause|play|next|previous|mute|unmute|volume_up|volume_down|set_volume|video_mute|video_unmute|video_volume_up|video_volume_down|fullscreen|forward|rewind|skip_ad"}
  For set_volume, include "volume" as an integer from 0 to 100.
- remember: save a note or fact about the user. args: {"text": "<text>"}
- chat: answer a question or talk. args: {"reply": "<1-3 short sentences>"}

Rules: use a tool whenever the user asks you to DO something. If the request needs an action
you have no tool for (alarms, calls, messages, settings...), use chat and say you can't do that yet.
Never say in chat that you opened or played something.

Examples:
User: open file explorer -> {"tool":"open_app","args":{"name":"file explorer"}}
User: start whatsapp -> {"tool":"open_app","args":{"name":"whatsapp"}}
User: take me to amazon.in -> {"tool":"open_url","args":{"url":"https://www.amazon.in"}}
User: play kalyani song -> {"tool":"youtube_play","args":{"query":"kalyani song"}}
User: look up best laptops under 50000 -> {"tool":"web_search","args":{"query":"best laptops under 50000"}}
User: note that my exam is on friday -> {"tool":"remember","args":{"text":"my exam is on friday"}}
User: who is the prime minister of india -> {"tool":"chat","args":{"reply":"Narendra Modi."}}
User: pause this video -> {"tool":"media_control","args":{"action":"pause"}}
User: make it louder -> {"tool":"media_control","args":{"action":"volume_up"}}
User: set volume to 100 -> {"tool":"media_control","args":{"action":"set_volume","volume":100}}
User: open github in edge -> {"tool":"open_url","args":{"url":"https://github.com","browser":"edge"}}
User: set an alarm for 6 am -> {"tool":"chat","args":{"reply":"I can't set alarms yet."}}
"""


def reply(text: str) -> Action:
    return Action("reply", text=text, label=text)


def build_messages(
    history: List[Dict[str, str]],
    facts: Dict[str, str],
    user_text: str,
    examples: List[Dict] = (),
) -> List[Dict[str, str]]:
    system = PROMPT

    if examples:
        system += "\nExamples taught by the user (follow these first):\n" + "\n".join(
            f"User: {e['say']} -> "
            + json.dumps(
                {"tool": e["tool"], "args": e.get("args", {})}
            )
            for e in examples
        )

    if facts:
        system += "\nKnown facts about the user:\n" + "\n".join(
            f"- {key}: {value}" for key, value in facts.items()
        )

    return [
        {"role": "system", "content": system},
        *history,
        {"role": "user", "content": user_text},
    ]


def _load(raw: str):
    try:
        return json.loads(raw)
    except ValueError:
        match = re.search(r"\{.*\}", raw, re.S)

        if match:
            try:
                return json.loads(match.group(0))
            except ValueError:
                return None

    return None


def parse_decision(raw: str) -> Action:
    data = _load(raw)

    if not isinstance(data, dict):
        return reply("Sorry, I didn't catch that. Could you say it again?")

    tool = str(data.get("tool", "")).strip().lower()
    args = data.get("args") if isinstance(data.get("args"), dict) else {}

    def arg(*names: str) -> str:
        for name in names:
            value = args.get(name)
            if isinstance(value, (str, int, float)) and str(value).strip():
                return str(value).strip()
        return ""

    if tool == "open_app" and arg("name"):
        return Action("open_named", arg("name"))

    browser = BROWSER_NAMES.get(arg("browser").lower(), "")

    if tool == "media_control":
        media_action = arg("action").lower().replace(" ", "_")

        if media_action in MEDIA_ACTIONS:
            if media_action == "set_volume":
                raw_volume = arg("volume", "percent", "value")

                try:
                    percent = int(raw_volume)
                except (TypeError, ValueError):
                    return reply("Tell me a volume percentage from 0 to 100.")

                if not 0 <= percent <= 100:
                    return reply("Volume must be between 0 and 100 percent.")

                return Action("media", "set_volume", value=percent)

            return Action("media", media_action)

    if tool == "open_url" and arg("url"):
        url = arg("url")

        if "://" not in url and "." in url and " " not in url:
            url = "https://" + url

        parsed = urlparse(url)

        if parsed.scheme in ("http", "https") and parsed.netloc:
            return Action(
                "open_website",
                url,
                label=f"Opening {parsed.netloc}",
                browser=browser,
            )

    if tool == "web_search" and arg("query"):
        action = _google_search(arg("query"))
        action.browser = browser
        return action

    if tool == "youtube_play" and arg("query"):
        return _youtube_play(arg("query"))

    if tool == "remember" and arg("text"):
        return Action("remember", arg("text"))

    if tool == "chat":
        return reply(
            arg("reply", "text", "answer")
            or "I'm not sure how to help with that."
        )

    return reply("Sorry, I can't do that yet.")


def decide(llm, history, facts, user_text: str, examples: List[Dict] = ()) -> Action:
    return parse_decision(
        llm.chat(
            build_messages(history, facts, user_text, examples),
            json_mode=True,
        )
    )
