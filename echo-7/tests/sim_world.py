"""
Echo's simulated world - every tool tested for real, without touching anything real.

Echo runs with its REAL brain and its REAL tool code, but against a fake world: a fake
PC (apps, windows, keys, shutdown), a fake TV (Cast + ADB), a fake Spotify account, a
fake phone line, a fake browser, fake screen/camera, fake ComfyUI. Echo itself runs
from a temporary copy of backend/, so your real phone book, memory, settings and files
are never written to. Reading the public web (searches, YouTube pages) is real and
read-only; anything that would *do* something lands in the fake world, where it is
checked.

    venv\\Scripts\\python.exe tests\\sim_world.py            all scenarios
    venv\\Scripts\\python.exe tests\\sim_world.py tv spotify  only scenarios whose name contains these

Needs Ollama running (the brain is real). Prints a pass/fail table and saves
tests/sim_world_report.json.
"""
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REAL_BACKEND = ROOT / "backend"
TEST_PASSCODE = "blue llama sunrise"
TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360f8cf000000020001e221bc330000000049454e44ae426082"
)


# --- the fake world ------------------------------------------------------------------

class World:
    def __init__(self):
        self.reset()

    def reset(self):
        self.events: list[tuple[str, str]] = []
        self.tv_app = "com.google.android.youtube.tv"
        self.tv_keys: list[int] = []
        self.tv_volume = 0.3
        self.cast_videos: list[str] = []
        self.cast_seeks: list[float] = []
        self.spotify = {"playing": False, "track": "Nothing", "volume": 50, "shuffle": False, "progress_ms": 60000}
        self.windows_closed: list[str] = []
        self.calls: list[tuple[str, str]] = []

    def log(self, kind: str, detail: str) -> None:
        self.events.append((kind, str(detail)))

    def did(self, kind: str, pattern: str = "") -> bool:
        return any(k == kind and re.search(pattern, d, re.I) for k, d in self.events)


world = World()

START_APPS = [
    {"Name": "Spotify", "AppID": "SpotifyAB.SpotifyMusic!Spotify"},
    {"Name": "Discord", "AppID": "com.squirrel.Discord.Discord"},
    {"Name": "Google Chrome", "AppID": "Chrome"},
    {"Name": "Steam", "AppID": "Valve.Steam"},
    {"Name": "Visual Studio Code", "AppID": "Microsoft.VisualStudioCode"},
    {"Name": "Calculator", "AppID": "Microsoft.WindowsCalculator!App"},
]
TV_PACKAGES = ["com.netflix.ninja", "com.google.android.youtube.tv", "com.disney.disneyplus",
               "com.hulu.livingroomplus", "com.spotify.tv.android", "tv.twitch.android.app"]


class _Done:
    def __init__(self, args, stdout="", returncode=0, binary=False):
        self.args, self.returncode, self.stderr = args, returncode, b"" if binary else ""
        self.stdout = stdout if not binary else (stdout if isinstance(stdout, bytes) else stdout.encode())


def _fake_adb(cmd, binary):
    args = [str(a) for a in cmd[1:]]
    if args[:1] == ["connect"]:
        return _Done(cmd, "connected to 192.168.1.50:5555", binary=binary)
    if args[:1] == ["kill-server"]:
        return _Done(cmd, "", binary=binary)
    if len(args) >= 3 and args[0] == "-s":
        rest = args[2:]
        if rest == ["get-state"]:
            return _Done(cmd, "device\n", binary=binary)
        if rest[:3] == ["exec-out", "screencap", "-p"]:
            world.log("tv", "screenshot")
            return _Done(cmd, TINY_PNG, binary=True)
        if rest[:1] == ["shell"]:
            sh = " ".join(rest[1:])
            if sh.startswith("am start"):
                pkg = re.search(r"(com\.[\w.]+|tv\.[\w.]+)(?:/[\w.]+)?\s*$", sh)
                url = re.search(r'-d "([^"]+)"', sh)
                world.tv_app = pkg.group(1) if pkg else world.tv_app
                world.log("tv_open", f"{world.tv_app} {url.group(1) if url else ''}")
                return _Done(cmd, "Starting: Intent { ... }", binary=binary)
            if sh.startswith("monkey"):
                pkg = re.search(r"-p ([\w.]+)", sh)
                world.tv_app = pkg.group(1) if pkg else world.tv_app
                world.log("tv_open", world.tv_app)
                return _Done(cmd, "Events injected: 1", binary=binary)
            if sh.startswith("pidof"):
                return _Done(cmd, "4321" if sh.split()[-1] == world.tv_app else "", binary=binary)
            if "mResumedActivity" in sh:
                return _Done(cmd, f"  mResumedActivity: ActivityRecord{{1 u0 {world.tv_app}/.MainActivity t1}}", binary=binary)
            if sh.startswith("input keyevent"):
                code = int(sh.split()[-1])
                world.tv_keys.append(code)
                world.log("tv_key", str(code))
                return _Done(cmd, "", binary=binary)
            if sh.startswith("pm list packages"):
                return _Done(cmd, "\n".join(f"package:{p}" for p in TV_PACKAGES), binary=binary)
    world.log("adb", " ".join(args))
    return _Done(cmd, "", binary=binary)


