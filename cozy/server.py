"""FastAPI app. Built by a factory so tests (and plugins) never share global state."""
from __future__ import annotations

import json
import logging
import queue
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, guard
from .bridge import ExtensionBridge
from .config import Settings, get_settings, lan_ip, load_token
from .context import Ctx
from .dispatcher import Dispatcher
from .llm import LocalLLM
from .netio import Http
from .plugins import load_plugins
from .scheduler import Scheduler
from .store import Store
from .tools import reminders as reminders_tools
from .tools.safety import status_snapshot
from .winops import WinOps

log = logging.getLogger("cozy.server")
STATIC = Path(__file__).parent / "static"
LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}
COLLECTIONS = {"facts": "fact:", "macros": "macro:", "snippets": "snippet:", "projects": "project:"}
PROJECT_KEYS = {"path", "editor", "run", "test", "url"}
SETTING_NAMES = {"offline", "speak_on_laptop", "city", "profile"}


class CommandIn(BaseModel):
    text: str
    confidence: Optional[float] = None
    dry_run: bool = False


class ApproveIn(BaseModel):
    approved: bool = True


class AckIn(BaseModel):
    ok: bool = True
    detail: str = ""
    data: Any = None


class ValueIn(BaseModel):
    value: Any


def build_ctx(settings: Settings, *, win=None, http=None, llm=None, bridge=None, clock=time.time) -> Ctx:
    store = Store(settings.db_path)
    ctx = Ctx(settings, store, bridge or ExtensionBridge(), win or WinOps(), http or Http(lambda: False), llm, clock=clock)
    ctx.http.is_offline = lambda: ctx.offline
    if llm is None:
        ctx.llm = LocalLLM(settings.ollama_url, settings.ollama_model, settings.ollama_timeout,
                           enabled=lambda: bool(ctx.setting("llm_enabled", True)))
    return ctx


def _hostname(header: str) -> str:
    header = (header or "").strip().lower()
    if header.startswith("["):
        return header.split("]")[0] + "]"
    return header.split(":")[0]


