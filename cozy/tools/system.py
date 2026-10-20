"""Windows controls: volume, brightness, power, Wi-Fi, window snapping, screenshots, clipboard."""
from __future__ import annotations

from ..registry import Result, rule, tool
from ..winops import OpFailed


# ----------------------------------------------------------------------------- volume
@tool("volume_set", desc="set the Windows volume", args='{"percent": 0-100}')
def volume_set(ctx, percent):
    percent = int(percent)
    if not 0 <= percent <= 100:
        return Result(False, "Volume must be between 0 and 100 percent.")
    try:
        previous = ctx.win.get_volume()
    except OpFailed:
        previous = None
    ctx.win.set_volume(percent)
    undo = {"tool": "volume_set", "args": {"percent": previous}, "label": f"volume back to {previous} percent"} if previous is not None else None
    return Result(True, f"Setting your laptop's volume to {percent} percent.", undo=undo)


@tool("volume_step", desc="make the Windows volume louder or quieter", args='{"direction": "up|down"}')
def volume_step(ctx, direction):
    try:
        current = ctx.win.get_volume()
    except OpFailed:
        for _ in range(5):                                   # no pycaw: fall back to the media keys
            ctx.win.hotkey("volume_up" if direction == "up" else "volume_down")
        return Result(True, f"{'Increasing' if direction == 'up' else 'Decreasing'} your laptop's volume.")
    target = max(0, min(100, current + (10 if direction == "up" else -10)))
    ctx.win.set_volume(target)
    return Result(True, f"Volume {target} percent.", undo={"tool": "volume_set", "args": {"percent": current}, "label": f"volume back to {current} percent"})


@tool("volume_mute", desc="mute or unmute the laptop", args='{"muted": true}')
def volume_mute(ctx, muted=True):
    try:
        ctx.win.set_mute(bool(muted))
    except OpFailed:
        ctx.win.hotkey("mute")
    return Result(True, "Muting your laptop." if muted else "Unmuting your laptop.",
                  undo={"tool": "volume_mute", "args": {"muted": not muted}, "label": "mute changed back"})


# ----------------------------------------------------------------------------- brightness
@tool("brightness_set", desc="set the screen brightness", args='{"percent": 0-100}')
def brightness_set(ctx, percent):
    percent = int(percent)
    if not 0 <= percent <= 100:
        return Result(False, "Brightness must be between 0 and 100 percent.")
    try:
        previous = ctx.win.get_brightness()
    except OpFailed:
        previous = None
    ctx.win.set_brightness(percent)
    undo = {"tool": "brightness_set", "args": {"percent": previous}, "label": f"brightness back to {previous} percent"} if previous is not None else None
    return Result(True, f"Brightness set to {percent} percent.", undo=undo)


@tool("brightness_step", desc="make the screen brighter or dimmer", args='{"direction": "up|down"}')
def brightness_step(ctx, direction):
    current = ctx.win.get_brightness()
    return brightness_set(ctx, max(0, min(100, current + (15 if direction == "up" else -15))))


# ----------------------------------------------------------------------------- power / network (risky ones ask first)
@tool("lock_screen", desc="lock the laptop")
def lock_screen(ctx):
    ctx.win.lock()
    return Result(True, "Locking the laptop.")


@tool("sleep_pc", desc="put the laptop to sleep", risky=True)
def sleep_pc(ctx):
    ctx.win.sleep()
    return Result(True, "Going to sleep.")


@tool("shutdown_pc", desc="shut down the laptop", risky=True)
def shutdown_pc(ctx):
    ctx.win.shutdown(restart=False)
    return Result(True, "Shutting down in 30 seconds. Say 'cancel shutdown' to stop it.")


@tool("restart_pc", desc="restart the laptop", risky=True)
def restart_pc(ctx):
    ctx.win.shutdown(restart=True)
    return Result(True, "Restarting in 30 seconds. Say 'cancel shutdown' to stop it.")


@tool("cancel_shutdown", desc="cancel a pending shutdown or restart")
def cancel_shutdown(ctx):
    ctx.win.cancel_shutdown()
    return Result(True, "Cancelled the shutdown.")


@tool("wifi_set", desc="change the Wi-Fi setting", risky=True, args='{"state": "on|off"}')
def wifi_set(ctx, state):
    ctx.win.set_wifi(state == "on")
    return Result(True, f"Wi-Fi turned {state}.")


# ----------------------------------------------------------------------------- windows / screenshots / clipboard
WINDOW_KEYS = {"snap_left": ("win", "left"), "snap_right": ("win", "right"), "maximize": ("win", "up"),
               "minimize": ("win", "down"), "show_desktop": ("win", "d"), "switch_app": ("alt", "tab"), "task_view": ("win", "tab")}
WINDOW_MESSAGES = {"snap_left": "Snapped the window to the left.", "snap_right": "Snapped the window to the right.",
                   "maximize": "Maximised the window.", "minimize": "Minimised the window.", "show_desktop": "Showing the desktop.",
                   "switch_app": "Switching apps.", "task_view": "Showing all windows."}


@tool("window_action", desc="snap, maximise, minimise or switch windows",
      args='{"action": "snap_left|snap_right|maximize|minimize|show_desktop|switch_app|task_view"}')
