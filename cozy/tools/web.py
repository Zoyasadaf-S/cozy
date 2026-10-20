"""Opening websites/apps/folders, searching, tab control, reading and summarising pages."""
from __future__ import annotations

import re
import webbrowser
from collections import Counter

from ..bridge import ExtensionUnavailable
from ..registry import Result, rule, tool
from ..winops import canonical_folder
from ._util import host_of, q

SITES = {
    "youtube": "https://www.youtube.com", "google": "https://www.google.com", "gmail": "https://mail.google.com",
    "calendar": "https://calendar.google.com", "chatgpt": "https://chatgpt.com", "github": "https://github.com",
    "whatsapp": "https://web.whatsapp.com", "whatsapp web": "https://web.whatsapp.com",
    "leetcode": "https://leetcode.com", "stack overflow": "https://stackoverflow.com", "linkedin": "https://www.linkedin.com",
    "maps": "https://maps.google.com", "drive": "https://drive.google.com", "spotify": "https://open.spotify.com",
    "youtube music": "https://music.youtube.com",
}
SITE_LABELS = {"youtube": "YouTube", "github": "GitHub", "gmail": "Gmail", "chatgpt": "ChatGPT", "whatsapp": "WhatsApp",
               "whatsapp web": "WhatsApp Web", "leetcode": "LeetCode", "linkedin": "LinkedIn"}
BROWSERS = {"google chrome": "chrome", "chrome": "chrome", "microsoft edge": "edge", "edge": "edge",
            "mozilla firefox": "firefox", "firefox": "firefox", "brave": "brave"}
_DOMAIN = re.compile(r"^[\w-]+(\.[\w-]+)+(/\S*)?$")


def open_in_default_browser(url: str) -> bool:
    try:
        return bool(webbrowser.open_new_tab(url))
    except Exception:
        return False


@tool("open_url", desc="open a web page", args='{"url": "https://...", "new_tab": false, "browser": "edge"}')
def open_url(ctx, url, new_tab=False, browser="", label=None):
    if not str(url).startswith(("http://", "https://")):
        return Result(False, "I can only open http or https addresses.")
    host = host_of(url)
    if browser and browser != "chrome":
        if ctx.win.open_in_browser(browser, url):
            return Result(True, label or f"Opening {host} in {browser.title()}.")
        return Result(False, f"I couldn't find {browser.title()} on this laptop.")
    try:
        res = ctx.browser({"type": "open_url", "url": url, "new_tab": bool(new_tab)}, retry=True)
    except ExtensionUnavailable:
        if open_in_default_browser(url):
            return Result(True, (label or f"Opening {host}.") + " (The Cozy extension isn't connected, so I used a new tab.)")
        return Result(False, f"I couldn't open {host}.")
    if res.get("ok"):
        return Result(True, label or f"Opening {host}.")
    return Result(False, f"I couldn't open {host}: {res.get('detail') or 'the browser did not respond'}")


@tool("open_target", desc="open a website, app or folder by name", args='{"name": "github|notepad|downloads", "new_tab": false}')
def open_target(ctx, name, new_tab=False, browser=""):
    clean = re.sub(r"\s+(?:in|from)\s+file explorer$", "", name.strip(), flags=re.I).rstrip(".!?")
    clean = re.sub(r"^(?:the|my)\s+", "", clean, flags=re.I)
    key = clean.lower()
    custom = ctx.store.kv_get("site:" + key)
    url = custom or SITES.get(key)
    if url:
        return open_url(ctx, url, new_tab=new_tab, browser=browser, label=f"Opening {SITE_LABELS.get(key, clean.title())}.")
    if _DOMAIN.match(clean):
        return open_url(ctx, "https://" + clean, new_tab=new_tab, browser=browser, label=f"Opening {clean}.")
    explicit = bool(re.search(r"\s+(?:folder|directory)$", clean, re.I))
    if explicit or canonical_folder(clean):
        folder_name = re.sub(r"\s+(?:folder|directory)$", "", clean, flags=re.I)
        path = ctx.win.folder(folder_name)
        if path is None:
            return Result(False, f"I couldn't find the {folder_name} folder. Tell me its location.")
        ctx.win.open_path(path)
        return Result(True, f"Opening {folder_name.title()} folder.")
    app = ctx.win.find_app(clean)
    if app is not None:
        ctx.win.launch(app)
        return Result(True, f"Opening {app.stem}.")
    res = open_url(ctx, f"https://www.google.com/search?q={q(clean)}")
    if res.ok:
        return Result(True, f"I couldn't find an app called '{clean}', so I searched Google for it.")
    return Result(False, f"I couldn't find or open '{clean}'.")


