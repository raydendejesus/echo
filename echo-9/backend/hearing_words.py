"""
The words Echo should be ready to hear - names that speech recognition has never
seen and otherwise mangles ("The Burnt Peanut" -> "bird peanut").

Collected automatically from what Echo already knows - phone book names, address
book places, YouTubers and Netflix shows sir has asked for - plus words sir adds
himself on the dashboard (backend/hearing_words.json, private). The listener hands
this list to the recognizer as "hotwords", which nudges it toward these spellings
without forcing them.
"""
import json
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
CUSTOM_FILE = BACKEND / "hearing_words.json"
ALWAYS = ["Echo", "Jarvis", "Netflix", "YouTube", "Spotify", "Steam", "Discord"]
# Whisper only reads the first couple of hundred tokens of a hint, so newest/most
# personal words go first and the list is capped.
MAX_WORDS = 60
REFRESH_SECONDS = 60

_cache: tuple[float, str] = (0.0, "")


def _json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def custom_words() -> list[str]:
    data = _json(CUSTOM_FILE, [])
    return [w for w in data if isinstance(w, str) and w.strip()]


def add_custom(word: str) -> list[str]:
    word = " ".join(word.split())
    words = [w for w in custom_words() if w.lower() != word.lower()]
    if word:
        words.insert(0, word)
    CUSTOM_FILE.write_text(json.dumps(words, indent=2), encoding="utf-8")
    _reset()
    return words


def remove_custom(word: str) -> list[str]:
    words = [w for w in custom_words() if w.lower() != word.strip().lower()]
    CUSTOM_FILE.write_text(json.dumps(words, indent=2), encoding="utf-8")
    _reset()
    return words


def _reset() -> None:
    global _cache
    _cache = (0.0, "")


def _learned() -> list[str]:
    words: list[str] = []
    for entry in _json(BACKEND / "phonebook.json", []):
        if isinstance(entry, dict) and entry.get("name") and entry["name"].lower() != "sir":
            words.append(entry["name"])
    for entry in _json(BACKEND / "address_book.json", []):
        if isinstance(entry, dict) and entry.get("name"):
            words.append(entry["name"])
    for entry in _json(BACKEND / "plugins_data" / "youtube_channels.json", {}).values():
        if isinstance(entry, dict) and entry.get("name"):
            words.append(entry["name"])
    for entry in _json(BACKEND / "plugins_data" / "netflix_titles.json", {}).values():
        if isinstance(entry, dict) and entry.get("name"):
            words.append(entry["name"])
    return words


def all_words() -> list[str]:
    seen, out = set(), []
    for w in custom_words() + _learned() + ALWAYS:
        key = w.lower()
        if key not in seen:
            seen.add(key)
            out.append(w)
    return out[:MAX_WORDS]


def hotwords() -> str:
    """The hint string for the recognizer, refreshed at most once a minute."""
    global _cache
    if time.time() - _cache[0] < REFRESH_SECONDS and _cache[1]:
        return _cache[1]
    text = ", ".join(all_words())
    _cache = (time.time(), text)
    return text
