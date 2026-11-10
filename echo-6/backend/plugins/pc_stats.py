"""
How the PC is doing, and how Echo itself is doing.

pc_stats: processor load, memory, the graphics card (temperature, load, memory, power,
fan), every drive's free space, how long the PC has been on, and what's using the most
memory. echo_self_report: Echo's own health - its processes and their memory, which AI
models are loaded on the graphics card, whether it can hear, how fast it has been
answering, how its tools have been going, and anything that went wrong.

Read straight from Windows (no extra libraries): memory/processor/uptime through the
Windows API, the graphics card through nvidia-smi, processes through tasklist/PowerShell.
"""
import asyncio
import ctypes
import json
import re
import shutil
import string
import subprocess
import time
from ctypes import wintypes

import launch_utils as lu

PLUGIN_NAME = "pc_stats"
TOGGLE_LABEL = "PC stats & Echo's health report"
CONFIG_KEY = "pc_stats_enabled"
ENABLED_BY_DEFAULT = True
VRAM_COST = "no local model"
SIR_CALLS = True

NO_WINDOW = 0x08000000
_k32 = ctypes.windll.kernel32


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD), ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def _memory() -> dict:
    m = _MEMORYSTATUSEX()
    m.dwLength = ctypes.sizeof(m)
    _k32.GlobalMemoryStatusEx(ctypes.byref(m))
    total = m.ullTotalPhys / 1024 ** 3
    free = m.ullAvailPhys / 1024 ** 3
    return {"total_gb": round(total, 1), "used_gb": round(total - free, 1), "percent": int(m.dwMemoryLoad)}


def _filetime(ft: wintypes.FILETIME) -> int:
    return (ft.dwHighDateTime << 32) | ft.dwLowDateTime


_last_cpu: tuple[int, int] | None = None


def _cpu_percent() -> float:
    """Processor load since the last reading (a short sample the first time)."""
    global _last_cpu

    def sample():
        idle, kernel, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
        _k32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))
        return _filetime(idle), _filetime(kernel) + _filetime(user)

    if _last_cpu is None:
        _last_cpu = sample()
        time.sleep(0.4)
    idle, total = sample()
    d_idle, d_total = idle - _last_cpu[0], total - _last_cpu[1]
    _last_cpu = (idle, total)
    return round(100 * (1 - d_idle / d_total), 1) if d_total > 0 else 0.0


def _uptime_hours() -> float:
    _k32.GetTickCount64.restype = ctypes.c_ulonglong
    return round(_k32.GetTickCount64() / 3_600_000, 1)


def _drives() -> list[dict]:
    mask = _k32.GetLogicalDrives()
    out = []
    for i, letter in enumerate(string.ascii_uppercase):
        if not mask & (1 << i):
            continue
        root = f"{letter}:\\"
        if _k32.GetDriveTypeW(root) != 3:  # fixed disks only
            continue
        try:
            usage = shutil.disk_usage(root)
        except OSError:
            continue
        out.append({"drive": f"{letter}:", "free_gb": round(usage.free / 1024 ** 3), "total_gb": round(usage.total / 1024 ** 3),
                    "percent_used": round(100 * usage.used / usage.total)})
    return out


def _gpu() -> dict | None:
    fields = "name,temperature.gpu,utilization.gpu,memory.used,memory.total,power.draw,power.limit,fan.speed"
    try:
        out = subprocess.run(["nvidia-smi", f"--query-gpu={fields}", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=5, creationflags=NO_WINDOW).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    if not out:
        return None
    parts = [p.strip() for p in out.splitlines()[0].split(",")]

    def num(v):
        try:
            return float(v)
        except ValueError:
            return None

    return {"name": parts[0], "temp_c": num(parts[1]), "busy_percent": num(parts[2]),
            "vram_used_gb": round((num(parts[3]) or 0) / 1024, 1), "vram_total_gb": round((num(parts[4]) or 0) / 1024),
            "power_w": num(parts[5]), "power_limit_w": num(parts[6]), "fan_percent": num(parts[7])}


def _top_processes(limit: int = 6) -> list[dict]:
    try:
        out = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True, timeout=10,
                             creationflags=NO_WINDOW).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    totals: dict[str, float] = {}
    for line in out.splitlines():
        cells = re.findall(r'"([^"]*)"', line)
        if len(cells) >= 5:
            mb = float(re.sub(r"[^\d]", "", cells[4]) or 0) / 1024
            name = cells[0].removesuffix(".exe")
            totals[name] = totals.get(name, 0) + mb
    top = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    return [{"name": n, "memory_gb": round(mb / 1024, 2)} for n, mb in top]


