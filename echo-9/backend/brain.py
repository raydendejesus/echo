"""
Which brain answers.

With Echo Mini on (the default), Echo's own brain (the "echo" model, one conversation at a
time) is kept for sir - at the dashboard or calling from a number marked sir - and for
background projects. Everyone else talks to Echo Mini: Qwen 2.5 3B with Echo's own persona.
Echo Mini loads only when it's needed and unloads after a few quiet minutes, so it costs no
graphics memory the rest of the time. A caller keeps their whole conversation whichever brain
answers - the history belongs to the call.

With Echo Mini off, Ollama runs two conversations at once on Echo's own brain (the "double
channel") and everyone gets the full Echo - at about twice the context memory.

While a heavy graphics job runs (3D model generation), Echo's own brain is unloaded to make
room and every conversation - sir's too - goes to Echo Mini until the job is done.

Why Qwen 2.5 3B: on eight phone-style requests (look-ups, "remember...", small talk, goodbyes)
it picked the right tool or answered plainly 7 times; Llama 3.2 3B 5 times; Hermes 3 3B - the
small sibling of Echo's own brain - once, writing its tool calls as broken text instead.
"""
import os
import re
import subprocess
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx

import ollama_control

MAIN = "echo"
HELPER = "echo-mini"
HELPER_BASE = "qwen2.5:3b"
HELPER_CONTEXT = 8192
HELPER_KEEP_ALIVE = "5m"
CONFIG_KEY = "helper_brain_enabled"
TAGS_URL = "http://127.0.0.1:11434/api/tags"

# Sir comes first: while he's in a conversation (any message in the last two minutes),
# background project steps wait so he never queues behind one.
SIR_ACTIVE_SECONDS = 120

_in_use = {"sir": 0, "guest": 0, "background": 0}  # who is on the main brain right now
_sir_seen = -1e9
_ready_checked_at = 0.0
_ready = False
_lent = 0  # heavy graphics jobs running right now (Echo's own brain is unloaded for them)
status = {"installing": False, "error": ""}


def sir_active() -> bool:
    return _in_use["sir"] > 0 or time.monotonic() - _sir_seen < SIR_ACTIVE_SECONDS


def gpu_lent() -> bool:
    return _lent > 0


def slots_for(cfg: dict) -> int:
    """Conversations Ollama runs at once on Echo's own brain: one with Echo Mini on, two without."""
    return 1 if cfg.get(CONFIG_KEY, True) else ollama_control.SLOTS_WHEN_ON


async def helper_ready() -> bool:
    """Whether Echo Mini is built (re-checked at most once a minute)."""
    global _ready_checked_at, _ready
    if time.monotonic() - _ready_checked_at > 60:
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                names = {m.get("name", "") for m in (await client.get(TAGS_URL)).json().get("models", [])}
            _ready = any(n.split(":")[0] == HELPER for n in names)
        except (httpx.HTTPError, ValueError):
            _ready = False
        _ready_checked_at = time.monotonic()
    return _ready


async def pick(cfg: dict, who: str = "guest") -> tuple[str, object]:
    """Which brain `who` talks to right now, and its keep_alive (nothing is counted - see
    for_conversation)."""
    if cfg.get(CONFIG_KEY, True) and (gpu_lent() or who != "sir") and await helper_ready():
        return HELPER, HELPER_KEEP_ALIVE
    return MAIN, -1


@asynccontextmanager
async def for_conversation(cfg: dict, who: str = "guest"):
    """The model for one conversation turn, and its keep_alive. Sir gets Echo's own brain; with
    Echo Mini on (and installed) everyone else gets Echo Mini, and while a heavy graphics job has
    the GPU, sir does too. Otherwise everyone shares Echo's own brain."""
    global _sir_seen
    if who == "sir":
        _sir_seen = time.monotonic()
    model, keep_alive = await pick(cfg, who)
    if model == HELPER:
        yield model, keep_alive
        return
    who = who if who in _in_use else "guest"
    _in_use[who] += 1
    try:
        yield MAIN, -1
    finally:
        _in_use[who] -= 1
        if who == "sir":
            _sir_seen = time.monotonic()


@asynccontextmanager
async def hold():
    """A background project step on Echo's own brain. (Steps pause while sir is active or a heavy
    graphics job has the GPU.)"""
    _in_use["background"] += 1
    try:
        yield
    finally:
        _in_use["background"] -= 1


async def _unload(model: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            await client.post("http://127.0.0.1:11434/api/generate", json={"model": model, "keep_alive": 0})
    except httpx.HTTPError:
        pass


@asynccontextmanager
async def lend_gpu():
    """For a heavy graphics job (3D generation needs most of a 24 GB card): unloads Echo's own
    brain first so the two don't squeeze each other, and sends every conversation to Echo Mini
    until the job is done. Echo's brain loads again on the next message after it."""
    global _lent
    import asyncio

    import config as config_module
    import turbo_engine
    _lent += 1
    engine_was_running = await turbo_engine.pause(config_module.load_config())
    try:
        await _unload(MAIN)
        yield
    finally:
        _lent -= 1
        # the TurboQuant engine (if it was running) comes back in the background
        asyncio.get_running_loop().create_task(turbo_engine.resume(config_module.load_config(), engine_was_running))


def _helper_modelfile() -> str:
    """Echo's own Modelfile - same persona and settings - on the small brain, with a smaller desk."""
    text = ollama_control._read_modelfile()
    text = re.sub(r"^FROM .*$", f"FROM {HELPER_BASE}", text, count=1, flags=re.M)
    return re.sub(r"^PARAMETER\s+num_ctx\s+\d+[ \t]*$", f"PARAMETER num_ctx {HELPER_CONTEXT}", text, count=1, flags=re.M)


_TERMINAL_CODES = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _said(result: subprocess.CompletedProcess) -> str:
    """ollama's own message, without its progress-bar drawing codes."""
    text = _TERMINAL_CODES.sub("", (result.stderr or "") + (result.stdout or ""))
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("pulling")]
    return (lines[-1] if lines else "no details")[-300:]


def _wait_for_ollama(seconds: int = 120) -> None:
    """Ollama may be restarting (turning this on also turns two-slot mode off) - wait it out."""
    for _ in range(seconds):
        if not ollama_control.status["restarting"]:
            try:
                httpx.get(TAGS_URL, timeout=2)
                return
            except httpx.HTTPError:
                pass
        time.sleep(1)


def install_helper() -> None:
    """Downloads Qwen 2.5 3B (about 2 GB, once) and builds Echo Mini from it. Blocking."""
    global _ready_checked_at
    status.update(installing=True, error="")
    path = None
    try:
        time.sleep(2)  # let a restart that was just asked for begin
        _wait_for_ollama()
        pull = subprocess.run(["ollama", "pull", HELPER_BASE], capture_output=True, text=True, timeout=3600,
                              encoding="utf-8", errors="replace", creationflags=0x08000000)
        if pull.returncode != 0:
            raise RuntimeError(f"Couldn't download {HELPER_BASE}: {_said(pull)}")
        fd, path = tempfile.mkstemp(suffix=".Modelfile")
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(_helper_modelfile())
        made = subprocess.run(["ollama", "create", HELPER, "-f", path], capture_output=True, text=True, timeout=600,
                              encoding="utf-8", errors="replace", creationflags=0x08000000)
        if made.returncode != 0:
            raise RuntimeError(f"Couldn't build Echo Mini: {_said(made)}")
    except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
        status["error"] = str(exc)
    finally:
        if path:
            Path(path).unlink(missing_ok=True)
        status["installing"] = False
        _ready_checked_at = 0.0  # look again
