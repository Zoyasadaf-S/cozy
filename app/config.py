from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings loaded from environment / .env. Secrets must NOT go here."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="COZY_", extra="ignore")

    host: str = "127.0.0.1"  # local only until auth + encryption exist
    port: int = 8000
    workspace: Path = Path("./workspace")
    wake_word: str = "ok cozy"
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5:1.5b"
    ollama_timeout: int = 120
    db_path: Path = Path("data/cozy.db")
    shortcuts_path: Path = Path("data/shortcuts.json")  # your own website shortcuts
    examples_path: Path = Path("data/examples.jsonl")  # things you taught Cozy
    chrome_port: int = 9222  # Cozy's own Chrome window is controlled through this port
    chrome_profile: Path = Path("data/chrome-profile")  # separate Chrome profile for Cozy


def get_settings() -> Settings:
    return Settings()