def create_app(settings: Optional[Settings] = None, *, ctx: Optional[Ctx] = None, start_scheduler: bool = True,
               plugins: bool = True) -> FastAPI:
    settings = settings or get_settings()
    ctx = ctx or build_ctx(settings)
    token = load_token(settings.data_dir)
    dispatcher = Dispatcher(ctx)
    ctx.scheduler = Scheduler(ctx.store, lambda item: reminders_tools.fire(ctx, item), clock=ctx.clock)
    ctx.plugins_loaded = load_plugins(settings.plugins_dir) if plugins else []

    @asynccontextmanager
    async def lifespan(_app):
        if start_scheduler:
            ctx.scheduler.start()
            ctx.scheduler.run_due()                     # catch up on anything that came due while Cozy was off
        yield
        ctx.scheduler.stop()

    app = FastAPI(title="Cozy", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.ctx, app.state.dispatcher, app.state.token = ctx, dispatcher, token
    app.add_middleware(GZipMiddleware, minimum_size=500)

    def is_loopback(request: Request) -> bool:
        return bool(request.client) and request.client.host in LOOPBACK

    @app.middleware("http")
    async def protect(request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/"):
            if _hostname(request.headers.get("host", "")) not in settings.allowed_hosts:
                return JSONResponse({"detail": "Host not allowed."}, status_code=403)           # DNS-rebinding guard
            if path != "/api/events" and not request.headers.get("x-cozy-client"):
                return JSONResponse({"detail": "Missing X-Cozy-Client header."}, status_code=403)  # cross-site (CSRF) guard
            if not is_loopback(request):
                supplied = request.headers.get("x-cozy-token") or request.query_params.get("t") or ""
                if not secrets.compare_digest(supplied, token):
                    return JSONResponse({"detail": "Pair this device first (open the link shown on the laptop)."}, status_code=401)
        response = await call_next(request)
        if path.startswith("/static/"):
            response.headers["Cache-Control"] = "public, max-age=604800"
        return response

    # ------------------------------------------------------------------ pages
    @app.get("/health")
    def health():
        return {"status": "ok", "version": __version__}

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/sw.js")
    def service_worker():
        return FileResponse(STATIC / "sw.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"})

    @app.get("/manifest.webmanifest")
    def manifest():
        return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")

    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

    # ------------------------------------------------------------------ commands
    @app.post("/api/command")
    def command(body: CommandIn):
        text = body.text.strip()
        if not text:
            raise HTTPException(400, "Say or type a command.")
        if len(text) > 500:
            raise HTTPException(400, "That command is too long.")
        return dispatcher.handle(text, confidence=body.confidence, dry_run=body.dry_run)

    @app.post("/api/approve/{approval_id}")
    def approve(approval_id: str, body: ApproveIn):
        return dispatcher.approve(approval_id, body.approved)

    @app.get("/api/status")
    def status():
        return status_snapshot(ctx)

    @app.get("/api/history")
    def history(limit: int = 30):
        return ctx.store.history(max(1, min(limit, 200)))

    @app.get("/api/pair")
    def pair(request: Request):
        if not is_loopback(request):
            raise HTTPException(403, "Open this on the laptop itself.")
        url = f"http://{lan_ip()}:{settings.port}/?t={token}"
        return {"lan_enabled": settings.lan, "url": url if settings.lan else None,
                "hint": None if settings.lan else "Restart Cozy with COZY_LAN=1 (or start_phone.bat) to allow your phone."}

    # ------------------------------------------------------------------ settings, reminders, collections
    @app.get("/api/settings")
    def get_settings_route():
        return {name: ctx.setting(name, "default" if name == "profile" else None) for name in SETTING_NAMES}

    @app.put("/api/settings/{name}")
    def put_setting(name: str, body: ValueIn):
        if name not in SETTING_NAMES:
            raise HTTPException(404, "Unknown setting.")
        ctx.set_setting(name, body.value)
        return {"name": name, "value": body.value}

    @app.get("/api/reminders")
    def list_reminders():
        return ctx.store.list_reminders()

    @app.delete("/api/reminders/{rid}")
    def delete_reminder(rid: int):
        if not ctx.scheduler.cancel(rid=rid):
            raise HTTPException(404, "No such reminder.")
        return {"status": "deleted"}

    def _prefix(coll: str) -> str:
        if coll not in COLLECTIONS:
            raise HTTPException(404, "Unknown collection.")
        return COLLECTIONS[coll]

    @app.get("/api/data/{coll}")
    def coll_list(coll: str):
        return ctx.store.kv_all(_prefix(coll))

    @app.put("/api/data/{coll}/{name}")
    def coll_put(coll: str, name: str, body: ValueIn, request: Request):
        prefix, name, value = _prefix(coll), name.strip().lower(), body.value
        if not name or len(name) > 80 or any(ord(c) < 32 for c in name):
            raise HTTPException(400, "Choose a shorter name without control characters.")
        if coll in ("facts", "snippets"):
            if not isinstance(value, str) or len(value) > 20000:
                raise HTTPException(400, "Value must be text under 20,000 characters.")
            if guard.is_sensitive(name, value):
                raise HTTPException(400, guard.REFUSAL)
        elif coll == "macros":
            if not isinstance(value, list) or not value or not all(isinstance(s, str) and s.strip() for s in value) or len(value) > 20:
                raise HTTPException(400, "A routine is a list of up to 20 commands.")
        else:
            if not isinstance(value, dict) or set(value) - PROJECT_KEYS or not isinstance(value.get("path"), str):
                raise HTTPException(400, "A project needs a path and may have editor, run, test and url.")
            if not Path(value["path"]).is_dir():
                raise HTTPException(400, "That folder doesn't exist.")
            if (value.get("run") or value.get("test")) and not is_loopback(request):
                raise HTTPException(403, "Run and test commands can only be changed on the laptop itself.")
        ctx.store.kv_set(prefix + name, value)
        return {"name": name, "value": value}

    @app.delete("/api/data/{coll}/{name}")
    def coll_delete(coll: str, name: str):
        if not ctx.store.kv_delete(_prefix(coll) + name.strip().lower()):
            raise HTTPException(404, "Not found.")
        return {"status": "deleted"}

    # ------------------------------------------------------------------ live events (only while the phone app is open)
    @app.get("/api/events")
    def events(once: bool = False, timeout: float = 0.0):
        def stream():
            sub = ctx.events.subscribe()
            started = time.monotonic()
            try:
                yield "retry: 20000\n\n"                  # polite reconnect delay: no reconnect storms
                while True:
                    remaining = (timeout - (time.monotonic() - started)) if timeout else 45.0
                    if timeout and remaining <= 0:
                        return
                    try:
                        event = sub.get(timeout=min(45.0, remaining))
                    except queue.Empty:
                        yield ": ping\n\n"                # one tiny keep-alive per 45 s, never faster
                        continue
                    yield f"data: {json.dumps(event)}\n\n"
                    if once:
                        return
            finally:
                ctx.events.unsubscribe(sub)
        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ------------------------------------------------------------------ Chrome extension (laptop only)
    def _local_only(request: Request) -> None:
        if not is_loopback(request):
            raise HTTPException(403, "The extension endpoints are for the laptop only.")

    @app.get("/api/ext/next")
    def ext_next(request: Request, wait: float = 25.0, v: Optional[str] = None):
        _local_only(request)
        return {"action": ctx.bridge.next(wait=wait, version=v)}

    @app.post("/api/ext/ack/{action_id}")
    def ext_ack(action_id: str, body: AckIn, request: Request):
        _local_only(request)
        outcome = ctx.bridge.ack(action_id, body.ok, body.detail, body.data)
        if outcome == "unknown":
            raise HTTPException(404, "Unknown or already finished action.")
        return {"status": outcome}

    return app
