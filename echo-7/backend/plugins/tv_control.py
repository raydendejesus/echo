"""
Puts things on the TV - "play <video> on the TV", "put Pokemon on the TV", pause,
volume - for a Google TV (or any Android TV) on the same Wi-Fi.

Two ways of talking to the TV, both local to the home network:

- Google Cast, the protocol a phone's Cast button uses. Echo finds the TV the same
  way a phone does (it announces itself on the network) and asks it to open YouTube
  at a video; the TV streams it itself. Works with any Cast device, no setup.
- ADB, Android's own debug link. A Google TV is an Android computer: with network
  debugging switched on in its developer options, Echo can open any app on it at a
  given page - the Netflix app straight to a show, for instance - and press its
  remote's buttons. Needed for anything besides YouTube, because Netflix and most
  apps don't accept casting from anything but their own phone apps. The first time,
  the TV asks once whether to allow this PC; choose "Always allow".

The TV's address is remembered in plugins_data/tv.json.
"""
import asyncio
import json
import re
import subprocess
import time
import uuid
from pathlib import Path
from urllib.parse import quote

import launch_utils as lu

PLUGIN_NAME = "tv_control"
TOGGLE_LABEL = "TV (Google TV: YouTube, live streams, Netflix, remote)"
CONFIG_KEY = "tv_control_enabled"
ENABLED_BY_DEFAULT = True
VRAM_COST = "no local model"
SIR_CALLS = True

ADB = Path(__file__).resolve().parent.parent.parent / "tools" / "platform-tools" / "adb.exe"
STATE_FILE = Path(__file__).resolve().parent.parent / "plugins_data" / "tv.json"
# Creators sir has asked for, by name -> YouTube handle, so the second time is instant
# (and so the hearing word list knows their names).
CHANNELS_FILE = Path(__file__).resolve().parent.parent / "plugins_data" / "youtube_channels.json"
MIN_CREATOR_SUBSCRIBERS = 50_000
CREATOR_NAME_MATCH = 0.75
_BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}
ADB_PORT = 5555
DISCOVERY_SECONDS = 6

NETFLIX_PACKAGE = "com.netflix.ninja"
YOUTUBE_PACKAGE = "com.google.android.youtube.tv"
# Apps sir is likely to name, so they open without a lookup.
KNOWN_APPS = {
    "netflix": NETFLIX_PACKAGE,
    "youtube": YOUTUBE_PACKAGE,
    "disney plus": "com.disney.disneyplus",
    "hulu": "com.hulu.livingroomplus",
    "prime video": "com.amazon.amazonvideo.livingroom",
    "max": "com.wbd.stream",
    "spotify": "com.spotify.tv.android",
    "twitch": "tv.twitch.android.app",
    "plex": "com.plexapp.android",
    "crunchyroll": "com.crunchyroll.crunchyroid",
}
# The remote's buttons, as Android key codes.
KEYS = {
    "play_pause": 85, "play": 126, "pause": 127, "stop": 86, "next": 87, "previous": 88,
    "forward": 90, "rewind": 89, "volume_up": 24, "volume_down": 25, "mute": 164,
    "home": 3, "back": 4, "power_off": 223, "power_on": 224,
}


# --- finding the TV ---------------------------------------------------------------

