"""Developer workflows: open a project, git by voice, run tests, GitHub shortcuts, snippets, LeetCode."""
from __future__ import annotations

import difflib
import json
import re
import shutil
from pathlib import Path
from typing import Optional, Tuple

from .. import guard
from ..registry import Result, rule, tool
from .web import open_url

_PRIMARY_EDITOR = "code"


# ----------------------------------------------------------------------------- project lookup
def find_project(ctx, name: Optional[str] = None) -> Optional[Tuple[str, dict]]:
    projects = ctx.store.kv_all("project:")
    if not projects:
        return None
    if name:
        key = re.sub(r"^(?:my|the)\s+", "", name.strip().lower())
        if key in projects:
            return key, projects[key]
        hit = [k for k in projects if key in k or k in key]
        if hit:
            return hit[0], projects[hit[0]]
        close = difflib.get_close_matches(key, list(projects), n=1, cutoff=0.7)
        return (close[0], projects[close[0]]) if close else None
    current = ctx.setting("current_project")
    if current in projects:
        return current, projects[current]
    if len(projects) == 1:                                   # only one project: obviously that one
        key = next(iter(projects))
        return key, projects[key]
    return None


def _need_project(ctx, name=None):
    found = find_project(ctx, name)
    if found:
        return found, None
    if name:
        return None, Result(False, f"I don't have a project called {name.strip()}. Say 'add project NAME at C:\\path'.")
    return None, Result(False, "Which project? Open one first, or say 'add project NAME at C:\\path'.")


def _git(ctx, path: str, *args, timeout: float = 30.0):
    try:
        return ctx.win.run_cmd(["git", "-C", path, *args], timeout=timeout)
    except FileNotFoundError:
        raise RuntimeError("Git isn't installed or isn't on PATH.")


@tool("project_add", desc="register a project folder", args='{"name": "chalkmind", "path": "C:\\\\code\\\\chalkmind"}')
def project_add(ctx, name, path):
    p = Path(path.strip().strip('"').strip("'")).expanduser()
    if not p.is_dir():
        return Result(False, f"I can't find the folder {p}.")
    key = name.strip().lower()
    ctx.store.kv_set("project:" + key, {"path": str(p), "editor": _PRIMARY_EDITOR, "run": None, "test": None, "url": None})
    if not ctx.setting("current_project"):
        ctx.set_setting("current_project", key)
    return Result(True, f"Added the project {name.strip()}. Set its run command, test command and URL in the Cozy app to use 'open my {key} project'.")


@tool("project_open", desc="open a project in the editor, start its dev server and open its URL", args='{"name": "chalkmind"}')
def project_open(ctx, name):
    found, err = _need_project(ctx, name)
    if err:
        return err
    key, proj = found
    ctx.set_setting("current_project", key)
    did = []
    editor = shutil.which(proj.get("editor") or _PRIMARY_EDITOR)
    if editor:
        ctx.win.spawn([editor, proj["path"]])
        did.append("opened the editor")
    elif proj.get("editor"):
        did.append(f"couldn't find {proj['editor']} on PATH")
    running = ctx.dev_servers.get(key)
    if proj.get("run") and (running is None or running.poll() is not None):
        ctx.dev_servers[key] = ctx.win.spawn(proj["run"], cwd=proj["path"], shell=True)
        did.append("started the dev server")
    if proj.get("url"):
        ctx.scheduler.add(ctx.clock() + 5, "Open dev URL", kind="command", command=f"open url {proj['url']}")
        did.append("opening the site in a few seconds")
    return Result(True, f"{key.title()}: " + (", ".join(did) if did else "selected as your current project") + ".")


@tool("project_stop", desc="stop the project's dev server", args='{"name": str}')
def project_stop(ctx, name=""):
    found, err = _need_project(ctx, name or None)
    if err:
        return err
    key, _ = found
    proc = ctx.dev_servers.pop(key, None)
    if proc is None or proc.poll() is not None:
        return Result(False, f"No dev server of mine is running for {key}.")
    proc.terminate()
    return Result(True, f"Stopped the {key} dev server.")