def window_action(ctx, action):
    if action not in WINDOW_KEYS:
        return Result(False, "I don't know that window action.")
    ctx.win.hotkey(*WINDOW_KEYS[action])
    return Result(True, WINDOW_MESSAGES[action])


@tool("screenshot", desc="take a screenshot")
def screenshot(ctx):
    ctx.win.hotkey("win", "prtsc")
    return Result(True, "Screenshot taken. It is saved in your Screenshots folder.")


@tool("copy_that", desc="copy my last answer to the clipboard")
def copy_that(ctx):
    if not ctx.last_message:
        return Result(False, "There is nothing to copy yet.")
    ctx.win.clipboard_set(ctx.last_message)
    return Result(True, "Copied.")


rule(r"^(?:(?:set|put|make|turn)\s+(?:the\s+)?(?:system\s+|laptop\s+)?(?:volume|sound)\s+(?:(?:to|at)\s+)?)(?P<percent>-?\d+)(?:\s*(?:%|percent|per cent))?$",
     "volume_set", conv={"percent": int}, priority=20)
rule(r"^(?:(?:increase|raise|decrease|lower|reduce)\s+(?:the\s+)?(?:system\s+)?(?:volume|sound)\s+to\s+)(?P<percent>-?\d+)(?:\s*(?:%|percent|per cent))?$",
     "volume_set", conv={"percent": int}, priority=20)
rule(r"^(?:volume|sound)\s+(?:to\s+)?(?P<percent>-?\d+)(?:\s*(?:%|percent|per cent))?$", "volume_set", conv={"percent": int}, priority=20)
rule(r"^(?:(?:turn |make )?(?:the )?(?:volume|sound) up|(?:increase|raise|turn up)(?: the)? (?:volume|sound)|louder)$",
     "volume_step", static={"direction": "up"}, priority=50)
rule(r"^(?:(?:turn |make )?(?:the )?(?:volume|sound) down|(?:decrease|lower|reduce|turn down)(?: the)? (?:volume|sound)|quieter)$",
     "volume_step", static={"direction": "down"}, priority=50)
rule(r"^mute(?:\s+(?:the|my))?(?:\s+(?:laptop|sound|audio|computer|volume|pc))?$", "volume_mute", static={"muted": True}, priority=50)
rule(r"^unmute(?:\s+(?:the|my))?(?:\s+(?:laptop|sound|audio|computer|volume|pc))?$", "volume_mute", static={"muted": False}, priority=50)
rule(r"^(?:set\s+)?brightness\s+(?:to\s+)?(?P<percent>\d+)\s*(?:%|percent)?$", "brightness_set", conv={"percent": int}, priority=20)
rule(r"^(?:increase|raise|turn up|brighten)(?:\s+the)?(?:\s+(?:brightness|screen))?$", "brightness_step", static={"direction": "up"}, priority=50)
rule(r"^(?:decrease|lower|reduce|turn down|dim)(?:\s+the)?\s+(?:brightness|screen)$|^dim$", "brightness_step", static={"direction": "down"}, priority=50)
rule(r"^lock(?:\s+(?:the\s+)?(?:laptop|computer|pc|screen))?$", "lock_screen", priority=30)
rule(r"^(?:go to sleep|sleep(?:\s+(?:the\s+)?(?:laptop|computer|pc))?|put\s+(?:the\s+)?(?:laptop|computer|pc)\s+to\s+sleep)$", "sleep_pc", priority=30)
rule(r"^(?:shut\s?down|turn off)(?:\s+(?:the\s+)?(?:laptop|computer|pc))?$", "shutdown_pc", priority=30)
rule(r"^(?:restart|reboot)(?:\s+(?:the\s+)?(?:laptop|computer|pc))?$", "restart_pc", priority=30)
rule(r"^cancel\s+(?:the\s+)?(?:shutdown|restart)$", "cancel_shutdown", priority=30)
rule(r"^turn\s+(?P<state>on|off)\s+(?:the\s+)?wi-?fi$", "wifi_set", conv={"state": str.lower}, priority=30)
for phrase, action in [(r"snap\s+(?:this\s+)?(?:window\s+)?(?:to\s+the\s+)?left", "snap_left"), (r"snap\s+(?:this\s+)?(?:window\s+)?(?:to\s+the\s+)?right", "snap_right"),
                       (r"maximi[sz]e(?:\s+(?:this|the))?(?:\s+window)?", "maximize"), (r"minimi[sz]e(?:\s+(?:this|the))?(?:\s+window)?", "minimize"),
                       (r"show\s+(?:the\s+)?desktop", "show_desktop"), (r"switch\s+(?:the\s+)?(?:app|window)s?|alt\s*tab", "switch_app"),
                       (r"(?:show\s+)?(?:all\s+)?windows|task\s+view", "task_view")]:
    rule(rf"^(?:{phrase})$", "window_action", static={"action": action}, priority=30)
rule(r"^(?:take\s+(?:a\s+)?)?screenshot$", "screenshot", priority=30)
rule(r"^copy\s+(?:that|this|the answer|your answer)$", "copy_that", priority=30)