def _state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(data: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


_cast = None


def _get_cast():
    """The TV as a Cast device (connected), found by its own announcement on the network."""
    global _cast
    import pychromecast

    if _cast is not None and _cast.socket_client.is_connected:
        return _cast
    info = _state()
    cast = None
    if info.get("host") and info.get("uuid"):
        # Known TV: connect straight to its address - no network-wide search needed.
        try:
            cast = pychromecast.get_chromecast_from_host(
                (info["host"], info.get("port", 8009), uuid.UUID(info["uuid"]), info.get("model"), info.get("name"))
            )
            cast.wait(timeout=10)
        except Exception:  # noqa: BLE001 - the TV may have a new address; search again below
            cast = None
    if cast is None:
        casts, browser = pychromecast.get_chromecasts(timeout=DISCOVERY_SECONDS)
        browser.stop_discovery()
        tvs = [c for c in casts if c.cast_info.cast_type == "cast"] or casts
        if info.get("name"):
            tvs = [c for c in tvs if c.cast_info.friendly_name == info["name"]] or tvs
        if not tvs:
            return None
        ci = tvs[0].cast_info
        _save_state({**info, "name": ci.friendly_name, "host": ci.host, "port": ci.port,
                     "uuid": str(ci.uuid), "model": ci.model_name})
        cast = pychromecast.get_chromecast_from_host((ci.host, ci.port, ci.uuid, ci.model_name, ci.friendly_name))
        cast.wait(timeout=10)
    _cast = cast
    return cast


def _tv_host() -> str | None:
    host = _state().get("host")
    if host:
        return host
    cast = _get_cast()
    return cast.cast_info.host if cast else None


# --- ADB --------------------------------------------------------------------------

def _adb(*args: str, timeout: int = 15) -> subprocess.CompletedProcess:
    return subprocess.run([str(ADB), *args], capture_output=True, text=True, timeout=timeout,
                          creationflags=lu.CREATE_NO_WINDOW)


def _adb_target() -> tuple[str | None, str]:
    """('host:5555', '') when the TV's debug link is usable, else (None, what's wrong)."""
    if not ADB.exists():
        return None, "the ADB tool isn't installed in Echo's tools folder"
    host = _tv_host()
    if not host:
        return None, "I can't find the TV on the network - is it on, and on the same Wi-Fi?"
    target = f"{host}:{ADB_PORT}"
    out = (_adb("connect", target).stdout or "").lower()
    if "connected" not in out:
        return None, ("the TV's network debugging isn't switched on - see the TV setup steps in the README "
                      "(Developer options, then Network debugging)")
    state = _adb("-s", target, "get-state").stdout.strip()
    if state != "device":
        return None, "the TV is asking whether to allow this PC - pick 'Always allow' on the TV, then ask again"
    return target, ""


def _adb_shell(target: str, command: str) -> str:
    # The TV reports failures on stderr - read both, or a refusal looks like success.
    result = _adb("-s", target, "shell", command)
    return (result.stdout or "") + (result.stderr or "")


# How each TV app wants to be opened at a page. Netflix's TV app only takes its links
# when they're handed straight to its main screen, as http (not https), with these
# flags - the same way the TV's own launcher opens it.
_APP_LINKS = {
    NETFLIX_PACKAGE: '-c android.intent.category.LEANBACK_LAUNCHER -a android.intent.action.VIEW -d "{url}" '
                     "-f 0x10808000 -e source 30 com.netflix.ninja/.MainActivity",
}


def _open_on_tv(target: str, url: str, package: str) -> bool:
    if package == NETFLIX_PACKAGE:
        url = url.replace("https://", "http://")
    template = _APP_LINKS.get(package, '-a android.intent.action.VIEW -d "{url}" ' + package)
    out = _adb_shell(target, "am start " + template.format(url=url))
    return "Error" not in out and "unable to resolve" not in out


def _app_running(target: str, package: str) -> bool:
    return bool(_adb_shell(target, f"pidof {package}").strip())


def _app_on_screen(target: str, package: str) -> bool:
    return package in _adb_shell(target, "dumpsys activity activities | grep mResumedActivity")


def _open_and_confirm(target: str, url: str, package: str) -> tuple[bool, bool]:
    """Opens the link, then checks a few seconds later that the app is still the one on
    screen (Netflix sometimes closes itself, leaving whatever was underneath showing) and
    sends the link once more if not. Returns (worked, app was already running)."""
    was_running = _app_running(target, package)
    if not _open_on_tv(target, url, package):
        return False, was_running
    for attempt in range(2):
        time.sleep(6)
        if _app_on_screen(target, package):
            return True, was_running
        if attempt == 0:
            _open_on_tv(target, url, package)
    return False, was_running


def _launch_app(target: str, package: str) -> bool:
    out = _adb_shell(target, f"monkey -p {package} -c android.intent.category.LEANBACK_LAUNCHER 1")
    return "No activities found" not in out and "aborted" not in out.lower()


# --- YouTube ----------------------------------------------------------------------

def _youtube_id(query: str) -> tuple[str, str] | None:
    """(video id, title) for the best YouTube match."""
    from ddgs import DDGS

    searches = (
        lambda d: d.videos(query, max_results=10),
        lambda d: d.text(f"{query} youtube", max_results=10),
        lambda d: d.text(f"site:youtube.com {query}", max_results=10),
    )
    for search in searches:
        try:
            with DDGS() as ddgs:
                results = list(search(ddgs))
        except Exception:  # noqa: BLE001 - search engines return nothing at times
            continue
        for r in results:
            url = r.get("content") or r.get("href") or ""
            m = re.search(r"(?:youtube\.com/watch\?v=|youtu\.be/)([\w-]{11})", url)
            if m:
                title = re.split(r"\s+-\s+YouTube|\s*\|\s*", r.get("title") or "")[0].strip()
                return m.group(1), (title or query)[:80]
    return None


def _cast_youtube(video_id: str) -> bool:
    from pychromecast.controllers.youtube import YouTubeController

    cast = _get_cast()
    if cast is None:
        return False
    yt = YouTubeController()
    cast.register_handler(yt)
    yt.play_video(video_id)
    return True


# --- YouTube creators: live now, or their newest video ---------------------------

def _channels() -> dict:
    try:
        return json.loads(CHANNELS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def known_channel_names() -> list[str]:
    return [v.get("name") or k for k, v in _channels().items()]


def _find_channel(name: str) -> tuple[str, str] | None:
    """(handle like '@TheBurntPeanut', display name) for a creator sir names."""
    key = lu.norm(name)
    cached = _channels().get(key)
    if cached:
        return cached["handle"], cached["name"]
    # A creator is a channel whose name is what sir said AND that people actually watch -
    # the search also lists tiny channels that just happen to be named after a topic.
    candidates = [(h, n) for h, n, subs in _search_youtube_channels(name) if subs >= MIN_CREATOR_SUBSCRIBERS]
    if not candidates:
        web = _search_web_for_channel(name)
        candidates = [web] if web else []
    return max(candidates, key=lambda c: _name_match(name, c), default=None)


def _name_match(said: str, channel: tuple[str, str]) -> float:
    """How much what sir said IS this channel's name (1.0 = exactly). A name that only covers
    part of it ("pokemon" in "pokemon theme song") scores by how much of it it covers."""
    from difflib import SequenceMatcher

    def forms(text: str) -> set[str]:
        flat = lu.norm(text).replace(" ", "")
        return {flat, flat[3:] if flat.startswith("the") and len(flat) > 6 else flat}

    handle, shown = channel
    best = 0.0
    for a in forms(said):
        for b in forms(shown) | forms(handle.lstrip("@")):
            if not a or not b:
                continue
            if a == b:
                return 1.0
            if a in b or b in a:
                score = min(len(a), len(b)) / max(len(a), len(b))
            else:
                score = SequenceMatcher(None, a, b).ratio()
            best = max(best, score)
    return best


def _remember_channel(said: str, handle: str, shown: str) -> None:
    data = _channels()
    data[lu.norm(said)] = {"handle": handle, "name": shown}
    CHANNELS_FILE.parent.mkdir(parents=True, exist_ok=True)
    CHANNELS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _squash(text: str) -> str:
    words = lu.norm(text).split()
    if words[:1] == ["the"]:
        words = words[1:]
    return "".join(words)


def _creator_for(said: str) -> tuple[str, str] | None:
    """The channel sir means, only if what he said really is that channel's name - "the burnt
    peanut" or even a misheard "bird peanut" is TheBurntPeanut, but "pokemon theme song" is a
    video, not the "Pokemon TV" channel YouTube's search happens to list first."""
    if not said.strip():
        return None
    cached = _channels().get(lu.norm(said))
    if cached:
        return cached["handle"], cached["name"]
    found = _find_channel(said)
    if not found:
        return None
    handle, shown = found
    if _name_match(said, found) < CREATOR_NAME_MATCH:
        return None
    _remember_channel(said, handle, shown)
    return handle, shown


def _subscribers(text: str) -> float:
    m = re.search(r"([\d.,]+)\s*([KMB]?)\s+subscribers", text)
    if not m:
        return 0.0
    return float(m.group(1).replace(",", "")) * {"": 1, "K": 1e3, "M": 1e6, "B": 1e9}[m.group(2)]


def _search_youtube_channels(name: str) -> list[tuple[str, str, float]]:
    """YouTube's own search, filtered to channels: [(handle, display name, subscribers)]."""
    from urllib.parse import quote_plus

    try:
        page = _youtube_page(f"results?search_query={quote_plus(name)}&sp=EgIQAg%253D%253D")
    except Exception:  # noqa: BLE001
        return []
    found = []
    for block in page.split('"channelRenderer":')[1:6]:
        block = block[:4000]
        shown = re.search(r'"simpleText":"([^"]+)"', block)
        handle = re.search(r'"canonicalBaseUrl":"/(@[^"]+)"', block)
        if shown and handle:
            found.append((handle.group(1), shown.group(1), _subscribers(block)))
    return found


def _search_web_for_channel(name: str) -> tuple[str, str] | None:
    from ddgs import DDGS

    for query in (f"{name} youtube channel", f"{name} youtube"):
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=10))
        except Exception:  # noqa: BLE001
            continue
        for r in results:
            m = re.search(r"youtube\.com/(@[\w.\-]+|channel/UC[\w-]{22})", r.get("href", ""))
            if m:
                shown = re.split(r"\s+-\s+YouTube", r.get("title") or "")[0].strip() or name
                return m.group(1), shown
    return None


