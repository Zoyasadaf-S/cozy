# Cozy 2.0 — your personal voice assistant

Say or type what you want. Cozy runs on your **Windows laptop**, controls **Chrome** through a small extension, and gives you a
**phone app (PWA)** as a remote that is built to barely touch your phone's battery.

```
 phone (PWA, push-to-talk) ──Wi-Fi──┐
 laptop browser (same PWA) ─────────┼──► Cozy server (FastAPI, 127.0.0.1:8000) ──► Windows (volume, brightness, apps, git…)
                                    │              ▲  one long-poll request
                                    │              └──────────────── Chrome extension ──► your tabs, YouTube, pages
                                    └── optional local LLM (Ollama) for phrases the rules don't know
```

## Quick start (Windows)
1. Install **Python 3.10+** (python.org, tick "Add to PATH"). Optional: Ollama for free-form phrases.
2. Double-click **`start_cozy.bat`**. First run creates `.venv` and installs the (tiny) requirements, then starts Cozy.
3. Chrome → `chrome://extensions` → Developer mode → **Load unpacked** → choose the `extension` folder. The version shown must be **2.0.0**.
   *(Optional, once: `python scripts/make_extension_key.py` gives the extension a permanent ID.)*
4. Open http://127.0.0.1:8000 and try: **play lofi**, **set volume to 40**, **remind me to stretch in 20 minutes**.
5. **Phone:** run **`start_phone.bat`** instead. It prints a link with a pairing token — open it once on your phone (same Wi-Fi),
   then use the browser menu → *Add to Home screen*. Allow Python through Windows Firewall on **Private** networks only.
6. Optional: `install_startup.bat` (start with Windows), `pip install pystray pillow` then `python -m cozy --tray` (tray icon).

## Why it is gentle on battery
**Phone** — no polling and no background work. The app fetches only when you open it or tap something; one tiny live-notification
connection exists **only while the app is on screen** (closed when you switch away, a 45 s keep-alive at most) and is switched off
automatically below 20 % battery (Auto battery saver). The microphone is push-to-talk (single phrase, then off — no always-listening
wake word). Pure-black background (saves power on OLED), system fonts, no images/animations/external requests, one cached
~19 KB page served from a service worker, gzip on everything.
**Laptop** — the extension keeps a single long-poll request open instead of polling every few seconds; the scheduler is one thread
that sleeps until the next reminder (or indefinitely if there is none); SQLite runs in WAL mode; no file watchers; the file index
is built only when you ask. Measured on a live server with the extension connected and a phone feed open: **0.03 CPU-seconds
per 20 s idle** (4 threads, ~49 MB).

## What you can say
| Area | Examples |
|---|---|
| Open & search | open youtube · open github in a new tab · open notepad · go to downloads · search cats on google · open github in edge |
| YouTube | play tenu leke mai jaunga · pause · next · skip ad · forward that video to 1 min · rewind 15 seconds · set video volume to 25 · play at 1.5x · mute the video · what song is this · lyrics · pause in 20 minutes · play my usual music · add X to queue / play next in queue · play X on spotify |
| Browser | close this tab · go back · scroll down · switch to github tab · list my tabs · summarize this page · read this page aloud |
| Laptop | set volume to 40 · louder · mute · brightness to 60 · lock · sleep / shut down / restart (asks first) · turn off wifi (asks first) · snap window left · take a screenshot · switch apps · copy that |
| Reminders | remind me to call mom in 20 minutes · timer for 10 minutes · alarm at 6 am · remind me every day at 8 pm to solve a leetcode problem · what's on my schedule |
| Developer | add project chalkmind at C:\code\chalkmind · open my chalkmind project · git status · what changed today · commit with message fix bug (asks first) · push (asks first) · run the tests · open issues / my prs · ci status · save snippet NAME: text · paste my NAME · random leetcode problem |
| Info | what time is it · 12 percent of 450 · 5 km to miles · 100 usd to inr · define serendipity · weather in Pune · latest news · translate hello to hindi · good morning (daily briefing) |
| Files | find my resume pdf · open my resume file |
| You teach it | teach work mode: open github then play lofi · schedule work mode every day at 9 am · remember my email is … · copy my email · create profile study · switch to study profile · set morning volume to 30 |
| Safety & control | yes / no · undo that · again · what did you do today · status · turn on offline mode · help |

