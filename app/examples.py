"""Things you taught Cozy ("when I say X, do Y"), reused as examples for the model.

This is how Cozy learns step by step without retraining: the most similar taught
examples are added to the model's prompt. The file is also a ready-made dataset
if you ever want to fine-tune a model later.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List

STOP = {"the", "a", "an", "to", "my", "me", "please", "can", "you", "could", "this", "that", "it",
        "on", "in", "of", "and", "ok", "okay", "cozy", "cosy", "hey"}


def _words(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9']+", text.lower()) if w not in STOP}


class ExampleStore:
    def __init__(self, path):
        self.path = Path(path)

    def all(self) -> List[Dict]:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out = []
        for ln in lines:
            try:
                d = json.loads(ln)
                if isinstance(d, dict) and d.get("say") and d.get("tool"):
                    out.append(d)
            except ValueError:
                continue
        return out

    def add(self, say: str, tool: str, args: Dict) -> None:
        keep = [e for e in self.all() if e["say"].strip().lower() != say.strip().lower()]
        keep.append({"say": say.strip(), "tool": tool, "args": args})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in keep) + "\n", encoding="utf-8")

    def similar(self, text: str, k: int = 4, min_score: float = 0.34) -> List[Dict]:
        qw = _words(text)
        if not qw:
            return []
        scored = []
        for e in self.all():
            ew = _words(e["say"])
            if ew:
                score = len(qw & ew) / len(qw | ew)
                if score >= min_score:
                    scored.append((score, e))
        scored.sort(key=lambda x: -x[0])
        return [e for _, e in scored[:k]]