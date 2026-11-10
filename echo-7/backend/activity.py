"""
A short running log of what Echo is doing - what it heard, which tools it decided
to use and what they returned, and what it replied - for the dashboard's
"Thinking" window. In memory only, newest last, capped; nothing is written to disk.
"""
import itertools
import json
import re
import time
from collections import deque

MAX_EVENTS = 150
MAX_TEXT = 400

_events: deque = deque(maxlen=MAX_EVENTS)
_seq = itertools.count(1)
STARTED = time.time()  # when Echo's server started
# How long each answer took, for Echo's own health report ("12.3s" at the end of a reply line).
reply_seconds: deque = deque(maxlen=200)
_TIMING = re.compile(r"\((\d+(?:\.\d+)?)s(?:,[^()]*)?\)\s*$")
# Functions that scrub secrets (the shutdown phrase) out of text before it's shown.
_redactors: list = []


def add_redactor(fn) -> None:
    _redactors.append(fn)


def _clip(value) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    text = " ".join(text.split())
    return text if len(text) <= MAX_TEXT else text[: MAX_TEXT - 1] + "…"


def log(kind: str, text, channel: str = "dashboard") -> None:
    """kind: heard | tool | result | reply | note | error"""
    if kind == "reply" and isinstance(text, str) and (timing := _TIMING.search(text)):
        reply_seconds.append(float(timing.group(1)))
    text = _clip(text)
    for redact in _redactors:
        try:
            text = redact(text)
        except Exception:  # noqa: BLE001 - a broken redactor must not break logging
            pass
    _events.append({"seq": next(_seq), "at": time.time(), "kind": kind, "channel": channel, "text": text})


def since(after: int = 0) -> list[dict]:
    return [e for e in _events if e["seq"] > after]
