"""Drop a .py file into plugins/ and restart Cozy: it can register tools and rules with the same
decorators the built-ins use. A broken plugin is skipped and reported, never fatal."""
from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from typing import List

log = logging.getLogger("cozy.plugins")


def load_plugins(directory: Path) -> List[str]:
    loaded: List[str] = []
    directory = Path(directory)
    if not directory.is_dir():
        return loaded
    for path in sorted(directory.glob("*.py")):
        if path.name.startswith("_"):
            continue
        try:
            spec = importlib.util.spec_from_file_location(f"cozy_plugin_{path.stem}", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)             # type: ignore[union-attr]
            loaded.append(path.stem)
        except Exception:
            log.exception("plugin %s failed to load", path.name)
    return loaded
