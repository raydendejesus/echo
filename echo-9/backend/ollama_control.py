"""
Switches Ollama between "one request at a time" and "two at once" (the double channel, which
Echo uses when Echo Mini is off - see brain.py), and compact memory, from the dashboard.

Ollama reads OLLAMA_NUM_PARALLEL only when it starts, so changing it means restarting Ollama.
With two, a second conversation starts straight away on Echo's own brain (each runs a little
slower while both are active, and the second slot doubles the context's graphics memory).

The setting is also written to the user's environment so Ollama keeps it the next
time it starts at login, and removed again (or put back to whatever the user had) when
the switch goes off. Only processes running from Ollama's own install folder are
ever stopped.
"""
import asyncio
import ctypes
import os
import re
import shutil
import subprocess
import winreg
from ctypes import wintypes as wt
from pathlib import Path

import httpx

ENV_NAME = "OLLAMA_NUM_PARALLEL"
SLOTS_WHEN_ON = 2
LOG_FILE = Path(os.environ.get("LOCALAPPDATA", "")) / "Ollama" / "server.log"
OLLAMA_TAGS_URL = "http://127.0.0.1:11434/api/tags"
RESTART_WAIT_SECONDS = 60

_NO_WINDOW = 0x08000000
_DETACHED = 0x00000008
_NEW_GROUP = 0x00000200

status = {"restarting": False, "error": ""}


def _server_config() -> str:
    """The running Ollama's newest "server config" log line - what it really started with."""
    found = ""
    try:
        with open(LOG_FILE, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if 'msg="server config"' in line:
                    found = line
    except OSError:
        pass
    return found


def current_slots() -> int | None:
    """What the running Ollama actually started with, read from its own log (the newest
    "server config" line) - the truth, not just what this app last asked for."""
    match = re.search(r"OLLAMA_NUM_PARALLEL:(\d+)", _server_config())
    return int(match.group(1)) if match else None


# Compact brain memory: the context window stored at 8 bits instead of 16 - measured on a 3090:
# about half the context's graphics memory (Echo at 16K: 6.8 -> 5.8 GB; 32K: 9.0 -> 7.0 GB) for
# about 4% slower replies. An Ollama-wide setting (needs flash attention), so it restarts Ollama.
KV_ENV = "OLLAMA_KV_CACHE_TYPE"
FLASH_ENV = "OLLAMA_FLASH_ATTENTION"
COMPACT_TYPE = "q8_0"


def compact_on() -> bool:
    return f"{KV_ENV}:{COMPACT_TYPE}" in _server_config()


def _env_get(name: str = ENV_NAME) -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            return str(winreg.QueryValueEx(key, name)[0])
    except OSError:
        return None


def _env_set(value: str | None, name: str = ENV_NAME) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as key:
        if value is None:
            try:
                winreg.DeleteValue(key, name)
            except FileNotFoundError:
                pass
        else:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
    # Let running programs (Explorer, so anything started from it) pick the change up.
    user32 = ctypes.WinDLL("user32")
    user32.SendMessageTimeoutW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPCWSTR, wt.UINT, wt.UINT, ctypes.POINTER(ctypes.c_ulong)]
    user32.SendMessageTimeoutW(wt.HWND(0xFFFF), 0x001A, 0, "Environment", 0x2, 5000, ctypes.byref(ctypes.c_ulong()))


def persist_user_env(enabled: bool, previous: str | None) -> str | None:
    """Writes (or removes) the setting in the user's environment. Returns the value the
    user had before, to be remembered while the switch is on and restored when it goes off."""
    if enabled:
        existing = _env_get()
        remember = existing if existing not in (None, str(SLOTS_WHEN_ON)) else previous
        _env_set(str(SLOTS_WHEN_ON))
        return remember
    _env_set(previous)
    return None


