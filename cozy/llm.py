"""Optional local LLM (Ollama). Everything still works without it; it only handles phrases the
rules can't parse, plus summaries and translation. A failed probe is remembered for a minute so
an absent model never slows commands down."""
from __future__ import annotations

import json
import time
import urllib.request
from typing import Callable, Dict, List, Optional


class LLMUnavailable(Exception):
    pass


def _post(url: str, payload: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - local Ollama URL
        return json.loads(resp.read())


class LocalLLM:
    def __init__(self, base_url: str, model: str, timeout: float = 20.0,
                 post: Callable[[str, dict, float], dict] = _post, clock: Callable[[], float] = time.monotonic,
                 enabled: Callable[[], bool] = lambda: True):
        self.base_url, self.model, self.timeout = base_url.rstrip("/"), model, timeout
        self._post, self._clock, self._enabled = post, clock, enabled
        self._down_until = 0.0

    def available(self) -> bool:
        return self._enabled() and self._clock() >= self._down_until

    def chat(self, messages: List[Dict[str, str]], json_mode: bool = False) -> str:
        if not self.available():
            raise LLMUnavailable("The local language model is not available.")
        payload = {"model": self.model, "messages": messages, "stream": False}
        if json_mode:
            payload["format"] = "json"
        try:
            data = self._post(f"{self.base_url}/api/chat", payload, self.timeout)
            return data["message"]["content"]
        except Exception as exc:
            self._down_until = self._clock() + 60.0
            raise LLMUnavailable(f"Ollama is not reachable: {exc}") from exc
