"""
"Is Echo set up?" - a checklist the dashboard's Setup tab shows, required things first,
then optional extras, each with how to fix it. Some can be fixed with one click (they
only download official files or rebuild Echo's own model).
"""
import asyncio
import json
import shutil
import subprocess
import zipfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
NO_WINDOW = 0x08000000
OLLAMA = "http://127.0.0.1:11434"
SANDBOX_PYTHON_URL = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip"
ADB_URL = "https://dl.google.com/android/repository/platform-tools-latest-windows.zip"
VISION_MODEL = "qwen2.5vl:7b"

_fixing: dict[str, str] = {}  # fix id -> "running" / "done" / error text


def _ollama_models() -> list[str] | None:
    try:
        tags = httpx.get(f"{OLLAMA}/api/tags", timeout=3).json()
        return [m["name"] for m in tags.get("models", [])]
    except Exception:  # noqa: BLE001
        return None


def _whisper_cached(name: str) -> bool:
    hub = Path.home() / ".cache" / "huggingface" / "hub"
    return any(hub.glob(f"models--*faster-whisper-{name}*")) or any(hub.glob(f"models--*{name}*"))


def _json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def checklist() -> list[dict]:
    import config as config_module

    cfg = config_module.load_config()
    models = _ollama_models()
    items = []

    def add(key, label, ok, detail, how="", fix=None, required=True):
        items.append({"key": key, "label": label, "ok": bool(ok), "detail": detail, "how": how,
                      "fix": fix, "fixing": _fixing.get(fix or ""), "required": required})

    add("ollama", "Ollama (runs the AI models)", models is not None,
        "running" if models is not None else "not running or not installed",
        "Install it from ollama.com (or run: winget install Ollama.Ollama), then start it.")
    has_echo = bool(models) and any(m.split(":")[0] == "echo" for m in models)
    add("brain", "Echo's brain (hermes3 + Echo's personality)", has_echo,
        "ready" if has_echo else "not built yet - about a 5 GB download",
        "Click Fix it, or run SETUP.bat.", fix="brain" if models is not None else None)
    add("hearing", "Speech recognition (Whisper large-v3-turbo)", _whisper_cached("large-v3-turbo"),
        "downloaded" if _whisper_cached("large-v3-turbo") else "downloads by itself (about 1.6 GB) the first time Echo listens",
        "Nothing to do - or run SETUP.bat to download it ahead of time.")
    add("passcode", "Your secret passcode", (BACKEND / "passcode.json").exists(),
        "set" if (BACKEND / "passcode.json").exists() else "not set - needed for shutdown and phone approvals",
        "Type one into the box next to the Shut Down PC toggle.")
    sandbox_py = ROOT / "tools" / "sandbox-python" / "python.exe"
    add("sandbox", "Sandbox for Echo's code (its own small Python)", sandbox_py.exists(),
        "installed" if sandbox_py.exists() else "missing - Echo can't run code it writes",
        "Click Fix it (downloads python.org's official 10 MB embeddable Python).", fix="sandbox")

    # optional extras
    add("node", "Node.js (lets Echo check and run JavaScript)", shutil.which("node") is not None,
        "installed" if shutil.which("node") else "not installed - app checking is skipped",
        "Install from nodejs.org (or: winget install OpenJS.NodeJS.LTS).", required=False)
    vision = bool(models) and any(m.startswith(VISION_MODEL) for m in models)
    add("vision", "Eyes (screen, camera, TV screenshots)", vision,
        "ready" if vision else "not downloaded (about 6 GB)", "Click Fix it to download it.",
        fix="vision" if models is not None else None, required=False)
    voicelab = False
    try:
        voicelab = httpx.get("http://127.0.0.1:8791/", timeout=1).status_code < 500
    except Exception:  # noqa: BLE001
        pass
    add("voice", "Custom voice (VoiceLab)", voicelab,
        "running" if voicelab else "not running - Echo uses its backup voice instead",
        "Optional. Echo works fine with the backup voice.", required=False)
    adb = ROOT / "tools" / "platform-tools" / "adb.exe"
    add("adb", "TV control tool (Google's ADB)", adb.exists(),
        "installed" if adb.exists() else "missing - needed for Netflix and apps on a Google TV",
        "Click Fix it (downloads Google's official platform-tools).", fix="adb", required=False)
    tv = _json(BACKEND / "plugins_data" / "tv.json")
    add("tv", "Google TV / Chromecast", bool(tv.get("host")),
        f"found: {tv.get('name')}" if tv.get("host") else "not found yet - ask Echo to put something on the TV",
        "TV and PC on the same Wi-Fi. For Netflix/apps: on the TV, Settings > System > About > press OK on "
        "'Android TV OS build' 7 times, then Developer options > USB debugging on.", required=False)
    phone = (BACKEND / "telephony_config.json").exists()
    add("phone", "Phone calls (Telnyx)", phone, "set up" if phone else "not set up",
        "Optional - see the README's calling section.", required=False)
    spotify = (BACKEND / "connections" / "spotify_token.json").exists()
    add("spotify", "Spotify", spotify, "connected" if spotify else "not connected",
        "Connections tab > Spotify (needs Spotify Premium).", required=False)
    return items


# --- one-click fixes (official downloads / Echo's own model only) ---------------------

async def _download_zip(url: str, dest: Path) -> None:
    async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
    tmp = dest.parent / (dest.name + "_download.zip")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(resp.content)
    with zipfile.ZipFile(tmp) as zf:
        zf.extractall(dest)
    tmp.unlink(missing_ok=True)


async def _run_fix(fix: str) -> None:
    _fixing[fix] = "running"
    try:
        if fix == "sandbox":
            target = ROOT / "tools" / "sandbox-python"
            shutil.rmtree(target, ignore_errors=True)
            await _download_zip(SANDBOX_PYTHON_URL, target)
        elif fix == "adb":
            await _download_zip(ADB_URL, ROOT / "tools")
        elif fix in ("brain", "vision"):
            if fix == "brain":
                await asyncio.to_thread(subprocess.run, ["ollama", "pull", "hermes3"], capture_output=True, creationflags=NO_WINDOW)
                result = await asyncio.to_thread(subprocess.run, ["ollama", "create", "echo", "-f", str(BACKEND / "Modelfile")],
                                                 capture_output=True, text=True, creationflags=NO_WINDOW)
            else:
                result = await asyncio.to_thread(subprocess.run, ["ollama", "pull", VISION_MODEL], capture_output=True,
                                                 text=True, creationflags=NO_WINDOW)
            if result.returncode != 0:
                raise RuntimeError((result.stderr or result.stdout or "ollama failed")[-300:])
        else:
            raise ValueError("unknown fix")
        _fixing[fix] = "done"
    except Exception as exc:  # noqa: BLE001 - shown on the Setup tab
        _fixing[fix] = f"failed: {exc}"


def start_fix(fix: str) -> str:
    if _fixing.get(fix) == "running":
        return "already running"
    asyncio.get_running_loop().create_task(_run_fix(fix))
    return "started"