def fake_run(cmd, *a, **kw):
    binary = not (kw.get("text") or kw.get("universal_newlines") or kw.get("encoding"))
    parts = [str(c) for c in (cmd if isinstance(cmd, (list, tuple)) else [cmd])]
    exe = Path(parts[0]).name.lower()
    joined = " ".join(parts)
    if exe == "adb.exe":
        return _fake_adb(parts, binary)
    if "Get-StartApps" in joined:
        return _Done(cmd, json.dumps(START_APPS), binary=binary)
    if exe == "tasklist":
        return _Done(cmd, "Spotify.exe   1234 Console  1  100,000 K\nsteam.exe   2345 Console 1 50,000 K", binary=binary)
    if exe == "taskkill":
        world.log("taskkill", joined)
        return _Done(cmd, "SUCCESS", binary=binary)
    if exe.startswith("nvidia-smi"):
        world.log("pc", "gpu read")
        return _Done(cmd, "NVIDIA GeForce RTX 3090, 61, 35, 9000, 24576, 120.5, 350.00, 40", binary=binary)
    if exe == "shutdown":
        world.log("shutdown", joined)
        return _Done(cmd, "", binary=binary)
    world.log("run", joined)
    return _Done(cmd, "", binary=binary)


class FakePopen:
    def __init__(self, cmd, *a, **kw):
        world.log("launch", " ".join(str(c) for c in (cmd if isinstance(cmd, (list, tuple)) else [cmd])))
        self.pid, self.returncode = 9999, 0

    def poll(self):
        return 0

    def wait(self, *a, **kw):
        return 0

    def communicate(self, *a, **kw):
        return ("", "")


def fake_startfile(target, *a, **kw):
    world.log("open", target)


# --- fake web services (only hosts that would *act*); public reads stay real -------

