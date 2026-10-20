"""Control ONE Chrome window so every step happens in the same tab.

Cozy starts its own Chrome (separate profile, remote-debugging on localhost) and
drives it with Playwright: navigate, type in the YouTube search bar, click the
top result, pause/play the video. One dedicated thread owns the browser because
Playwright objects cannot be shared between threads.
"""
from __future__ import annotations

import os
import queue
import shutil
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import Callable, Dict, Optional
from urllib.parse import quote_plus


class BrowserUnavailable(Exception):
    pass


# Finding installed browsers.
_EXE = {
    "chrome": "chrome.exe",
    "edge": "msedge.exe",
    "firefox": "firefox.exe",
    "brave": "brave.exe",
}

_DIRS = {
    "chrome": [
        ("ProgramFiles", r"Google\Chrome\Application\chrome.exe"),
        ("ProgramFiles(x86)", r"Google\Chrome\Application\chrome.exe"),
        ("LOCALAPPDATA", r"Google\Chrome\Application\chrome.exe"),
    ],
    "edge": [
        ("ProgramFiles(x86)", r"Microsoft\Edge\Application\msedge.exe"),
        ("ProgramFiles", r"Microsoft\Edge\Application\msedge.exe"),
    ],
    "firefox": [
        ("ProgramFiles", r"Mozilla Firefox\firefox.exe"),
        ("ProgramFiles(x86)", r"Mozilla Firefox\firefox.exe"),
    ],
    "brave": [
        ("ProgramFiles", r"BraveSoftware\Brave-Browser\Application\brave.exe"),
        ("LOCALAPPDATA", r"BraveSoftware\Brave-Browser\Application\brave.exe"),
    ],
}


def find_browser(name: str) -> Optional[str]:
    exe = _EXE.get(name)
    if not exe:
        return None

    found = shutil.which(exe)
    if found:
        return found

    for env, rel in _DIRS.get(name, []):
        base = os.environ.get(env)
        if base and (Path(base) / rel).exists():
            return str(Path(base) / rel)

    return None


def open_in_browser(name: str, url: str) -> bool:
    """Open a URL in a specific browser (new tab). Used for 'in Edge' etc."""
    exe = find_browser(name)
    if not exe:
        return False

    subprocess.Popen([exe, url])
    return True


# Windows media keys.
MEDIA_KEYS = {
    "toggle": 0xB3,
    "pause": 0xB3,
    "play": 0xB3,
    "next": 0xB0,
    "previous": 0xB1,
    "mute": 0xAD,
    "unmute": 0xAD,
    "volume_up": 0xAF,
    "volume_down": 0xAE,
}


def set_system_volume(percent: int) -> bool:
    """Set Windows master output volume to an exact percentage using pycaw."""
    if (
        not sys.platform.startswith("win")
        or isinstance(percent, bool)
        or not isinstance(percent, int)
        or not 0 <= percent <= 100
    ):
        return False

    try:
        from pycaw.pycaw import AudioUtilities

        speakers = AudioUtilities.GetSpeakers()
        endpoint = speakers.EndpointVolume
        endpoint.SetMasterVolumeLevelScalar(percent / 100.0, None)
        return True
    except Exception:
        return False


def set_system_mute(muted: bool) -> bool:
    """Set mute state on the default Windows output endpoint."""
    if not sys.platform.startswith("win") or not isinstance(muted, bool):
        return False
    try:
        from pycaw.pycaw import AudioUtilities
        AudioUtilities.GetSpeakers().EndpointVolume.SetMute(muted, None)
        return True
    except Exception:
        return False


def press_media_key(action: str) -> bool:
    """Fallback when no video is open in Cozy's tab: press the keyboard media key."""
    if not sys.platform.startswith("win") or action not in MEDIA_KEYS:
        return False

    import ctypes

    try:
        vk = MEDIA_KEYS[action]
        ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
        ctypes.windll.user32.keybd_event(vk, 0, 2, 0)
        return True
    except Exception:
        return False


# Page operations.
SEARCH_BOX = 'input[name="search_query"]'
FIRST_RESULT = "ytd-video-renderer a#video-title"
KEY_ACTIONS = {"next": "Shift+N", "previous": "Shift+P"}

MEDIA_MESSAGES = {
    "pause": "Paused",
    "play": "Playing",
    "toggle": "Done",
    "next": "Next video",
    "previous": "Previous video",
    "mute": "Muted",
    "unmute": "Unmuted",
    "volume_up": "Volume up",
    "volume_down": "Volume down",
    "fullscreen": "Toggled full screen",
    "forward": "Skipped forward 10 seconds",
    "rewind": "Went back 10 seconds",
    "skip_ad": "Skipped the ad",
}

