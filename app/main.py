
from __future__ import annotations

import json
import time
import uuid
import webbrowser
import queue
import threading
from collections import deque
from pathlib import Path
from typing import Dict

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import actions, router, secrets_store, youtube
from .app_finder import AppFinder
from .browser import (
    open_in_browser,
    press_media_key,
    set_system_mute,
    set_system_volume,
)
from .config import get_settings
from .examples import ExampleStore
from .llm import LLMUnavailable, OllamaClient
from .logging_setup import setup_logging
from .profile_store import ProfileLocked, ProfileStore

log = setup_logging()
settings = get_settings()
app = FastAPI(title="Cozy", version="0.2.0")

finder = AppFinder()
examples = ExampleStore(settings.examples_path)
llm = OllamaClient(
    settings.ollama_url,
    settings.ollama_model,
    settings.ollama_timeout,
)

_store = None
_history: deque = deque(maxlen=8)
_pending: Dict[str, actions.Action] = {}
_last_yt = {"q": None}
launchers = {"open_url": None, "open_app": None}

# Actions are consumed by the Chrome extension running in the user's normal profile.
_extension_lock = threading.Lock()
_extension_last_seen = 0.0
_extension_pending: dict[str, dict] = {}
_extension_action_queue: "queue.Queue[str]" = queue.Queue()
_extension_waiters: dict[str, threading.Event] = {}
_extension_results: dict[str, dict] = {}
_extension_state_path = Path(settings.db_path).with_name("extension_queue.json")
EXTENSION_TIMEOUT = 75.0
EXTENSION_ACTION_LEASE = 30.0


def _load_extension_actions() -> None:
    """Restore unacknowledged browser actions after an ordinary backend restart."""
    try:
        records = json.loads(_extension_state_path.read_text(encoding="utf-8"))
        if not isinstance(records, dict):
            return
        for action_id, item in records.items():
            if isinstance(item, dict) and isinstance(item.get("action"), dict):
                # A claimed action is eligible again after process restart.
                item["claimed_at"] = 0.0
                item["attempts"] = min(int(item.get("attempts", 0)), 2)
                _extension_pending[action_id] = item
                _extension_action_queue.put(action_id)
    except FileNotFoundError:
        pass
    except (OSError, ValueError, TypeError):
        log.warning("Could not restore the extension action queue", exc_info=True)


def _save_extension_actions_locked() -> None:
    try:
        _extension_state_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = _extension_state_path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(_extension_pending), encoding="utf-8")
        temp_path.replace(_extension_state_path)
    except OSError:
        log.warning("Could not persist the extension action queue", exc_info=True)


_load_extension_actions()


def _extension_available() -> bool:
    with _extension_lock:
        return time.monotonic() - _extension_last_seen < EXTENSION_TIMEOUT


def _send_to_extension(action: dict, wait_for_result: bool = False):
    """Queue a browser action; optionally wait briefly for an execution ack.

    Returns None when disconnected, otherwise a result mapping when waiting or
    an accepted marker for fire-and-forget callers.
    """
    if not _extension_available():
        return None

    action_id = uuid.uuid4().hex
    completed = threading.Event() if wait_for_result else None
    with _extension_lock:
        _extension_pending[action_id] = {
            "action": {**action, "id": action_id},
            "claimed_at": 0.0,
            "attempts": 0,
        }
        if completed:
            _extension_waiters[action_id] = completed
        _extension_action_queue.put(action_id)
        _save_extension_actions_locked()
    if not completed:
        return {"accepted": True}
    if not completed.wait(40.0):
        with _extension_lock:
            _extension_waiters.pop(action_id, None)
            _extension_results.pop(action_id, None)
            _extension_pending.pop(action_id, None)
            _save_extension_actions_locked()
        return {"success": False, "timeout": True}
    with _extension_lock:
        result = _extension_results.pop(action_id, {"success": False})
        _extension_waiters.pop(action_id, None)
    return result


def get_store() -> ProfileStore:
    global _store

    if _store is None:
        _store = ProfileStore(
            settings.db_path,
            secrets_store.get_or_create_master_key(),
        )

    return _store


def _load_shortcuts() -> Dict[str, str]:
    try:
        data = json.loads(
            Path(settings.shortcuts_path).read_text(encoding="utf-8")
        )
        return {
            k.lower().strip(): v
            for k, v in data.items()
            if isinstance(v, str)
        }
    except (OSError, ValueError, AttributeError):
        return {}