def _spotify(request):
    import httpx

    s = world.spotify
    path = request.url.path.replace("/v1", "")
    method = request.method
    q = dict(request.url.params)
    if path == "/me":
        return httpx.Response(200, json={"id": "simuser", "display_name": "Sim"})
    if path == "/me/player/devices":
        return httpx.Response(200, json={"devices": [{"id": "dev1", "name": "SIM-PC", "type": "Computer", "is_active": True}]})
    if path in ("/me/player", "/me/player/currently-playing") and method == "GET":
        return httpx.Response(200, json={
            "is_playing": s["playing"], "progress_ms": s["progress_ms"], "shuffle_state": s["shuffle"],
            "device": {"id": "dev1", "name": "SIM-PC", "volume_percent": s["volume"]},
            "item": {"name": s["track"], "uri": "spotify:track:sim", "duration_ms": 240000,
                     "artists": [{"name": "Sim Artist"}], "album": {"name": "Sim Album", "uri": "spotify:album:sim"}},
        })
    if path == "/search":
        kind = q.get("type", "track")
        name = q.get("q", "song").title()
        item = {"name": name, "uri": f"spotify:{kind}:sim1", "id": "sim1", "artists": [{"name": "Original Artist"}],
                "album": {"name": name, "uri": "spotify:album:sim1", "album_type": "single"}, "owner": {"display_name": "x"}}
        return httpx.Response(200, json={f"{kind}s": {"items": [item]}})
    if path == "/me/playlists":
        return httpx.Response(200, json={"items": [{"name": "Gym Mix", "uri": "spotify:playlist:gym"},
                                                   {"name": "Chill", "uri": "spotify:playlist:chill"}], "next": None})
    if path == "/me/tracks":
        return httpx.Response(200, json={"items": [{"track": {"uri": "spotify:track:liked1", "name": "Liked One"}}]})
    if path == "/me/player/play":
        body = json.loads(request.content or b"{}")
        s["playing"] = True
        s["track"] = body.get("context_uri") or (body.get("uris") or ["(resumed)"])[0]
        if s["track"].startswith("spotify:"):
            s["track"] = "Spooky Scary Skeletons" if "sim1" in s["track"] else s["track"]
        world.log("spotify", f"play {s['track']}")
        return httpx.Response(204)
    if path == "/me/player/pause":
        s["playing"] = False
        world.log("spotify", "pause")
        return httpx.Response(204)
    if path in ("/me/player/next", "/me/player/previous"):
        world.log("spotify", path.rsplit("/", 1)[-1])
        return httpx.Response(204)
    if path == "/me/player/seek":
        s["progress_ms"] = int(q.get("position_ms", 0))
        world.log("spotify", f"seek {s['progress_ms']}")
        return httpx.Response(204)
    if path == "/me/player/volume":
        s["volume"] = int(q.get("volume_percent", 50))
        world.log("spotify", f"volume {s['volume']}")
        return httpx.Response(204)
    if path in ("/me/player/shuffle", "/me/player/repeat"):
        world.log("spotify", f"{path.rsplit('/', 1)[-1]} {q.get('state')}")
        return httpx.Response(204)
    return httpx.Response(204)


def _google(request):
    import httpx

    world.log("google", f"{request.method} {request.url.path}")
    if request.method == "POST":
        return httpx.Response(200, json={"presentationId": "simslides", "id": "sim"})
    return httpx.Response(200, json={"messages": [], "items": [], "files": [], "resultSizeEstimate": 0})


# A public repo only this world has: its newest version ("zephyr-9") can't be guessed, only read.
SIM_REPO = "/repos/sim-owner/sim-echo"


def _github(request):
    import httpx

    path = request.url.path.rstrip("/")
    world.log("github", f"{request.method} {path}")
    if path == SIM_REPO:
        return httpx.Response(200, json={"full_name": "sim-owner/sim-echo", "description": "A test-world assistant",
                                         "pushed_at": "2031-05-04T10:00:00Z", "license": {"spdx_id": "MIT"},
                                         "stargazers_count": 3})
    if path == SIM_REPO + "/contents":
        return httpx.Response(200, json=[{"name": n, "type": "dir"} for n in ("v1", "v2", "zephyr-9")]
                              + [{"name": "README.md", "type": "file"}])
    if path == SIM_REPO + "/commits":
        msgs = [("2031-05-04", "Zephyr-9: lanterns"), ("2031-02-01", "v2: faster replies"), ("2030-12-25", "v1: first release")]
        return httpx.Response(200, json=[{"commit": {"message": m, "committer": {"date": d + "T10:00:00Z"}}} for d, m in msgs])
    if path == SIM_REPO + "/readme":
        return httpx.Response(200, text="# Sim Echo\n\n## v1\nThe first release.\n\n## v2\nFaster replies.\n\n"
                                        "## zephyr-9 - lanterns\nAdds lantern mode: the dashboard glows like a lantern.\n")
    if path.startswith(SIM_REPO):
        return httpx.Response(404, json={"message": "Not Found"})
    return httpx.Response(200, json={"access_token": "sim", "expires_in": 3600, "data": {}})


FAKE_HOSTS = {"api.spotify.com": _spotify, "accounts.spotify.com": None, "api.telnyx.com": None,
              "www.googleapis.com": _google, "gmail.googleapis.com": _google, "slides.googleapis.com": _google,
              "oauth2.googleapis.com": None, "api.github.com": _github, "discord.com": None, "api.notion.com": None}


