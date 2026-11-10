import base64
import ctypes
import os
import queue
import re
import site
import tempfile
import threading
import time
from pathlib import Path

import httpx
import numpy as np
import sounddevice as sd

import config as config_module
import hearing_words
import passcode

# faster-whisper's CUDA path needs cuBLAS/cuDNN, which the pip-installed
# nvidia-cublas-cu12 / nvidia-cudnn-cu12 packages provide but don't put on
# Windows' DLL search path automatically - without this, GPU transcription
# loads fine but crashes on the first real inference.
for _pkg in ("cublas", "cudnn", "cuda_nvrtc"):
    for _site_dir in site.getsitepackages():
        _dll_dir = Path(_site_dir) / "nvidia" / _pkg / "bin"
        if _dll_dir.is_dir():
            os.add_dll_directory(str(_dll_dir))

SAMPLE_RATE = 16000
BLOCK_DURATION = 0.1
BLOCK_SIZE = int(SAMPLE_RATE * BLOCK_DURATION)
SILENCE_RMS_THRESHOLD = 500
SPEECH_START_BLOCKS = 2
# 3s of silence needed before treating speech as over - room to breathe/pause
# mid-sentence without getting cut off. Trades some response latency for that.
SILENCE_END_BLOCKS = 30
# Smart end-of-speech: after this much quiet (1.2 s) the words so far are checked - a
# finished-sounding sentence is answered straight away instead of waiting the full 3 s;
# one that trails off ("and...", "um") keeps listening.
EARLY_END_BLOCKS = 12
_TRAILING_WORDS = {"and", "um", "uh", "so", "but", "or", "the", "to", "a", "an", "like", "because", "with", "of",
                   "for", "then", "my", "your", "if", "can", "could", "please", "on", "in", "at", "is"}
# Things speech recognition is known to "hear" in silence or noise (it learned them from
# video subtitles). Never treated as something sir said.
_HALLUCINATIONS = ("thanks for watching", "thank you for watching", "see you next time", "see you in the next",
                   "please subscribe", "don't forget to subscribe", "like and subscribe", "subtitles by",
                   "sous-titrage", "amara.org", "transcribed by")
_ONLY_NOISE = {"you", "thank you", "thanks", "bye", "okay", "hmm", "uh", "um", "oh", "so"}
MAX_UTTERANCE_SECONDS = 12
MIN_UTTERANCE_SECONDS = 0.4
AWAKE_WINDOW_SECONDS = 45
# Each wake word maps to the max edit distance allowed for fuzzy matching.
# "echo" is only 4 letters, so it gets 1 - at 2, ordinary words like "each"
# and "ache" would wake it. "jarvis" is the original name and still works.
WAKE_WORDS = {"echo": 1, "jarvis": 2}
# Whisper often writes "echo" as a near-homophone that's too short or too far
# off for fuzzy matching, so these are accepted exactly.
WAKE_ALIASES = {"eco", "ecko", "ekko", "ecco", "eko"}
CHAT_URL = "http://127.0.0.1:8765/api/chat"
REMINDERS_URL = "http://127.0.0.1:8765/api/calendar/reminders"
REMINDER_POLL_SECONDS = 15
LISTENER_EVENT_URL = "http://127.0.0.1:8765/api/listener/event"
LISTENER_MIC_URL = "http://127.0.0.1:8765/api/listener/mic"
# How often to look for a change of Windows' default mic, and how long the mic can
# send nothing at all before the stream is treated as dead (device unplugged).
DEVICE_CHECK_SECONDS = 3
STALL_SECONDS = 5
MIC_REPORT_SECONDS = 1.5

_whisper_model = None
_audio_queue: "queue.Queue[np.ndarray]" = queue.Queue()
_awake_until = 0.0

# Prefer a real physical mic over whatever Windows currently has set as the
# default recording device - that default has a way of silently becoming a
# virtual device (Voicemod, a VR headset, etc.) instead of the mic you're
# actually speaking into.
PREFERRED_DEVICE_NAMES = ["Insta360 Link", "HyperX Cloud", "Headset Microphone"]