@tool("web_search", desc="search Google", args='{"query": str}')
def web_search(ctx, query, browser=""):
    query = query.strip().strip(".!?")
    return open_url(ctx, f"https://www.google.com/search?q={q(query)}", browser=browser, label=f"Searching Google for {query}.")


@tool("youtube_search", desc="show YouTube search results without playing", args='{"query": str}')
def youtube_search(ctx, query):
    query = query.strip().strip(".!?")
    return open_url(ctx, f"https://www.youtube.com/results?search_query={q(query)}", label=f"Searching YouTube for {query}.")


# ----------------------------------------------------------------------------- tabs
def _tab(ctx, op, **extra):
    try:
        res = ctx.browser({"type": "tab", "op": op, **extra}, retry=(op in ("list", "switch")))
    except ExtensionUnavailable:
        raise
    return res


def _tab_result(res, ok_message):
    return Result(True, ok_message, data=res.get("data")) if res.get("ok") else Result(False, res.get("detail") or "That didn't work.")


@tool("close_tab", desc="close the current browser tab")
def close_tab(ctx):
    return _tab_result(_tab(ctx, "close"), "Closed the tab.")


@tool("browser_back", desc="go back one page in the browser")
def browser_back(ctx):
    return _tab_result(_tab(ctx, "back"), "Going back.")


@tool("browser_forward", desc="go forward one page in the browser")
def browser_forward(ctx):
    return _tab_result(_tab(ctx, "forward"), "Going forward.")


@tool("scroll", desc="scroll the current page", args='{"direction": "down|up|top|bottom"}')
def scroll(ctx, direction="down"):
    return _tab_result(_tab(ctx, "scroll", direction=direction), f"Scrolling {direction}.")


@tool("switch_tab", desc="switch to an open tab by name", args='{"query": "github"}')
def switch_tab(ctx, query):
    res = _tab(ctx, "switch", query=query)
    return _tab_result(res, f"Switched to {query}.")


@tool("list_tabs", desc="list open tabs")
def list_tabs(ctx):
    res = _tab(ctx, "list")
    if not res.get("ok"):
        return Result(False, res.get("detail") or "I couldn't read the tab list.")
    tabs = res.get("data") or []
    titles = [t.get("title") or t.get("url", "") for t in tabs][:8]
    return Result(True, f"You have {len(tabs)} tabs open: " + "; ".join(titles) + ("." if titles else ""), data=tabs)


# ----------------------------------------------------------------------------- read / summarise
_STOP = set("the a an and or of to in is are was were be been it this that for on with as at by from but not you your "
            "we they their he she his her i have has had will would can could do does did so if than then there which".split())


def extractive_summary(text: str, sentences: int = 4) -> str:
    """Pick the highest-scoring sentences (word-frequency) and keep their original order."""
    parts = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if 40 <= len(s.strip()) <= 400]
    if len(parts) <= sentences:
        return " ".join(parts)
    freq = Counter(w for w in re.findall(r"[a-z']{3,}", text.lower()) if w not in _STOP)
    scored = sorted(range(len(parts)), key=lambda i: -sum(freq[w] for w in re.findall(r"[a-z']{3,}", parts[i].lower())) / (len(parts[i]) ** 0.5))
    keep = sorted(scored[:sentences])
    return " ".join(parts[i] for i in keep)