def install_http_fakes():
    import httpx

    real_async_send = httpx.AsyncClient.send
    real_send = httpx.Client.send

    def route(request):
        host = request.url.host
        if host in FAKE_HOSTS:
            handler = FAKE_HOSTS[host]
            return handler(request) if handler else httpx.Response(200, json={"access_token": "sim", "expires_in": 3600, "data": {}})
        if host in ("127.0.0.1", "localhost") and request.url.port in (11434,):
            return None  # the real brain
        if host in ("127.0.0.1", "localhost"):
            world.log("local", f"{request.method} {request.url}")
            return httpx.Response(200, json={})
        if request.method != "GET":
            world.log("blocked", f"{request.method} {request.url}")
            return httpx.Response(403, json={"error": "simulated world blocks writes to the internet"})
        return None  # public read-only web: real

    def routed(request):
        fake = route(request)
        if fake is not None:
            fake.request = request
        return fake

    async def async_send(self, request, *a, **kw):
        fake = routed(request)
        return fake if fake is not None else await real_async_send(self, request, *a, **kw)

    def send(self, request, *a, **kw):
        fake = routed(request)
        return fake if fake is not None else real_send(self, request, *a, **kw)

    httpx.AsyncClient.send = async_send
    httpx.Client.send = send


# --- fake devices plugged into Echo's modules ---------------------------------------

class FakeMedia:
    def __init__(self):
        self.status = type("S", (), {})()
        self._refresh()

    def _refresh(self):
        st = self.status
        st.player_is_playing = True
        st.current_time = st.adjusted_current_time = 120.0
        st.duration = 3600.0
        st.title = "Sim Video"

    def update_status(self, *a, **kw):
        self._refresh()

    def seek(self, t):
        world.cast_seeks.append(t)
        world.log("cast", f"seek {t}")

    def pause(self):
        world.log("cast", "pause")

    def play(self):
        world.log("cast", "play")


class FakeCast:
    def __init__(self):
        self.socket_client = type("SC", (), {"is_connected": True})()
        self.cast_info = type("CI", (), {"host": "192.168.1.50", "friendly_name": "Sim TV", "port": 8009,
                                         "uuid": None, "model_name": "SimTV", "cast_type": "cast"})()
        self.status = type("St", (), {"display_name": "YouTube", "volume_muted": False})()
        self.media_controller = FakeMedia()

    def set_volume(self, v):
        world.tv_volume = v
        world.log("cast", f"volume {v}")

    def volume_up(self, d=0.05):
        world.log("cast", "volume up")

    def volume_down(self, d=0.05):
        world.log("cast", "volume down")

    def set_volume_muted(self, m):
        world.log("cast", f"mute {m}")

    def quit_app(self):
        world.log("cast", "stop")

    def register_handler(self, h):
        pass

    def disconnect(self, *a, **kw):
        pass


class FakeUser32:
    def __getattr__(self, name):
        def call(*a, **kw):
            world.log("user32", name)
            return 1
        return call

    def GetForegroundWindow(self):
        return 500


FAKE_WINDOWS = [
    {"name": "Google Chrome", "exe": "chrome.exe", "keys": {"Google Chrome", "chrome"}, "pids": {11},
     "windows": [{"hwnd": 101, "title": "YouTube - Google Chrome", "pid": 11},
                 {"hwnd": 102, "title": "Netflix - Google Chrome", "pid": 11}]},
    {"name": "Discord", "exe": "discord.exe", "keys": {"Discord"}, "pids": {22}, "windows": [{"hwnd": 201, "title": "Discord", "pid": 22}]},
    {"name": "Spotify", "exe": "spotify.exe", "keys": {"Spotify"}, "pids": {33}, "windows": [{"hwnd": 301, "title": "Spotify Premium", "pid": 33}]},
    {"name": "Steam", "exe": "steam.exe", "keys": {"Steam"}, "pids": {44}, "windows": [{"hwnd": 401, "title": "Steam", "pid": 44}]},
]


