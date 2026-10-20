import threading
import time

import pytest
from fastapi.testclient import TestClient

from cozy.bridge import ExtensionBridge
from cozy.config import Settings
from cozy.netio import Http
from cozy.server import build_ctx, create_app
from cozy.winops import WinOps

H = {"X-Cozy-Client": "test"}


@pytest.fixture
def api(tmp_path, env):
    settings = Settings(data_dir=tmp_path / "data", plugins_dir=tmp_path / "none")
    settings.allowed_hosts.add("testserver")
    ctx = build_ctx(settings, win=env.win, http=Http(lambda: False, fetcher=lambda u, t: b"{}"), bridge=ExtensionBridge())
    app = create_app(settings, ctx=ctx, start_scheduler=False, plugins=False)
    client = TestClient(app)
    client.ctx, client.app_, client.settings, client.rec = ctx, app, settings, env.rec
    client.token = app.state.token
    return client


def phone(api, **kw):
    return TestClient(api.app_, client=("192.168.1.50", 5000), **kw)


# ----------------------------------------------------------------------------- pages
def test_health_pages_and_compression(api):
    assert api.get("/health").json()["status"] == "ok"
    page = api.get("/")
    assert page.status_code == 200 and "Cozy" in page.text and page.headers["content-encoding"] == "gzip"
    assert api.get("/manifest.webmanifest").headers["content-type"].startswith("application/manifest+json")
    assert "cozy-shell" in api.get("/sw.js").text
    assert api.get("/static/icon-192.png").headers["cache-control"].startswith("public")


# ----------------------------------------------------------------------------- protection
def test_api_requires_the_custom_header_and_a_known_host(api):
    assert api.get("/api/status").status_code == 403                                   # cross-site requests can't add headers
    assert api.get("/api/status", headers={**H, "host": "evil.example"}).status_code == 403   # DNS rebinding
    assert api.get("/api/status", headers=H).status_code == 200


def test_phone_needs_the_pairing_token_and_cannot_use_laptop_only_endpoints(api):
    p = phone(api)
    assert p.get("/api/status", headers=H).status_code == 401
    assert p.get("/api/status", headers={**H, "X-Cozy-Token": "wrong"}).status_code == 401
    ok = {**H, "X-Cozy-Token": api.token}
    assert p.get("/api/status", headers=ok).status_code == 200
    assert p.post("/api/command", json={"text": "what time is it"}, headers=ok).json()["status"] == "done"
    assert p.get("/api/ext/next?wait=0", headers=ok).status_code == 403
    assert p.post("/api/ext/ack/x", json={}, headers=ok).status_code == 403
    assert p.get("/api/pair", headers=ok).status_code == 403
    assert p.get(f"/api/events?once=1&timeout=0.1&t={api.token}").status_code == 200   # EventSource passes the token in the URL
    assert p.get("/api/events?once=1&timeout=0.1").status_code == 401


def test_phone_cannot_set_shell_commands_but_laptop_can(api, tmp_path):
    folder = tmp_path / "proj"
    folder.mkdir()
    body = {"value": {"path": str(folder), "test": "pytest -q"}}
    p, ok = phone(api), {**H, "X-Cozy-Token": api.token}
    assert p.put("/api/data/projects/demo", json=body, headers=ok).status_code == 403
    assert p.put("/api/data/projects/demo", json={"value": {"path": str(folder)}}, headers=ok).status_code == 200
    assert api.put("/api/data/projects/demo", json=body, headers=H).status_code == 200


def test_token_is_stable_across_restarts(api, tmp_path):
    from cozy.config import load_token
    assert load_token(tmp_path / "data") == api.token and len(api.token) >= 24


def test_pairing_info_depends_on_phone_mode(api):
    r = api.get("/api/pair", headers=H).json()
    assert r["lan_enabled"] is False and r["url"] is None and "COZY_LAN" in r["hint"] or "start_phone" in r["hint"]
    api.settings.lan = True
    r = api.get("/api/pair", headers=H).json()
    assert r["url"].endswith(f"/?t={api.token}")


# ----------------------------------------------------------------------------- commands
def test_command_validation_and_dry_run(api):
    assert api.post("/api/command", json={"text": "   "}, headers=H).status_code == 400
    assert api.post("/api/command", json={"text": "x" * 501}, headers=H).status_code == 400
    r = api.post("/api/command", json={"text": "set volume to 10", "dry_run": True}, headers=H).json()
    assert r["status"] == "dry_run" and r["tool"] == "volume_set" and api.rec.runs == []