def _web(url: str, browser_name: str = "") -> bool:
    """Open or reuse a website tab through the Cozy Chrome extension when connected."""
    if launchers["open_url"]:
        launchers["open_url"](url)
        return True

    target_browser = (browser_name or "chrome").lower()

    if target_browser == "chrome":
        dispatched = _send_to_extension({"type": "open_url", "url": url}, wait_for_result=True)
        if dispatched is not None:
            return bool(dispatched.get("success"))

    # Fallback when the extension is not installed or connected.
    try:
        return bool(open_in_browser(target_browser, url))
    except Exception:
        log.warning("Could not open URL in %s", target_browser, exc_info=True)

        if browser_name:
            return False

        try:
            return bool(webbrowser.open_new_tab(url))
        except Exception:
            log.warning("Default browser fallback failed", exc_info=True)
            return False


def _open_named(name: str) -> dict:
    """Open a website shortcut, else an installed app, else search Google."""
    key = name.lower().strip()
    url = _load_shortcuts().get(key)

    if url:
        if _web(url):
            return {
                "status": "done",
                "action": "open_website",
                "message": f"Opening {actions.WEBSITE_NAMES.get(key, key.title())}.",
            }

        return {
            "status": "error",
            "action": "open_website",
            "message": "I couldn't open that website.",
        }

    entry = finder.find(name)

    if entry:
        try:
            (launchers["open_app"] or finder.launch)(entry)
            return {
                "status": "done",
                "action": "open_app",
                "message": f"Opening {entry.name}.",
            }
        except Exception:
            log.warning("Could not open app %s", name, exc_info=True)
            return {
                "status": "error",
                "action": "open_app",
                "message": f"I couldn't open {entry.name}.",
            }

    if _web(actions._google_search(name).target):
        return {
            "status": "done",
            "action": "open_website",
            "message": f"I couldn't find an app called '{name}', so I searched Google for it.",
        }

    return {
        "status": "error",
        "action": "open_website",
        "message": f"I couldn't find or open '{name}'.",
    }


def _play_youtube(query: str, browser_name: str = "") -> dict:
    """Reuse the YouTube tab and attempt playback through the Cozy Chrome extension."""
    q = query or _last_yt["q"]

    if not q:
        return {
            "status": "done",
            "action": "unsupported",
            "message": "Tell me what to play first, for example: play Kalyani song.",
        }

    _last_yt["q"] = q
    target_browser = (browser_name or "chrome").lower()
    search_url = actions._youtube_search(q).target

    dispatched = None
    if target_browser == "chrome":
        dispatched = _send_to_extension(
            {"type": "play_youtube", "url": search_url, "query": q},
            wait_for_result=True,
        )
    if dispatched and dispatched.get("success"):
        return {
            "status": "done",
            "action": "youtube_play",
            "message": f"Playing {q} on YouTube.",
        }

    if dispatched is None and _web(search_url, browser_name):
        return {
            "status": "done",
            "action": "open_website",
            "message": (
                "Opened YouTube search results, but automatic playback requires "
                "the Cozy extension to be connected."
            ),
        }

    if dispatched is not None:
        detail = dispatched.get("detail", "").strip()
        message = "I couldn't start playback in the Chrome tab."
        if detail:
            message = f"I couldn't start playback in the Chrome tab: {detail}"
        return {"status": "error", "action": "youtube_play", "message": message}

    return {
        "status": "error",
        "action": "open_website",
        "message": f"I couldn't open YouTube in {target_browser}.",
    }


