"""YouTube playback and video controls (through the Chrome extension), queue, speed, sleep timer,
now playing, lyrics and 'play my usual music'."""
from __future__ import annotations

import re

from ..bridge import ExtensionUnavailable
from ..registry import Result, rule, tool
from ..timeparse import UNIT_SECONDS
from ._util import host_of, human_duration, mmss, parse_duration, parse_timestamp, q
from .web import open_in_default_browser, open_url


def _band(hour: int) -> str:
    return "morning" if hour < 12 else "afternoon" if hour < 17 else "evening" if hour < 21 else "night"


def _apply_time_defaults(ctx) -> None:
    prof = ctx.profile()
    if not prof.get("auto_defaults"):
        return
    volume = (prof.get("defaults") or {}).get(_band(ctx.now().hour), {}).get("volume")
    if isinstance(volume, int):
        try:
            ctx.win.set_volume(volume)
        except Exception:
            pass


@tool("youtube_play", desc="search YouTube and play the top result", args='{"query": str}')
def youtube_play(ctx, query=""):
    qtext = (query or ctx.last_play_query or "").strip().strip(".!?")
    if not qtext:
        return Result(False, "Tell me what to play first, for example: play Kalyani song.")
    ctx.last_play_query = qtext
    ctx.store.record_play(qtext)
    _apply_time_defaults(ctx)
    url = f"https://www.youtube.com/results?search_query={q(qtext)}"
    try:
        res = ctx.browser({"type": "play_youtube", "url": url, "query": qtext}, retry=True, timeout=60)
    except ExtensionUnavailable:
        if open_in_default_browser(url):
            return Result(True, "Opened YouTube search results, but automatic playback needs the Cozy extension to be connected.")
        return Result(False, "I couldn't open YouTube.")
    if res.get("ok"):
        return Result(True, f"Playing {qtext} on YouTube.", data=res.get("data"))
    return Result(False, f"I couldn't start playback in the Chrome tab: {res.get('detail') or 'no details'}")


MESSAGES = {"pause": "Paused.", "play": "Playing.", "toggle": "Toggling playback.", "next": "Playing the next video.",
            "previous": "Playing the previous video.", "mute": "Muted the video.", "unmute": "Unmuted the video.",
            "volume_up": "Increasing the video's volume.", "volume_down": "Decreasing the video's volume.",
            "fullscreen": "Toggling full screen.", "skip_ad": "Skipped the ad."}
UNDO = {"pause": "play", "play": "pause", "mute": "unmute", "unmute": "mute"}


@tool("media_control", desc="control the YouTube video that is open in Chrome",
      args='{"action": "pause|play|next|previous|mute|unmute|volume_up|volume_down|set_volume|seek_to|forward|rewind|fullscreen|skip_ad", "value": int}')
def media_control(ctx, action, value=None):
    if action == "set_volume" and (value is None or not 0 <= int(value) <= 100):
        return Result(False, "Video volume must be between 0 and 100 percent.")
    if action == "seek_to" and (value is None or not 0 <= int(value) <= 86400):
        return Result(False, "Tell me a valid video time, such as 1:00.")
    payload = {"type": "media", "action": action}
    if value is not None:
        payload["value"] = int(value)
    res = ctx.browser(payload, retry=False)               # at most once: never replay a relative skip
    if not res.get("ok"):
        detail = (res.get("detail") or "").strip()
        if "no video" in detail.lower() or "youtube tab" in detail.lower():
            return Result(False, "I couldn't find a video to control. Open a YouTube video first.")
        return Result(False, f"I couldn't control the video{': ' + detail if detail else '.'}")
    if action == "set_volume":
        msg = f"Setting the video's volume to {int(value)} percent."
    elif action == "seek_to":
        msg = f"Moving the video to {mmss(int(value))}."
    elif action in ("forward", "rewind"):
        n = int(value) if value is not None else 10
        msg = f"{'Skipping forward' if action == 'forward' else 'Rewinding'} {human_duration(n)}."
    else:
        msg = MESSAGES.get(action, "Done.")
    undo = {"tool": "media_control", "args": {"action": UNDO[action]}, "label": MESSAGES[UNDO[action]].rstrip(".").lower()} if action in UNDO else None
    return Result(True, msg, undo=undo)