def _youtube_page(path: str) -> str:
    import httpx

    r = httpx.get(f"https://www.youtube.com/{path}", headers=_BROWSER_HEADERS, cookies={"CONSENT": "YES+1"},
                  follow_redirects=True, timeout=15)
    return r.text


def _live_now(handle: str) -> tuple[str, str] | None:
    """(video id, title) if the channel is streaming right now."""
    page = _youtube_page(f"{handle}/live")
    if '"isLiveNow":true' not in page:
        return None
    m = re.search(r'<link rel="canonical" href="https://www\.youtube\.com/watch\?v=([\w-]{11})"', page)
    if not m:
        return None
    title = re.search(r'<meta name="title" content="([^"]*)"', page)
    return m.group(1), (title.group(1) if title else "the live stream")


def _latest_video(handle: str) -> tuple[str, str] | None:
    page = _youtube_page(f"{handle}/videos")
    ids = re.findall(r'"videoId":"([\w-]{11})"', page)
    # The first video card's title (the page's other "title" fields are buttons and menus).
    titles = re.findall(r'"title":\{"content":"([^"]+)"', page) or re.findall(r'"title":\{"runs":\[\{"text":"([^"]+)"', page)
    if not ids:
        return None
    return ids[0], (titles[0] if titles else "their newest video")


