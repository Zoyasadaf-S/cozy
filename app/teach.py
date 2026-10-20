"""Teach Cozy from the terminal (server must be running).

  python -m app.teach "quieten the telly" media_control action=volume_down
  python -m app.teach "go to my college site" open_url url=https://example.com
  python -m app.teach --list
"""
from __future__ import annotations

import argparse
import json
import sys

import httpx


def parse_args(text: str) -> dict:
    """Accepts key=value[,key=value] or raw JSON."""
    text = text.strip()
    if text.startswith("{"):
        return json.loads(text)
    out = {}
    for part in filter(None, (p.strip() for p in text.split(","))):
        k, _, v = part.partition("=")
        out[k.strip()] = v.strip()
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Teach Cozy what a phrase means")
    ap.add_argument("say", nargs="?", help="what you say")
    ap.add_argument("tool", nargs="?", help="open_app | open_url | web_search | youtube_play | media_control | remember | chat")
    ap.add_argument("args", nargs="?", default="", help="arguments, like action=pause (or JSON)")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    a = ap.parse_args(argv)
    if a.list:
        print(json.dumps(httpx.get(f"{a.url}/teach", timeout=10).json(), indent=2, ensure_ascii=False))
        return 0
    if not a.say or not a.tool:
        ap.print_help()
        return 1
    r = httpx.post(f"{a.url}/teach", json={"say": a.say, "tool": a.tool, "args": parse_args(a.args)}, timeout=10)
    print(r.json())
    return 0 if r.status_code == 200 else 1


if __name__ == "__main__":
    sys.exit(main())