@tool("playback_speed", desc="change the video playback speed", args='{"value": 1.5}')
def playback_speed(ctx, value=None, step=None):
    payload = {"type": "media", "action": "speed"}
    if value is not None:
        if not 0.25 <= float(value) <= 2:
            return Result(False, "YouTube speeds go from 0.25x to 2x.")
        payload["value"] = float(value)
    else:
        payload["step"] = step or 0.25
    res = ctx.browser(payload, retry=False)
    if not res.get("ok"):
        return Result(False, f"I couldn't change the speed: {res.get('detail') or 'no details'}")
    speed = (res.get("data") or {}).get("speed")
    return Result(True, f"Playing at {speed}x." if speed else "Changed the speed.")


@tool("sleep_timer", desc="pause playback after a delay", args='{"value": seconds}')
def sleep_timer(ctx, value):
    ctx.scheduler.add(ctx.clock() + int(value), "Sleep timer", kind="sleep", command="pause")
    return Result(True, f"I'll pause playback in {human_duration(int(value))}.")


@tool("cancel_sleep_timer", desc="cancel the sleep timer")
def cancel_sleep_timer(ctx):
    n = ctx.scheduler.cancel(kind="sleep")
    return Result(bool(n), "Sleep timer cancelled." if n else "There is no sleep timer running.")


@tool("now_playing", desc="say which song or video is playing")
def now_playing(ctx):
    res = ctx.browser({"type": "now_playing"}, retry=True)
    data = res.get("data") or {}
    if not res.get("ok") or not data.get("title"):
        return Result(False, "I couldn't find a video that is playing. Open a YouTube video first.")
    by = f" by {data['channel']}" if data.get("channel") else ""
    return Result(True, f"Now playing \"{data['title']}\"{by}.", data=data)


@tool("lyrics", desc="open lyrics for the current or a named song", args='{"query": str}')
def lyrics(ctx, query=""):
    title = query.strip()
    if not title:
        info = now_playing(ctx)
        if not info.ok:
            return Result(False, "Tell me the song, or play something first.")
        title = (info.data or {}).get("title", "")
    return open_url(ctx, f"https://www.google.com/search?q={q(title + ' lyrics')}", label=f"Opening lyrics for {title}.")


@tool("play_usual", desc="play the song you play most often")
def play_usual(ctx):
    top = ctx.store.top_plays(1)
    if not top:
        return Result(False, "I don't know your favourites yet. Play a few songs and ask again.")
    res = youtube_play(ctx, top[0]["query"])
    return Result(res.ok, f"Your usual: {top[0]['query']}. " + res.message if res.ok else res.message)


@tool("music_search", desc="search Spotify or YouTube Music (opens results)", args='{"query": str, "service": "spotify|ytmusic"}')
def music_search(ctx, query, service="spotify"):
    if service == "spotify":
        url, name = f"https://open.spotify.com/search/{q(query)}", "Spotify"
    else:
        url, name = f"https://music.youtube.com/search?q={q(query)}", "YouTube Music"
    res = open_url(ctx, url, label=f"Searching {name} for {query}.")
    return Result(res.ok, res.message + (" (I can open the results; press play yourself.)" if res.ok else ""))


@tool("queue_add", desc="add a song to the play queue", args='{"query": str}')
def queue_add(ctx, query):
    items = ctx.store.kv_get("queue", [])
    items.append(query.strip())
    ctx.store.kv_set("queue", items[-50:])
    return Result(True, f"Added {query.strip()} to the queue. {len(items)} in the queue.")


@tool("queue_next", desc="play the next song in the queue")
def queue_next(ctx):
    items = ctx.store.kv_get("queue", [])
    if not items:
        return Result(False, "The queue is empty.")
    nxt, rest = items[0], items[1:]
    ctx.store.kv_set("queue", rest)
    res = youtube_play(ctx, nxt)
    return Result(res.ok, res.message + (f" {len(rest)} left in the queue." if res.ok and rest else ""))