def plug_in_fakes(tools, server, plugins):
    """Swap the device layers of Echo's modules for the fake world. Done after import, so
    everything above the device layer - the brain, the tool code - is the real thing."""
    import browser_control
    import location
    import telephony
    import vision

    # screen, camera, vision (the 6 GB vision model would make every run slow; its own
    # behaviour is tested elsewhere)
    tools._capture_screenshot_bytes = lambda: TINY_PNG
    tools._capture_camera_bytes = lambda *a, **k: TINY_PNG

    async def describe(image_bytes, question="Describe"):
        world.log("vision", question)
        return "A desktop with Google Chrome open on a YouTube video, and Discord in the corner."
    vision.describe_image = describe
    location.get_location = lambda: {"latitude": 41.08, "longitude": -81.51, "label": "Akron, OH"}

    # browser extension
    browser_control.is_connected = lambda: True

    async def run_command(command):
        world.log("browser", json.dumps(command)[:120])
        action = command.get("action") or command.get("type") or ""
        if "scan" in action:
            return {"ok": True, "data": {"elements": [{"id": 1, "role": "button", "label": "Sign in"},
                                                      {"id": 2, "role": "textbox", "label": "Search"}]}}
        if "read" in action:
            return {"ok": True, "data": {"title": "Sim Page", "url": "https://sim.invalid",
                                         "text": "Welcome to the simulated page. Today's special is pancakes."}}
        return {"ok": True, "data": {}}
    browser_control.run_command = run_command

    # phone line
    sim_cfg = {"owner_number": "+13305550100", "from_number": "+13305559999", "public_base_url": "https://sim.invalid",
               "api_key": "sim", "connection_id": "sim"}
    telephony.is_configured = lambda: True
    telephony.load_config = lambda: sim_cfg

    async def place_call(cfg, number, contact_name, message, *a, **kw):
        world.calls.append((number, message))
        world.log("call", f"{contact_name or number}: {message}")
        return "sim-call"
    telephony.place_outbound_call = place_call

    async def notify(cfg, message):
        world.log("page", message)
    telephony.notify_owner = notify

    async def no_audio(*a, **kw):
        return "clip"

    async def no_warm(*a, **kw):
        return None
    tools._call_prep["warm"], tools._call_prep["render"] = no_warm, no_audio

    # Spotify, Google: signed in, against the fake services above
    import spotify_auth
    import google_auth

    async def token():
        return "sim-token"
    spotify_auth.get_valid_token = token
    spotify_auth.is_connected = lambda: True
    google_auth.get_valid_token = token if hasattr(google_auth, "get_valid_token") else None
    google_auth.is_connected = lambda: True

    # PC windows, keys, the TV
    p = plugins
    p["app_closer"]._running_apps = lambda: [dict(w) for w in FAKE_WINDOWS]
    p["app_closer"]._process_alive = lambda pid: False
    p["app_closer"]._user32 = FakeUser32()
    p["media_keys"]._press = lambda vk: world.log("key", hex(vk))
    p["netflix_play"]._press = lambda name, times=1: world.log("key", f"{name} x{times}")
    p["netflix_play"]._netflix_window = lambda: 102
    p["netflix_play"]._bring_to_front = lambda h: True
    p["netflix_play"]._user32 = FakeUser32()
    tv = p["tv_control"]
    fake_adb = Path(tempfile.gettempdir()) / "echo_sim_adb" / "adb.exe"  # exists, named adb; its calls are faked
    fake_adb.parent.mkdir(exist_ok=True)
    fake_adb.touch()
    tv.ADB = fake_adb
    tv._get_cast = lambda: FakeCast()

    def cast_youtube(video_id):
        world.cast_videos.append(video_id)
        world.log("cast", f"youtube {video_id}")
        return True
    tv._cast_youtube = cast_youtube
    tv._save_state({"name": "Sim TV", "host": "192.168.1.50"})

    # ComfyUI image / 3D generation
    import comfyui_shared as comfy

    async def yes(*a, **kw):
        return True

    async def submit(workflow):
        world.log("comfyui", "workflow submitted")
        return "sim-prompt"

    async def history(prompt_id, timeout_seconds=0):
        return None  # "no output" - the plugin must report that honestly, not invent an image

    async def no_ref(*a, **kw):
        return None
    comfy.is_running, comfy.ensure_running, comfy.submit_workflow = yes, yes, submit
    comfy.wait_for_history, comfy.fetch_reference_image = history, no_ref


# --- scenarios ------------------------------------------------------------------------
# name, what sir says (one or more turns), tools that count as right, extra world check

def S(name, say, tools, check=None):
    return {"name": name, "say": say if isinstance(say, list) else [say], "tools": tools, "check": check}