def snapshot() -> dict:
    return {"cpu_percent": _cpu_percent(), "memory": _memory(), "gpu": _gpu(), "drives": _drives(),
            "uptime_hours": _uptime_hours(), "top_processes": _top_processes()}


# --- Echo's own health -------------------------------------------------------------

def _echo_processes() -> list[dict]:
    script = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match "
              "'ECHO_TRAY|backend\\\\server\\.py|voicelab\\.py|echo_switch|ComfyUI' -or $_.Name -match '^(ollama|llama-server)' } | "
              "Select-Object Name, CommandLine, WorkingSetSize | ConvertTo-Json -Compress")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True, timeout=15,
                             creationflags=NO_WINDOW).stdout
        rows = json.loads(out) if out.strip() else []
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return []
    rows = rows if isinstance(rows, list) else [rows]
    names = [("ECHO_TRAY", "Listener & tray"), ("server.py", "Echo's server"), ("voicelab", "Custom voice"),
             ("echo_switch", "Tap switch"), ("ComfyUI", "Image/3D generator"), ("llama-server", "AI model runner"),
             ("ollama", "Ollama")]
    out_rows: dict[str, float] = {}
    for r in rows:
        text = f"{r.get('Name', '')} {r.get('CommandLine') or ''}"
        label = next((label for key, label in names if key.lower() in text.lower()), r.get("Name", "?"))
        out_rows[label] = out_rows.get(label, 0) + (r.get("WorkingSetSize") or 0) / 1024 ** 3
    return [{"name": k, "memory_gb": round(v, 2)} for k, v in sorted(out_rows.items(), key=lambda kv: -kv[1])]


def _loaded_models() -> list[dict]:
    try:
        import httpx
        data = httpx.get("http://127.0.0.1:11434/api/ps", timeout=3).json()
    except Exception:  # noqa: BLE001
        return []
    return [{"name": m.get("name", "?"), "vram_gb": round(m.get("size_vram", 0) / 1024 ** 3, 1)} for m in data.get("models", [])]


def _hearing() -> dict:
    """What the listener last reported: which mic, and how long ago (a stale report = not listening)."""
    try:
        import httpx
        mic = httpx.get("http://127.0.0.1:8765/api/listener/mic", timeout=2).json()
    except Exception:  # noqa: BLE001
        return {"listening": False, "device": ""}
    age = mic.get("age")
    return {"listening": age is not None and age < 30, "device": mic.get("device", "")}


def self_report() -> dict:
    import activity
    import usage

    times = list(activity.reply_seconds)
    errors = [e["text"] for e in activity.since(0) if e["kind"] == "error"][-3:]
    recent_uses = usage.recent()[:100]
    failed = [u for u in recent_uses if re.search(r"\b(failed|couldn't|could not|error)\b", u.get("result", ""), re.I)]
    return {
        "up_hours": round((time.time() - activity.STARTED) / 3600, 1),
        "hearing": _hearing(),
        "processes": _echo_processes(),
        "models": _loaded_models(),
        "requests": len(times),
        "avg_reply_s": round(sum(times) / len(times), 1) if times else None,
        "slowest_reply_s": max(times) if times else None,
        "tool_uses": len(recent_uses),
        "tool_problems": len(failed),
        "last_problem": failed[0]["result"] if failed else "",
        "errors": errors,
    }


