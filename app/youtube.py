"""Find the top YouTube result for a query so Cozy can play it directly.

Uses yt-dlp (search only, nothing is downloaded). If yt-dlp is missing or
YouTube can't be reached, returns None and the caller falls back to opening
the search results page.
"""
from __future__ import annotations

from typing import Optional


def first_video_url(query: str, timeout: int = 15) -> Optional[str]:
    try:
        import yt_dlp
    except ImportError:
        return None
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
        "socket_timeout": timeout,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"ytsearch1:{query}", download=False)
        entries = (info or {}).get("entries") or []
        if not entries:
            return None
        vid = entries[0].get("id") or ""
        if len(vid) == 11:
            return f"https://www.youtube.com/watch?v={vid}"
        return entries[0].get("url")
    except Exception:
        return None