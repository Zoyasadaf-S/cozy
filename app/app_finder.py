"""Find and launch ANY app installed on this laptop (Windows).

Instead of a hardcoded list, we read what Windows itself knows: Start Menu
shortcuts and the Start-menu app list (includes Store apps), plus a few
built-in tools and your user folders. Spoken names are matched loosely.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from difflib import get_close_matches
from pathlib import Path
from typing import Callable, Dict, Optional


@dataclass(frozen=True)
class AppEntry:
    name: str
    kind: str  # path | uwp | cmd
    target: str


# Built-in Windows tools that are not always in the Start menu.
SYSTEM_TOOLS = {
    "file explorer": "explorer.exe",
    "explorer": "explorer.exe",
    "files": "explorer.exe",
    "this pc": "explorer.exe",
    "task manager": "taskmgr",
    "settings": "ms-settings:",
    "control panel": "control",
    "command prompt": "cmd.exe",
    "cmd": "cmd.exe",
    "powershell": "powershell.exe",
    "notepad": "notepad.exe",
    "paint": "mspaint.exe",
    "calculator": "calc.exe",
    "snipping tool": "snippingtool.exe",
    "run": "explorer.exe shell:::{2559a1f3-21d7-11d4-bdaf-00c04f60b9f0}",
}

USER_FOLDERS = {
    "downloads": "Downloads",
    "documents": "Documents",
    "desktop": "Desktop",
    "pictures": "Pictures",
    "music": "Music",
    "videos": "Videos",
}


def normalize(name: str) -> str:
    n = name.lower().strip().rstrip(".!?")
    n = re.sub(r"^(?:the|my|a)\s+", "", n)
    n = re.sub(r"\s+(?:app|application|program|software|folder)$", "", n)
    return n.strip()


def _start_menu_dirs():
    for var in ("ProgramData", "APPDATA"):
        base = os.environ.get(var)
        if base:
            yield Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def scan_start_menu() -> Dict[str, AppEntry]:
    out: Dict[str, AppEntry] = {}
    for d in _start_menu_dirs():
        if not d.exists():
            continue
        for p in d.rglob("*.lnk"):
            if "uninstall" in p.stem.lower():
                continue
            out[normalize(p.stem)] = AppEntry(p.stem, "path", str(p))
    return out


def scan_start_apps() -> Dict[str, AppEntry]:
    """Everything in the Start menu, including Microsoft Store apps."""
    if not sys.platform.startswith("win"):
        return {}
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Get-StartApps | ConvertTo-Json"],
            capture_output=True, text=True, timeout=25,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        data = json.loads(r.stdout or "[]")
    except Exception:
        return {}
    if isinstance(data, dict):
        data = [data]
    out: Dict[str, AppEntry] = {}
    for a in data:
        name, appid = a.get("Name"), a.get("AppID")
        if name and appid and "uninstall" not in name.lower():
            out[normalize(name)] = AppEntry(name, "uwp", appid)
    return out


def default_index() -> Dict[str, AppEntry]:
    idx: Dict[str, AppEntry] = {}
    idx.update(scan_start_apps())
    idx.update(scan_start_menu())  # shortcuts are the most reliable, so they win
    for n, d in USER_FOLDERS.items():
        idx[n] = AppEntry(n.title(), "path", str(Path.home() / d))
    for n, t in SYSTEM_TOOLS.items():
        idx[n] = AppEntry(n.title(), "cmd", t)
    return idx


class AppFinder:
    def __init__(self, builder: Callable[[], Dict[str, AppEntry]] = default_index):
        self._builder = builder
        self._index: Optional[Dict[str, AppEntry]] = None
        self._built = 0.0

    @property
    def index(self) -> Dict[str, AppEntry]:
        if self._index is None:
            self.refresh()
        return self._index  # type: ignore[return-value]

    def refresh(self) -> None:
        self._index = self._builder()
        self._built = time.time()

    @staticmethod
    def _match(idx: Dict[str, AppEntry], q: str) -> Optional[AppEntry]:
        if q in idx:
            return idx[q]
        words = q.split()
        hits = [k for k in idx if all(w in k.split() for w in words)]
        if hits:
            return idx[min(hits, key=len)]
        starts = [k for k in idx if k.startswith(q)]
        if starts:
            return idx[min(starts, key=len)]
        close = get_close_matches(q, list(idx), n=1, cutoff=0.78)
        return idx[close[0]] if close else None

    def find(self, name: str) -> Optional[AppEntry]:
        q = normalize(name)
        if len(q) < 2:
            return None
        hit = self._match(self.index, q)
        if hit is None and time.time() - self._built > 30:  # app may be newly installed
            self.refresh()
            hit = self._match(self.index, q)
        return hit

    def launch(self, e: AppEntry) -> None:
        if e.kind == "uwp":
            subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{e.target}"])
        elif sys.platform.startswith("win"):
            if e.kind == "cmd" and " " in e.target:
                subprocess.Popen(e.target)
            else:
                os.startfile(e.target)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", e.target])