# Devices that silently capture something other than a real physical mic
# (game-audio bridges, VR audio loopbacks, voice-changer virtual devices) -
# if Windows' actual default input is one of these, it's worth overriding.
# Otherwise, trust the real default rather than a hardcoded preference list -
# a fixed priority order previously picked a webcam's mic over the user's
# actual headset just because it came first in PREFERRED_DEVICE_NAMES, even
# though the headset was already correctly set as the Windows default.
BLOCKED_DEVICE_NAME_FRAGMENTS = [
    "voicemod", "virtual desktop audio", "oculus virtual", "vdvad",
    "steelseries sonar", "stereo mix", "wave out mix",
]


def _pick_input_device() -> int | None:
    try:
        devices = sd.query_devices()
        default_input = sd.query_devices(kind="input")
    except Exception as exc:  # noqa: BLE001
        print(f"[listener] could not query audio devices: {exc}", flush=True)
        return None

    if config_module.load_config().get("vr_mode_enabled"):
        # VR Mode (Virtual Desktop passes the Quest's own mic into Windows as
        # a device this blocklist would normally reject - see BLOCKED_DEVICE_NAME_FRAGMENTS)
        # trusts Windows' actual default unconditionally, since sir explicitly
        # wants to talk through the headset's mic while in VR.
        print(f"[listener] VR Mode is on - trusting Windows' default input device unconditionally: {default_input['name']}", flush=True)
        return None

    default_name = (default_input.get("name") or "").lower()
    if not any(bad in default_name for bad in BLOCKED_DEVICE_NAME_FRAGMENTS):
        print(f"[listener] using Windows' default input device: {default_input['name']}", flush=True)
        return None  # None tells sounddevice to use the system default itself

    print(f"[listener] Windows' default input ('{default_input['name']}') looks like a virtual/loopback device - looking for a real mic instead", flush=True)
    for preferred in PREFERRED_DEVICE_NAMES:
        for i, d in enumerate(devices):
            if d["max_input_channels"] > 0 and preferred.lower() in d["name"].lower():
                print(f"[listener] using fallback input device: {d['name']}", flush=True)
                return i

    print("[listener] no known-good mic found either - falling back to system default anyway", flush=True)
    return None


class _WAVEINCAPSW(ctypes.Structure):
    _fields_ = [
        ("wMid", ctypes.c_ushort), ("wPid", ctypes.c_ushort), ("vDriverVersion", ctypes.c_uint),
        ("szPname", ctypes.c_wchar * 32), ("dwFormats", ctypes.c_uint), ("wChannels", ctypes.c_ushort),
        ("wReserved1", ctypes.c_ushort),
    ]


def _windows_mic_fingerprint() -> tuple:
    """Which mic Windows prefers right now, asked live. PortAudio reads the device list
    once and never notices a change, so this is what tells the listener to reopen
    when sir switches devices. Cheap enough to call every few seconds."""
    try:
        winmm = ctypes.windll.winmm
        count = winmm.waveInGetNumDevs()
        dev_id, flags = ctypes.c_uint(0), ctypes.c_uint(0)
        # DRVM_MAPPER_PREFERRED_GET on the wave mapper (WAVE_MAPPER = -1).
        winmm.waveInMessage(ctypes.c_uint(0xFFFFFFFF), 0x2015, ctypes.byref(dev_id), ctypes.byref(flags))
        caps = _WAVEINCAPSW()
        winmm.waveInGetDevCapsW(dev_id.value, ctypes.byref(caps), ctypes.sizeof(caps))
        return (count, caps.szPname)
    except Exception:  # noqa: BLE001 - no change detection is better than no listener
        return ()


