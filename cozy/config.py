"""Settings come from environment variables (COZY_*); everything has a safe default."""
from __future__ import annotations

import os
import secrets
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Set


@dataclass
class Settings:
    host: str = "127.0.0.1"
    port: int = 8000
    data_dir: Path = Path("data")
    plugins_dir: Path = Path("plugins")
    lan: bool = False                       # True => phone on the same Wi-Fi may connect (token required)
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "llama3.2:3b"
    ollama_timeout: float = 20.0
    allowed_hosts: Set[str] = field(default_factory=lambda: {"127.0.0.1", "localhost", "[::1]", "::1"})

    @property
    def db_path(self) -> Path:
        return self.data_dir / "cozy.db"


def lan_ip() -> str:
    """Best-effort LAN address (a UDP connect sends no packets)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def get_settings() -> Settings:
    e = os.environ.get
    lan = e("COZY_LAN", "0") == "1"
    s = Settings(
        host=e("COZY_HOST", "0.0.0.0" if lan else "127.0.0.1"),
        port=int(e("COZY_PORT", "8000")),
        data_dir=Path(e("COZY_DATA", "data")),
        plugins_dir=Path(e("COZY_PLUGINS", "plugins")),
        lan=lan,
        ollama_url=e("COZY_OLLAMA_URL", "http://127.0.0.1:11434"),
        ollama_model=e("COZY_OLLAMA_MODEL", "llama3.2:3b"),
    )
    if lan:
        s.allowed_hosts.add(lan_ip())
    extra = e("COZY_ALLOWED_HOSTS", "")
    s.allowed_hosts.update(h.strip() for h in extra.split(",") if h.strip())
    return s


def load_token(data_dir: Path) -> str:
    """Pairing token for non-local clients (your phone). Created once, kept on disk."""
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "token.txt"
    if path.exists():
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value
    value = secrets.token_urlsafe(24)
    path.write_text(value, encoding="utf-8")
    return value