SCENARIOS = [
    # core
    S("web search", "who won the 2024 world series?", ["web_search", "check_knowledge"]),
    S("fetch page", "read me what's on https://example.com", ["fetch_webpage"]),
    # sir gives an address: Echo reads that exact page and reports what's really there
    S("github repo", "check the GitHub repo at github.com/sim-owner/sim-echo and tell me which version is newest",
      ["fetch_webpage"], lambda w: w.did("github", "sim-echo/readme") and w.did("reply", "zephyr")),
    S("remember", "remember that my favorite color is forest green", ["remember"],
      lambda w: True),
    S("screen", "what's on my screen right now?", ["view_screen"], lambda w: w.did("vision")),
    S("camera", "what do you see through my camera?", ["view_camera"], lambda w: w.did("vision")),
    S("location", "where am I right now?", ["get_location"]),
    S("call", "call Danie and tell her I'm running ten minutes late", ["call_phone_number"],
      lambda w: bool(w.calls) and "late" in w.calls[-1][1].lower()),
    # never a new call when sir asks to hang up (the fake line has no active call, so "no call to end" is right)
    S("hang up", "hang up the call with Danie", ["hang_up_call", "call_phone_number"], lambda w: not w.calls),
    S("browser read", "what does this web page say?", ["browser_read_page"], lambda w: w.did("browser")),
    S("browser click", "click the sign in button on this page", ["browser_scan_page", "browser_click"],
      lambda w: w.did("browser", "click")),
    S("browser scroll", "scroll down on this page", ["browser_scroll"]),
    # A whole page written into the reply instead of the tool is still caught by the dashboard and put on the
    # canvas (server._extract_embedded_html) - so that counts too.
    S("canvas", "make me a bouncing ball animation", ["write_canvas_code", "start_project", "generate_3d_model", "generate_image"],
      lambda w: w.did("tool", "write_canvas_code|start_project") or w.did("reply_html")),
    # address book
    S("address save", "save my gym's address as 500 Market Street, Akron Ohio", ["save_address"]),
    # Echo knows saved places from its context, so answering without a lookup is right too.
    S("address get", ["save the gym as 500 Market Street, Akron Ohio", "what's the address of the gym?"], ["get_address", "save_address"],
      lambda w: True),
    S("address list", "what places are in my address book?", ["list_addresses"]),
    S("directions", ["save home as 9 Oak Avenue, Akron Ohio", "give me directions to home"], ["directions"],
      lambda w: w.did("open", "google.com/maps/dir")),
    S("address forget", ["save the gym as 500 Market Street, Akron Ohio", "remove the gym from my address book"], ["forget_address"]),
    # PC
    S("whats running", "what apps are open on my computer?", ["list_running_apps"]),
    S("close app", "close Discord", ["close_app"], lambda w: w.did("user32", "PostMessage")),
    S("force close", "force close Steam", ["close_app"], lambda w: w.did("taskkill", "44")),
    S("open app", "open Discord", ["open_app"], lambda w: w.did("launch", "Discord")),
    S("shortcut open", "open YouTube", ["open_shortcut", "open_app"], lambda w: w.did("open") or w.did("launch")),
    S("shortcut add", "add a shortcut called Crunchyroll for crunchyroll.com", ["add_shortcut", "open_shortcut"],
      lambda w: "crunchyroll" in (Path("plugins_data") / "app_shortcuts.json").read_text(encoding="utf-8").lower()),
    S("shortcut list", "what website shortcuts do you have?", ["list_shortcuts"]),
    S("media keys", "turn my computer volume up", ["media_control"], lambda w: w.did("key")),
    S("steam", "launch a steam game for me, whatever's first in my library", ["launch_steam_game"]),
    S("shutdown + passcode", ["turn off my pc", TEST_PASSCODE], ["shutdown_pc"], lambda w: w.did("shutdown", "/s")),
    S("shutdown wrong code", ["turn off my pc", "purple monkey dishwasher"], ["shutdown_pc"],
      lambda w: not w.did("shutdown", "/s")),
    S("cancel shutdown", "cancel the shutdown", ["cancel_shutdown"], lambda w: w.did("shutdown", "/a")),
    # Spotify
    S("spotify song", "play spooky scary skeletons on spotify", ["spotify_play"], lambda w: w.did("spotify", "play")),
    S("spotify playlists", "what playlists do I have?", ["spotify_my_playlists"]),
    S("spotify pause", "pause spotify", ["spotify_playback"], lambda w: w.did("spotify", "pause")),
    S("spotify next", "skip to the next song", ["spotify_playback", "media_control"],
      lambda w: w.did("spotify", "next") or w.did("key")),
    S("spotify now playing", "what song is playing?", ["spotify_now_playing"]),
    S("spotify liked", "play my liked songs", ["spotify_play"], lambda w: w.did("spotify", "play")),
    # Netflix on the PC
    S("netflix play", "play Squid Game on Netflix", ["netflix_play"], lambda w: w.did("open", "netflix.com")),
    S("netflix pause", "pause Netflix", ["netflix_control"], lambda w: w.did("key", "space")),
    S("netflix skip", "skip ahead two minutes on Netflix", ["netflix_control"], lambda w: w.did("key", "right x12")),
    # TV
    S("tv creator live", "put the Burnt Peanut on the TV", ["tv_youtube_creator", "tv_play_youtube"], lambda w: bool(w.cast_videos)),
    S("tv is live", "is the Burnt Peanut live right now?", ["tv_youtube_creator"]),
    S("tv youtube video", "play the pokemon theme song on the TV", ["tv_play_youtube", "tv_youtube_creator"],
      lambda w: bool(w.cast_videos) and not w.did("note", "channel")),
    S("tv netflix", "put Stranger Things on the TV", ["tv_play_netflix"], lambda w: w.did("tv_open", "netflix")),
    S("tv app", "open Disney Plus on the TV", ["tv_open_app"], lambda w: w.did("tv_open", "disney")),
    S("tv pause", "pause the TV", ["tv_control"], lambda w: 85 in w.tv_keys or 127 in w.tv_keys or w.did("cast", "pause")),
    S("tv volume up", "turn the TV up", ["tv_control"], lambda w: 24 in w.tv_keys or w.did("cast", "volume")),
    S("tv seek", "fast forward five minutes on the TV", ["tv_seek"], lambda w: bool(w.cast_seeks) and w.cast_seeks[-1] > 300),
    S("tv whats on", "what's on the TV right now?", ["tv_whats_on"]),
    S("tv look", "what's on my TV screen?", ["tv_look"], lambda w: w.did("tv", "screenshot")),
    S("tv set volume", "set the TV volume to 20", ["tv_set_volume"], lambda w: abs(w.tv_volume - 0.2) < 0.01),
    S("tv sleep", "turn the TV off in 30 minutes", ["tv_sleep_timer"]),
    # generation (ComfyUI faked: it returns no output, Echo must say so, not invent one)
    S("image", "make me a picture of a dragon on a mountain", ["generate_image"]),
    S("3d model", "make me a 3D model of a pumpkin", ["generate_3d_model"]),
    # connected accounts
    S("gmail", "do I have any emails from Amazon?", ["search_gmail"]),
    S("calendar", "what's on my calendar this week?", ["list_calendar_events"]),
    # projects and approvals (the background work itself is tested in tests/agent_projects.py)
    S("project start", "make me a tip calculator app I can keep using", ["start_project", "write_canvas_code"]),
    S("project status", ["make me a tip calculator app I can keep using", "how's the project going?"], ["project_status", "list_projects"]),
    # nothing is waiting, and Echo is told the real list each message - answering without a lookup is fine
    S("approvals list", "what are you waiting on me for?", ["list_requests", "_none"]),
    # PC stats and Echo's report on itself (read-only)
    S("pc stats", "how's my PC doing? how hot is the graphics card?", ["pc_stats"], lambda w: w.did("pc", "gpu")),
    S("self report", "how are you running? give me a report on yourself", ["echo_self_report"]),
    # manners and honesty
    S("greeting", "I just got done at the gym", []),
]