def _refresh_portaudio() -> None:
    """Make PortAudio re-read the device list, so a new default mic is actually seen."""
    try:
        sd._terminate()
        sd._initialize()
    except Exception as exc:  # noqa: BLE001
        print(f"[listener] couldn't refresh the audio device list: {exc}", flush=True)


def _device_name(device: int | None) -> str:
    try:
        return sd.query_devices(device if device is not None else sd.default.device[0], "input")["name"]
    except Exception:  # noqa: BLE001
        try:
            return sd.query_devices(kind="input")["name"]
        except Exception:  # noqa: BLE001
            return "unknown mic"


def _report_mic(name: str, level: float, note: str = "") -> None:
    """Which mic is in use and how loud it's hearing - shown on the dashboard so sir can
    tell at a glance whether Echo can hear him. Sent off-thread; never blocks capture."""
    def send():
        try:
            httpx.post(LISTENER_MIC_URL, json={"device": name, "level": level, "note": note}, timeout=1.5)
        except Exception:  # noqa: BLE001
            pass
    threading.Thread(target=send, daemon=True).start()


def _load_whisper_cpu():
    from faster_whisper import WhisperModel
    print("[listener] loading Whisper on CPU (int8)", flush=True)
    return WhisperModel("small.en", device="cpu", compute_type="int8")


# large-v3-turbo: the same size class as medium.en (809M vs 769M parameters) but about
# twice as fast and better on cheap/quiet mics, in Echo's own hearing test (good mic 94%,
# cheap headset 91%, bad mic 82% of whole requests right, vs 93/89/73% for medium.en).
SPEECH_MODEL = "large-v3-turbo"


def _get_whisper():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        try:
            _whisper_model = WhisperModel(SPEECH_MODEL, device="cuda", compute_type="float16")
            print(f"[listener] Whisper loaded on GPU (cuda/float16, {SPEECH_MODEL})", flush=True)
        except Exception as exc:
            print(f"[listener] GPU load failed ({exc}), falling back to CPU", flush=True)
            _whisper_model = _load_whisper_cpu()
    return _whisper_model


def _rms(block: np.ndarray) -> float:
    samples = block.astype(np.float64).ravel()
    return float(np.sqrt(np.dot(samples, samples) / samples.size))


def _audio_callback(indata, frames, time_info, status) -> None:
    _audio_queue.put(indata.copy())


def _drain_queue() -> None:
    while not _audio_queue.empty():
        try:
            _audio_queue.get_nowait()
        except queue.Empty:
            break


def _levenshtein(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + (ca != cb))
        prev = curr
    return prev[-1]


# Whisper regularly mishears the wake word as something close-but-not-exact
# ("Jervis", "Jovis", "Ecko"...) - requiring an exact substring match meant
# a real, clearly-directed utterance would just silently get dropped.
# Fuzzy-matching each word against the wake words (allowing a small edit
# distance) catches those near-misses while a minimum word length keeps
# short unrelated words from matching by chance.
_WAKE_WORD_RE = re.compile(r"[a-z']+")

# Spelling it out letter by letter ("E. C. H. O." / "J. A. R. V. I. S.") is a
# deliberate, unmistakable way to get through even when Whisper keeps
# mangling the word as a whole - spelled-out letters transcribe as single
# characters, which the word-level fuzzy match can never catch.
_SPELLED_OUT_RE = re.compile(r"\b(?:e\W*c\W*h\W*o|j\W*a\W*r\W*v\W*i\W*s)\b")

# "Wake up" as a second, independent wake phrase, requested as an
# alternative to needing the name to land correctly at all - trades some
# false-positive risk (it's a far more common phrase in ordinary speech/media
# than "Echo") for a phrase Whisper has no trouble transcribing accurately.
_ALT_WAKE_PHRASE_RE = re.compile(r"\bwake\s+up\b")


