"""
Shuts the PC down when sir asks - at most MAX_SHUTDOWNS_PER_DAY times a day, and only after he says his
passcode.

Whether sir said the passcode is decided from his own words as they arrive
(passcode.scrub, turn_context.passcode_said), never from anything the model passes
in, so the model can't guess, invent or repeat its way into a shutdown - it never
even sees the passcode. Three wrong passcodes in a day lock shutdown until tomorrow.

The shutdown is scheduled a minute out, so there's time to cancel it, and so a
phone call can finish its sentence. Windows force-closes apps when the timer
runs out, so anything unsaved is lost.
"""
import datetime
import json
import re
import subprocess
from pathlib import Path

import launch_utils as lu
import passcode
import turn_context

PLUGIN_NAME = "pc_power"
TOGGLE_LABEL = "Shut Down PC (5 a day, needs your passcode)"
CONFIG_KEY = "pc_shutdown_enabled"
ENABLED_BY_DEFAULT = False
VRAM_COST = "no local model"
SIR_CALLS = True

STATE_FILE = Path(__file__).resolve().parent.parent / "plugins_data" / "pc_power_state.json"
SHUTDOWN_DELAY_SECONDS = 60
MAX_SHUTDOWNS_PER_DAY = 5
MAX_WRONG_PHRASES_PER_DAY = 3
_ASKING_TO_SHUT_DOWN = re.compile(r"\b(shut|turn|power|switch)\w*\b.*\b(off|down)\b|\bshutdown\b", re.I)


def _today() -> str:
    return datetime.date.today().isoformat()


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


async def shutdown_pc(args: dict) -> str:
    state = _load(STATE_FILE)
    today = _today()
    done = state.get("shutdowns", {})
    used_today = done.get("count", 0) if done.get("day") == today else 0
    if used_today >= MAX_SHUTDOWNS_PER_DAY:
        return lu.say(f"I've already shut the PC down {MAX_SHUTDOWNS_PER_DAY} times today, sir - that's the daily limit, so I'll leave it on.")
    wrong = state.get("wrong", {})
    if wrong.get("day") == today and wrong.get("count", 0) >= MAX_WRONG_PHRASES_PER_DAY:
        return lu.say("Shutdown is locked for the rest of today after too many wrong passcodes.")

    if not passcode.is_set():
        return lu.say("There's no passcode set yet, so I can't shut the PC down. Set one in the dashboard, next to the shutdown toggle.")

    if not turn_context.passcode_said.get():
        if _ASKING_TO_SHUT_DOWN.search(turn_context.user_text.get()):
            return lu.say("To confirm the shutdown, say your passcode.")
        count = (wrong.get("count", 0) if wrong.get("day") == today else 0) + 1
        state["wrong"] = {"day": today, "count": count}
        _save_state(state)
        left = MAX_WRONG_PHRASES_PER_DAY - count
        if left <= 0:
            return lu.say("That isn't the passcode. Shutdown is now locked for the rest of today.")
        return lu.say(f"That isn't the passcode, so I've left the PC on. {left} tr{'ies' if left != 1 else 'y'} left today.")

    result = subprocess.run(
        ["shutdown", "/s", "/t", str(SHUTDOWN_DELAY_SECONDS), "/c", "Echo is shutting this PC down. Run 'shutdown /a' to cancel."],
        capture_output=True, text=True, timeout=15, creationflags=lu.CREATE_NO_WINDOW,
    )
    if result.returncode != 0:
        return lu.say("Windows wouldn't schedule the shutdown - another shutdown or restart may already be pending.")
    state["shutdowns"] = {"day": today, "count": used_today + 1}
    state.pop("last_shutdown_day", None)
    state.pop("wrong", None)
    _save_state(state)
    away = " This call will drop when it goes off." if turn_context.channel.get() == "phone" else ""
    return lu.say(
        f"Confirmed. The PC will shut down in one minute - anything unsaved will be lost. "
        f"Say cancel shutdown if you change your mind.{away}"
    )


async def cancel_shutdown(args: dict) -> str:
    result = subprocess.run(["shutdown", "/a"], capture_output=True, text=True, timeout=15, creationflags=lu.CREATE_NO_WINDOW)
    if result.returncode != 0:
        return lu.say("There's no shutdown pending to cancel.")
    return lu.say("Shutdown cancelled - the PC will stay on.")


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "shutdown_pc",
            "description": (
                "Shuts down sir's computer. Call it when sir asks to turn off or shut down the PC, and call it "
                "again when he answers with his passcode (you'll see it as [passcode]) - it checks it itself. "
                "Never ask what the passcode is, guess it, or repeat it."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_shutdown",
            "description": "Cancels a pending PC shutdown.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

DISPATCH = {"shutdown_pc": shutdown_pc, "cancel_shutdown": cancel_shutdown}
