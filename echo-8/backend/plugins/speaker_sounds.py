"""
Sound on a speaker: white noise to sleep or focus by, and wake-up alarms - like a smart speaker.

Two outputs (Setup -> Sound): Echo's voice always plays where it did (the Windows default - your
headphones - or a device you pick), and a SPEAKER that can be switched on and chosen. With the
speaker on, noise and alarms play on it; with it off, they play on the voice output.

White, pink and brown noise are made here, not downloaded: random sound shaped in frequency
(pink falls off like rain, brown deeper, like a fan or a waterfall), built as one seamless loop.
An alarm rings with a chime and Echo saying the time and the day's first events, then again every
minute for up to five minutes until sir says stop. Alarms live in backend/alarms.json (private).
"""
import asyncio
import io
import json
import re
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

import launch_utils as lu

PLUGIN_NAME = "speaker_sounds"
TOGGLE_LABEL = "White noise & alarms"
CONFIG_KEY = "speaker_sounds_enabled"
ENABLED_BY_DEFAULT = True
VRAM_COST = "no local model"
SIR_CALLS = True

ALARMS_FILE = Path(__file__).resolve().parent.parent / "alarms.json"
NOISE_KINDS = ("white", "pink", "brown")
NOISE_VOLUME = 0.35
RING_EVERY_SECONDS = 60
RING_FOR_SECONDS = 5 * 60
_NOISE_WORDS = {"white": "white", "static": "white", "pink": "pink", "rain": "pink", "brown": "brown", "fan": "brown",
                "waterfall": "brown", "ocean": "brown", "wave": "brown", "deep": "brown"}

_state = {"device": None, "noise": None, "noise_kind": "", "noise_task": None, "ringing": None}
_synthesize = None  # set by the server: text -> spoken audio (mp3 bytes)


def register(synthesize) -> None:
    global _synthesize
    _synthesize = synthesize


# --- the outputs ---------------------------------------------------------------------------------

def output_devices() -> list[str]:
    """The PC's sound outputs, by the names Windows shows."""
    import pygame
    import pygame._sdl2.audio as sdl_audio
    if not pygame.mixer.get_init():
        pygame.init()
    try:
        return [n for n in sdl_audio.get_audio_device_names(False) if n]
    except Exception:  # noqa: BLE001 - no audio subsystem: no list
        return []


def chosen_output(cfg: dict) -> tuple[str, str]:
    """(device name or "" for the Windows default, which one: "speaker" or "voice")."""
    if cfg.get("speaker_enabled") and cfg.get("speaker_device"):
        return cfg["speaker_device"], "speaker"
    return cfg.get("voice_output_device") or "", "voice"


def _mixer(device: str):
    """pygame's mixer opened on `device` (reopened only when the choice changes)."""
    import pygame
    if pygame.mixer.get_init() and _state["device"] == device:
        return pygame.mixer
    if pygame.mixer.get_init():
        pygame.mixer.quit()
    try:
        pygame.mixer.init(frequency=48000, size=-16, channels=2, devicename=device or None)
    except pygame.error:
        pygame.mixer.init(frequency=48000, size=-16, channels=2)  # the device is gone - the default
        device = ""
    pygame.mixer.set_num_channels(8)
    _state["device"] = device
    return pygame.mixer


def _current_mixer():
    import config as config_module
    return _mixer(chosen_output(config_module.load_config())[0])


# --- sounds, made here ---------------------------------------------------------------------------

def noise(kind: str, seconds: float = 12.0, rate: int = 48000) -> np.ndarray:
    """A seamless stereo loop of white, pink or brown noise (int16)."""
    n = int(seconds * rate)
    rng = np.random.default_rng()
    channels = []
    for _ in range(2):
        spectrum = np.fft.rfft(rng.standard_normal(n))
        f = np.fft.rfftfreq(n, 1 / rate)
        f[0] = f[1]
        if kind == "pink":
            spectrum /= np.sqrt(f)
        elif kind == "brown":
            spectrum /= f
            spectrum[f < 25] = 0  # no rumble a speaker can't play
        wave = np.fft.irfft(spectrum, n)  # periodic, so the loop has no seam
        channels.append(wave / (np.max(np.abs(wave)) or 1))
    stereo = np.stack(channels, axis=1) * 0.8
    return (stereo * 32767).astype(np.int16)


