"""One sleeping thread for every reminder, timer, alarm and scheduled command.

It waits exactly until the next due item (and not at all when there is none), so an idle
Cozy costs no CPU wake-ups. The wait is capped so a laptop resuming from sleep catches up.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Callable, Optional

from .store import Store
from .timeparse import next_due

log = logging.getLogger("cozy.scheduler")


class Scheduler:
    def __init__(self, store: Store, fire: Callable[[dict], None], clock: Callable[[], float] = time.time,
                 max_sleep: float = 120.0):
        self.store, self.fire, self.clock, self.max_sleep = store, fire, clock, max_sleep
        self._cv = threading.Condition()
        self._thread: Optional[threading.Thread] = None
        self._stop = False

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop = False
        self._thread = threading.Thread(target=self._loop, name="cozy-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        with self._cv:
            self._stop = True
            self._cv.notify_all()
        if self._thread:
            self._thread.join(timeout=2)

    def add(self, due_ts: float, text: str, *, kind: str = "reminder", command: Optional[str] = None,
            repeat: Optional[dict] = None) -> int:
        rid = self.store.add_reminder(due_ts, kind, text, command, repeat)
        with self._cv:
            self._cv.notify_all()
        return rid

    def cancel(self, rid: Optional[int] = None, kind: Optional[str] = None) -> int:
        n = self.store.cancel_reminders(rid, kind)
        with self._cv:
            self._cv.notify_all()
        return n

    def run_due(self) -> int:
        """Fire everything that is due now. Public so tests (and resume-from-sleep) can call it."""
        now = self.clock()
        items = self.store.due_reminders(now)
        for item in items:
            item["late_by"] = now - item["due"]
            if item["repeat"]:
                nxt = next_due(item["repeat"], datetime.fromtimestamp(item["due"]), datetime.fromtimestamp(now))
                self.store.reschedule(item["id"], nxt.timestamp())
            else:
                self.store.finish_reminder(item["id"])
            try:
                self.fire(item)
            except Exception:  # one bad reminder must not kill the scheduler
                log.exception("scheduled item %s failed", item.get("id"))
        return len(items)

    def _loop(self) -> None:
        while True:
            with self._cv:
                if self._stop:
                    return
                nxt = self.store.next_due()
                now = self.clock()
                if nxt is None:
                    self._cv.wait()                      # nothing scheduled: sleep until add()
                    continue
                if nxt > now:
                    self._cv.wait(min(self.max_sleep, nxt - now))
                    continue
            self.run_due()