def _clean_title(title: str) -> str:
    # Stream titles are full of emoji and shouting; keep it speakable.
    title = re.sub(r"[^\w\s'&,.!?:#-]", "", title)
    return re.sub(r"\s+", " ", title).strip()[:90]


# --- tools ------------------------------------------------------------------------

async def tv_play_youtube(args: dict) -> str:
    query = (args.get("query") or args.get("title") or args.get("creator") or args.get("name") or "").strip()
    if not query:
        return lu.say("What should I put on the TV?")
    if not args.get("_from_creator") and await asyncio.to_thread(_creator_for, query):
        return await tv_youtube_creator({"creator": query})
    found = await asyncio.to_thread(_youtube_id, query)
    if not found:
        return lu.say(f"I couldn't find {query} on YouTube.")
    video_id, title = found
    try:
        cast_ok = await asyncio.to_thread(_cast_youtube, video_id)
    except Exception as exc:  # noqa: BLE001 - fall back to the TV's YouTube app over ADB
        print(f"[tv] YouTube cast failed: {exc!r}", flush=True)
        cast_ok = False
    if not cast_ok:
        target, problem = await asyncio.to_thread(_adb_target)
        if not target or not await asyncio.to_thread(_open_on_tv, target, f"https://www.youtube.com/watch?v={video_id}", YOUTUBE_PACKAGE):
            return lu.say(f"I found {title}, but couldn't get it onto the TV - {problem or 'the TV did not respond'}.")
    return lu.say(f"Playing {title} on the TV.")