def chime(rate: int = 48000) -> np.ndarray:
    """A soft two-note bell."""
    out = np.zeros(int(rate * 1.8))
    for start, freq in ((0.0, 880.0), (0.35, 1318.5)):
        t = np.arange(int(rate * 1.4)) / rate
        tone = (np.sin(2 * np.pi * freq * t) + 0.3 * np.sin(2 * np.pi * freq * 2.01 * t)) * np.exp(-3.2 * t)
        s = int(start * rate)
        out[s:s + len(tone)] += tone
    out = out / np.max(np.abs(out)) * 0.6
    return (np.stack([out, out], axis=1) * 32767).astype(np.int16)


def _play_array(arr: np.ndarray, loops: int = 0, fade_ms: int = 0, volume: float = 1.0):
    import pygame
    mixer = _current_mixer()
    freq, _, chans = mixer.get_init()
    if freq != 48000:
        idx = (np.arange(int(len(arr) * freq / 48000)) * 48000 / freq).astype(int)
        arr = arr[idx]
    if chans == 1:
        arr = arr[:, :1].copy()
    sound = pygame.sndarray.make_sound(np.ascontiguousarray(arr))
    sound.set_volume(volume)
    sound.play(loops=loops, fade_ms=fade_ms)
    return sound


async def _say_on_output(text: str) -> None:
    if _synthesize is None:
        return
    import pygame
    audio = await _synthesize(text)
    _current_mixer()
    sound = pygame.mixer.Sound(file=io.BytesIO(audio))
    sound.play()
    await asyncio.sleep(sound.get_length() + 0.3)


def stop_noise(fade_ms: int = 1500) -> bool:
    sound = _state["noise"]
    task = _state["noise_task"]
    if task and not task.done():
        task.cancel()
    _state.update(noise=None, noise_kind="", noise_task=None)
    if sound is None:
        return False
    sound.fadeout(fade_ms)
    return True


def stop_ringing() -> bool:
    task = _state["ringing"]
    _state["ringing"] = None
    if task and not task.done():
        task.cancel()
        return True
    return False


# --- alarms --------------------------------------------------------------------------------------