def test_approval_round_trip_over_http(api):
    r = api.post("/api/command", json={"text": "shut down"}, headers=H).json()
    assert r["status"] == "approval_required"
    assert not any(run[0][:1] == ["shutdown"] for run in api.rec.runs)
    no = api.post(f"/api/approve/{r['approval_id']}", json={"approved": False}, headers=H).json()
    assert no["message"] == "Okay, cancelled."
    r = api.post("/api/command", json={"text": "restart"}, headers=H).json()
    yes = api.post(f"/api/approve/{r['approval_id']}", json={"approved": True}, headers=H).json()
    assert yes["status"] == "done" and api.rec.runs[-1][0][:2] == ["shutdown", "/r"]
    assert api.post("/api/approve/nope", json={"approved": True}, headers=H).json()["status"] == "error"


def test_status_history_settings_and_reminders(api):
    api.post("/api/command", json={"text": "remind me to stretch in 10 minutes"}, headers=H)
    s = api.get("/api/status", headers=H).json()
    assert s["version"] == "2.0.0" and s["extension_connected"] is False and s["scheduled"] == 1 and s["tools"] > 50
    assert api.get("/api/history?limit=5", headers=H).json()[0]["tool"] == "set_reminder"
    rid = api.get("/api/reminders", headers=H).json()[0]["id"]
    assert api.delete(f"/api/reminders/{rid}", headers=H).status_code == 200
    assert api.delete(f"/api/reminders/{rid}", headers=H).status_code == 404
    assert api.put("/api/settings/offline", json={"value": True}, headers=H).json()["value"] is True
    assert api.get("/api/settings", headers=H).json()["offline"] is True
    assert api.put("/api/settings/hacker", json={"value": 1}, headers=H).status_code == 404


def test_collections_crud_and_validation(api, tmp_path):
    assert api.put("/api/data/facts/Email", json={"value": "z@example.com"}, headers=H).status_code == 200
    assert api.get("/api/data/facts", headers=H).json() == {"email": "z@example.com"}
    assert api.put("/api/data/facts/bank password", json={"value": "x"}, headers=H).status_code == 400
    assert api.put("/api/data/snippets/s", json={"value": "my api key is abc"}, headers=H).status_code == 400
    assert api.put("/api/data/snippets/s", json={"value": 5}, headers=H).status_code == 400
    assert api.put("/api/data/macros/work", json={"value": ["open github", "play lofi"]}, headers=H).status_code == 200
    assert api.put("/api/data/macros/bad", json={"value": "open github"}, headers=H).status_code == 400
    assert api.put("/api/data/projects/p", json={"value": {"path": str(tmp_path / "missing")}}, headers=H).status_code == 400
    assert api.put("/api/data/projects/p", json={"value": {"path": str(tmp_path), "evil": 1}}, headers=H).status_code == 400
    assert api.put("/api/data/nope/x", json={"value": "v"}, headers=H).status_code == 404
    assert api.delete("/api/data/facts/email", headers=H).status_code == 200
    assert api.delete("/api/data/facts/email", headers=H).status_code == 404
    api.put("/api/data/macros/work2", json={"value": ["set volume to 15"]}, headers=H)
    assert api.post("/api/command", json={"text": "work2"}, headers=H).json()["status"] == "done"


def test_live_events_stream_delivers_notifications_then_closes(api):
    threading.Timer(0.2, lambda: api.ctx.events.publish("notify", {"title": "Reminder", "message": "stretch", "speak": True})).start()
    r = api.get("/api/events?once=1&timeout=5", headers=H)
    assert r.headers["content-type"].startswith("text/event-stream")
    assert 'data: {"kind": "notify"' in r.text and "stretch" in r.text and r.text.startswith("retry: 20000")
    assert api.ctx.events.subscribers == 0                                   # nothing left subscribed => no idle work


# ----------------------------------------------------------------------------- extension protocol
def test_a_command_travels_phone_to_server_to_extension_and_back(api):
    ext = TestClient(api.app_)
    seen = []

    def fake_extension():
        for _ in range(4):
            a = ext.get("/api/ext/next?wait=2&v=9.9", headers={"X-Cozy-Client": "extension"}).json()["action"]
            if a:
                seen.append(a)
                ext.post(f"/api/ext/ack/{a['id']}", json={"ok": True, "data": {"position": 60}}, headers={"X-Cozy-Client": "extension"})
                return
    t = threading.Thread(target=fake_extension, daemon=True)
    t.start()
    time.sleep(0.3)
    started = time.monotonic()
    r = api.post("/api/command", json={"text": "forward that video to 1min"}, headers=H).json()
    t.join(3)
    assert r["status"] == "done" and "1:00" in r["message"]
    assert seen[0]["type"] == "media" and seen[0]["action"] == "seek_to" and seen[0]["value"] == 60
    assert time.monotonic() - started < 2
    assert api.ctx.bridge.version == "9.9"
    assert api.post("/api/ext/ack/unknown-id", json={"ok": True}, headers={"X-Cozy-Client": "e"}).status_code == 404