async def tv_play_netflix(args: dict) -> str:
    title = (args.get("title") or "").strip()
    if not title:
        return lu.say("What should I put on Netflix on the TV?")
    from plugins import netflix_play
    import turn_context

    found = await asyncio.to_thread(netflix_play._find, title)
    # The brain sometimes sends a YouTuber here ("put the Burnt Peanut on the TV"). If sir didn't say
    # Netflix, play the creator instead when it's one he's played before, or when Netflix was really
    # searched and has nothing like it (a failed search proves nothing - many shows have YouTube channels).
    said = (turn_context.user_text.get() or "").lower()
    if not found and not re.search(r"netflix|\bshow\b|movie|episode|series|season", said):
        if lu.norm(title) in _channels() or (netflix_play.not_on_netflix(title) and await asyncio.to_thread(_creator_for, title)):
            return await tv_youtube_creator({"creator": title})
    target, problem = await asyncio.to_thread(_adb_target)
    if not target:
        return lu.say(f"I can't open Netflix on the TV yet - {problem}.")
    if found:
        tid, name = found
        worked, was_running = await asyncio.to_thread(_open_and_confirm, target, f"https://www.netflix.com/watch/{tid}", NETFLIX_PACKAGE)
        if worked:
            # A fresh start of Netflix stops at "Who's watching?" first.
            pick = "" if was_running else " If it asks who's watching, pick your profile and it'll start."
            return lu.say(f"Putting {name} on the TV.{pick}")
    worked, _ = await asyncio.to_thread(_open_and_confirm, target, f"https://www.netflix.com/search?q={quote(title)}", NETFLIX_PACKAGE)
    if worked:
        return lu.say(f"I couldn't tell for sure which {title} to play, so I've opened Netflix on the TV searching for it.")
    await asyncio.to_thread(_launch_app, target, NETFLIX_PACKAGE)
    return lu.say(f"I've opened Netflix on the TV - I couldn't jump straight to {title}.")


async def tv_open_app(args: dict) -> str:
    name = (args.get("name") or "").strip()
    target, problem = await asyncio.to_thread(_adb_target)
    if not target:
        return lu.say(f"I can't open apps on the TV yet - {problem}.")
    package = KNOWN_APPS.get(lu.norm(name))
    if package is None:
        installed = [line.split(":", 1)[1].strip() for line in _adb_shell(target, "pm list packages").splitlines() if ":" in line]
        ranked = lu.rank(name, [{"name": p.rsplit(".", 1)[-1], "pkg": p} for p in installed])
        package = ranked[0][1]["pkg"] if ranked and ranked[0][0] >= lu.MATCH_THRESHOLD else None
    if package is None or not await asyncio.to_thread(_launch_app, target, package):
        return lu.say(f"I couldn't find an app called {name} on the TV.")
    return lu.say(f"Opening {name} on the TV.")