def load_alarms() -> list[dict]:
    try:
        data = json.loads(ALARMS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return sorted([a for a in data if isinstance(a, dict) and a.get("id") and a.get("time")],
                  key=lambda a: (a.get("date") or "", a["time"]))


def _save_alarms(alarms: list[dict]) -> None:
    ALARMS_FILE.write_text(json.dumps(alarms[-100:], indent=2), encoding="utf-8")


def add_alarm(time_hm: str, day: str = "", repeat: str = "", label: str = "") -> dict:
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", time_hm or ""):
        raise ValueError("Alarm times look like 07:00.")
    if repeat not in ("", "daily", "weekdays"):
        raise ValueError("An alarm repeats daily, on weekdays, or not at all.")
    if not repeat and not day:
        now = datetime.now()
        day = (now.date() if time_hm > now.strftime("%H:%M") else now.date() + timedelta(days=1)).isoformat()
    alarm = {"id": uuid.uuid4().hex[:8], "time": time_hm, "date": "" if repeat else day, "repeat": repeat,
             "label": " ".join((label or "").split())[:80], "last_rang": ""}
    _save_alarms(load_alarms() + [alarm])
    return alarm


def remove_alarm(alarm_id: str) -> bool:
    alarms = load_alarms()
    kept = [a for a in alarms if a["id"] != alarm_id]
    _save_alarms(kept)
    return len(kept) != len(alarms)


def describe_alarm(a: dict) -> str:
    from plugins.calendar_events import spoken_day, spoken_time
    when = {"daily": "every day", "weekdays": "every weekday"}.get(a.get("repeat"), "")
    if not when:
        when = spoken_day(date.fromisoformat(a["date"])) if a.get("date") else ""
    return f"{spoken_time(a['time'])} {when}".strip() + (f" ({a['label']})" if a.get("label") else "")


def due_alarms(now: datetime | None = None) -> list[dict]:
    """Alarms whose minute has come (within the last two minutes, so a short gap doesn't skip one).
    One-off alarms are removed once they ring; repeating ones remember the day they rang."""
    now = now or datetime.now()
    alarms, due, keep = load_alarms(), [], []
    today = now.date().isoformat()
    for a in alarms:
        at = datetime.fromisoformat(f"{a.get('date') or today}T{a['time']}")
        fits_day = (not a.get("repeat") or a["repeat"] == "daily" or (a["repeat"] == "weekdays" and now.weekday() < 5))
        if fits_day and at <= now <= at + timedelta(minutes=2) and a.get("last_rang") != today:
            due.append(a)
            if a.get("repeat"):
                a["last_rang"] = today
                keep.append(a)
        elif a.get("repeat") or at > now:
            keep.append(a)  # a one-off alarm that's long past is dropped
    if len(keep) != len(alarms) or due:
        _save_alarms(keep)
    return due


def wake_up_text(a: dict, now: datetime | None = None) -> str:
    from plugins import calendar_events
    from plugins.calendar_events import spoken_time
    now = now or datetime.now()
    greeting = "Good morning, sir." if now.hour < 12 else ("Good afternoon, sir." if now.hour < 18 else "Good evening, sir.")
    text = f"{greeting} It's {spoken_time(now.strftime('%H:%M'))}."
    if a.get("label"):
        text += f" Your alarm: {a['label']}."
    today = calendar_events.between(now.date(), now.date())
    if today:
        text += " Today: " + "; ".join(calendar_events.describe(e, with_day=False) for e in today[:3]) + "."
    return text


async def ring(a: dict) -> None:
    """Chime and wake-up words now, then again every minute for five minutes until stopped."""
    started = time.monotonic()
    try:
        while time.monotonic() - started < RING_FOR_SECONDS:
            for _ in range(3):
                _play_array(chime())
                await asyncio.sleep(1.6)
            await _say_on_output(wake_up_text(a))
            await asyncio.sleep(RING_EVERY_SECONDS)
    except asyncio.CancelledError:
        pass


def start_ringing(a: dict) -> None:
    stop_ringing()
    _state["ringing"] = asyncio.get_running_loop().create_task(ring(a))


# --- tools ---------------------------------------------------------------------------------------

def _where(cfg: dict) -> str:
    device, which = chosen_output(cfg)
    return f"on the speaker ({device})" if which == "speaker" else "on your headphones"


async def play_noise(args: dict) -> str:
    import config as config_module
    import turn_context
    said = f"{args.get('kind') or ''} {turn_context.user_text.get() or ''}".lower()
    kind = next((v for k, v in _NOISE_WORDS.items() if k in said), "white")
    minutes = args.get("minutes")
    stop_noise(fade_ms=300)
    try:
        sound = await asyncio.to_thread(lambda: _play_array(noise(kind), loops=-1, fade_ms=3000, volume=NOISE_VOLUME))
    except Exception as exc:  # noqa: BLE001 - say what went wrong instead of pretending
        return lu.say(f"I couldn't play sound just now: {exc}.")
    _state.update(noise=sound, noise_kind=kind)
    note = ""
    if minutes:
        async def stop_later():
            await asyncio.sleep(float(minutes) * 60)
            stop_noise(fade_ms=8000)
        _state["noise_task"] = asyncio.get_running_loop().create_task(stop_later())
        note = f" for {int(minutes)} minutes"
    return lu.say(f"{kind.title()} noise is playing {_where(config_module.load_config())}{note}. Say stop when you've had enough.")


async def stop_sounds(args: dict) -> str:
    stopped = [what for what, done in (("the alarm", stop_ringing()), ("the noise", stop_noise())) if done]
    if not stopped:
        return lu.say("Nothing is playing, sir.")
    return lu.say(f"Stopped {' and '.join(stopped)}.")


async def set_alarm(args: dict) -> str:
    from plugins.calendar_events import _user_words, parse_day, parse_time, spoken_day
    words = _user_words()
    t = parse_time(args.get("time") or "", prefer_morning=True) or parse_time(words, prefer_morning=True)
    if t is None:
        return lu.say("What time should the alarm go off, sir?")
    repeat = (args.get("repeat") or "").lower()
    if not repeat and re.search(r"\bevery ?day|\bdaily\b|\beach (day|morning)\b", words, re.I):
        repeat = "daily"
    if not repeat and re.search(r"\bweekdays?\b|\bevery work ?day\b", words, re.I):
        repeat = "weekdays"
    day = ""
    if not repeat:
        d = parse_day(args.get("day") or "") or parse_day(words)
        if d is not None:
            if d < date.today():
                return lu.say(f"{spoken_day(d)} has already passed, sir.")
            day = d.isoformat()
    try:
        alarm = add_alarm(t, day, repeat if repeat in ("daily", "weekdays") else "", args.get("label") or "")
    except ValueError as exc:
        return lu.say(str(exc))
    import config as config_module
    return lu.say(f"Alarm set for {describe_alarm(alarm)}. It'll ring {_where(config_module.load_config())}.")


async def list_alarms(args: dict) -> str:
    alarms = load_alarms()
    if not alarms:
        return lu.say("No alarms are set, sir.")
    return lu.say("Your alarms: " + "; ".join(describe_alarm(a) for a in alarms) + ".")


async def cancel_alarm(args: dict) -> str:
    from plugins.calendar_events import _user_words, parse_time
    alarms = load_alarms()
    if not alarms:
        return lu.say("No alarms are set, sir.")
    t = parse_time(args.get("which") or "", prefer_morning=True) or parse_time(_user_words(), prefer_morning=True)
    matches = [a for a in alarms if a["time"] == t] if t else []
    if not matches and len(alarms) == 1:
        matches = alarms
    if not matches:
        return lu.say("Which alarm, sir? " + "; ".join(describe_alarm(a) for a in alarms) + ".")
    for a in matches:
        remove_alarm(a["id"])
    return lu.say("Cancelled the " + " and ".join(describe_alarm(a) for a in matches) + " alarm.")


SCHEMAS = [
    {"type": "function", "function": {
        "name": "play_noise",
        "description": "Plays white, pink (like rain) or brown (like a fan) noise to sleep or focus, on sir's speaker or headphones.",
        "parameters": {"type": "object", "properties": {
            "kind": {"type": "string", "description": "white, pink, brown - or how sir described it (rain, fan)"},
            "minutes": {"type": "integer", "description": "Only if sir said how long"}}}}},
    {"type": "function", "function": {
        "name": "stop_sounds",
        "description": "Stops the noise or a ringing alarm.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "set_alarm",
        "description": "Sets a wake-up alarm: it chimes and Echo says the time and the day's events, on the speaker.",
        "parameters": {"type": "object", "properties": {
            "time": {"type": "string", "description": "The time as sir said it ('7am', 'half past 6')"},
            "day": {"type": "string", "description": "The day, only if sir said one ('tomorrow', 'Saturday')"},
            "repeat": {"type": "string", "enum": ["", "daily", "weekdays"], "description": "Only if sir asked for it to repeat"},
            "label": {"type": "string", "description": "What it's for, only if sir said"}}, "required": ["time"]}}},
    {"type": "function", "function": {
        "name": "list_alarms",
        "description": "Lists sir's alarms.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "cancel_alarm",
        "description": "Cancels one of sir's alarms.",
        "parameters": {"type": "object", "properties": {
            "which": {"type": "string", "description": "The alarm's time as sir said it"}}}}},
]

DISPATCH = {
    "play_noise": play_noise,
    "stop_sounds": stop_sounds,
    "set_alarm": set_alarm,
    "list_alarms": list_alarms,
    "cancel_alarm": cancel_alarm,
}
