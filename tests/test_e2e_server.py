"""Real sockets: starts the actual server and talks to it like the extension and the phone do."""
import http.client
import json
import socket
import threading
import time

import pytest
import uvicorn

from cozy.config import Settings
from cozy.server import build_ctx, create_app


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live(tmp_path, env):
    port = free_port()
    settings = Settings(port=port, data_dir=tmp_path / "d", plugins_dir=tmp_path / "none")
    ctx = build_ctx(settings, win=env.win)
    app = create_app(settings, ctx=ctx, start_scheduler=True, plugins=False)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.1).close()
            break
        except OSError:
            time.sleep(0.05)
    yield port, ctx
    server.should_exit = True
    thread.join(5)


def call(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(method, path, json.dumps(body) if body is not None else None, {"X-Cozy-Client": "e2e", "Content-Type": "application/json", **(headers or {})})
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp.status, (json.loads(data) if data and resp.getheader("content-type", "").startswith("application/json") else data)


def test_extension_long_poll_makes_commands_instant_over_real_http(live):
    port, ctx = live
    got = []

    def extension():
        status, body = call(port, "GET", "/api/ext/next?wait=10&v=2.0.0")
        got.append(body["action"])
        call(port, "POST", f"/api/ext/ack/{body['action']['id']}", {"ok": True, "detail": "", "data": None})
    t = threading.Thread(target=extension, daemon=True)
    t.start()
    time.sleep(0.3)
    started = time.monotonic()
    status, reply = call(port, "POST", "/api/command", {"text": "pause"})
    t.join(5)
    assert status == 200 and reply["status"] == "done" and got[0]["action"] == "pause"
    assert time.monotonic() - started < 2.0                         # the poll was already open: no polling delay
    assert ctx.bridge.available()


def test_real_server_rejects_rebinding_hosts_and_missing_headers(live):
    port, _ = live
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", "/api/status", headers={"X-Cozy-Client": "x", "Host": "evil.example:%d" % port})
    assert conn.getresponse().status == 403
    conn.close()
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", "/api/status")
    assert conn.getresponse().status == 403
    conn.close()
    assert call(port, "GET", "/api/status")[0] == 200


def test_scheduler_thread_fires_a_reminder_on_time_in_a_live_server(live):
    port, ctx = live
    sub = ctx.events.subscribe()
    ctx.scheduler.clock = time.time
    ctx.clock = time.time
    ctx.scheduler.add(time.time() + 0.4, "tea is ready", kind="reminder")
    event = sub.get(timeout=5)
    assert event["kind"] == "notify" and event["payload"]["message"] == "tea is ready"
    assert ctx.store.list_reminders() == []
