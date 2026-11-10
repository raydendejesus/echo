"""
Sees what's open on the PC and closes apps by name.

Closing is polite first: each window is asked to close the same way clicking its
X does, so an app with unsaved work gets to ask about saving. Only "force close"
ends the process outright. Echo itself (its Python processes) and the model
server are never listed or closed, and Windows' own shell is never force-closed.

Only apps with a window on screen are listed - background services and tray-only
programs aren't, so the list matches what sir sees on the taskbar.
"""
import asyncio
import ctypes
import os
import re
import subprocess
from ctypes import wintypes
from pathlib import Path

import launch_utils as lu

PLUGIN_NAME = "app_closer"
TOGGLE_LABEL = "Close Apps / See What's Running"
CONFIG_KEY = "app_closer_enabled"
ENABLED_BY_DEFAULT = True
VRAM_COST = "no local model"
# Offered on phone calls from a phone book contact with the "sir" switch on.
SIR_CALLS = True

WM_CLOSE = 0x0010
GW_OWNER = 4
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
DWMWA_CLOAKED = 14
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259
CLOSE_WAIT_SECONDS = 5

# Echo, its voice and the model server - closing these would end the conversation.
_NEVER_TOUCH = re.compile(r"^(pythonw?(\d[\d.]*)?|ollama.*|llama-server)\.exe$", re.I)
# Part of Windows: a window of theirs may be closed, the process is never killed.
_NEVER_FORCE = {
    "explorer.exe", "applicationframehost.exe", "dwm.exe", "csrss.exe", "winlogon.exe", "lsass.exe",
    "services.exe", "svchost.exe", "sihost.exe", "shellexperiencehost.exe", "startmenuexperiencehost.exe",
    "searchhost.exe", "textinputhost.exe", "systemsettings.exe", "taskmgr.exe",
}
_NAME_NOISE = re.compile(r"\b(client\s+)?web\s?helper\b|\bhelper\b|\bapplication\b|\.exe$", re.I)

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_dwmapi = ctypes.WinDLL("dwmapi")
_version = ctypes.WinDLL("version")

_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_user32.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.IsWindow.argtypes = [wintypes.HWND]
_user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
_user32.GetWindow.restype = wintypes.HWND
_user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
_user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
_kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

_descriptions: dict[str, str] = {}


def _exe_path(pid: int) -> str:
    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if _kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        _kernel32.CloseHandle(handle)