# --- running ------------------------------------------------------------------------

def prepare_copy() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="echo_sim_"))
    skip = shutil.ignore_patterns("__pycache__", "*.log", "generated_images", "generated_3d_models", "knowledge_backup*")
    shutil.copytree(REAL_BACKEND, tmp / "backend", ignore=skip)
    shutil.copytree(ROOT / "frontend", tmp / "frontend", ignore=skip)
    return tmp / "backend"


async def run_scenario(sc, server, tools, cfg):
    world.reset()
    server.conversation_history.clear()
    import launch_utils
    launch_utils._recent_launches.clear()
    called = []
    real_execute = tools.execute_tool

    async def recording_execute(name, args):
        world.log("tool", name)
        result = await real_execute(name, args)
        called.append((name, args, str(result)[:300]))
        return result

    tools.execute_tool = recording_execute
    started = time.perf_counter()
    replies = []
    try:
        for line in sc["say"]:
            reply = await server.run_chat_turn(line, cfg)
            world.log("reply", reply)
            if server._extract_embedded_html(reply):
                world.log("reply_html", "page in the reply - the dashboard puts it on the canvas")
            replies.append(reply)
        error = None
    except Exception as exc:  # noqa: BLE001
        error = repr(exc)
    finally:
        tools.execute_tool = real_execute
    secs = time.perf_counter() - started
    names = [c[0] for c in called]
    if not sc["tools"]:
        tool_ok = not names
    else:
        tool_ok = any(n in sc["tools"] for n in names) or ("_none" in sc["tools"] and not names)
    try:
        check_ok = sc["check"](world) if sc["check"] else True
    except Exception as exc:  # noqa: BLE001
        check_ok, error = False, f"check failed: {exc!r}"
    return {"name": sc["name"], "ok": bool(tool_ok and check_ok and not error), "tool_ok": tool_ok, "check_ok": check_ok,
            "called": called, "replies": replies, "secs": round(secs, 2), "error": error,
            "world": world.events[-6:]}


