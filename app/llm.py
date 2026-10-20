"""Local LLM adapter (Ollama). Nothing leaves your laptop."""
from __future__ import annotations

from typing import Dict, List, Optional

import httpx

SYSTEM_PROMPT = (
    "You are Cozy, a private personal voice assistant. Answer in 1-3 short, "
    "clear sentences suitable for speaking aloud. Never reveal the user's "
    "personal facts unless the user asks about them. You cannot open apps or "
    "websites, play media or perform actions yourself; if asked, say you cannot "
    "do that and never claim you did."
)


class LLMUnavailable(Exception):
    pass


def build_messages(history: List[Dict[str, str]], facts: Dict[str, str], user_text: str) -> List[Dict[str, str]]:
    system = SYSTEM_PROMPT
    if facts:
        system += "\nKnown facts about the user:\n" + "\n".join(f"- {k}: {v}" for k, v in facts.items())
    return [{"role": "system", "content": system}, *history, {"role": "user", "content": user_text}]


class OllamaClient:
    def __init__(self, url: str, model: str, timeout: float = 120, client: Optional[httpx.Client] = None):
        self.url = url.rstrip("/")
        self.model = model
        self._client = client or httpx.Client(timeout=timeout)

    def chat(self, messages: List[Dict[str, str]], json_mode: bool = False) -> str:
        payload = {"model": self.model, "messages": messages, "stream": False, "options": {"num_predict": 256}}
        if json_mode:  # forces valid JSON and makes the output deterministic
            payload["format"] = "json"
            payload["options"]["temperature"] = 0
        try:
            r = self._client.post(f"{self.url}/api/chat", json=payload)
            r.raise_for_status()
            return r.json()["message"]["content"].strip()
        except httpx.ConnectError as e:
            raise LLMUnavailable("Ollama is not running. Start it (open the Ollama app or run 'ollama serve').") from e
        except httpx.TimeoutException as e:
            raise LLMUnavailable("The model took too long to answer. Try again.") from e
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise LLMUnavailable(f"Model '{self.model}' not found. Run: ollama pull {self.model}") from e
            raise LLMUnavailable(f"Ollama error {e.response.status_code}") from e
        except (KeyError, ValueError) as e:
            raise LLMUnavailable("Unexpected reply from Ollama.") from e