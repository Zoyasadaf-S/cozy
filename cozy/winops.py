"""Everything that touches Windows lives here, behind injectable callables so the rest of Cozy
(and the tests) never need a real Windows machine. Anything unsupported raises Unsupported."""
from __future__ import annotations

import difflib
from contextlib import contextmanager
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional


class Unsupported(Exception):
    pass


class OpFailed(Exception):
    pass


VK = {
    "ctrl": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B,
    "tab": 0x09, "enter": 0x0D, "left": 0x25, "up": 0x26,
    "right": 0x27, "down": 0x28, "prtsc": 0x2C, "d": 0x44,
    "l": 0x4C, "m": 0x4D, "v": 0x56, "s": 0x53,
    "next": 0xB0, "previous": 0xB1, "playpause": 0xB3,
    "volume_up": 0xAF, "volume_down": 0xAE, "mute": 0xAD,
}

KNOWN_FOLDERS = (
    "downloads", "documents", "desktop", "pictures",
    "videos", "music", "screenshots",
)

_FOLDER_ALIASES = {
    "screen shorts": "screenshots",
    "screenshot": "screenshots",
    "screen shots": "screenshots",
    "download": "downloads",
    "document": "documents",
    "photos": "pictures",
    "images": "pictures",
}

BROWSER_EXES = {
    "edge": "msedge.exe",
    "firefox": "firefox.exe",
    "brave": "brave.exe",
    "chrome": "chrome.exe",
}

_SAFE_APP = re.compile(r"^[\w .+()\-]{1,60}$")


def canonical_folder(spoken: str) -> Optional[str]:
    s = re.sub(r"^(?:my|the)\s+", "", spoken.strip().lower())
    s = re.sub(r"\s+(?:folder|directory)$", "", s)
    s = _FOLDER_ALIASES.get(s, s)
    return s if s in KNOWN_FOLDERS else None


def _default_endpoint():
    from pycaw.pycaw import AudioUtilities  # type: ignore

    spk = AudioUtilities.GetSpeakers()

    if hasattr(spk, "EndpointVolume"):
        return spk.EndpointVolume

    from ctypes import POINTER, cast
    from comtypes import CLSCTX_ALL  # type: ignore
    from pycaw.pycaw import IAudioEndpointVolume  # type: ignore

    return cast(
        spk.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None),
        POINTER(IAudioEndpointVolume),
    )