@tool("page_summary", desc="summarise or read aloud the current page", args='{"mode": "summary|read"}')
def page_summary(ctx, mode="summary"):
    res = ctx.browser({"type": "page_text"}, retry=True)
    if not res.get("ok"):
        return Result(False, res.get("detail") or "I couldn't read this page.")
    data = res.get("data") or {}
    text, title = (data.get("text") or "").strip(), data.get("title") or "this page"
    if len(text) < 80:
        return Result(False, "There isn't enough readable text on this page.")
    if mode == "read":
        return Result(True, f"Reading {title}.", speak=text[:1500])
    summary = None
    if ctx.llm is not None and ctx.llm.available():
        try:
            summary = ctx.llm.chat([{"role": "system", "content": "Summarise the page in 3 short sentences."},
                                    {"role": "user", "content": text[:6000]}]).strip()
        except Exception:
            summary = None
    summary = summary or extractive_summary(text)
    return Result(True, f"{title}: {summary}", speak=summary)


# ----------------------------------------------------------------------------- rules
def _browser(v: str) -> str:
    return BROWSERS[v.lower()]


rule(r"^(?:open|go to|launch|start|take me to)\s+(?P<name>.+?)\s+in\s+(?:a\s+)?new\s+tab$", "open_target", static={"new_tab": True}, priority=25)
rule(r"^(?:open|go to|launch|start)\s+(?P<name>.+?)\s+(?:in|on|using|with)\s+(?:the\s+)?"
     r"(?P<browser>google chrome|chrome|microsoft edge|edge|mozilla firefox|firefox|brave)(?:\s+browser)?$",
     "open_target", conv={"browser": _browser}, priority=25)
rule(r"^open\s+url\s+(?P<url>https?://\S+)$", "open_url", priority=20)
rule(r"^(?:search|google)\s+(?:for\s+)?(?P<query>.+?)\s+(?:on|in)\s+google$", "web_search", priority=30)
rule(r"^(?:search|find)\s+(?P<query>.+?)\s+(?:on|in)\s+youtube(?:\.com)?$", "youtube_search", priority=30)
rule(r"^youtube\s+search\s+(?P<query>.+)$", "youtube_search", priority=30)
rule(r"^close\s+(?:this|the current|current)\s+tab$", "close_tab", priority=30)
rule(r"^(?:go|navigate)\s+back$|^back$", "browser_back", priority=30)
rule(r"^go\s+forward$", "browser_forward", priority=30)
rule(r"^scroll\s+(?:to\s+the\s+)?(?P<direction>down|up|top|bottom)$", "scroll", priority=30)
rule(r"^switch\s+to\s+(?:the\s+)?(?P<query>.+?)\s+tab$", "switch_tab", priority=30)
rule(r"^switch\s+to\s+(?P<query>youtube|github|gmail|whatsapp|chatgpt|google|leetcode)$", "switch_tab", priority=30)
rule(r"^(?:list|show|what are)\s+(?:my\s+)?(?:open\s+)?tabs$", "list_tabs", priority=30)
rule(r"^(?:summari[sz]e|tl;?dr)(?:\s+(?:this|the|current))?(?:\s+(?:page|article|tab|website))?$", "page_summary", priority=30)
rule(r"^what(?:'s| is)\s+this\s+page\s+about$", "page_summary", priority=30)
rule(r"^read\s+(?:this|the)\s+(?:page|article)(?:\s+(?:out|aloud|to me))?$|^read\s+(?:it\s+)?(?:out|aloud)$",
     "page_summary", static={"mode": "read"}, priority=30)
rule(r"^(?:search|google)\s+(?:for\s+)?(?P<query>.+)$", "web_search", priority=85)
rule(r"^(?:open|launch|start|go to)\s+(?P<name>.+)$", "open_target", priority=88)