def _media(action: str, value: int | None = None) -> dict:
    """Control Windows master volume or control media in the browser."""

    # Exact percentage: this changes the Windows master output volume.
    if action == "set_volume":
        if value is not None and set_system_volume(value):
            return {
                "status": "done",
                "action": "system_volume",
                "message": f"Setting your laptop's volume to {value} percent.",
            }

        return {
            "status": "error",
            "action": "system_volume",
            "message": (
                "I couldn't set the Windows system volume. "
                "Check that pycaw and comtypes are installed and try again."
            ),
        }

    video_controls = {
        "video_volume_up": ("volume_up", "Increasing the video's volume."),
        "video_volume_down": ("volume_down", "Decreasing the video's volume."),
        "video_mute": ("mute", "Muting the video."),
        "video_unmute": ("unmute", "Unmuting the video."),
    }
    if action in video_controls:
        browser_action, message = video_controls[action]
        dispatched = _send_to_extension(
            {"type": "media", "action": browser_action}, wait_for_result=True
        )
        if dispatched and dispatched.get("success"):
            return {"status": "done", "action": "media", "message": message}
        if dispatched is None:
            return {"status": "error", "action": "media", "message": "I couldn't connect to the Chrome extension to control the video."}
        detail = dispatched.get("detail", "").strip()
        if "no video" in detail.lower() or "couldn't find a youtube tab" in detail.lower():
            return {"status": "error", "action": "media", "message": "I couldn't find a video to control. Open a YouTube video first."}
        return {
            "status": "error", "action": "media",
            "message": f"I couldn't control the YouTube video{': ' + detail if detail else '.'}",
        }

    # Relative volume and mute actions affect Windows master output.
    if action in ("volume_up", "volume_down", "mute", "unmute"):
        succeeded = (
            set_system_mute(action == "mute")
            if action in ("mute", "unmute")
            else press_media_key(action)
        )
        if succeeded:
            return {
                "status": "done",
                "action": "system_volume",
                "message": {
                    "volume_up": "Increasing your laptop's volume.",
                    "volume_down": "Decreasing your laptop's volume.",
                    "mute": "Muting your laptop.",
                    "unmute": "Unmuting your laptop.",
                }[action],
            }

        return {
            "status": "error",
            "action": "system_volume",
            "message": "I couldn't control the Windows system volume.",
        }

    # Browser playback belongs to the normal-profile extension, never the
    # separate Playwright profile. Windows volume remains on the system path.
    dispatched = _send_to_extension({"type": "media", "action": action}, wait_for_result=True)
    if dispatched and dispatched.get("success"):
        messages = {
            "pause": "Paused.", "play": "Playing.", "toggle": "Toggling playback.",
            "next": "Playing the next video.", "previous": "Playing the previous video.",
            "mute": "Muted.", "unmute": "Unmuted.", "fullscreen": "Toggling full screen.",
            "forward": "Skipping forward.", "rewind": "Rewinding.", "skip_ad": "Trying to skip the ad.",
        }
        return {"status": "done", "action": "media", "message": messages.get(action, "Controlling playback.")}
    if dispatched is None:
        return {"status": "error", "action": "media", "message": "I couldn't connect to the Chrome extension to control playback."}
    detail = dispatched.get("detail", "").strip()
    if "no video" in detail.lower() or "couldn't find a youtube tab" in detail.lower():
        return {"status": "error", "action": "media", "message": "I couldn't find a video to control. Open a YouTube video first."}
    message = f"I couldn't complete the playback action: {detail}" if detail else "I couldn't complete the playback action."
    return {"status": "error", "action": "media", "message": message}


class CommandIn(BaseModel):
    text: str
    dry_run: bool = False


class FactIn(BaseModel):
    value: str


class TeachIn(BaseModel):
    say: str
    tool: str
    args: dict = {}


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "wake_word": settings.wake_word,
        "model": settings.ollama_model,
    }


@app.get("/extension/next")
def extension_next():
    """Chrome extension heartbeat and next queued action; localhost only."""
    global _extension_last_seen

    now = time.monotonic()
    with _extension_lock:
        _extension_last_seen = now
        # Reclaim a command whose worker disappeared before acknowledging it.
        for action_id, item in list(_extension_pending.items()):
            if item["claimed_at"] and now - item["claimed_at"] > EXTENSION_ACTION_LEASE:
                if item["attempts"] >= 3:
                    del _extension_pending[action_id]
                else:
                    item["claimed_at"] = 0.0
                    _extension_action_queue.put(action_id)
        action = None
        try:
            action_id = _extension_action_queue.get_nowait()
            item = _extension_pending.get(action_id)
            if item:
                item["claimed_at"] = now
                item["attempts"] += 1
                action = item["action"]
        except queue.Empty:
            pass
        _save_extension_actions_locked()

    return {"action": action}


@app.post("/extension/ack/{action_id}")
def extension_ack(action_id: str, succeeded: bool = True, detail: str = ""):
    """Acknowledge extension execution; retry failed actions a bounded number of times."""
    with _extension_lock:
        item = _extension_pending.get(action_id)
        if item is None:
            raise HTTPException(404, "unknown extension action")
        del _extension_pending[action_id]
        _save_extension_actions_locked()
        waiter = _extension_waiters.get(action_id)
        if waiter:
            _extension_results[action_id] = {"success": succeeded, "detail": detail[:300]}
            waiter.set()
    return {"status": "acknowledged" if succeeded else "failed"}


