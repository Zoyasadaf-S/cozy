"""Find and open files in your Documents, Desktop and Downloads. The index is built on demand and
cached for 10 minutes: no background watcher, so no idle battery use."""
from __future__ import annotations

import os
from pathlib import Path
from typing import List, Tuple

from ..registry import Result, rule, tool

SKIP_DIRS = {"node_modules", ".git", "venv", ".venv", "__pycache__", "site-packages", "$recycle.bin", "appdata"}
EXT_WORDS = {"pdf", "docx", "doc", "xlsx", "pptx", "txt", "png", "jpg", "jpeg", "zip", "py", "mp3", "mp4", "csv", "md"}
NOISE = {"my", "the", "a", "file", "files", "document", "documents", "of", "named", "called"}
MAX_FILES, MAX_DEPTH, TTL = 60000, 5, 600.0


def default_roots(ctx) -> List[Path]:
    configured = ctx.setting("file_roots")
    if configured:
        return [Path(p) for p in configured]
    return [ctx.win.home / d for d in ("Documents", "Desktop", "Downloads")]


def build_index(roots: List[Path]) -> List[Tuple[str, str, float]]:
    out: List[Tuple[str, str, float]] = []
    for root in roots:
        if not root.is_dir():
            continue
        base_depth = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root):
            if len(Path(dirpath).parts) - base_depth >= MAX_DEPTH:
                dirnames[:] = []
            dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_DIRS and not d.startswith(".")]
            for name in filenames:
                if name.startswith("."):
                    continue
                full = os.path.join(dirpath, name)
                try:
                    out.append((name.lower(), full, os.path.getmtime(full)))
                except OSError:
                    continue
                if len(out) >= MAX_FILES:
                    return out
    return out


def _index(ctx):
    cache = getattr(ctx, "_file_index", None)
    if cache is None or ctx.clock() - cache[0] > TTL:
        cache = (ctx.clock(), build_index(default_roots(ctx)))
        ctx._file_index = cache
    return cache[1]


def search(ctx, query: str, limit: int = 5):
    words = [w for w in query.lower().replace(".", " ").split() if w not in NOISE]
    exts = {w for w in words if w in EXT_WORDS}
    terms = [w for w in words if w not in exts]
    if not words:
        return []
    hits = []
    for name, full, mtime in _index(ctx):
        if exts and name.rsplit(".", 1)[-1] not in exts:
            continue
        if all(t in name for t in terms):
            hits.append((mtime, full))
    hits.sort(reverse=True)
    return [p for _, p in hits[:limit]]


@tool("find_file", desc="find files by name in Documents, Desktop and Downloads", args='{"query": "resume pdf"}')
def find_file(ctx, query):
    hits = search(ctx, query)
    if not hits:
        return Result(False, f"I couldn't find a file matching '{query.strip()}'.")
    names = [Path(h).name for h in hits[:3]]
    more = f" and {len(hits) - 3} more" if len(hits) > 3 else ""
    return Result(True, f"I found {len(hits)}: " + "; ".join(names) + more + ". Say 'open my ... file' to open the first.", data=hits)


@tool("open_file", desc="open a file by name", args='{"query": "resume pdf"}')
def open_file(ctx, query):
    hits = search(ctx, query, 1)
    if not hits:
        return Result(False, f"I couldn't find a file matching '{query.strip()}'.")
    ctx.win.open_path(Path(hits[0]))
    return Result(True, f"Opening {Path(hits[0]).name}.")


rule(r"^(?:find|locate)\s+(?:my\s+|the\s+)?(?P<query>.+?)(?:\s+(?:file|files|document))?$", "find_file", priority=60)
rule(r"^open\s+(?:my\s+|the\s+)?(?P<query>.+?)\s+(?:file|document)$", "open_file", priority=24)