async def main(only):
    backend = prepare_copy()
    os.chdir(backend)
    sys.path.insert(0, str(backend))
    subprocess.run, subprocess.Popen, os.startfile = fake_run, FakePopen, fake_startfile
    install_http_fakes()

    import config as config_module
    cfg = config_module.load_config()
    for key in ("app_closer_enabled", "pc_shutdown_enabled", "netflix_play_enabled", "tv_control_enabled",
                "spotify_control_enabled", "media_keys_enabled", "steam_launcher_enabled", "app_launcher_enabled",
                "app_shortcuts_enabled", "address_book_enabled", "calling_enabled", "screen_access", "camera_access",
                "browser_control_enabled", "code_canvas_enabled", "image_generator_enabled",
                "model_3d_generator_enabled", "google_workspace_enabled", "projects_enabled", "ai_enabled"):
        cfg[key] = True
    cfg["location_mode"] = "approximate"
    config_module.save_config(cfg)

    import passcode
    passcode.set_passcode(TEST_PASSCODE)
    import plugin_loader
    import server
    import tools
    plugins = {m.__name__.split(".")[-1]: m for m in plugin_loader._loaded_plugins}
    plug_in_fakes(tools, server, plugins)

    scenarios = [s for s in SCENARIOS if not only or any(o.lower() in s["name"].lower() for o in only)]
    results = []
    for sc in scenarios:
        r = await run_scenario(sc, server, tools, cfg)
        results.append(r)
        mark = "PASS" if r["ok"] else "FAIL"
        tools_used = ", ".join(c[0] for c in r["called"]) or "-"
        print(f"{mark}  {r['name']:22} {r['secs']:5.1f}s  tools: {tools_used}", flush=True)
        if not r["ok"]:
            print(f"        said: {(r['replies'] or [''])[-1][:160]!r}", flush=True)
            for c in r["called"]:
                print(f"        {c[0]} -> {c[2][:160]!r}", flush=True)
            if r["error"]:
                print(f"        error: {r['error'][:200]}", flush=True)
    passed = sum(r["ok"] for r in results)
    print(f"\n{passed}/{len(results)} scenarios passed; median {sorted(r['secs'] for r in results)[len(results)//2]:.1f}s per request")
    (ROOT / "tests" / "sim_world_report.json").write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
    return passed, len(results)


if __name__ == "__main__":
    try:
        asyncio.run(main(sys.argv[1:]))
    finally:
        os.chdir(ROOT)
        for leftover in Path(tempfile.gettempdir()).glob("echo_sim*"):
            shutil.rmtree(leftover, ignore_errors=True)
