"""Long-poll bridge to the Chrome extension.

The extension keeps ONE request open (GET /api/ext/next). The server answers the moment an
action exists and otherwise holds it, so there is no 5-second polling loop on either side.

Delivery rules (they matter for media commands):
  * retry=True  actions are idempotent (open a URL, play a search) -> redelivered if the
                worker vanishes or reports failure, at most 3 attempts.
  * retry=False actions (forward, volume up, close tab...) are delivered AT MOST ONCE, so a
                failure can never double-apply them.
"""
from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from typing import Callable, Dict, Optional


class ExtensionUnavailable(Exception):
    pass


class ExtensionBridge:
    LEASE = 20.0            # seconds before an unacknowledged retry=True action is redelivered
    MAX_ATTEMPTS = 3
    ALIVE_FOR = 45.0        # connected = polling now, or polled within this many seconds

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._cv = threading.Condition()
        self._queue: "deque[str]" = deque()
        self._items: Dict[str, dict] = {}
        self._polling = 0
        self.last_seen = 0.0
        self.version: Optional[str] = None

    # ---- status ----
    def available(self) -> bool:
        with self._cv:
            return self._polling > 0 or (self.last_seen > 0 and self._clock() - self.last_seen < self.ALIVE_FOR)

    def pending(self) -> int:
        with self._cv:
            return sum(1 for i in self._items.values() if not i["done"].is_set())

    # ---- server side ----
    def submit(self, action: dict, *, retry: bool = False, wait: bool = True, timeout: float = 45.0) -> dict:
        if not self.available():
            raise ExtensionUnavailable("The Cozy Chrome extension is not connected.")
        aid = uuid.uuid4().hex
        item = {"action": {**action, "id": aid}, "retry": retry, "attempts": 0, "claimed_at": 0.0,
                "done": threading.Event(), "result": None}
        with self._cv:
            self._items[aid] = item
            self._queue.append(aid)
            self._cv.notify_all()
        if not wait:
            return {"ok": True, "accepted": True}
        if not item["done"].wait(timeout):
            with self._cv:
                self._items.pop(aid, None)
            return {"ok": False, "timeout": True, "detail": "The Chrome extension did not finish in time."}
        with self._cv:
            self._items.pop(aid, None)
        return item["result"]

    # ---- extension side ----
    def next(self, wait: float = 25.0, version: Optional[str] = None) -> Optional[dict]:
        end = self._clock() + max(0.0, min(wait, 30.0))
        with self._cv:
            self._polling += 1
            if version:
                self.version = version
            try:
                while True:
                    self._reclaim_locked()
                    action = self._claim_locked()
                    if action is not None:
                        return action
                    remaining = end - self._clock()
                    if remaining <= 0:
                        return None
                    self._cv.wait(min(remaining, 15.0))
            finally:
                self._polling -= 1
                self.last_seen = self._clock()

    def ack(self, aid: str, ok: bool, detail: str = "", data=None) -> str:
        with self._cv:
            item = self._items.get(aid)
            if item is None or item["done"].is_set():
                return "unknown"
            if not ok and item["retry"] and item["attempts"] < self.MAX_ATTEMPTS:
                item["claimed_at"] = 0.0
                self._queue.append(aid)
                self._cv.notify_all()
                return "retrying"
            item["result"] = {"ok": bool(ok), "detail": (detail or "")[:300], "data": data}
            item["done"].set()
            return "acknowledged" if ok else "failed"

    # ---- internals (lock held) ----
    def _claim_locked(self) -> Optional[dict]:
        while self._queue:
            aid = self._queue.popleft()
            item = self._items.get(aid)
            if item is None or item["done"].is_set() or item["claimed_at"]:
                continue                                  # dead or duplicate entry
            item["claimed_at"] = self._clock()
            item["attempts"] += 1
            return item["action"]
        return None

    def _reclaim_locked(self) -> None:
        now = self._clock()
        for aid, item in list(self._items.items()):
            if item["claimed_at"] and not item["done"].is_set() and now - item["claimed_at"] > self.LEASE:
                if not item["retry"]:
                    continue                                # at-most-once: caller will time out
                if item["attempts"] >= self.MAX_ATTEMPTS:
                    item["result"] = {"ok": False, "detail": "The extension stopped responding.", "data": None}
                    item["done"].set()
                else:
                    item["claimed_at"] = 0.0
                    self._queue.append(aid)