MEDIA_JS = """(a) => {
  const v = document.querySelector('video');
  if (!v) return 'none';
  if (a === 'pause') { v.pause(); return 'ok'; }
  if (a === 'play') { v.play(); return 'ok'; }
  if (a === 'toggle') { v.paused ? v.play() : v.pause(); return 'ok'; }
  if (a === 'mute') { v.muted = true; return 'ok'; }
  if (a === 'unmute') { v.muted = false; return 'ok'; }
  if (a === 'volume_up') { v.muted = false; v.volume = Math.min(1, v.volume + 0.15); return 'ok'; }
  if (a === 'volume_down') { v.volume = Math.max(0, v.volume - 0.15); return 'ok'; }
  if (a === 'forward') { v.currentTime += 10; return 'ok'; }
  if (a === 'rewind') { v.currentTime = Math.max(0, v.currentTime - 10); return 'ok'; }
  if (a === 'skip_ad') {
    const b = document.querySelector('.ytp-skip-ad-button, .ytp-ad-skip-button, .ytp-ad-skip-button-modern');
    if (b) { b.click(); return 'ok'; }
    return 'noad';
  }
  return 'unsupported';
}"""


def _op_goto(get_page, state, url):
    get_page(state).goto(url, wait_until="domcontentloaded")


def _op_youtube_play(get_page, state, query):
    page = get_page(state)

    try:
        if "youtube.com" not in page.url or "/results" in page.url:
            page.goto("https://www.youtube.com", wait_until="domcontentloaded")

        page.wait_for_selector(SEARCH_BOX, timeout=8000)
        page.fill(SEARCH_BOX, query)
        page.keyboard.press("Enter")

    except Exception:
        page.goto(
            f"https://www.youtube.com/results?search_query={quote_plus(query)}",
            wait_until="domcontentloaded",
        )

    page.wait_for_selector(FIRST_RESULT, timeout=12000)
    page.click(FIRST_RESULT)
    return True


def _op_media(get_page, state, action):
    page = get_page(state)

    if not page.evaluate("() => !!document.querySelector('video')"):
        return None

    if action in KEY_ACTIONS:
        page.keyboard.press(KEY_ACTIONS[action])
        return MEDIA_MESSAGES[action]

    res = page.evaluate(MEDIA_JS, action)

    if res == "noad":
        return "There's no ad to skip"

    if res == "ok":
        return MEDIA_MESSAGES.get(action, "Done")

    return None


# Chrome via Playwright.
def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def _launch_chrome(port: int, profile: Path) -> None:
    exe = find_browser("chrome")
    if not exe:
        raise BrowserUnavailable("Google Chrome was not found on this laptop.")

    profile.mkdir(parents=True, exist_ok=True)
    subprocess.Popen([
        exe,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile.resolve()}",
        "--no-first-run",
        "--no-default-browser-check",
    ])

    for _ in range(60):
        if _port_open(port):
            return
        time.sleep(0.25)

    raise BrowserUnavailable("Chrome did not start in time.")


def default_get_page(port: int, profile: Path) -> Callable[[Dict], object]:
    def get(state):
        page = state.get("page")
        if page is not None and not page.is_closed():
            return page

        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise BrowserUnavailable("Browser control needs: pip install playwright") from e

        br = state.get("browser")

        if br is None or not br.is_connected():
            if not _port_open(port):
                _launch_chrome(port, profile)

            if state.get("pw") is None:
                state["pw"] = sync_playwright().start()

            try:
                br = state["pw"].chromium.connect_over_cdp(
                    f"http://127.0.0.1:{port}"
                )
            except Exception as e:
                raise BrowserUnavailable(f"Could not connect to Chrome: {e}") from e

            state["browser"] = br

        ctx = br.contexts[0] if br.contexts else br.new_context()
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        state["page"] = page
        return page

    return get


class BrowserController:
    def __init__(self, get_page=None, port: int = 9222, profile_dir=Path("data/chrome-profile")):
        self._get_page = get_page or default_get_page(port, Path(profile_dir))
        self._tasks: "queue.Queue" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def _loop(self) -> None:
        state: Dict = {}

        while True:
            fn, args, fut = self._tasks.get()

            try:
                fut.set_result(fn(self._get_page, state, *args))
            except BaseException as e:
                fut.set_exception(e)

    def _run(self, fn, *args, timeout: float = 60):
        with self._lock:
            if self._thread is None:
                self._thread = threading.Thread(target=self._loop, daemon=True)
                self._thread.start()

        fut: Future = Future()
        self._tasks.put((fn, args, fut))

        try:
            return fut.result(timeout=timeout)
        except FutureTimeout as e:
            raise BrowserUnavailable("The browser took too long to respond.") from e

    def goto(self, url: str) -> None:
        self._run(_op_goto, url)

    def youtube_play(self, query: str) -> bool:
        return self._run(_op_youtube_play, query, timeout=60)

    def media(self, action: str) -> Optional[str]:
        return self._run(_op_media, action)