# ----------------------------------------------------------------------------- git
@tool("git_status", desc="summarise git status for the current project", args='{"name": str}')
def git_status(ctx, name=""):
    found, err = _need_project(ctx, name or None)
    if err:
        return err
    key, proj = found
    out = _git(ctx, proj["path"], "status", "--short", "--branch")
    if out.returncode != 0:
        return Result(False, f"{key} isn't a git repository.")
    lines = [l for l in out.stdout.splitlines() if l]
    branch = lines[0][3:].split("...")[0] if lines else "unknown"
    changes = lines[1:]
    untracked = sum(1 for l in changes if l.startswith("??"))
    modified = len(changes) - untracked
    if not changes:
        return Result(True, f"{key.title()} is clean on branch {branch}.")
    return Result(True, f"{key.title()} on {branch}: {modified} changed and {untracked} new files.")


@tool("git_changes_today", desc="what changed in git today", args='{"name": str}')
def git_changes_today(ctx, name=""):
    found, err = _need_project(ctx, name or None)
    if err:
        return err
    key, proj = found
    log = _git(ctx, proj["path"], "log", "--since=midnight", "--oneline")
    if log.returncode != 0:
        return Result(False, f"{key} isn't a git repository.")
    commits = [l for l in log.stdout.splitlines() if l]
    status = _git(ctx, proj["path"], "status", "--short")
    pending = len([l for l in status.stdout.splitlines() if l])
    text = f"Today in {key}: {len(commits)} commit{'s' if len(commits) != 1 else ''}"
    if commits:
        text += f", latest: {commits[0].split(' ', 1)[-1]}"
    return Result(True, text + f". {pending} uncommitted change{'s' if pending != 1 else ''}.")


@tool("git_commit", desc="commit all changes with that message", risky=True, args='{"message": str}')
def git_commit(ctx, message, name=""):
    found, err = _need_project(ctx, name or None)
    if err:
        return err
    key, proj = found
    msg = message.strip().strip('"')
    if not msg:
        return Result(False, "I need a commit message.")
    add = _git(ctx, proj["path"], "add", "-A")
    out = _git(ctx, proj["path"], "commit", "-m", msg)
    if add.returncode != 0 or out.returncode != 0:
        detail = (out.stdout or out.stderr or add.stderr or "").strip().splitlines()
        return Result(False, "Git said: " + (detail[0] if detail else "the commit failed") + ".")
    return Result(True, f"Committed in {key}: {msg}.")


@tool("git_push", desc="push the current branch to GitHub", risky=True)
def git_push(ctx, name=""):
    found, err = _need_project(ctx, name or None)
    if err:
        return err
    key, proj = found
    out = _git(ctx, proj["path"], "push", timeout=90)
    if out.returncode != 0:
        detail = (out.stderr or out.stdout or "").strip().splitlines()
        return Result(False, "Push failed: " + (detail[-1] if detail else "unknown error") + ".")
    return Result(True, f"Pushed {key}.")


def _repo_url(ctx, proj) -> Optional[str]:
    out = _git(ctx, proj["path"], "remote", "get-url", "origin")
    if out.returncode != 0:
        return None
    url = out.stdout.strip()
    m = re.match(r"^(?:git@|ssh://git@|https?://)(?:[\w.-]*@)?([\w.-]+)[:/](.+?)(?:\.git)?/?$", url)
    return f"https://{m.group(1)}/{m.group(2)}" if m else None


@tool("github_open", desc="open GitHub pull requests, issues, actions or the repo", args='{"target": "prs|issues|actions|repo"}')
def github_open(ctx, target):
    target = target.lower()
    if target in ("prs", "pull requests", "pull request"):
        return open_url(ctx, "https://github.com/pulls", label="Opening your pull requests.")
    found, err = _need_project(ctx)
    if err:
        return err
    base = _repo_url(ctx, found[1])
    if not base:
        return Result(False, "This project has no GitHub remote.")
    suffix = {"issues": "/issues", "actions": "/actions", "repo": ""}.get(target, "")
    return open_url(ctx, base + suffix, label=f"Opening {found[0]} {target} on GitHub.")


