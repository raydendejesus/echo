"""
Plays a show or movie on Netflix by name, in the default browser (where sir is
already signed in).

Netflix has no public API and blocks programs from reading its pages, so the title
is found with a web search: when a result is Netflix's own "Watch <Title> | Netflix"
page and that title matches what sir asked for, Echo opens straight to playing it.
When it can't be sure (no match, or several shows share the name, like "Pokemon"),
it opens Netflix's own search for the name instead of guessing - picking the wrong
show is worse than one extra click. Titles found before are remembered.

Controlling what's playing (pause, skip ahead, volume, fullscreen) uses the Netflix
player's own keyboard shortcuts: Echo brings the browser window showing Netflix to
the front, presses the key, and puts back whatever window sir had in front.
"""
import asyncio
import ctypes
import json
import os
import re
from pathlib import Path
from urllib.parse import quote

import launch_utils as lu

PLUGIN_NAME = "netflix_play"
TOGGLE_LABEL = "Netflix (play shows by name, pause, skip)"
CONFIG_KEY = "netflix_play_enabled"
ENABLED_BY_DEFAULT = True
VRAM_COST = "no local model"
# Offered on phone calls from a phone book contact with the "sir" switch on.
SIR_CALLS = True

CACHE_FILE = Path(__file__).resolve().parent.parent / "plugins_data" / "netflix_titles.json"
_TITLE_URL = re.compile(r"netflix\.com/(?:[a-z]{2}(?:-[a-z]{2})?/)?title/(\d+)", re.I)
# Netflix's own page titles: "Watch Avatar: The Last Airbender | Netflix Official Site"
_NETFLIX_PAGE = re.compile(r"^\s*Watch\s+(.+?)\s*\|\s*Netflix", re.I)
CONFIDENT = 0.85


def _cache() -> dict:
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _remember(query: str, title_id: str, name: str) -> None:
    data = _cache()
    data[lu.norm(query)] = {"id": title_id, "name": name}
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


last_lookup: dict = {}  # what the latest search showed: {"query", "searched", "near", "mentioned"}
# The search engine sometimes answers with one or two results, or none: that proves nothing.
ENOUGH_RESULTS = 5


def _search(query: str) -> list[tuple[str, str]]:
    """[(netflix title id, Netflix's own name for it)] from a web search."""
    from ddgs import DDGS

    found: list[tuple[str, str]] = []
    wanted = lu.norm(query)
    last_lookup.update(query=wanted, searched=False, mentioned=False)
    for text in (f"{query} Netflix", f"watch {query} netflix"):
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(text, max_results=12))
        except Exception:  # noqa: BLE001 - the search engine returns nothing at times
            continue
        if len(results) >= ENOUGH_RESULTS:
            last_lookup["searched"] = True
        for r in results:
            # A netflix.com page named for the show (its Tudum news page, say) is evidence enough
            # that it's a Netflix show, even when no title page made it into the results.
            # (Whole words only: the search library sometimes glues two results' titles together.)
            if wanted and "netflix.com/" in r.get("href", "").lower() and re.search(rf"\b{re.escape(wanted)}\b", lu.norm(r.get("title", ""))):
                last_lookup["mentioned"] = True
            id_match = _TITLE_URL.search(r.get("href", ""))
            name_match = _NETFLIX_PAGE.match(r.get("title", ""))
            if id_match and name_match:
                found.append((id_match.group(1), name_match.group(1).strip()))
        if found:
            break
    return found


def not_on_netflix(query: str) -> bool:
    """True only when the last lookup for this title really searched and found nothing like it -
    not when the search engine simply didn't answer, or answered with next to nothing."""
    return (last_lookup.get("query") == lu.norm(query) and last_lookup.get("searched")
            and not last_lookup.get("near") and not last_lookup.get("mentioned"))


def _find(query: str) -> tuple[str, str] | None:
    cached = _cache().get(lu.norm(query))
    if cached:
        return cached["id"], cached["name"]
    hits = _search(query)
    scored = sorted(((lu.score(query, name), tid, name) for tid, name in hits), reverse=True)
    last_lookup["near"] = bool(scored) and scored[0][0] >= 0.5
    if not scored or scored[0][0] < CONFIDENT:
        return None
    # Two different shows matching about as well (a remake, a sequel): don't guess.
    rivals = [s for s in scored if s[1] != scored[0][1] and s[0] >= scored[0][0] - 0.05]
    if rivals:
        return None
    _, tid, name = scored[0]
    _remember(query, tid, name)
    return tid, name


def _open(url: str, key: str) -> str | None:
    blocked = lu.launch_blocked_reason(key)
    if blocked:
        return blocked
    os.startfile(url)
    lu.note_launch(key)
    return None


async def netflix_play(args: dict) -> str:
    title = (args.get("title") or "").strip()
    if not title:
        return lu.say("What should I put on Netflix?")
    found = await asyncio.to_thread(_find, title)
    if found:
        tid, name = found
        blocked = _open(f"https://www.netflix.com/watch/{tid}", f"netflix:{tid}")
        if blocked:
            return lu.say(f"{name}: {blocked}.")
        return lu.say(f"Putting on {name} on Netflix.")
    blocked = _open(f"https://www.netflix.com/search?q={quote(title)}", f"netflix-search:{lu.norm(title)}")
    if blocked:
        return lu.say(f"Netflix: {blocked}.")
    return lu.say(f"I couldn't tell for sure which {title} to play, so I've opened Netflix's search for it - pick the one you want.")