def _process_alive(pid: int) -> bool:
    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        return bool(_kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == STILL_ACTIVE
    finally:
        _kernel32.CloseHandle(handle)


def _file_description(path: str) -> str:
    """The friendly name Windows shows for an .exe ("Google Chrome" for chrome.exe)."""
    if path in _descriptions:
        return _descriptions[path]
    text = ""
    try:
        size = _version.GetFileVersionInfoSizeW(path, None)
        if size:
            data = ctypes.create_string_buffer(size)
            if _version.GetFileVersionInfoW(path, 0, size, data):
                ptr, length = ctypes.c_void_p(), wintypes.UINT()
                if _version.VerQueryValueW(data, "\\VarFileInfo\\Translation", ctypes.byref(ptr), ctypes.byref(length)) and length.value >= 4:
                    lang, codepage = ctypes.cast(ptr, ctypes.POINTER(wintypes.WORD * 2)).contents
                    key = f"\\StringFileInfo\\{lang:04x}{codepage:04x}\\FileDescription"
                    if _version.VerQueryValueW(data, key, ctypes.byref(ptr), ctypes.byref(length)) and length.value:
                        text = ctypes.wstring_at(ptr).strip()
    except OSError:
        text = ""
    _descriptions[path] = text
    return text


def _friendly_name(path: str) -> str:
    stem = Path(path).stem
    name = _NAME_NOISE.sub("", _file_description(path) or "").strip(" -")
    return name or stem[:1].upper() + stem[1:]


def _open_windows() -> list[dict]:
    """Every taskbar-style window: visible, titled, not owned by another window, not a tool
    window, and not hidden by Windows (suspended Store apps are "cloaked")."""
    found = []

    def visit(hwnd, _lparam):
        if not _user32.IsWindowVisible(hwnd) or _user32.GetWindow(hwnd, GW_OWNER):
            return True
        if _user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
            return True
        cloaked = ctypes.c_int(0)
        _dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        if cloaked.value:
            return True
        length = _user32.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        _user32.GetWindowTextW(hwnd, buf, length + 1)
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        found.append({"hwnd": hwnd, "title": buf.value, "pid": pid.value})
        return True

    _user32.EnumWindows(_WNDENUMPROC(visit), 0)
    return found


def _running_apps() -> list[dict]:
    """Open windows grouped into apps: [{name, exe, keys, windows, pids}]."""
    own = {os.getpid(), os.getppid()}
    apps: dict[str, dict] = {}
    for win in _open_windows():
        if win["pid"] in own:
            continue
        path = _exe_path(win["pid"])
        exe = Path(path).name.lower() if path else ""
        if not exe or _NEVER_TOUCH.match(exe):
            continue
        if exe == "explorer.exe" and win["title"] == "Program Manager":
            continue  # the desktop itself
        if exe == "applicationframehost.exe":
            # A Store app (Settings, Calculator...): the frame host owns every one of
            # them, so each window is its own app, named by its title.
            key, name = f"uwp:{win['hwnd']}", win["title"]
        elif exe == "explorer.exe":
            key, name = "explorer", "File Explorer"
        else:
            key, name = path.lower(), _friendly_name(path)
        app = apps.setdefault(key, {"name": name, "exe": exe, "keys": {name, Path(exe).stem}, "windows": [], "pids": set()})
        app["windows"].append(win)
        app["pids"].add(win["pid"])
    return list(apps.values())


def _describe(apps: list[dict]) -> str:
    parts = []
    for app in sorted(apps, key=lambda a: a["name"].lower()):
        count = len(app["windows"])
        parts.append(f"{app['name']} ({count} windows)" if count > 1 else app["name"])
    return ", ".join(parts)


def _match(query: str, apps: list[dict]) -> tuple[dict, list[dict]] | None:
    """The app sir meant, and which of its windows. A query that names one window's title
    better than any app name ("the YouTube window") closes just that window."""
    best = None
    for app in apps:
        app_score = max(lu.score(query, k) for k in app["keys"])
        if best is None or app_score > best[0]:
            best = (app_score, app, app["windows"])
        for win in app["windows"]:
            win_score = lu.score(query, win["title"])
            if win_score > best[0] and win_score > app_score:
                best = (win_score, app, [win])
    if best is None or best[0] < lu.MATCH_THRESHOLD:
        return None
    return best[1], best[2]


async def list_running_apps(args: dict) -> str:
    apps = await asyncio.to_thread(_running_apps)
    if not apps:
        return lu.say("Nothing is open on the desktop right now, sir.")
    return lu.say(f"You've got {len(apps)} apps open: {_describe(apps)}.")


async def _close_one(query: str, force: bool) -> str:
    apps = await asyncio.to_thread(_running_apps)
    found = _match(query, apps)
    if found is None:
        near = ", ".join(a["name"] for _, a in lu.rank(query, apps)[:3])
        return f"I don't see anything open called {query}" + (f" - open right now: {near}." if near else ".")
    app, windows = found
    label = app["name"] if len(windows) == len(app["windows"]) else f"the {app['name']} window \"{windows[0]['title']}\""

    if force:
        if app["exe"] in _NEVER_FORCE:
            return f"I won't force-close {app['name']} - it's part of Windows. I can close its window normally instead."
        pids = {w["pid"] for w in windows}
        for pid in pids:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=15, creationflags=lu.CREATE_NO_WINDOW)
        await asyncio.sleep(0.5)
        if any(_process_alive(pid) for pid in pids):
            return f"I tried to force-close {label}, but it's still running - it may need administrator rights."
        return f"Force-closed {label}."

    for win in windows:
        _user32.PostMessageW(win["hwnd"], WM_CLOSE, 0, 0)
    handles = [w["hwnd"] for w in windows]
    still_open = handles
    for _ in range(CLOSE_WAIT_SECONDS * 4):
        await asyncio.sleep(0.25)
        still_open = [h for h in handles if _user32.IsWindow(h) and _user32.IsWindowVisible(h)]
        if not still_open:
            break
    if still_open:
        return (
            f"{label} is still open - it's probably asking whether to save something. Say force close "
            f"{app['name']} to close it anyway, but anything unsaved would be lost."
        )
    if len(windows) == len(app["windows"]) and any(_process_alive(pid) for pid in app["pids"]):
        await asyncio.sleep(1.5)
        if any(_process_alive(pid) for pid in app["pids"]) and app["exe"] not in _NEVER_FORCE:
            return (
                f"Closed {label}'s window, but it's still running in the background. Say force close "
                f"{app['name']} if you want it fully shut."
            )
    return f"Closed {label}."


async def close_app(args: dict) -> str:
    names = args.get("names") or []
    if isinstance(names, str):
        names = [names]
    if args.get("name"):
        names = [args["name"], *names]
    names = [n.strip() for n in names if isinstance(n, str) and n.strip()]
    if not names:
        return lu.say("Which app should I close?")
    force = bool(args.get("force"))
    results = [await _close_one(name, force) for name in dict.fromkeys(names)]
    return lu.say(" ".join(results))


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "list_running_apps",
            "description": "Lists the apps that are open on sir's computer right now (what's on the taskbar).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "close_app",
            "description": (
                "Closes open apps on sir's computer by name, the same way clicking the X does (the app can still "
                "ask to save). Pass several names to close several apps. Set force only when sir says force close "
                "or kill - that ends the app without saving."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "The app (or window) to close, as sir said it."},
                    "names": {"type": "array", "items": {"type": "string"}, "description": "Several apps to close."},
                    "force": {"type": "boolean", "description": "Only when sir explicitly asks to force close."},
                },
            },
        },
    },
]

DISPATCH = {"list_running_apps": list_running_apps, "close_app": close_app}