async def tv_control(args: dict) -> str:
    action = (args.get("action") or "").strip().lower().replace(" ", "_")
    action = {"resume": "play", "unmute": "mute", "off": "power_off", "on": "power_on", "turn_off": "power_off",
              "turn_on": "power_on", "skip": "next", "louder": "volume_up", "quieter": "volume_down"}.get(action, action)
    if action not in KEYS:
        action = lu.action_from_words(set(KEYS), [
            (("turn the tv off", "tv off", "turn off the tv"), "power_off"), (("turn the tv on", "tv on"), "power_on"),
            (("unmute", "mute"), "mute"), (("turn it up", "turn the tv up", "louder", "volume up"), "volume_up"),
            (("turn it down", "turn the tv down", "quieter", "volume down"), "volume_down"),
            (("resume", "play"), "play"), (("pause",), "pause"), (("stop",), "stop"), (("next", "skip"), "next"),
            (("home",), "home"), (("back",), "back")]) or action
    if action not in KEYS:
        return lu.say("I can pause or play the TV, change its volume, mute it, skip, go home or back, or turn it on or off.")
    presses = 1
    if action in ("volume_up", "volume_down"):
        steps = args.get("steps")
        presses = int(steps) if isinstance(steps, (int, float)) and 0 < steps <= 20 else 3

    target, _ = await asyncio.to_thread(_adb_target)
    if target:
        for _ in range(presses):
            await asyncio.to_thread(_adb_shell, target, f"input keyevent {KEYS[action]}")
    else:
        # No debug link: what Cast alone can do.
        cast = await asyncio.to_thread(_get_cast)
        if cast is None:
            return lu.say("I can't find the TV on the network - is it on, and on the same Wi-Fi?")
        mc = cast.media_controller
        if action in ("pause", "play_pause") and mc.status.player_is_playing:
            mc.pause()
        elif action in ("play", "play_pause"):
            mc.play()
        elif action == "stop":
            cast.quit_app()
        elif action in ("volume_up", "volume_down"):
            (cast.volume_up if action == "volume_up" else cast.volume_down)(0.05 * presses)
        elif action == "mute":
            cast.set_volume_muted(not cast.status.volume_muted)
        else:
            return lu.say("That needs the TV's network debugging switched on - see the TV setup steps in the README.")
    spoken = {"play_pause": "Done.", "play": "Playing.", "pause": "Paused.", "stop": "Stopped.", "next": "Skipped.",
              "previous": "Gone back.", "volume_up": "Turned the TV up.", "volume_down": "Turned the TV down.",
              "mute": "Toggled mute on the TV.", "home": "Back to the home screen.", "back": "Done.",
              "power_off": "Turning the TV off.", "power_on": "Turning the TV on.", "forward": "Skipping ahead.",
              "rewind": "Rewinding."}
    return lu.say(spoken[action])


async def _cast_or_adb_youtube(video_id: str) -> str | None:
    """Puts a YouTube video on the TV; None on success, else what went wrong."""
    try:
        if await asyncio.to_thread(_cast_youtube, video_id):
            return None
    except Exception as exc:  # noqa: BLE001 - fall back to the TV's YouTube app over ADB
        print(f"[tv] YouTube cast failed: {exc!r}", flush=True)
    target, problem = await asyncio.to_thread(_adb_target)
    if target and await asyncio.to_thread(_open_on_tv, target, f"https://www.youtube.com/watch?v={video_id}", YOUTUBE_PACKAGE):
        return None
    return problem or "the TV did not respond"


async def tv_youtube_creator(args: dict) -> str:
    """A YouTuber / streamer by name: live stream if they're live, else (or if asked) newest video."""
    name = (args.get("creator") or args.get("query") or args.get("name") or "").strip()
    want = (args.get("which") or "auto").lower()
    if not name:
        return lu.say("Which creator?")
    channel = await asyncio.to_thread(_creator_for, name)
    if channel is None:
        if want == "check":
            return lu.say(f"I couldn't find a YouTube channel called {name}.")
        return await tv_play_youtube({"query": name, "_from_creator": True})
    handle, shown = channel
    live = await asyncio.to_thread(_live_now, handle) if want in ("auto", "live", "check") else None
    if want == "check":
        if live:
            return lu.say(f"Yes - {shown} is live right now: {_clean_title(live[1])}. Want it on the TV?")
        return lu.say(f"{shown} isn't live right now.")
    if want == "live" and not live:
        return lu.say(f"{shown} isn't live right now. I can put on their newest video instead.")
    pick = live or await asyncio.to_thread(_latest_video, handle)
    if pick is None:
        return lu.say(f"I found {shown}'s channel but couldn't read their videos.")
    video_id, title = pick
    problem = await _cast_or_adb_youtube(video_id)
    if problem:
        return lu.say(f"I found it, but couldn't get it onto the TV - {problem}.")
    what = "live stream" if live else "newest video"
    return lu.say(f"Putting {shown}'s {what} on the TV: {_clean_title(title)}.")


