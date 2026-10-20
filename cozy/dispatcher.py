"""Command text -> tool calls -> reply. Handles approvals ("are you sure?"), undo, repeat,
macros, multi-step commands, offline mode and low-confidence speech."""
from __future__ import annotations

import logging
import re
import uuid
from typing import Optional

from . import router
from .bridge import ExtensionUnavailable
from .context import Ctx
from .llm import LLMUnavailable
from .netio import OfflineError
from .parser import normalize, split_steps
from .registry import TOOLS, Result, match_rules
from .winops import OpFailed, Unsupported

log = logging.getLogger("cozy.dispatcher")

YES = re.compile(r"^(?:yes|yeah|yep|yup|sure|ok|okay|do it|go ahead|confirm|haan|ha|han)\W*$", re.I)
NO = re.compile(r"^(?:no|nope|nah|cancel|stop|don'?t|never ?mind|nahi|mat karo)\W*$", re.I)
AGAIN = re.compile(r"^(?:again|repeat(?: that)?|do (?:that|it) again|same again|play it again|one more time)$", re.I)
META = {"undo", "copy_that", "status", "what_did_you_do", "chat"}
LOW_CONFIDENCE = 0.55
APPROVAL_TTL = 60.0


def _reply(status: str, message: str, **extra) -> dict:
    return {"status": status, "message": message, **extra}