# --- controlling playback through the player's keyboard shortcuts -------------------
VK = {"space": 0x20, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28, "f": 0x46, "m": 0x4D, "alt": 0x12}
SEEK_STEP_SECONDS = 10  # one arrow press in the Netflix player
_user32 = ctypes.windll.user32
_user32.GetForegroundWindow.restype = ctypes.c_void_p
_user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
_user32.IsIconic.argtypes = [ctypes.c_void_p]
_user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]


def _press(name: str, times: int = 1) -> None:
    for _ in range(times):
        _user32.keybd_event(VK[name], 0, 0, 0)
        _user32.keybd_event(VK[name], 0, 2, 0)


def _netflix_window():
    """The browser window whose current tab is Netflix, if there is one."""
    from plugins.app_closer import _open_windows  # same window list the app closer uses

    for win in _open_windows():
        if "netflix" in win["title"].lower():
            return win["hwnd"]
    return None


def _bring_to_front(hwnd) -> bool:
    if _user32.IsIconic(hwnd):
        _user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    # Windows only lets the app the user last used change the front window; a tap of
    # Alt counts as using it, which lets Echo bring the player forward.
    _press("alt")
    _user32.SetForegroundWindow(hwnd)
    return _user32.GetForegroundWindow() == hwnd


_SPOKEN = {
    "play_pause": "Done - toggled play and pause on Netflix.",
    "fullscreen": "Toggled fullscreen.",
    "mute": "Toggled mute on Netflix.",
    "volume_up": "Turned Netflix up.",
    "volume_down": "Turned Netflix down.",
}


async def netflix_control(args: dict) -> str:
    action = (args.get("action") or "").strip().lower()
    aliases = {"pause": "play_pause", "play": "play_pause", "resume": "play_pause", "rewind": "back",
               "backward": "back", "skip": "forward", "fast_forward": "forward", "unmute": "mute",
               "louder": "volume_up", "quieter": "volume_down"}
    action = aliases.get(action.replace(" ", "_"), action)
    if action not in ("play_pause", "forward", "back", "fullscreen", "mute", "volume_up", "volume_down"):
        action = lu.action_from_words({"play_pause", "forward", "back", "fullscreen", "mute", "volume_up", "volume_down"}, [
            (("full screen", "fullscreen"), "fullscreen"), (("unmute", "mute"), "mute"),
            (("turn it up", "turn netflix up", "louder", "volume up"), "volume_up"),
            (("turn it down", "turn netflix down", "quieter", "volume down"), "volume_down"),
            (("skip ahead", "fast forward", "forward"), "forward"), (("go back", "rewind", "back"), "back"),
            (("pause", "play", "resume"), "play_pause")]) or action
    keys = {"play_pause": "space", "forward": "right", "back": "left", "fullscreen": "f", "mute": "m",
            "volume_up": "up", "volume_down": "down"}
    if action not in keys:
        return lu.say("I can pause or play Netflix, skip forward or back, change its volume, mute it, or go fullscreen.")

    hwnd = await asyncio.to_thread(_netflix_window)
    if hwnd is None:
        return lu.say("I don't see Netflix open - if it's in a browser tab, that tab needs to be the one showing.")
    previous = _user32.GetForegroundWindow()
    if not _bring_to_front(hwnd):
        await asyncio.sleep(0.2)
        if not _bring_to_front(hwnd):
            return lu.say("Windows wouldn't let me bring Netflix to the front just now - click on it once and ask again.")
    await asyncio.sleep(0.15)

    if action in ("forward", "back"):
        seconds = args.get("seconds")
        seconds = seconds if isinstance(seconds, (int, float)) and seconds > 0 else SEEK_STEP_SECONDS
        presses = max(1, min(60, round(seconds / SEEK_STEP_SECONDS)))
        _press(keys[action], presses)
        moved = presses * SEEK_STEP_SECONDS
        amount = f"{moved // 60} minute{'s' if moved >= 120 else ''}" if moved % 60 == 0 else f"{moved} seconds"
        reply = f"Skipped {'ahead' if action == 'forward' else 'back'} {amount}."
    elif action in ("volume_up", "volume_down"):
        _press(keys[action], 3)
        reply = _SPOKEN[action]
    else:
        _press(keys[action])
        reply = _SPOKEN[action]

    # Put sir's own window back in front - except after going fullscreen, which is the point.
    if previous and previous != hwnd and action != "fullscreen":
        await asyncio.sleep(0.1)
        _press("alt")
        _user32.SetForegroundWindow(previous)
    return lu.say(reply)


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "netflix_play",
            "description": (
                "Plays a show or movie on Netflix by name - any 'play / put on / throw on <show> (on Netflix)' "
                "request for a TV show or movie (e.g. 'play Pokemon on Netflix', 'throw on Squid Game'). "
                "Opens it in the browser; if the exact title can't be found it opens Netflix's search for it."
            ),
            "parameters": {
                "type": "object",
                "properties": {"title": {"type": "string", "description": "The show or movie, as sir said it."}},
                "required": ["title"],
            },
        },
    },
]

SCHEMAS.append({
    "type": "function",
    "function": {
        "name": "netflix_control",
        "description": (
            "Controls what's playing on Netflix in the browser: pause/play, skip forward or back (seconds, "
            "in 10 second steps), volume up/down, mute, or fullscreen. Use this, not Spotify or media keys, "
            "whenever sir mentions Netflix or is watching a show - e.g. 'pause Netflix', 'skip ahead 2 minutes', "
            "'turn Netflix up' (volume_up), 'make Netflix full screen' (fullscreen)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["play_pause", "forward", "back", "volume_up", "volume_down", "mute", "fullscreen"]},
                "seconds": {"type": "number", "description": "How far to skip, for forward/back. Default 10."},
            },
            "required": ["action"],
        },
    },
})

DISPATCH = {"netflix_play": netflix_play, "netflix_control": netflix_control}