Multi-step: "set volume to 30 and then play lofi". Hinglish: "YouTube kholo", "Isko pause karo", "Volume thoda kam karo".

## Safety model
* The server listens on **127.0.0.1** only. `--phone` opens it to your LAN **with a secret token**; the extension endpoints and
  anything that sets shell commands stay laptop-only. Wi-Fi traffic is **not encrypted** — don't use phone mode on public networks.
* Every API call needs a custom header and a known `Host`, which blocks other websites from driving your laptop (CSRF / DNS rebinding).
* Risky actions (shutdown, restart, sleep, Wi-Fi, git commit/push) always ask "are you sure?" and **never** run from a schedule.
* Passwords, PINs, card numbers, API keys etc. are refused by memory, snippets, clipboard helpers and are redacted from the log.
* The extension can touch only YouTube and your tab list by default; reading/scrolling arbitrary pages is an **optional permission**
  you grant (and can revoke) on the extension's options page.
* Offline mode blocks every internet lookup (weather, news, currency, dictionary) — local features keep working.

## Make it yours
* **Routines** — "teach NAME: step then step" or the Teach tab. **Facts / snippets / projects** — Teach tab (project run/test commands
  can only be edited from the laptop).
* **Plugins** — drop a `.py` file in `plugins/` (see `plugins/example_dice.py`: say "roll 2 dice"). Plugins use the same
  `@tool` and `rule()` as the built-ins; a broken plugin is skipped, not fatal.
* **Optional local AI** — install Ollama and `ollama pull llama3.2:3b`. It only *chooses* a tool for phrases no rule understands
  (and summarises/translates); Cozy performs and validates every action. Environment: `COZY_OLLAMA_MODEL`, `COZY_OLLAMA_URL`.
* Environment variables: `COZY_PORT`, `COZY_DATA`, `COZY_LAN=1`, `COZY_ALLOWED_HOSTS`.

## Tests
```
pip install -r requirements-dev.txt
python -m pytest tests -q                       # backend, HTTP, real-socket end-to-end (160 tests)
cd extension && node --test tests/service_worker.test.js     # extension with a mocked Chrome (27 tests)
```

## Honest limits (read this)
**Not included:** Bluetooth and Do-Not-Disturb toggles (no dependable user-mode Windows API), calendar integration (needs OAuth),
"skip intro"/chapter jumps (YouTube's chapter markup is too fragile), an always-listening wake word and fully local speech-to-text.
Voice input uses your browser's Web Speech API — **Chrome sends that audio to Google**; typing never leaves your network.
Spotify / YouTube Music: Cozy opens the search results, but can't press play there.
**Not verifiable without your machines** (tested only with fakes): the real Windows calls (pycaw volume, WMI brightness, netsh, hotkeys,
balloon notifications, Start-menu app search), live YouTube page markup (the extension tries several selectors and, on failure, says what
the tab was showing), live weather/news/currency/dictionary endpoints (`cozy/tools/info.py` constants), the tray icon, PyInstaller
(`build_exe.bat` is a template), Chrome keeping the extension's worker alive during the long-poll (a 30 s alarm restarts it if not),
and the phone UI/speech APIs on your particular browser.

## Troubleshooting
* "I can't reach the Chrome extension" → check `chrome://extensions` shows version **2.0.0**, click Reload, then say **status**.
* Phone says "Pair this device first" → open the full link printed by `start_phone.bat` (it contains `?t=…`).
* "Tabs cannot be edited right now" → you were dragging a tab; Cozy retries briefly, never opens a duplicate, then tells you.
* `play` says YouTube didn't expose a result → the message includes what the tab was showing (e.g. a consent page); accept it once.
