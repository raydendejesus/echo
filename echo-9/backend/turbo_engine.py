"""
TurboQuant engine (optional, off by default): runs Echo's own brain - the same Hermes 3 file Ollama
already has - in a build of llama.cpp with TurboQuant context compression (TheTom/llama-cpp-
turboquant, a fork of the official llama.cpp; Ollama and the official llama.cpp declined it in
2026). Everything else - the vision model, Echo Mini - stays on Ollama.

TurboQuant rotates the context's numbers (a Walsh-Hadamard rotation) before squeezing them, so
they survive being stored at about 3 bits. The fork's recommended pair is used: keys at 8 bits,
values in "turbo3" (3.1 bits) - the context takes about a third of its 16-bit size, against half
with Ollama's own compact memory.

The engine is a separate program (tools/turboquant, downloaded from the fork's release - built by
GitHub's own build service, checked against its published SHA-256). It's started and stopped by
Echo, listens only on 127.0.0.1, and Echo's requests are translated between Ollama's way of
writing a chat and the engine's (OpenAI-style) way - so the rest of Echo doesn't change. If the
engine isn't answering, Echo's brain falls back to Ollama by itself.
"""
import asyncio
import json
import re
import shutil
import subprocess
import time
import zipfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
ENGINE_DIR = ROOT / "tools" / "turboquant"
LOG_FILE = ENGINE_DIR / "engine.log"
PORT = 8767
BASE = f"http://127.0.0.1:{PORT}"
CONFIG_KEY = "turbo_engine_enabled"
CACHE_K, CACHE_V = "q8_0", "turbo3"
DEFAULT_TEMPERATURE = 0.6  # Echo's Modelfile setting
START_TIMEOUT_SECONDS = 180

RELEASE_TAG = "tqp-v0.4.0"
RELEASE_ASSET = "turboquant-plus-tqp-v0.4.0-windows-x64-cuda12.4.zip"
RELEASE_URL = f"https://github.com/TheTom/llama-cpp-turboquant/releases/download/{RELEASE_TAG}/{RELEASE_ASSET}"
RELEASE_SHA256 = "033c0a2ce4d876bd1f4c840f1c6eef9a3a55f6af54e20f61bde59e7b2181e6d7"

_NO_WINDOW = 0x08000000
status = {"starting": False, "installing": False, "error": "", "model": ""}
_health = {"at": 0.0, "ok": False}
_proc: subprocess.Popen | None = None
_paused = 0


# --- the program and the brain file --------------------------------------------------------------

def exe() -> Path | None:
    if not ENGINE_DIR.exists():
        return None
    found = [p for p in ENGINE_DIR.rglob("llama-server.exe") if "_dl" not in p.parts]
    return found[0] if found else None


def installed() -> bool:
    return exe() is not None


def install() -> None:
    """Downloads the engine (about 840 MB, once), checks its SHA-256 and unpacks it. Blocking."""
    import hashlib
    status.update(installing=True, error="")
    dl = ENGINE_DIR / "_dl"
    try:
        dl.mkdir(parents=True, exist_ok=True)
        zip_path = dl / RELEASE_ASSET
        digest = hashlib.sha256()
        with httpx.stream("GET", RELEASE_URL, follow_redirects=True, timeout=60) as resp:
            resp.raise_for_status()
            with open(zip_path, "wb") as fh:
                for chunk in resp.iter_bytes(1 << 20):
                    fh.write(chunk)
                    digest.update(chunk)
        if digest.hexdigest() != RELEASE_SHA256:
            raise RuntimeError("The download didn't match the release's published checksum, so it wasn't used.")
        target = ENGINE_DIR / RELEASE_TAG
        with zipfile.ZipFile(zip_path) as z:
            for name in z.namelist():  # never write outside the engine's own folder
                if name.startswith(("/", "\\")) or ".." in Path(name).parts:
                    raise RuntimeError(f"Unexpected path in the download: {name}")
            z.extractall(target)
        if exe() is None:
            raise RuntimeError("The download didn't contain llama-server.exe.")
    except (OSError, httpx.HTTPError, RuntimeError, zipfile.BadZipFile) as exc:
        status["error"] = str(exc)
    finally:
        shutil.rmtree(dl, ignore_errors=True)
        status["installing"] = False


def model_file() -> str | None:
    """The GGUF file of Echo's base brain (FROM in Echo's Modelfile - hermes3) inside Ollama's store."""
    import ollama_control
    m = re.search(r"^FROM\s+(\S+)", ollama_control._read_modelfile(), re.M)
    base = m.group(1) if m else "hermes3"
    try:
        out = subprocess.run(["ollama", "show", base, "--modelfile"], capture_output=True, text=True,
                             timeout=30, creationflags=_NO_WINDOW).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"^FROM\s+(.+)$", out, re.M)
    path = m.group(1).strip() if m else ""
    return path if path and Path(path).exists() else None


def args(cfg: dict, model: str) -> list[str]:
    import brain
    import ollama_control
    slots = brain.slots_for(cfg)
    tokens = ollama_control.context_tokens() or 16384
    return [str(exe()), "-m", model, "--host", "127.0.0.1", "--port", str(PORT), "-a", "echo",
            "-ngl", "99", "-fa", "on", "-c", str(tokens * slots), "-np", str(slots),
            "--cache-type-k", CACHE_K, "--cache-type-v", CACHE_V, "--jinja", "--no-webui"]


# --- running it ----------------------------------------------------------------------------------

async def healthy() -> bool:
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            ok = (await client.get(f"{BASE}/health")).status_code == 200
    except httpx.HTTPError:
        ok = False
    _health.update(at=time.monotonic(), ok=ok)
    return ok


