import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cozy.tools  # noqa: E402,F401  (registers every tool)
from cozy.bridge import ExtensionBridge, ExtensionUnavailable  # noqa: E402
from cozy.config import Settings  # noqa: E402
from cozy.context import Ctx  # noqa: E402
from cozy.dispatcher import Dispatcher  # noqa: E402
from cozy.netio import Http  # noqa: E402
from cozy.scheduler import Scheduler  # noqa: E402
from cozy.store import Store  # noqa: E402
from cozy.tools import reminders as reminders_tools  # noqa: E402
from cozy.winops import WinOps  # noqa: E402

START = datetime(2026, 10, 19, 9, 0, 0).timestamp()     # a Monday morning


class Clock:
    def __init__(self, t=START):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr, self.terminated = returncode, stdout, stderr, False

    def poll(self):
        return None if not self.terminated else 0

    def terminate(self):
        self.terminated = True


class Endpoint:
    def __init__(self):
        self.level, self.muted = 0.5, False

    def GetMasterVolumeLevelScalar(self):
        return self.level

    def SetMasterVolumeLevelScalar(self, value, _ctx):
        self.level = value

    def SetMute(self, muted, _ctx):
        self.muted = muted


class Recorder:
    """Stands in for every OS call: subprocess, keyboard, startfile."""
    def __init__(self):
        self.runs, self.popens, self.keys, self.started = [], [], [], []
        self.endpoint = Endpoint()
        self.outputs = {}          # first-arg-tuple prefix -> Proc

    def run(self, args, **kw):
        self.runs.append((args, kw))
        key = tuple(args) if isinstance(args, (list, tuple)) else args
        for prefix, proc in self.outputs.items():
            if (isinstance(key, tuple) and key[:len(prefix)] == prefix) or key == prefix:
                return proc
        return Proc()

    def popen(self, args, **kw):
        self.popens.append((args, kw))
        return Proc()

    def keybd(self, code, scan, flags, extra):
        self.keys.append((code, flags))

    def startfile(self, path):
        self.started.append(path)


class FakeBridge:
    """Records browser actions and returns canned results."""
    def __init__(self):
        self.calls, self.connected, self.result, self.results = [], True, {"ok": True, "detail": "", "data": None}, {}
        self.version = "2.0.0"

    def available(self):
        return self.connected

    def pending(self):
        return 0

    def submit(self, action, *, retry=False, wait=True, timeout=45.0):
        if not self.connected:
            raise ExtensionUnavailable("down")
        self.calls.append((action, retry))
        return self.results.get(action["type"], self.result)


@pytest.fixture
def env(tmp_path):
    clock = Clock()
    rec = Recorder()
    home = tmp_path / "home"
    home.mkdir()
    win = WinOps(run=rec.run, popen=rec.popen, platform="win32", home=home, env={"APPDATA": str(tmp_path / "appdata")},
                 endpoint=lambda: rec.endpoint, keybd=rec.keybd, startfile=rec.startfile)
    fetched = {}

    def fetcher(url, timeout):
        fetched.setdefault("urls", []).append(url)
        for needle, payload in fetched.get("routes", {}).items():
            if needle in url:
                return payload if isinstance(payload, bytes) else __import__("json").dumps(payload).encode()
        raise OSError("no route for " + url)

    settings = Settings(data_dir=tmp_path / "data", plugins_dir=tmp_path / "plugins")
    store = Store(":memory:")
    bridge = FakeBridge()
    ctx = Ctx(settings, store, bridge, win, Http(lambda: False, fetcher=fetcher, clock=clock), llm=None, clock=clock)
    ctx.http.is_offline = lambda: ctx.offline
    ctx.scheduler = Scheduler(store, lambda item: reminders_tools.fire(ctx, item), clock=clock)
    disp = Dispatcher(ctx)
    return SimpleNamespace(ctx=ctx, disp=disp, rec=rec, bridge=bridge, clock=clock, win=win, home=home,
                           fetched=fetched, store=store, tmp=tmp_path)