class Dispatcher:
    def __init__(self, ctx: Ctx):
        self.ctx = ctx
        ctx.dispatch = self.handle
        ctx.undo_fn = self.undo

    # ------------------------------------------------------------------ entry point
    def handle(self, text: str, *, confidence: Optional[float] = None, dry_run: bool = False,
               source: str = "user") -> dict:
        ctx = self.ctx
        profile = ctx.profile()
        wake = tuple(x for x in ("ok cozy", "okay cozy", profile.get("wake")) if x)
        text = normalize(text, wake)
        if not text:
            return _reply("done", "Yes, I'm listening.")

        pending = self._latest_pending()
        if pending:
            if YES.match(text):
                return self._resolve(pending, True)
            if NO.match(text):
                return self._resolve(pending, False)
            ctx.pending.clear()                              # any other command abandons the question

        if confidence is not None and confidence < LOW_CONFIDENCE and source == "user" and not dry_run:
            aid = uuid.uuid4().hex
            ctx.pending[aid] = {"kind": "text", "text": text, "expires": ctx.clock() + APPROVAL_TTL}
            msg = f'I\'m not sure I heard that right: "{text}". Should I go ahead?'
            return _reply("confirm", msg, speak=msg, approval_id=aid)

        macro = ctx.store.kv_get("macro:" + text.lower())
        steps = macro if isinstance(macro, list) and macro else split_steps(text)
        if len(steps) == 1:
            return self._run_step(steps[0], source, dry_run)
        results = []
        for step in steps:
            r = self._run_step(step, source, dry_run)
            results.append(r)
            if r["status"] in ("approval_required", "confirm"):
                break
        ok = all(r["status"] in ("done", "dry_run") for r in results)
        out = _reply("done" if ok else "error" if results[-1]["status"] == "error" else results[-1]["status"],
                     " ".join(r["message"] for r in results if r.get("message")), steps=results)
        if results[-1].get("approval_id"):
            out["approval_id"] = results[-1]["approval_id"]
        ctx.last_message = out["message"]
        return out

    # ------------------------------------------------------------------ approvals
    def _latest_pending(self) -> Optional[tuple]:
        ctx = self.ctx
        now = ctx.clock()
        for aid in [k for k, v in ctx.pending.items() if v["expires"] < now]:
            ctx.pending.pop(aid, None)
        if not ctx.pending:
            return None
        aid = list(ctx.pending)[-1]
        return aid, ctx.pending[aid]

    def approve(self, aid: str, approved: bool) -> dict:
        item = self.ctx.pending.get(aid)
        if item is None or item["expires"] < self.ctx.clock():
            self.ctx.pending.pop(aid, None)
            return _reply("error", "That request expired. Please ask again.")
        return self._resolve((aid, item), approved)

    def _resolve(self, pending: tuple, approved: bool) -> dict:
        aid, item = pending
        self.ctx.pending.pop(aid, None)
        if not approved:
            return _reply("done", "Okay, cancelled.")
        if item.get("kind") == "text":
            return self.handle(item["text"])
        return self._execute(item["call"], item["text"], "user", False, approved=True)

    # ------------------------------------------------------------------ one step
    def _run_step(self, step: str, source: str, dry_run: bool) -> dict:
        ctx = self.ctx
        call = None
        if AGAIN.match(step) and ctx.last_call:
            call = dict(ctx.last_call)
        else:
            found = match_rules(step)
            if found:
                call = {"tool": found[0], "args": found[1]}
            elif not dry_run:
                call = router.route(ctx, step)
        if call is None:
            msg = "I don't know how to do that yet."
            ctx.store.log_action(step, "unknown", False, msg)
            return _reply("unknown", msg)
        return self._execute(call, step, source, dry_run)

    def _execute(self, call: dict, original: str, source: str, dry_run: bool, approved: bool = False) -> dict:
        ctx = self.ctx
        name = call["tool"]
        if name == "chat":
            msg = call["args"].get("reply", "")
            ctx.last_message = msg
            return _reply("done", msg, tool="chat", speak=msg)
        tool = TOOLS.get(name)
        if tool is None:
            return _reply("unknown", "I don't know how to do that yet.")
        if dry_run:
            return _reply("dry_run", f"Would run {name}.", tool=name, args=call["args"])
        if tool.risky and not approved:
            if source == "scheduled":
                return _reply("error", f"I won't run '{tool.desc}' automatically; ask me directly.")
            aid = uuid.uuid4().hex
            ctx.pending[aid] = {"kind": "call", "call": call, "text": original, "expires": ctx.clock() + APPROVAL_TTL}
            msg = f"Are you sure you want me to {tool.desc.lower().rstrip('.')}? Say yes or no."
            return _reply("approval_required", msg, speak=msg, approval_id=aid, tool=name)
        if tool.net and ctx.offline:
            msg = "Offline mode is on, so I can't look that up. Say 'turn off offline mode' to allow it."
            return _reply("error", msg, tool=name)
        try:
            res = tool.fn(ctx, **call["args"])
            if not isinstance(res, Result):
                res = Result(True, str(res) if res else "Done.")
        except OfflineError:
            res = Result(False, "Offline mode is on, so I can't look that up.")
        except ExtensionUnavailable:
            res = Result(False, "I can't reach the Chrome extension. Open Chrome and check that Cozy Tab Controller is enabled.")
        except (Unsupported, OpFailed, LLMUnavailable) as exc:
            res = Result(False, str(exc) or "That didn't work.")
        except TypeError:
            log.exception("bad arguments for %s: %r", name, call["args"])
            res = Result(False, "I didn't understand the details of that request.")
        except Exception:                                    # never crash the assistant on a tool bug
            log.exception("tool %s failed", name)
            res = Result(False, "Something went wrong while doing that. The details are in the log.")
        if res.ok and name not in META:
            ctx.last_call = call
        if res.ok and res.undo:
            ctx.undo_stack.append(res.undo)
        ctx.last_message = res.message
        ctx.store.log_action(original, name, res.ok, res.message)
        out = _reply("done" if res.ok else "error", res.message, tool=name)
        if res.speak or res.ok:
            out["speak"] = res.speak or res.message
        if res.data is not None:
            out["data"] = res.data
        return out

    # ------------------------------------------------------------------ undo
    def undo(self) -> Result:
        ctx = self.ctx
        if not ctx.undo_stack:
            return Result(False, "There is nothing I can undo.")
        spec = ctx.undo_stack.pop()
        tool = TOOLS.get(spec["tool"])
        if tool is None:
            return Result(False, "I can't undo that any more.")
        res = tool.fn(ctx, **spec["args"])
        if isinstance(res, Result) and res.ok:
            return Result(True, f"Undone: {spec.get('label', 'last change')}.")
        return res if isinstance(res, Result) else Result(True, "Undone.")