async def serving(cfg: dict) -> bool:
    """Whether Echo's brain should go to the engine right now (switched on, not paused for a 3D
    job, and answering - checked at most every few seconds)."""
    if not cfg.get(CONFIG_KEY) or _paused:
        return False
    if time.monotonic() - _health["at"] > 5:
        await healthy()
    return _health["ok"]


def _ours() -> list[int]:
    """Process ids of engine programs started from tools/turboquant - never Ollama's own copy."""
    folder = str(ENGINE_DIR).rstrip("\\").replace("'", "''") + "\\"
    script = (f"$d = '{folder}'; Get-CimInstance Win32_Process -Filter \"Name='llama-server.exe'\" | "
              "Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($d, [StringComparison]::OrdinalIgnoreCase) } | "
              "ForEach-Object { $_.ProcessId }")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True,
                             timeout=30, creationflags=_NO_WINDOW).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [int(x) for x in out.split() if x.isdigit()]


def stop() -> None:
    global _proc
    for pid in _ours():
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=15, creationflags=_NO_WINDOW)
    _proc = None
    _health.update(ok=False, at=time.monotonic())


async def start(cfg: dict) -> None:
    """Starts the engine (stopping an older one first) and waits until it answers."""
    global _proc
    if not installed():
        status["error"] = "The TurboQuant engine isn't downloaded yet."
        return
    status.update(starting=True, error="")
    try:
        await asyncio.to_thread(stop)
        model = await asyncio.to_thread(model_file)
        if model is None:
            raise RuntimeError("Couldn't find Echo's brain file in Ollama (is hermes3 downloaded?).")
        status["model"] = model
        # Ollama's copy of the brain would hold the same graphics memory twice
        async with httpx.AsyncClient(timeout=30) as client:
            await client.post("http://127.0.0.1:11434/api/generate", json={"model": "echo", "keep_alive": 0})
        log = open(LOG_FILE, "w", encoding="utf-8", errors="replace")  # noqa: SIM115 - kept open for the process
        _proc = subprocess.Popen(args(cfg, model), cwd=str(exe().parent), stdout=log, stderr=subprocess.STDOUT,
                                 creationflags=_NO_WINDOW)
        deadline = time.monotonic() + START_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            await asyncio.sleep(1)
            if _proc.poll() is not None:
                raise RuntimeError(f"The engine stopped while starting - see {LOG_FILE}: {_tail()}")
            if await healthy():
                return
        raise RuntimeError(f"The engine didn't come up within {START_TIMEOUT_SECONDS} seconds.")
    except (OSError, RuntimeError, httpx.HTTPError) as exc:
        status["error"] = str(exc)
        await asyncio.to_thread(stop)
    finally:
        status["starting"] = False


def _tail() -> str:
    try:
        lines = [ln for ln in LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
    except OSError:
        return ""
    return " | ".join(lines[-3:])[-400:]


async def pause(cfg: dict) -> bool:
    """Steps the engine out of graphics memory (a 3D job). Returns whether it was running."""
    global _paused
    _paused += 1
    if cfg.get(CONFIG_KEY) and await asyncio.to_thread(_ours):
        await asyncio.to_thread(stop)
        return True
    return False


async def resume(cfg: dict, was_running: bool) -> None:
    global _paused
    _paused = max(0, _paused - 1)
    if was_running and not _paused and cfg.get(CONFIG_KEY):
        await start(cfg)


# --- Ollama's chat <-> the engine's chat ---------------------------------------------------------

def to_engine(payload: dict) -> dict:
    """An Ollama /api/chat request, written the engine's (OpenAI) way: tool calls get ids and
    string arguments, and tool results point back at the call they answer."""
    messages, waiting, n = [], [], 0
    for m in payload.get("messages", []):
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            calls = []
            for call in m["tool_calls"]:
                n += 1
                fn = call.get("function", {})
                arguments = fn.get("arguments", {})
                calls.append({"id": f"call_{n}", "type": "function", "function": {
                    "name": fn.get("name", ""),
                    "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments)}})
                waiting.append(f"call_{n}")
            messages.append({"role": "assistant", "content": m.get("content") or "", "tool_calls": calls})
        elif role == "tool":
            messages.append({"role": "tool", "tool_call_id": waiting.pop(0) if waiting else f"call_{n}",
                             "content": str(m.get("content") or "")})
        else:
            messages.append({"role": role, "content": m.get("content") or ""})
    options = payload.get("options") or {}
    body = {"model": "echo", "messages": messages, "stream": False,
            "temperature": options.get("temperature", DEFAULT_TEMPERATURE)}
    if "num_predict" in options:
        body["max_tokens"] = options["num_predict"]
    if payload.get("tools"):
        body["tools"] = payload["tools"]
    if payload.get("format") == "json":
        body["response_format"] = {"type": "json_object"}
    return body


def from_engine(data: dict) -> dict:
    """The engine's answer, written the way Ollama answers - what the rest of Echo reads."""
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    out = {"role": "assistant", "content": msg.get("content") or ""}
    calls = []
    for call in msg.get("tool_calls") or []:
        fn = call.get("function") or {}
        arguments = fn.get("arguments") or {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments.strip() else {}
            except json.JSONDecodeError:
                arguments = {}
        calls.append({"function": {"name": fn.get("name", ""), "arguments": arguments}})
    if calls:
        out["tool_calls"] = calls
    usage = data.get("usage") or {}
    return {"model": "echo", "message": out, "done": True,
            "done_reason": "length" if choice.get("finish_reason") == "length" else "stop",
            "prompt_eval_count": usage.get("prompt_tokens"), "eval_count": usage.get("completion_tokens")}


async def chat(payload: dict, timeout: float = 120) -> dict:
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(f"{BASE}/v1/chat/completions", json=to_engine(payload))
        resp.raise_for_status()
        return from_engine(resp.json())