def _plural(n, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def _how_long(hours: float) -> str:
    if hours < 1:
        return _plural(max(1, round(hours * 60)), "minute")
    if hours < 48:
        return _plural(round(hours, 1) if hours < 10 else round(hours), "hour")
    return _plural(round(hours / 24, 1), "day")


def _say_pc(s: dict) -> str:
    parts = [f"The processor is at {round(s['cpu_percent'])} percent, and memory is at {s['memory']['used_gb']} of "
             f"{s['memory']['total_gb']} gigabytes."]
    g = s["gpu"]
    if g:
        parts.append(f"The graphics card is at {round(g['temp_c'] or 0)} degrees, {round(g['busy_percent'] or 0)} percent busy, "
                     f"using {g['vram_used_gb']} of {g['vram_total_gb']} gigabytes of its memory"
                     + (f" and drawing {round(g['power_w'])} watts." if g.get("power_w") else "."))
    low = [d for d in s["drives"] if d["total_gb"] >= 32 and (d["free_gb"] < 50 or d["percent_used"] > 90)]
    if low:
        parts.append("Running low on space: " + ", ".join(f"{d['drive'][0]} drive has {d['free_gb']} gigabytes free" for d in low) + ".")
    else:
        parts.append("All drives have plenty of space.")
    parts.append(f"It's been on for {_how_long(s['uptime_hours'])}.")
    if s["top_processes"]:
        parts.append("Using the most memory: " + ", ".join(p["name"] for p in s["top_processes"][:3]) + ".")
    return " ".join(parts)


async def pc_stats(args: dict) -> str:
    s = await asyncio.to_thread(snapshot)
    return lu.say(_say_pc(s))


async def echo_self_report(args: dict) -> str:
    r = await asyncio.to_thread(self_report)
    mine = sum(p["memory_gb"] for p in r["processes"])
    models = ", ".join(f"{m['name'].split(':')[0]} on {m['vram_gb']} gigabytes of graphics memory" for m in r["models"]) or "no AI models loaded right now"
    hearing = r["hearing"]
    parts = [f"I've been running for {_how_long(r['up_hours'])}."
             + (f" All in, I'm using about {round(mine, 1)} gigabytes of memory, with {models}." if mine >= 0.1 else f" Loaded right now: {models}."),
             f"I'm listening through {hearing['device'] or 'the default mic'}." if hearing["listening"]
             else "My listener isn't running, so I can't hear you right now - only the dashboard and phone work."]
    if r["avg_reply_s"] is not None:
        parts.append(f"I've answered {_plural(r['requests'], 'request')} since I started, in {r['avg_reply_s']} seconds on average"
                     + (f" - the slowest took {r['slowest_reply_s']} seconds." if r["requests"] > 1 else "."))
    if r["tool_uses"]:
        if r["tool_problems"]:
            parts.append(f"Of my last {_plural(r['tool_uses'], 'action')}, {r['tool_problems']} ran into trouble"
                         f" - most recently: {r['last_problem'][:120]}.")
        else:
            parts.append("My last action went fine." if r["tool_uses"] == 1 else f"My last {r['tool_uses']} actions all went fine.")
    parts.append("No errors to report." if not r["errors"] else f"Recent error: {r['errors'][-1][:120]}.")
    return lu.say(" ".join(parts))


SCHEMAS = [
    {"type": "function", "function": {
        "name": "pc_stats",
        "description": "How sir's PC is doing right now: processor and memory use, graphics card temperature/load/memory/power, drive space, uptime, what uses the most memory.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "echo_self_report",
        "description": "Echo reports on itself: its own memory and graphics memory use, which AI models are loaded, how fast it has been answering, and whether its tools have been working.",
        "parameters": {"type": "object", "properties": {}}}},
]

DISPATCH = {"pc_stats": pc_stats, "echo_self_report": echo_self_report}