@tool("ci_status", desc="latest GitHub Actions result for the current project")
def ci_status(ctx):
    found, err = _need_project(ctx)
    if err:
        return err
    key, proj = found
    try:
        out = ctx.win.run_cmd(["gh", "run", "list", "--limit", "1", "--json", "status,conclusion,name,headBranch"], cwd=proj["path"], timeout=30)
    except FileNotFoundError:
        return github_open(ctx, "actions")
    if out.returncode != 0:
        return github_open(ctx, "actions")
    try:
        run = json.loads(out.stdout)[0]
    except (ValueError, IndexError):
        return Result(False, "I couldn't find any CI runs for this project.")
    state = run.get("conclusion") or run.get("status")
    return Result(True, f"The latest CI run for {key} ({run.get('name')}) is {state}.")


# ----------------------------------------------------------------------------- tests
def _detect_test_command(path: str) -> Optional[str]:
    p = Path(path)
    if (p / "pytest.ini").exists() or (p / "tests").is_dir() or (p / "pyproject.toml").exists():
        return "python -m pytest -q"
    if (p / "package.json").exists():
        return "npm test --silent"
    return None


def summarise_test_output(text: str, code: int) -> str:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    for line in reversed(lines):
        if re.search(r"\b(passed|failed|error|errors)\b", line, re.I) and re.search(r"\d", line):
            return line.strip("= ")
        if line.startswith("Tests:"):
            return line
    return lines[-1] if lines else ("no output")


@tool("run_tests", desc="run the project's tests and read the result", args='{"name": str}')
def run_tests(ctx, name=""):
    found, err = _need_project(ctx, name or None)
    if err:
        return err
    key, proj = found
    cmd = proj.get("test") or _detect_test_command(proj["path"])
    if not cmd:
        return Result(False, f"I don't know how to run {key}'s tests. Set a test command for it in the Cozy app.")
    try:
        out = ctx.win.run_cmd(cmd, cwd=proj["path"], timeout=300, shell=True)
    except Exception as exc:
        if exc.__class__.__name__ == "TimeoutExpired":
            return Result(False, f"The {key} tests took longer than 5 minutes, so I stopped them.")
        raise
    summary = summarise_test_output((out.stdout or "") + "\n" + (out.stderr or ""), out.returncode)
    return Result(out.returncode == 0, f"{key.title()} tests {'passed' if out.returncode == 0 else 'failed'}: {summary}.")


# ----------------------------------------------------------------------------- snippets
@tool("snippet_save", desc="save a code or text snippet", args='{"name": "fastapi boilerplate", "text": str}')
def snippet_save(ctx, name, text):
    if guard.is_sensitive(name, text):
        return Result(False, guard.REFUSAL)
    ctx.store.kv_set("snippet:" + name.strip().lower(), text)
    return Result(True, f"Saved the snippet {name.strip()}.")


@tool("snippet_paste", desc="paste a saved snippet or fact into the active window", args='{"name": str}')
def snippet_paste(ctx, name):
    key = re.sub(r"^(?:my|the)\s+", "", name.strip().lower())
    key = re.sub(r"\s+snippet$", "", key)
    text = ctx.store.kv_get("snippet:" + key)
    if text is None:
        snippets = ctx.store.kv_all("snippet:")
        close = difflib.get_close_matches(key, list(snippets), n=1, cutoff=0.6)
        text = snippets[close[0]] if close else None
    if text is None:
        fact = ctx.store.kv_get("fact:" + key)
        if fact is not None and guard.is_sensitive(key, str(fact)):
            return Result(False, guard.REFUSAL)
        text = fact
    if text is None:
        return Result(False, f"I don't have a snippet or saved fact called {key}.")
    ctx.win.clipboard_set(str(text))
    try:
        ctx.win.hotkey("ctrl", "v")
        return Result(True, f"Pasted {key}.")
    except Exception:
        return Result(True, f"Copied {key}; press Ctrl+V to paste it.")