def _seek_words(seconds: float) -> str:
    if seconds % 60 == 0:
        minutes = int(seconds // 60)
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    return f"{int(seconds)} seconds"


async def tv_seek(args: dict) -> str:
    seconds = args.get("seconds")
    seconds = float(seconds) if isinstance(seconds, (int, float)) and seconds > 0 else 30.0
    forward = (args.get("direction") or "forward").lower() != "back"
    amount = _seek_words(seconds)

    def via_cast() -> bool:
        cast = _get_cast()
        if cast is None:
            return False
        mc = cast.media_controller
        mc.update_status()
        time.sleep(0.6)
        status = mc.status
        if status is None or status.current_time is None or not status.duration:
            return False
        target = status.adjusted_current_time + (seconds if forward else -seconds)
        mc.seek(max(0.0, min(target, status.duration - 2)))
        return True

    try:
        if await asyncio.to_thread(via_cast):
            return lu.say(f"Skipped {'ahead' if forward else 'back'} {amount}.")
    except Exception as exc:  # noqa: BLE001 - not a Cast session; use the remote instead
        print(f"[tv] cast seek failed: {exc!r}", flush=True)
    target, problem = await asyncio.to_thread(_adb_target)
    if not target:
        return lu.say(f"I can't skip on the TV right now - {problem}.")
    # TV players (YouTube, Netflix) jump 10 seconds per press of right/left on the remote.
    presses = max(1, min(60, round(seconds / 10)))
    code = 22 if forward else 21
    for _ in range(presses):
        await asyncio.to_thread(_adb_shell, target, f"input keyevent {code}")
    return lu.say(f"Skipped {'ahead' if forward else 'back'} about {amount}.")


async def tv_whats_on(args: dict) -> str:
    def look() -> str:
        cast = _get_cast()
        if cast is not None and cast.status:
            app = cast.status.display_name or "the home screen"
            mc = cast.media_controller
            title = mc.status.title if mc.status and mc.status.title else ""
            return f"The TV is on {app}" + (f", playing {title}" if title else "") + "."
        return ""

    said = await asyncio.to_thread(look)
    if said:
        return lu.say(said)
    target, problem = await asyncio.to_thread(_adb_target)
    if not target:
        return lu.say(f"I can't see what's on the TV - {problem}.")
    out = await asyncio.to_thread(_adb_shell, target, "dumpsys activity activities | grep mResumedActivity")
    m = re.search(r"u0 ([\w.]+)/", out)
    pkg = m.group(1) if m else "something I don't recognise"
    name = next((k for k, v in KNOWN_APPS.items() if v == pkg), pkg)
    return lu.say(f"The TV is on {name}.")


async def tv_look(args: dict) -> str:
    """Screenshot of the TV, described by the vision model."""
    target, problem = await asyncio.to_thread(_adb_target)
    if not target:
        return lu.say(f"I can't see the TV's screen - {problem}.")

    def grab() -> bytes:
        return subprocess.run([str(ADB), "-s", target, "exec-out", "screencap", "-p"],
                              capture_output=True, timeout=20, creationflags=lu.CREATE_NO_WINDOW).stdout

    shot = await asyncio.to_thread(grab)
    if not shot or not shot.startswith(b"\x89PNG"):
        return lu.say("The TV wouldn't give me a screenshot - some apps, like Netflix, block it.")
    import vision

    question = (args.get("question") or "Describe what is on this TV screen in one or two sentences.").strip()
    return await vision.describe_image(shot, question)


async def tv_set_volume(args: dict) -> str:
    level = args.get("level")
    if not isinstance(level, (int, float)) or not 0 <= level <= 100:
        return lu.say("Give me a volume from 0 to 100.")

    def apply() -> bool:
        cast = _get_cast()
        if cast is None:
            return False
        cast.set_volume(level / 100)
        return True

    if await asyncio.to_thread(apply):
        return lu.say(f"TV volume set to {int(level)}.")
    return lu.say("I can't find the TV on the network - is it on, and on the same Wi-Fi?")


_sleep_task = None


async def tv_sleep_timer(args: dict) -> str:
    global _sleep_task
    minutes = args.get("minutes")
    if _sleep_task and not _sleep_task.done():
        _sleep_task.cancel()
    if not isinstance(minutes, (int, float)) or minutes <= 0:
        return lu.say("Sleep timer cancelled.")
    minutes = min(float(minutes), 600)

    async def later():
        await asyncio.sleep(minutes * 60)
        await tv_control({"action": "power_off"})

    _sleep_task = asyncio.create_task(later())
    return lu.say(f"I'll turn the TV off in {int(minutes)} minute{'s' if minutes != 1 else ''}.")


def on_disable() -> None:
    if ADB.exists():
        _adb("kill-server", timeout=10)


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "tv_play_youtube",
            "description": "Plays a YouTube video on sir's TV (Google TV), found by search - 'play <video> on the TV'.",
            "parameters": {"type": "object", "properties": {"query": {"type": "string", "description": "What to search YouTube for."}}, "required": ["query"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tv_play_netflix",
            "description": "Opens a show or movie in the Netflix app on sir's TV - 'put <show> on the TV'. For Netflix on the PC use netflix_play.",
            "parameters": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tv_open_app",
            "description": "Opens an app on sir's TV by name (Disney Plus, Hulu, Spotify, Twitch...).",
            "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tv_control",
            "description": ("Presses the TV's remote: pause/play, volume up/down, mute, skip, home, back, or turn the TV on/off. "
                            "'Turn the TV up/down' is volume_up/volume_down - just do it, never ask for a level."),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": list(KEYS)},
                    "steps": {"type": "number", "description": "Volume steps, default 3."},
                },
                "required": ["action"],
            },
        },
    },
]

