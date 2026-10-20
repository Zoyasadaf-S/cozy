"""Shared state handed to every tool."""
from __future__ import annotations

import time
from collections import deque
from datetime import datetime
from typing import Any, Callable, Deque, Dict, Optional

from .bridge import ExtensionBridge, ExtensionUnavailable
from .config import Settings
from .events import EventBus
from .netio import Http
from .store import Store
from .winops import WinOps


class Ctx:
    def __init__(self, settings: Settings, store: Store, bridge: ExtensionBridge, win: WinOps, http: Http,
                 llm=None, events: Optional[EventBus] = None, clock: Callable[[], float] = time.time):
        self.settings, self.store, self.bridge, self.win, self.http, self.llm = settings, store, bridge, win, http, llm
        self.events = events or EventBus()
        self.clock = clock
        self.scheduler = None                       # set by the app factory
        self.dispatch: Callable[..., dict] = lambda *a, **k: {"status": "error", "message": "not ready"}
        self.undo_fn: Callable[[], Any] = lambda: None
        self.last_call: Optional[dict] = None
        self.last_message = ""
        self.last_play_query: Optional[str] = None
        self.undo_stack: Deque[dict] = deque(maxlen=10)
        self.pending: Dict[str, dict] = {}
        self.dev_servers: Dict[str, Any] = {}
        self.started = clock()

    # ---- settings (stored, so they survive restarts) ----
    def setting(self, name: str, default: Any = None) -> Any:
        return self.store.kv_get("setting:" + name, default)

    def set_setting(self, name: str, value: Any) -> None:
        self.store.kv_set("setting:" + name, value)

    @property
    def offline(self) -> bool:
        return bool(self.setting("offline", False))

    def now(self) -> datetime:
        return datetime.fromtimestamp(self.clock())

    # ---- profiles ----
    def profile_name(self) -> str:
        return self.setting("profile", "default")

    def profile(self) -> dict:
        return self.store.kv_get("profile:" + self.profile_name(), {}) or {}

    def save_profile(self, data: dict) -> None:
        self.store.kv_set("profile:" + self.profile_name(), data)

    # ---- helpers ----
    def browser(self, action: dict, *, retry: bool = False, timeout: float = 45.0) -> dict:
        """Run an action in Chrome through the extension (raises ExtensionUnavailable)."""
        return self.bridge.submit(action, retry=retry, timeout=timeout)

    def notify(self, title: str, message: str, speak: bool = False) -> None:
        self.events.publish("notify", {"title": title, "message": message, "speak": speak})
        try:
            self.win.notify(title, message)
            if self.setting("speak_on_laptop", False):
                self.win.speak(message)
        except Exception:  # notifications are best-effort
            pass

    def run_command(self, text: str) -> dict:
        return self.dispatch(text, source="scheduled")


__all__ = ["Ctx", "ExtensionUnavailable"]