@tool("snippet_list", desc="list saved snippets")
def snippet_list(ctx):
    names = list(ctx.store.kv_all("snippet:"))
    return Result(True, "Your snippets: " + ", ".join(names) + "." if names else "You have no snippets yet.", data=names)


# ----------------------------------------------------------------------------- DSA practice
@tool("leetcode_random", desc="open a random LeetCode problem", args='{"difficulty": "easy|medium|hard"}')
def leetcode_random(ctx, difficulty=""):
    if difficulty:
        return open_url(ctx, f"https://leetcode.com/problemset/?difficulty={difficulty.upper()}", label=f"Opening LeetCode {difficulty} problems. Pick one from the list.")
    return open_url(ctx, "https://leetcode.com/problems/random-one-question/all", label="Opening a random LeetCode problem.")


rule(r"^(?:add|register)\s+project\s+(?P<name>.+?)\s+(?:at|in|from)\s+(?P<path>.+)$", "project_add", priority=20)
rule(r"^(?:open|start|launch)\s+(?:my\s+|the\s+)?(?P<name>.+?)\s+project$", "project_open", priority=20)
rule(r"^stop\s+(?:my\s+|the\s+)?(?:(?P<name>.+?)\s+)?(?:project|dev server)$", "project_stop", priority=20)
rule(r"^git\s+status$|^(?:what(?:'s| is)\s+)?(?:the\s+)?(?:git|repo)\s+status$", "git_status", priority=20)
rule(r"^what\s+changed\s+today$|^(?:what did i|what have i)\s+(?:change|commit)\s*(?:today)?$", "git_changes_today", priority=20)
rule(r"^commit(?:\s+(?:all|everything|changes))?\s+(?:with\s+(?:the\s+)?message\s+|with\s+)(?P<message>.+)$", "git_commit", priority=20)
rule(r"^git\s+push$|^push(?:\s+(?:my\s+)?(?:code|changes|commits|to github))?$", "git_push", priority=20)
rule(r"^(?:open|show)\s+(?:my\s+)?(?:github\s+)?(?P<target>pull requests|prs|issues|actions)$", "github_open", priority=20)
rule(r"^(?:open|show)\s+(?:this\s+|the\s+|my\s+)?repo(?:\s+on github)?$", "github_open", static={"target": "repo"}, priority=20)
rule(r"^(?:ci|build)\s+status$|^did\s+(?:the\s+)?(?:ci|build)\s+pass$|^(?:how(?:'s| is)\s+)?(?:the\s+)?ci$", "ci_status", priority=20)
rule(r"^run\s+(?:the\s+|my\s+)?tests$|^run\s+tests(?:\s+for\s+(?P<name>.+))?$", "run_tests", priority=20)
rule(r"^(?:save|add)\s+snippet\s+(?P<name>[\w -]+?)\s*[:=]\s*(?P<text>.+)$", "snippet_save", priority=20)
rule(r"^(?:paste|insert)\s+(?:my\s+|the\s+)?(?P<name>.+)$", "snippet_paste", priority=40)
rule(r"^(?:list|show)\s+(?:my\s+)?snippets$", "snippet_list", priority=20)
rule(r"^(?:open\s+|give me\s+|pick\s+)?(?:a\s+|another\s+)?random\s+(?:leetcode|dsa)(?:\s+(?:problem|question))?(?:\s+(?P<difficulty>easy|medium|hard))?$",
     "leetcode_random", conv={"difficulty": str.lower}, priority=20)
rule(r"^(?:open\s+|give me\s+|pick\s+)?(?:a\s+|another\s+)?random\s+(?:(?P<difficulty>easy|medium|hard)\s+)?(?:leetcode|dsa)(?:\s+(?:problem|question))?$",
     "leetcode_random", conv={"difficulty": str.lower}, priority=20)
rule(r"^(?:open\s+|give me\s+|pick\s+)?(?:a\s+|another\s+)?random\s+(?:leetcode|dsa)\s+(?P<difficulty>easy|medium|hard)\s+(?:problem|question)$",
     "leetcode_random", conv={"difficulty": str.lower}, priority=20)
