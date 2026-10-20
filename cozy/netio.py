"""Internet access with a tiny cache, short timeouts and an offline switch."""
from __future__ import annotations

import json
import time
import urllib.request
from typing import Any, Callable, Dict, Optional, Tuple


class OfflineError(Exception):
    pass


def _fetch(url: str, timeout: float) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Cozy/2.0 (+local assistant)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - https URLs we construct
        return resp.read(1_000_000)


class Http:
    def __init__(self, is_offline: Callable[[], bool], fetcher: Callable[[str, float], bytes] = _fetch,
                 timeout: float = 6.0, clock: Callable[[], float] = time.monotonic):
        self.is_offline, self._fetch, self.timeout, self._clock = is_offline, fetcher, timeout, clock
        self._cache: Dict[str, Tuple[float, bytes]] = {}

    def get_bytes(self, url: str, ttl: float = 600.0) -> bytes:
        if self.is_offline():
            raise OfflineError("Offline mode is on.")
        hit = self._cache.get(url)
        if hit and self._clock() - hit[0] < ttl:
            return hit[1]
        data = self._fetch(url, self.timeout)
        if len(self._cache) > 64:
            self._cache.clear()
        self._cache[url] = (self._clock(), data)
        return data

    def get_text(self, url: str, ttl: float = 600.0) -> str:
        return self.get_bytes(url, ttl).decode("utf-8", "replace")

    def get_json(self, url: str, ttl: float = 600.0) -> Any:
        return json.loads(self.get_bytes(url, ttl))