class WinOps:
    def __init__(
        self,
        run: Callable = subprocess.run,
        popen: Callable = subprocess.Popen,
        platform: str = sys.platform,
        home: Optional[Path] = None,
        env: Optional[dict] = None,
        endpoint: Optional[Callable] = None,
        keybd: Optional[Callable] = None,
        startfile: Optional[Callable] = None,
    ):
        self._run, self._popen, self.platform = run, popen, platform
        self.home = Path(home) if home else Path.home()
        self.env = dict(os.environ if env is None else env)

        # Injected endpoints (usually test doubles) do not need COM initialization.
        self._uses_default_endpoint = endpoint is None
        self._endpoint_factory = (
            endpoint if endpoint is not None else _default_endpoint
        )

        self._keybd = keybd
        self._startfile = startfile or getattr(os, "startfile", None)
        self._apps: Optional[Dict[str, Path]] = None

    @property
    def is_windows(self) -> bool:
        return self.platform.startswith("win")

    def _need_win(self) -> None:
        if not self.is_windows:
            raise Unsupported("That only works on Windows.")

    # ---- audio / COM initialization ----

    @contextmanager
    def _audio_com(self):
        """Initialize COM on the current thread for one Windows audio operation.

        COM initialization is thread-local. FastAPI can execute successive
        requests on different worker threads, so initializing COM only once
        during application startup is not sufficient.

        Injected endpoints used by tests bypass COM initialization.
        """
        if not self._uses_default_endpoint:
            yield
            return

        import comtypes  # type: ignore

        try:
            comtypes.CoInitialize()
        except Exception as exc:
            # RPC_E_CHANGED_MODE means COM is already initialized on this thread
            # using a different apartment model. COM is usable there, but this
            # failed call did not acquire an initialization count.
            if getattr(exc, "hresult", None) != -2147417850:
                raise
            yield
            return

        try:
            yield
        finally:
            comtypes.CoUninitialize()

    def get_volume(self) -> int:
        self._need_win()

        try:
            with self._audio_com():
                endpoint = self._endpoint_factory()
                return round(
                    endpoint.GetMasterVolumeLevelScalar() * 100
                )
        except Exception as exc:
            raise OpFailed(
                f"Windows volume control failed "
                f"({type(exc).__name__}: {exc}). "
                "Check Windows audio and pycaw."
            ) from exc

    def set_volume(self, percent: int) -> None:
        self._need_win()

        if (
            isinstance(percent, bool)
            or not isinstance(percent, int)
            or not 0 <= percent <= 100
        ):
            raise OpFailed(
                "Volume must be a whole number from 0 to 100."
            )

        try:
            with self._audio_com():
                endpoint = self._endpoint_factory()
                endpoint.SetMasterVolumeLevelScalar(
                    percent / 100.0, None
                )
        except Exception as exc:
            raise OpFailed(
                f"Windows volume control failed "
                f"({type(exc).__name__}: {exc}). "
                "Check Windows audio and pycaw."
            ) from exc

    def set_mute(self, muted: bool) -> None:
        self._need_win()

        try:
            with self._audio_com():
                endpoint = self._endpoint_factory()
                endpoint.SetMute(bool(muted), None)
        except Exception as exc:
            raise OpFailed(
                f"Windows mute control failed "
                f"({type(exc).__name__}: {exc})."
            ) from exc

    # ---- keyboard (window snapping, screenshots, paste, media keys) ----

    def hotkey(self, *keys: str) -> None:
        self._need_win()

        try:
            codes = [VK[k] for k in keys]
        except KeyError as exc:
            raise OpFailed(f"Unknown key {exc}") from exc

        keybd = self._keybd

        if keybd is None:
            import ctypes
            keybd = ctypes.windll.user32.keybd_event  # type: ignore[attr-defined]

        for c in codes:
            keybd(c, 0, 0, 0)

        for c in reversed(codes):
            keybd(c, 0, 2, 0)

    # ---- PowerShell helper (values are passed through env vars, never interpolated) ----

    def _ps(
        self,
        script: str,
        extra_env: Optional[dict] = None,
        timeout: float = 15.0,
    ) -> str:
        self._need_win()
        env = {**self.env, **(extra_env or {})}

        proc = self._run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )

        if getattr(proc, "returncode", 0) != 0:
            raise OpFailed(
                (proc.stderr or proc.stdout or "PowerShell failed").strip()[:200]
            )

        return (proc.stdout or "").strip()

    # ---- display / power / network ----

    def get_brightness(self) -> int:
        out = self._ps(
            "(Get-CimInstance -Namespace root/WMI "
            "-ClassName WmiMonitorBrightness).CurrentBrightness"
        )

        try:
            return int(out.splitlines()[0])
        except (ValueError, IndexError) as exc:
            raise OpFailed(
                "This display does not report brightness "
                "(external monitors usually don't)."
            ) from exc

    def set_brightness(self, percent: int) -> None:
        if not 0 <= int(percent) <= 100:
            raise OpFailed("Brightness must be 0 to 100.")

        self._ps(
            "(Get-CimInstance -Namespace root/WMI "
            "-ClassName WmiMonitorBrightnessMethods)"
            f".WmiSetBrightness(1,{int(percent)})"
        )

    def lock(self) -> None:
        self._need_win()
        self._run(
            ["rundll32.exe", "user32.dll,LockWorkStation"],
            check=False,
        )

    def sleep(self) -> None:
        self._need_win()
        self._run(
            ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
            check=False,
        )

    def shutdown(self, restart: bool = False, delay: int = 30) -> None:
        self._need_win()
        self._run(
            ["shutdown", "/r" if restart else "/s", "/t", str(delay)],
            check=False,
        )

    def cancel_shutdown(self) -> None:
        self._need_win()
        self._run(["shutdown", "/a"], check=False)

    def set_wifi(self, enabled: bool) -> None:
        self._need_win()

        proc = self._run(
            [
                "netsh",
                "interface",
                "set",
                "interface",
                "name=Wi-Fi",
                "admin=enabled" if enabled else "admin=disabled",
            ],
            capture_output=True,
            text=True,
        )

        if getattr(proc, "returncode", 0) != 0:
            raise OpFailed(
                "Windows refused. Changing Wi-Fi needs Cozy to run as administrator."
            )

    # ---- notifications, speech, clipboard ----

    def notify(self, title: str, message: str) -> None:
        if not self.is_windows:
            return

        script = (
            "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
            "$n=New-Object System.Windows.Forms.NotifyIcon;"
            "$n.Icon=[System.Drawing.SystemIcons]::Information;"
            "$n.Visible=$true;"
            "$n.ShowBalloonTip(10000,$env:COZY_TITLE,$env:COZY_MSG,"
            "[System.Windows.Forms.ToolTipIcon]::Info);"
            "Start-Sleep -Seconds 11;$n.Dispose()"
        )

        self._popen(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                script,
            ],
            env={
                **self.env,
                "COZY_TITLE": title[:60],
                "COZY_MSG": message[:240],
            },
        )

    def speak(self, text: str) -> None:
        if not self.is_windows:
            return

        script = (
            "Add-Type -AssemblyName System.Speech;"
            "(New-Object System.Speech.Synthesis.SpeechSynthesizer)"
            ".Speak($env:COZY_MSG)"
        )

        self._popen(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                script,
            ],
            env={**self.env, "COZY_MSG": text[:1000]},
        )

    def clipboard_set(self, text: str) -> None:
        if len(text) > 30000:
            raise OpFailed("That is too long to copy.")

        self._ps(
            "Set-Clipboard -Value $env:COZY_CLIP",
            {"COZY_CLIP": text},
        )

    # ---- generic process helpers (work on any OS; used by the developer tools) ----

    def run_cmd(
        self,
        args,
        cwd=None,
        timeout: float = 60.0,
        shell: bool = False,
    ):
        return self._run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=shell,
        )

    def spawn(self, args, cwd=None, shell: bool = False):
        return self._popen(args, cwd=cwd, shell=shell)

    # ---- apps, folders, browsers ----

    def folder(self, name: str) -> Optional[Path]:
        """An existing folder for a spoken name, or None. Never returns a path that doesn't exist."""
        key = canonical_folder(name)

        if not key:
            return None

        candidates: List[Path] = []
        roots = (
            [self.home]
            + [self.home / d for d in ("OneDrive",)]
            + [
                Path(self.env[v])
                for v in ("OneDrive", "OneDriveConsumer")
                if self.env.get(v)
            ]
        )

        for root in roots:
            if key == "screenshots":
                candidates += [
                    root / "Pictures" / "Screenshots",
                    root / "Screenshots",
                ]
            else:
                candidates.append(root / key.title())

        return next(
            (p.resolve() for p in candidates if p.is_dir()),
            None,
        )

    def open_path(self, path: Path) -> None:
        self._need_win()

        if self._startfile:
            self._startfile(str(path))
        else:
            self._popen(["explorer", str(path)])

    def _scan_apps(self) -> Dict[str, Path]:
        apps: Dict[str, Path] = {}
        bases = [self.env.get("ProgramData"), self.env.get("APPDATA")]

        for base in bases:
            if not base:
                continue

            root = (
                Path(base)
                / "Microsoft"
                / "Windows"
                / "Start Menu"
                / "Programs"
            )

            if root.is_dir():
                for lnk in root.rglob("*.lnk"):
                    apps.setdefault(lnk.stem.lower(), lnk)

        return apps

    def find_app(self, name: str) -> Optional[Path]:
        if self._apps is None:
            self._apps = self._scan_apps()

        key = name.strip().lower()

        for attempt in range(2):
            if key in self._apps:
                return self._apps[key]

            contains = [k for k in self._apps if key in k]
            if contains:
                return self._apps[min(contains, key=len)]

            close = difflib.get_close_matches(
                key,
                list(self._apps),
                n=1,
                cutoff=0.8,
            )
            if close:
                return self._apps[close[0]]

            if attempt == 0:
                self._apps = self._scan_apps()

        exe = shutil.which(key) if _SAFE_APP.match(key) else None
        return Path(exe) if exe else None

    def launch(self, target: Path) -> None:
        self._need_win()

        if self._startfile:
            self._startfile(str(target))
        else:
            self._popen([str(target)])

    def open_in_browser(self, browser: str, url: str) -> bool:
        exe_name = BROWSER_EXES.get(browser)

        if not exe_name or not url.startswith(("http://", "https://")):
            return False

        exe = shutil.which(exe_name)

        if not exe:
            for var in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
                base = self.env.get(var)
                if not base:
                    continue

                for sub in (
                    "Microsoft/Edge/Application",
                    "Mozilla Firefox",
                    "BraveSoftware/Brave-Browser/Application",
                    "Google/Chrome/Application",
                ):
                    p = Path(base) / sub / exe_name
                    if p.exists():
                        exe = str(p)
                        break

                if exe:
                    break

        if not exe:
            return False

        self._popen([exe, url])
        return True