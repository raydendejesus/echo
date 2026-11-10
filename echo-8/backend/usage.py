"""
What Echo did with each tool - "8:42 PM, by phone: Putting TheBurntPeanut's live stream on the
TV." - so the dashboard's Switches page can show, under every switch, how it was actually used.
Every tool call is recorded with what came back, newest first. Kept in backend/usage_log.json
(private), capped.
"""
import json
import time
from collections import deque
from pathlib import Path

FILE = Path(__file__).resolve().parent / "usage_log.json"
MAX_KEPT = 400

_log: deque = deque(maxlen=MAX_KEPT)
try:
    _log.extend(json.loads(FILE.read_text(encoding="utf-8")))
except (OSError, json.JSONDecodeError):
    pass


def _short(value, limit: int) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def record(tool: str, args: dict, result, channel: str) -> None:
    _log.append({"at": time.time(), "tool": tool, "args": _short(args, 200), "result": _short(result, 220), "channel": channel})
    try:
        FILE.write_text(json.dumps(list(_log)), encoding="utf-8")
    except OSError:
        pass


def recent() -> list[dict]:
    return list(reversed(_log))