def _find_wake_word(lower: str) -> tuple[int, int] | None:
    candidates = []

    spelled = _SPELLED_OUT_RE.search(lower)
    if spelled:
        candidates.append((spelled.start(), spelled.end()))

    alt = _ALT_WAKE_PHRASE_RE.search(lower)
    if alt:
        candidates.append((alt.start(), alt.end()))

    for match in _WAKE_WORD_RE.finditer(lower):
        word = match.group()
        if word in WAKE_ALIASES or (
            len(word) >= 4
            and any(abs(len(word) - len(name)) <= tol and _levenshtein(word, name) <= tol
                    for name, tol in WAKE_WORDS.items())
        ):
            candidates.append((match.start(), match.end()))
            break

    if not candidates:
        return None
    return min(candidates, key=lambda span: span[0])


def _is_sleep_command(lower: str) -> bool:
    # A minor transcription slip (one wrong or missing word) is common enough
    # that requiring the exact phrase "night echo" made this fail silently
    # whenever the mic misheard even slightly - checking for both words
    # appearing anywhere, rather than as one exact substring, is far more
    # forgiving while still being clearly intentional rather than accidental.
    return "night" in lower and _find_wake_word(lower) is not None


def _extract_command(transcript: str) -> str | None:
    lower = transcript.lower()
    span = _find_wake_word(lower)
    if span is None:
        return None
    return transcript[span[1]:].strip(" ,.:!-")


