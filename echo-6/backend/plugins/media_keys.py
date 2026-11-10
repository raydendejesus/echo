"""
Sends system media keys (play/pause, next, previous, stop, mute, volume) to
whatever is playing - works for any player that listens to Windows media keys,
with no account or setup.
"""
import asyncio
import ctypes

import launch_utils as lu

PLUGIN_NAME = "media_keys"
TOGGLE_LABEL = "Media Keys (play/pause, skip, volume)"
CONFIG_KEY = "media_keys_enabled"
ENABLED_BY_DEFAULT = True
VRAM_COST = "no local model"
# Offered on phone calls from a phone book contact with the "sir" switch on.
SIR_CALLS = True

MEDIA_KEYS = {
    "play_pause": 0xB3,
    "next": 0xB0,
    "previous": 0xB1,
    "stop": 0xB2,
    "mute": 0xAD,
    "volume_down": 0xAE,
    "volume_up": 0xAF,
}
_VOLUME_STEP_PERCENT = 2
_SPOKEN = {
    "play_pause": "Done - toggled play and pause.",
    "next": "Skipping to the next track.",
    "previous": "Back to the previous track.",
    "stop": "Stopped.",
    "mute": "Toggled mute.",
    "volume_up": "Turned the volume up.",
    "volume_down": "Turned the volume down.",
}


def _press(vk: int) -> None:
    user32 = ctypes.windll.user32
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, 2, 0)


_WORDS_TO_ACTION = [
    (("volume up", "turn it up", "turn up", "louder", "raise the volume"), "volume_up"),
    (("volume down", "turn it down", "turn down", "quieter", "lower the volume"), "volume_down"),
    (("unmute", "mute"), "mute"),
    (("next", "skip"), "next"),
    (("previous", "go back", "last song", "last track"), "previous"),
    (("stop",), "stop"),
    (("pause", "play", "resume"), "play_pause"),
]


def _action_from_words(text: str) -> str:
    low = text.lower()
    for words, action in _WORDS_TO_ACTION:
        if any(w in low for w in words):
            return action
    return ""


async def media_control(args: dict) -> str:
    action = (args.get("action") or "").strip().lower().replace(" ", "_")
    if action not in MEDIA_KEYS:
        # The brain picked the right tool but left the action out (or garbled it):
        # what sir said says which button he meant.
        import turn_context
        action = _action_from_words(turn_context.user_text.get()) or action
    if action not in MEDIA_KEYS:
        return f"Unknown media action '{action}'. Use one of: {', '.join(MEDIA_KEYS)}."
    presses = 1
    if action in ("volume_up", "volume_down"):
        percent = args.get("percent")
        if isinstance(percent, (int, float)) and percent > 0:
            presses = max(1, min(50, round(percent / _VOLUME_STEP_PERCENT)))
        else:
            presses = 5
    for _ in range(presses):
        _press(MEDIA_KEYS[action])
        await asyncio.sleep(0.02)
    return _SPOKEN.get(action, "Done.")


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "media_control",
            "description": (
                "Sends a system media key to whatever is playing: play_pause, next, previous, stop, mute, "
                "volume_up, volume_down. For volume changes, percent is roughly how much to change it by. "
                "For Spotify specifically, prefer the Spotify tools."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": list(MEDIA_KEYS)},
                    "percent": {"type": "number", "description": "Optional, volume_up/volume_down only."},
                },
                "required": ["action"],
            },
        },
    },
]

DISPATCH = {"media_control": lu.spoken(media_control)}