def persist_compact_env(enabled: bool, previous: dict | None) -> dict | None:
    """Like persist_user_env, for compact memory's two settings. Returns what the user had before
    (kept while it's on, put back when it goes off)."""
    names = (KV_ENV, FLASH_ENV)
    if enabled:
        remember = previous if previous is not None else {n: _env_get(n) for n in names}
        _env_set(COMPACT_TYPE, KV_ENV)
        _env_set("1", FLASH_ENV)
        return remember
    for n in names:
        _env_set((previous or {}).get(n), n)
    return None


def spawn_env(config: dict) -> dict:
    """The environment to start Ollama with, per the saved switches."""
    env = os.environ.copy()
    wanted = {ENV_NAME: str(SLOTS_WHEN_ON) if config.get("ollama_parallel_2_enabled") else None}
    if config.get("brain_compact_enabled"):
        wanted.update({KV_ENV: COMPACT_TYPE, FLASH_ENV: "1"})
    else:
        wanted.update({KV_ENV: None, FLASH_ENV: None})
    for name, value in wanted.items():
        value = value if value is not None else _env_get(name)
        if value is None:
            env.pop(name, None)
        else:
            env[name] = value
    return env


def _install_dir() -> Path | None:
    exe = shutil.which("ollama")
    return Path(exe).parent if exe else None


def _find_app() -> Path | None:
    install = _install_dir()
    candidates = [install / "ollama app.exe"] if install else []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "Programs" / "Ollama" / "ollama app.exe")
    return next((c for c in candidates if c.exists()), None)


def _stop_ollama() -> None:
    """Stops everything running from Ollama's own install folder (the tray app, the server
    and the model runner) and nothing else."""
    install = _install_dir()
    if install is None:
        return
    folder = str(install).rstrip("\\").replace("'", "''") + "\\"
    script = (
        f"$d = '{folder}'; "
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($d, [StringComparison]::OrdinalIgnoreCase) } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True, timeout=30, creationflags=_NO_WINDOW,
    )


def _ollama_alive() -> bool:
    for image in ("ollama.exe", "ollama app.exe"):
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {image}", "/NH"],
            capture_output=True, text=True, timeout=10, creationflags=_NO_WINDOW,
        ).stdout
        if image.lower() in out.lower():
            return True
    return False


async def _api_up() -> bool:
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            await client.get(OLLAMA_TAGS_URL)
        return True
    except httpx.HTTPError:
        return False


# --- the brain's context window: everything it can see while writing one reply ---------------
# (its persona and rules, the tools, sir's saved facts, the recent conversation, a page it read).
# Set in the Modelfile, so every part of Echo that talks to the brain uses the same size - a
# request asking for a different size would make Ollama reload the whole brain.
MODELFILE = Path(__file__).resolve().parent / "Modelfile"
MODEL_NAME = "echo"
CONTEXT_CHOICES = (8192, 16384, 32768, 65536)
_NUM_CTX = re.compile(r"^PARAMETER\s+num_ctx\s+(\d+)[ \t]*$", re.M)
# Measured on an RTX 3090 with hermes3 8B: the brain itself, plus the memory Ollama sets aside
# per token for every request it can run at once (so "two replies at once" doubles that part).
BRAIN_BASE_GB = 4.6
GB_PER_TOKEN_PER_SLOT = 0.000138
context_status = {"changing": False, "error": ""}


def _read_modelfile() -> str:
    with open(MODELFILE, encoding="utf-8", newline="") as fh:
        return fh.read()


def context_tokens() -> int:
    try:
        m = _NUM_CTX.search(_read_modelfile())
    except OSError:
        return 0
    return int(m.group(1)) if m else 4096  # Ollama's own default


# The TurboQuant engine's pair: keys at 8 bits (8.5 bits a value) and values in turbo3 (3.125) -
# about 36% of the 16-bit size. (An estimate from the formats; measured numbers replace it.)
TURBO_SHARE = (8.5 + 3.125) / 32


def vram_gb(tokens: int, slots: int, compact: bool = False, turbo: bool = False) -> float:
    per_token = GB_PER_TOKEN_PER_SLOT * (TURBO_SHARE if turbo else 0.5 if compact else 1)
    return round(BRAIN_BASE_GB + tokens * max(slots, 1) * per_token, 1)


