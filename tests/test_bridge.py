import threading
import time

from cozy.bridge import ExtensionBridge, ExtensionUnavailable
import pytest


class Ticker:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def worker(bridge, results, ok=True, count=1, detail=""):
    def run():
        for _ in range(count):
            action = bridge.next(wait=0.3)
            if action:
                results.append(action)
                bridge.ack(action["id"], ok, detail, {"echo": action.get("type")})
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def test_submit_requires_a_connected_extension():
    with pytest.raises(ExtensionUnavailable):
        ExtensionBridge().submit({"type": "open_url"})


def test_long_poll_answers_immediately_when_an_action_arrives():
    b = ExtensionBridge()
    got = []
    started = time.monotonic()
    poller = threading.Thread(target=lambda: got.append(b.next(wait=10)), daemon=True)
    poller.start()
    time.sleep(0.1)
    assert b.available()                                              # connected while the poll is open
    res = {}
    submitter = threading.Thread(target=lambda: res.update(b.submit({"type": "ping"}, timeout=5)), daemon=True)
    submitter.start()
    poller.join(3)
    assert got[0]["type"] == "ping" and time.monotonic() - started < 2   # no 5-second polling delay
    b.ack(got[0]["id"], True, data={"x": 1})
    submitter.join(3)
    assert res == {"ok": True, "detail": "", "data": {"x": 1}}


def test_empty_poll_returns_none_after_the_wait():
    b = ExtensionBridge()
    started = time.monotonic()
    assert b.next(wait=0.2) is None and time.monotonic() - started >= 0.19


def test_media_actions_are_delivered_at_most_once_even_on_failure():
    b = ExtensionBridge()
    b.next(wait=0)
    seen = []
    t = worker(b, seen, ok=False, detail="boom", count=3)
    result = b.submit({"type": "media", "action": "forward"}, retry=False, timeout=3)
    t.join(4)
    assert result["ok"] is False and result["detail"] == "boom" and len(seen) == 1


def test_idempotent_actions_are_retried_up_to_three_attempts_then_report_the_last_error():
    b = ExtensionBridge()
    b.next(wait=0)
    seen = []
    t = worker(b, seen, ok=False, detail="tab locked", count=5)
    result = b.submit({"type": "open_url"}, retry=True, timeout=5)
    t.join(6)
    assert result["ok"] is False and result["detail"] == "tab locked" and len(seen) == 3


def test_lease_expiry_redelivers_only_idempotent_actions():
    clock = Ticker()
    b = ExtensionBridge(clock=clock)
    b.next(wait=0)
    out = {}
    threading.Thread(target=lambda: out.update(b.submit({"type": "open_url"}, retry=True, timeout=3)), daemon=True).start()
    time.sleep(0.1)
    first = b.next(wait=0)
    assert first and b.next(wait=0) is None                           # claimed, not yet expired
    clock.t += b.LEASE + 1
    again = b.next(wait=0)
    assert again and again["id"] == first["id"]                       # worker died: redelivered
    b.ack(again["id"], True)

    out2 = {}
    threading.Thread(target=lambda: out2.update(b.submit({"type": "media"}, retry=False, timeout=0.5)), daemon=True).start()
    time.sleep(0.1)
    media = b.next(wait=0)
    clock.t += b.LEASE + 1
    assert b.next(wait=0) is None                                     # never replayed
    time.sleep(0.6)
    assert out2["timeout"] is True and media


def test_dead_and_duplicate_queue_entries_are_skipped():
    b = ExtensionBridge()
    b.next(wait=0)
    t = threading.Thread(target=lambda: b.submit({"type": "a"}, timeout=2), daemon=True)
    t.start()
    time.sleep(0.1)
    with b._cv:
        live = next(iter(b._items))
        b._queue.appendleft("gone")
        b._queue.append(live)                                          # duplicate of the same action
    first = b.next(wait=0)
    assert first["id"] == live and b.next(wait=0) is None
    assert b.ack("nonexistent", True) == "unknown"
    b.ack(live, True)
    t.join(3)


def test_availability_expires_when_the_extension_goes_quiet():
    clock = Ticker()
    b = ExtensionBridge(clock=clock)
    assert not b.available()
    b.next(wait=0)
    assert b.available()
    clock.t += b.ALIVE_FOR + 1
    assert not b.available()