@tool("queue_show", desc="say what is in the play queue")
def queue_show(ctx):
    items = ctx.store.kv_get("queue", [])
    return Result(True, "The queue has: " + "; ".join(items) + "." if items else "The queue is empty.", data=items)


@tool("queue_clear", desc="clear the play queue")
def queue_clear(ctx):
    ctx.store.kv_set("queue", [])
    return Result(True, "Queue cleared.")


# ----------------------------------------------------------------------------- rules
def _speed(v):
    return float(v)


rule(r"^skip\s+(?:the\s+)?ads?$", "media_control", static={"action": "skip_ad"}, priority=20)
rule(r"^(?:(?:seek|jump|go|move|forward|fast forward|skip forward|take)(?:\s+(?:(?:the|that|this)\s+)?(?:video|song))?"
     r"|(?:forward|fast forward|skip forward|seek|jump)\s+(?:it|this|that))\s+(?:to|at)\s+(?P<value>.+)$",
     "media_control", static={"action": "seek_to"}, conv={"value": parse_timestamp}, priority=20)
rule(r"^(?:forward|fast forward|skip forward)(?:\s+(?:(?:the|that|this)\s+)?(?:video|song))?(?:\s+by)?\s+"
     r"(?P<value>\d+\s*(?:seconds?|secs?|s|minutes?|mins?|m))$",
     "media_control", static={"action": "forward"}, conv={"value": parse_duration}, priority=20)
rule(r"^(?:rewind|backward|skip back)(?:\s+(?:(?:the|that|this)\s+)?(?:video|song))?(?:\s+by)?\s+"
     r"(?P<value>\d+\s*(?:seconds?|secs?|s|minutes?|mins?|m))$",
     "media_control", static={"action": "rewind"}, conv={"value": parse_duration}, priority=20)
rule(r"^(?:forward|fast forward|skip forward)$", "media_control", static={"action": "forward"}, priority=30)
rule(r"^(?:rewind|backward|skip back)$", "media_control", static={"action": "rewind"}, priority=30)
rule(r"^(?:(?:set|put|make|turn|increase|raise|decrease|lower|reduce)\s+)?(?:the\s+)?(?:video|youtube)(?:\s+video)?\s+"
     r"(?:volume|sound)\s+(?:(?:to|at)\s+)?(?P<value>-?\d+)(?:\s*(?:%|percent|per cent))?$",
     "media_control", static={"action": "set_volume"}, conv={"value": int}, priority=20)
rule(r"^(?:turn up|increase|raise)\s+(?:the\s+)?(?:video|youtube)(?:\s+video)?\s+(?:volume|sound)$|"
     r"^(?:turn\s+)?(?:the\s+)?(?:video|youtube)(?:\s+video)?\s+(?:volume|sound)\s+up$",
     "media_control", static={"action": "volume_up"}, priority=20)
rule(r"^(?:turn down|decrease|lower|reduce)\s+(?:the\s+)?(?:video|youtube)(?:\s+video)?\s+(?:volume|sound)$|"
     r"^(?:turn\s+)?(?:the\s+)?(?:video|youtube)(?:\s+video)?\s+(?:volume|sound)\s+down$",
     "media_control", static={"action": "volume_down"}, priority=20)
rule(r"^(?P<action>mute|unmute)\s+(?:the\s+)?(?:video|youtube)(?:\s+video)?$", "media_control", conv={"action": str.lower}, priority=20)
rule(r"^(?:play\s+(?:it\s+|this\s+|the video\s+)?at|set\s+(?:the\s+)?(?:playback\s+)?speed\s+to|speed)\s+"
     r"(?P<value>\d(?:\.\d+)?)\s*(?:x|times)?(?:\s+speed)?$", "playback_speed", conv={"value": _speed}, priority=20)