def size_for_card(total_gb: float | None, compact: bool = True) -> int:
    """For a first install: the biggest size up to 16K that leaves room on this graphics card.
    With compact memory (the default) an 8 GB card - the most common - fits 16K; without it, 8K."""
    if not total_gb:
        return 16384
    fitting = [n for n in CONTEXT_CHOICES if n <= 16384 and vram_gb(n, 1, compact) <= total_gb - 1.5]
    return max(fitting) if fitting else CONTEXT_CHOICES[0]


def write_context_tokens(tokens: int) -> str:
    """Puts the size into the Modelfile; returns what the file said before."""
    if tokens not in CONTEXT_CHOICES:
        raise ValueError(f"Choose one of {CONTEXT_CHOICES}.")
    old = _read_modelfile()
    if _NUM_CTX.search(old):
        new = _NUM_CTX.sub(f"PARAMETER num_ctx {tokens}", old, count=1)
    else:
        new = re.sub(r"^(FROM .*?)(\r?\n)", rf"\1\2PARAMETER num_ctx {tokens}\2", old, count=1, flags=re.M)
    with open(MODELFILE, "w", encoding="utf-8", newline="") as fh:
        fh.write(new)
    return old


def set_context_tokens(tokens: int) -> None:
    """Writes the new size into the Modelfile and rebuilds the echo model from it - the same
    brain and persona, only the size changes. The file is put back if the rebuild fails."""
    old = write_context_tokens(tokens)
    try:
        result = subprocess.run(["ollama", "create", MODEL_NAME, "-f", str(MODELFILE)],
                                capture_output=True, text=True, timeout=600, creationflags=_NO_WINDOW)
        failed = result.returncode != 0 and ((result.stderr or result.stdout or "").strip()[-300:] or "ollama create failed")
    except (OSError, subprocess.TimeoutExpired) as exc:
        failed = str(exc)
    if failed:
        with open(MODELFILE, "w", encoding="utf-8", newline="") as fh:
            fh.write(old)
        raise RuntimeError(f"Couldn't rebuild the brain at the new size: {failed}")


async def restart(slots: int, config: dict, warm=None) -> None:
    """Restarts Ollama so it serves `slots` requests at once, and waits until it's back.
    Progress and any problem are reported through `status`."""
    status.update(restarting=True, error="")
    try:
        await asyncio.to_thread(_stop_ollama)
        for _ in range(20):
            if not await asyncio.to_thread(_ollama_alive):
                break
            await asyncio.sleep(0.5)
        else:
            raise RuntimeError("Couldn't stop the running Ollama, so nothing was changed.")
        await asyncio.sleep(1.0)

        env = spawn_env(config)
        app, cli = _find_app(), shutil.which("ollama")
        flags = _NO_WINDOW | _DETACHED | _NEW_GROUP
        if app is not None:
            subprocess.Popen([str(app)], env=env, creationflags=flags, close_fds=True)
        elif cli is not None:
            subprocess.Popen([cli, "serve"], env=env, creationflags=flags, close_fds=True)
        else:
            raise RuntimeError("Couldn't find Ollama to restart it.")

        for waited in range(RESTART_WAIT_SECONDS):
            await asyncio.sleep(1)
            if await _api_up():
                break
            if waited == 25 and app is not None and cli is not None:
                # The tray app didn't bring the server up - start the server directly.
                subprocess.Popen([cli, "serve"], env=env, creationflags=flags, close_fds=True)
        else:
            raise RuntimeError(f"Ollama didn't come back within {RESTART_WAIT_SECONDS} seconds.")

        if warm is not None:
            await warm()
        for _ in range(10):
            if current_slots() == slots:
                return
            await asyncio.sleep(0.5)
        status["error"] = f"Ollama restarted but reports {current_slots()} at once, not {slots}."
    except Exception as exc:  # noqa: BLE001 - reported to the dashboard instead of raised
        status["error"] = str(exc)
    finally:
        status["restarting"] = False