@app.post("/command")
def command(body: CommandIn):
    if not body.text.strip():
        raise HTTPException(400, "text is empty")

    action = actions.parse_command(body.text, settings.wake_word)

    if action.needs_approval:
        pid = uuid.uuid4().hex
        _pending[pid] = action
        return {
            "status": "approval_required",
            "approval_id": pid,
            "action": action.kind,
            "text": action.text,
        }

    if body.dry_run:
        return {
            "status": "dry_run",
            "action": action.kind,
            "target": action.target,
        }

    return _run(action)


def _run(action: actions.Action) -> dict:
    """Execute a parsed action or pass free-form text to the LLM router."""
    if action.query:
        _last_yt["q"] = action.query

    if action.kind == "youtube_play":
        return _play_youtube(action.query, action.browser)

    if action.kind == "media":
        return _media(action.target, action.value)

    if action.kind == "open_website":
        if _web(action.target, action.browser):
            host = action.target.split("/")[2].removeprefix("www.") if "://" in action.target else action.target
            return {
                "status": "done",
                "action": "open_website",
                "message": action.label.rstrip(".!?") + "." if action.label else f"Opening {host}.",
            }

        return {
            "status": "error",
            "action": "open_website",
            "message": f"I couldn't open {action.target}.",
        }

    if action.kind == "open_named":
        return _open_named(action.target)

    if action.kind == "remember":
        get_store().set(f"note:{int(time.time() * 1000)}", action.target)
        return {
            "status": "done",
            "action": "remember",
            "message": "Got it, I'll remember that.",
        }

    if action.kind == "chat":
        try:
            facts = get_store().all()
        except ProfileLocked:
            facts = {}
        except Exception:
            log.warning("Profile unavailable", exc_info=True)
            facts = {}

        try:
            decision = router.decide(
                llm,
                list(_history),
                facts,
                action.text,
                examples.similar(action.text),
            )
        except LLMUnavailable as e:
            return {
                "status": "llm_unavailable",
                "action": "chat",
                "message": str(e),
            }

        if decision.kind == "reply":
            _history.append({"role": "user", "content": action.text})
            _history.append({"role": "assistant", "content": decision.label})
            return {
                "status": "done",
                "action": "chat",
                "message": decision.label,
            }

        return _run(decision)

    result = actions.execute(action)
    log.info("Executed %s", action.kind)

    return {
        "status": "done",
        "action": action.kind,
        "message": result,
    }


@app.post("/approve/{approval_id}")
def approve(approval_id: str, approved: bool = True):
    action = _pending.pop(approval_id, None)

    if action is None:
        raise HTTPException(404, "unknown approval id")

    if not approved:
        return {"status": "rejected"}

    # Approved risky actions are recorded, not executed, until the Android phase.
    return {
        "status": "approved",
        "action": action.kind,
        "note": "execution arrives in a later phase",
    }


@app.post("/teach")
def teach(body: TeachIn):
    """Teach Cozy: when you say <say>, use <tool> with <args>."""
    if body.tool not in router.TOOLS:
        raise HTTPException(400, f"tool must be one of {sorted(router.TOOLS)}")

    examples.add(body.say, body.tool, body.args)
    return {
        "status": "learned",
        "say": body.say,
        "tool": body.tool,
    }


@app.get("/teach")
def taught():
    return {"examples": examples.all()}


@app.get("/apps")
def list_apps():
    """Debug helper: every app/folder name Cozy can open on this laptop."""
    return {
        "count": len(finder.index),
        "names": sorted(finder.index),
    }


@app.get("/profile")
def profile_keys():
    return {"keys": get_store().keys()}


@app.get("/profile/{key}")
def profile_get(key: str):
    value = get_store().get(key)

    if value is None:
        raise HTTPException(404, "no such fact")

    return {"key": key, "value": value}


@app.put("/profile/{key}")
def profile_set(key: str, body: FactIn):
    get_store().set(key, body.value)
    return {"status": "saved", "key": key}


@app.delete("/profile/{key}")
def profile_delete(key: str):
    if not get_store().delete(key):
        raise HTTPException(404, "no such fact")

    return {"status": "deleted", "key": key}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=settings.port,
    )