rule(r"^(?:normal|regular)\s+speed$", "playback_speed", static={"value": 1.0}, priority=20)
rule(r"^(?:speed\s+(?:it\s+)?up|faster)$", "playback_speed", static={"step": 0.25}, priority=20)
rule(r"^(?:slow\s+(?:it\s+)?down|slower)$", "playback_speed", static={"step": -0.25}, priority=20)
rule(r"^(?:pause|stop)(?:\s+(?:the|this|that))?(?:\s+(?:video|song|music|playback|youtube|it))?\s+(?:in|after)\s+"
     r"(?P<value>\d+\s*(?:seconds?|secs?|minutes?|mins?|hours?|hrs?))$", "sleep_timer", conv={"value": parse_duration}, priority=20)
rule(r"^(?:set\s+(?:a\s+)?)?sleep\s+timer(?:\s+(?:for|of))?\s+(?P<value>\d+\s*(?:seconds?|secs?|minutes?|mins?|hours?|hrs?))$",
     "sleep_timer", conv={"value": parse_duration}, priority=20)
rule(r"^cancel\s+(?:the\s+)?sleep\s+timer$", "cancel_sleep_timer", priority=20)
rule(r"^(?:what(?:'s| is)(?:\s+(?:this|playing|the song|that song))?(?:\s+right now)?|what song is (?:this|playing)|"
     r"now playing|what am i (?:listening to|watching))$", "now_playing", priority=20)
rule(r"^(?:(?:show|open|find|get)\s+)?lyrics(?:\s+(?:for|of)\s+(?P<query>.+))?$", "lyrics", priority=20)
rule(r"^play\s+(?:my\s+)?(?:usual|favou?rites?)(?:\s+(?:music|songs?|playlist))?$", "play_usual", priority=20)
rule(r"^add\s+(?P<query>.+?)\s+to\s+(?:the\s+|my\s+)?queue$", "queue_add", priority=20)
rule(r"^queue\s+(?P<query>.+)$", "queue_add", priority=25)
rule(r"^(?:play\s+)?(?:the\s+)?next\s+(?:song\s+|track\s+)?(?:in|from)\s+(?:the\s+|my\s+)?queue$|^play\s+(?:the\s+|my\s+)?queue$",
     "queue_next", priority=20)
rule(r"^(?:show|what(?:'s| is) in)\s+(?:the\s+|my\s+)?queue$", "queue_show", priority=20)
rule(r"^clear\s+(?:the\s+|my\s+)?queue$", "queue_clear", priority=20)
rule(r"^play\s+(?P<query>.+?)\s+on\s+spotify$", "music_search", static={"service": "spotify"}, priority=25)
rule(r"^play\s+(?P<query>.+?)\s+on\s+(?:youtube music|yt music)$", "music_search", static={"service": "ytmusic"}, priority=25)
# generic controls
rule(r"^(?:pause|stop)(?:\s+(?:the|this|that))?(?:\s+(?:video|song|music|playback|youtube|it))?$", "media_control", static={"action": "pause"}, priority=40)
rule(r"^(?:resume|continue|unpause)(?:\s+(?:the|this|that))?(?:\s+(?:video|song|music|playback|it))?$|"
     r"^play(?:\s+(?:the|this|that))?(?:\s+(?:video|song|music|it))?$", "media_control", static={"action": "play"}, priority=40)
rule(r"^(?:next|skip(?:\s+to(?:\s+the)?\s+next)?)(?:\s+(?:the|this|that))?(?:\s+(?:video|song|track|one))?$", "media_control", static={"action": "next"}, priority=40)
rule(r"^(?:previous|prev|play\s+(?:the\s+)?previous)(?:\s+(?:video|song|track|one))?$", "media_control", static={"action": "previous"}, priority=40)
rule(r"^(?:make\s+)?(?:it\s+|the video\s+)?full\s?screen$|^exit\s+full\s?screen$", "media_control", static={"action": "fullscreen"}, priority=40)
rule(r"^(?:play|watch)\s+(?:the\s+)?(?:first|top|1st)(?:\s+(?:result|one))?$", "youtube_play", priority=40)
rule(r"^(?:play|watch)\s+(?P<query>.+?)\s+on\s+youtube(?:\.com)?$", "youtube_play", priority=45)
rule(r"^(?:youtube\s+play|play|watch)\s+(?P<query>.+)$", "youtube_play", priority=90)