SCHEMAS += [
    {"type": "function", "function": {
        "name": "tv_youtube_creator",
        "description": ("Puts a YouTuber or streamer on the TV by name - their live stream if they're live right "
                        "now, otherwise their newest video. Use for 'put on <creator>', 'play <streamer>, he's live', "
                        "'is <creator> live?' (which='check'). For a specific video by title use tv_play_youtube."),
        "parameters": {"type": "object", "properties": {
            "creator": {"type": "string", "description": "The creator/channel name as sir said it."},
            "which": {"type": "string", "enum": ["auto", "live", "latest", "check"],
                      "description": "auto = live if live else newest; check = only say whether they're live."}},
            "required": ["creator"]}}},
    {"type": "function", "function": {
        "name": "tv_seek",
        "description": "Skips forward or back in what's playing on the TV ('fast forward 5 minutes', 'go back 30 seconds').",
        "parameters": {"type": "object", "properties": {
            "seconds": {"type": "number"}, "direction": {"type": "string", "enum": ["forward", "back"]}},
            "required": ["seconds"]}}},
    {"type": "function", "function": {
        "name": "tv_whats_on",
        "description": "Says which app and video are on the TV right now.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "tv_look",
        "description": "Takes a screenshot of the TV and describes it ('what's on my TV screen?', 'read what the TV says').",
        "parameters": {"type": "object", "properties": {"question": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "tv_set_volume",
        "description": "Sets the TV volume to an exact number from 0 to 100 - only when sir says a number ('set the TV volume to 20').",
        "parameters": {"type": "object", "properties": {"level": {"type": "number"}}, "required": ["level"]}}},
    {"type": "function", "function": {
        "name": "tv_sleep_timer",
        "description": "Turns the TV off after some minutes ('turn the TV off in 30 minutes'); 0 cancels the timer.",
        "parameters": {"type": "object", "properties": {"minutes": {"type": "number"}}, "required": ["minutes"]}}},
]

DISPATCH = {
    "tv_youtube_creator": tv_youtube_creator,
    "tv_seek": tv_seek,
    "tv_whats_on": tv_whats_on,
    "tv_look": tv_look,
    "tv_set_volume": tv_set_volume,
    "tv_sleep_timer": tv_sleep_timer,
    "tv_play_youtube": tv_play_youtube,
    "tv_play_netflix": tv_play_netflix,
    "tv_open_app": tv_open_app,
    "tv_control": tv_control,
}
