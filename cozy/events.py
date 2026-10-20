"""Tiny pub/sub used by the dashboard live feed. No subscribers => no work."""
from __future__ import annotations

import queue
import threading
from typing import Any, List


class EventBus:
    def __init__(self):
        self._lock = threading.Lock()
        self._subs: List["queue.Queue"] = []

    def subscribe(self) -> "queue.Queue":
        q: "queue.Queue" = queue.Queue(maxsize=50)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def publish(self, kind: str, payload: Any = None) -> None:
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait({"kind": kind, "payload": payload})
            except queue.Full:
                pass

    @property
    def subscribers(self) -> int:
        with self._lock:
            return len(self._subs)