def _boost(audio: np.ndarray) -> np.ndarray:
    """Automatic volume: a quiet or cheap mic is brought up to a normal speaking level before
    recognition (the loudest third of the audio is scaled to a set level, at most 30x)."""
    frame = 1600
    n = len(audio) // frame
    if n == 0:
        return audio
    rms = np.sqrt(np.mean(audio[: n * frame].reshape(n, frame) ** 2, axis=1))
    loud = float(np.sort(rms)[-max(1, n // 3):].mean())
    if loud < 1e-5:
        return audio
    return np.clip(audio * min(0.1 / loud, 30.0), -1, 1).astype(np.float32)


def _is_noise(text: str) -> bool:
    low = re.sub(r"[^a-z' ]+", " ", text.lower()).strip()
    return not low or low in _ONLY_NOISE or any(h in low for h in _HALLUCINATIONS)


def _recognize(model, audio_f32: np.ndarray) -> str:
    # No example sentence as a hint any more: the old one ("Hey Echo, how can I help you
    # today?") got "heard" in silence and sent to Echo as a command. The hint is now just
    # the words Echo should know, and the silence filter runs AFTER the volume boost, so
    # it no longer mistakes a quiet voice for silence.
    segments, _ = model.transcribe(_boost(audio_f32), language="en", beam_size=5, condition_on_previous_text=False,
                                   vad_filter=True, hotwords=hearing_words.hotwords())
    text = " ".join(seg.text for seg in segments).strip()
    return "" if _is_noise(text) else text


def _transcribe(audio: np.ndarray) -> str:
    global _whisper_model
    model = _get_whisper()
    audio_f32 = audio.astype(np.float32) / 32768.0
    try:
        return _recognize(model, audio_f32)
    except RuntimeError as exc:
        # The GPU model can load successfully but still fail on the first real
        # inference (e.g. a missing CUDA library) - self-heal to CPU rather than
        # fail this same way on every single utterance from now on.
        print(f"[listener] GPU transcription failed ({exc}), switching to CPU for future requests", flush=True)
        _whisper_model = _load_whisper_cpu()
        return _recognize(_whisper_model, audio_f32)


def _sounds_finished(text: str) -> bool:
    words = re.findall(r"[a-z']+", text.lower())
    return len(words) >= 2 and text.rstrip().endswith((".", "?", "!")) and words[-1] not in _TRAILING_WORDS


_voice_device = [None]  # the output the mixer is open on


def _open_voice_output() -> None:
    """Echo's voice plays on the output chosen on the Setup page ("" = the Windows default, your
    headphones) - reopened only when that choice changes."""
    import pygame
    wanted = config_module.load_config().get("voice_output_device") or ""
    if pygame.mixer.get_init() and _voice_device[0] == wanted:
        return
    if pygame.mixer.get_init():
        pygame.mixer.quit()
    try:
        pygame.mixer.init(devicename=wanted or None)
    except pygame.error:
        pygame.mixer.init()  # that output is gone (unplugged) - the default instead
    _voice_device[0] = wanted


def _play_audio(audio_b64: str) -> None:
    import pygame
    _open_voice_output()

    audio_bytes = base64.b64decode(audio_b64)
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name
    try:
        pygame.mixer.music.load(tmp_path)
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            time.sleep(0.1)
    finally:
        pygame.mixer.music.unload()
        Path(tmp_path).unlink(missing_ok=True)


def _report_status(phase: str, text: str = "") -> None:
    """Best-effort only - the dashboard's live status indicator is a nice-to-have,
    never worth risking the actual listener over if the backend happens to be
    slow or briefly unreachable."""
    try:
        httpx.post(LISTENER_EVENT_URL, json={"phase": phase, "text": text}, timeout=2)
    except Exception:  # noqa: BLE001
        pass


def _send_to_echo(text: str) -> None:
    try:
        resp = httpx.post(CHAT_URL, json={"message": text}, timeout=120)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001 - keep the listener alive on any request failure
        print(f"[listener] chat request failed: {exc}", flush=True)
        _report_status("idle")
        return

    if not data.get("reply") or not data.get("audio"):
        print("[listener] Echo is switched off, staying silent", flush=True)
        _report_status("idle")
        return
    _report_status("speaking", data["reply"])
    _play_audio(data["audio"])
    _report_status("idle")


def _say_due_reminders() -> bool:
    """Speaks calendar reminders that came due (the mic is paused meanwhile, like any reply, so
    Echo never hears itself). Returns whether anything was said."""
    if not config_module.load_config().get("ai_enabled", True):
        return False
    try:
        due = httpx.get(REMINDERS_URL, params={"speak": 1}, timeout=30).json()
    except Exception:  # noqa: BLE001 - the server may be restarting; try again next time
        return False
    for reminder in due:
        try:
            _report_status("speaking", reminder["text"])
            _play_audio(reminder["audio"])
        except Exception as exc:  # noqa: BLE001 - a bad clip mustn't stop the listener
            print(f"[listener] couldn't say a reminder: {exc}", flush=True)
    if due:
        _report_status("idle")
        _drain_queue()
    return bool(due)


def _handle_utterance(audio: np.ndarray, transcript: str | None = None) -> None:
    """Runs synchronously on the capture loop's thread, deliberately - this pauses
    listening for the duration, so Echo speaking its own reply out loud never gets
    picked back up by the mic as a new utterance."""
    try:
        _handle_utterance_inner(audio, transcript)
    except Exception as exc:  # noqa: BLE001 - nothing in here may ever be allowed to kill the listener thread
        print(f"[listener] unexpected error handling utterance, skipping it: {exc}", flush=True)


def _handle_utterance_inner(audio: np.ndarray, transcript: str | None = None) -> None:
    global _awake_until

    duration = len(audio) / SAMPLE_RATE
    if duration < MIN_UTTERANCE_SECONDS:
        return

    if transcript is None:
        transcript = _transcribe(audio)
    if not transcript:
        return
    # The passcode never reaches the log or the dashboard; Echo itself gets the raw words
    # and cuts it out on arrival.
    print(f"[listener] heard: {passcode.scrub(transcript)[0]!r}", flush=True)

    now = time.time()
    lower = transcript.lower()

    if now < _awake_until:
        if _is_sleep_command(lower):
            _awake_until = 0.0
            print("[listener] back to sleep", flush=True)
            _report_status("asleep")  # the dashboard's orb goes to its night look
            return
        command = transcript.strip()
    elif _is_sleep_command(lower):
        # Already asleep - "night echo" said again is a harmless no-op,
        # not a command to pass along.
        return
    else:
        command = _extract_command(transcript)
        if command is None:
            return

    if not command:
        _awake_until = now + AWAKE_WINDOW_SECONDS
        return

    shown = passcode.scrub(command)[0]
    print(f"[listener] command: {shown!r}", flush=True)
    _awake_until = now + AWAKE_WINDOW_SECONDS
    _report_status("thinking", shown)
    _send_to_echo(command)
    _drain_queue()


def run() -> None:
    print("[listener] background voice listener starting", flush=True)
    first = True
    while True:
        if not first:
            _refresh_portaudio()
        first = False
        fingerprint = _windows_mic_fingerprint()
        device = _pick_input_device()
        name = _device_name(device)
        _drain_queue()
        _report_mic(name, 0.0)
        try:
            with sd.InputStream(
                samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                blocksize=BLOCK_SIZE, callback=_audio_callback, device=device,
            ):
                reason = _capture(fingerprint, name)
        except Exception as exc:  # noqa: BLE001 - a mic that vanished mustn't end the listener
            reason = f"the mic couldn't be opened ({exc})"
            _report_mic(name, 0.0, "can't open this mic")
            time.sleep(2)
        print(f"[listener] {reason} - reopening the mic", flush=True)


def _capture(fingerprint: tuple, name: str) -> str:
    """Listens until the mic needs reopening, and says why."""
    buffer: list[np.ndarray] = []
    speech_blocks = 0
    silence_blocks = 0
    in_speech = False
    early_checked = False
    last_block = last_check = last_report = last_reminders = time.monotonic()
    peak = 0.0

    while True:
        now = time.monotonic()
        if not in_speech and now - last_check >= DEVICE_CHECK_SECONDS:
            last_check = now
            current = _windows_mic_fingerprint()
            if fingerprint and current and current != fingerprint:
                return f"Windows' default mic changed to {current[1]!r}"
        if now - last_report >= MIC_REPORT_SECONDS:
            last_report = now
            _report_mic(name, round(peak, 1))
            peak = 0.0
        if not in_speech and now - last_reminders >= REMINDER_POLL_SECONDS:
            last_reminders = now
            if _say_due_reminders():
                buffer, speech_blocks, silence_blocks = [], 0, 0
                last_block = time.monotonic()

        if not config_module.load_config().get("ai_enabled", True):
            _drain_queue()
            buffer, speech_blocks, silence_blocks, in_speech = [], 0, 0, False
            last_block = time.monotonic()
            time.sleep(1.0)
            continue

        try:
            block = _audio_queue.get(timeout=1.0).flatten()
        except queue.Empty:
            if time.monotonic() - last_block > STALL_SECONDS:
                return "the mic stopped sending audio"
            continue
        last_block = time.monotonic()

        level = _rms(block)
        peak = max(peak, level)

        if level > SILENCE_RMS_THRESHOLD:
            speech_blocks += 1
            silence_blocks = 0
            early_checked = False
            buffer.append(block)
            in_speech = in_speech or speech_blocks >= SPEECH_START_BLOCKS
        elif in_speech:
            silence_blocks += 1
            buffer.append(block)
        else:
            speech_blocks = 0
            buffer = []

        utterance_too_long = in_speech and len(buffer) * BLOCK_DURATION > MAX_UTTERANCE_SECONDS
        utterance_done = in_speech and silence_blocks >= SILENCE_END_BLOCKS
        early_text = None
        if in_speech and not utterance_done and not early_checked and silence_blocks >= EARLY_END_BLOCKS:
            early_checked = True
            heard = _transcribe(np.concatenate(buffer))
            if heard and _sounds_finished(heard):
                utterance_done, early_text = True, heard

        if utterance_done or utterance_too_long:
            audio = np.concatenate(buffer) if buffer else np.array([], dtype=np.int16)
            buffer, speech_blocks, silence_blocks, in_speech = [], 0, 0, False
            early_checked = False
            if len(audio) > 0:
                _handle_utterance(audio, early_text)
            last_block = time.monotonic()
