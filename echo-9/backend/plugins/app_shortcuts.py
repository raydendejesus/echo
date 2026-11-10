"""
Saved shortcuts to things that aren't installed Windows apps - mainly websites
like Netflix, YouTube or Twitch - opened by name in the default browser (or in a
new tab through the Echo browser extension when browser control is on).

Common sites (GitHub, Excalidraw, Gmail, Google Docs, ChatGPT...) are known by
name without saving anything; a saved shortcut with the same name wins.

Adding one is a spoken request ("add a shortcut called Crunchyroll for
crunchyroll.com") or a line in plugins_data/app_shortcuts.json. The add tool
only accepts web addresses; pointing a shortcut at a local program is done by
editing that file by hand, so a manipulated request can never register one.
"""
import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse

import launch_utils as lu

PLUGIN_NAME = "app_shortcuts"
TOGGLE_LABEL = "Custom Shortcuts (Netflix, websites)"
CONFIG_KEY = "app_shortcuts_enabled"
ENABLED_BY_DEFAULT = True
VRAM_COST = "no local model"
# Offered on phone calls from a phone book contact with the "sir" switch on.
SIR_CALLS = True

DATA_FILE = Path(__file__).resolve().parent.parent / "plugins_data" / "app_shortcuts.json"

DEFAULT_SHORTCUTS = {
    "Netflix": "https://www.netflix.com",
    "YouTube": "https://www.youtube.com",
    "Twitch": "https://www.twitch.tv",
    "Disney Plus": "https://www.disneyplus.com",
    "Hulu": "https://www.hulu.com",
    "Prime Video": "https://www.primevideo.com",
}

# Known by name without being saved - "open Excalidraw", "go to GitHub".
KNOWN_SITES = {
    "GitHub": "https://github.com",
    "Excalidraw": "https://excalidraw.com",
    "Google": "https://www.google.com",
    "Gmail": "https://mail.google.com",
    "Google Drive": "https://drive.google.com",
    "Google Docs": "https://docs.google.com",
    "Google Sheets": "https://sheets.google.com",
    "Google Maps": "https://maps.google.com",
    "ChatGPT": "https://chatgpt.com",
    "Claude": "https://claude.ai",
    "Reddit": "https://www.reddit.com",
    "Wikipedia": "https://www.wikipedia.org",
    "Amazon": "https://www.amazon.com",
    "Twitter": "https://x.com",
    "Instagram": "https://www.instagram.com",
    "Facebook": "https://www.facebook.com",
    "TikTok": "https://www.tiktok.com",
    "Pinterest": "https://www.pinterest.com",
    "LinkedIn": "https://www.linkedin.com",
    "Figma": "https://www.figma.com",
    "Canva": "https://www.canva.com",
    "Stack Overflow": "https://stackoverflow.com",
    "Hugging Face": "https://huggingface.co",
    "CodePen": "https://codepen.io",
    "Crunchyroll": "https://www.crunchyroll.com",
}
_SITE_FILLER = re.compile(r"\b(the|my|a|website|web site|site|page|home ?page|dot com)\b")


def _load() -> dict[str, str]:
    if not DATA_FILE.exists():
        _save(DEFAULT_SHORTCUTS)
        return dict(DEFAULT_SHORTCUTS)
    try:
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULT_SHORTCUTS)
    return {str(k): str(v) for k, v in data.items()}


def _save(shortcuts: dict[str, str]) -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(shortcuts, indent=2, ensure_ascii=False), encoding="utf-8")


def _clean_web_address(raw: str) -> str | None:
    raw = raw.strip()
    if not raw or "\\" in raw or " " in raw:
        return None
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    host = parsed.hostname or ""
    if parsed.scheme not in ("http", "https") or "." not in host:
        return None
    if not re.fullmatch(r"[A-Za-z0-9.-]+", host):
        return None
    return raw


def _as_items(shortcuts: dict[str, str]) -> list[dict]:
    return [{"name": name, "target": target} for name, target in shortcuts.items()]


_WEB_ADDRESS = re.compile(r"\b(?:https?://)?[\w-]+(?:\.[\w-]+)*\.(?:com|net|org|tv|io|gg|app|co|us|me|fm|ly)\b(?:/\S*)?", re.I)
_ADD_WORDS = re.compile(r"\b(add|save|create|make|set up)\b", re.I)


def _all() -> dict[str, str]:
    """Sir's saved shortcuts first, then the common sites - so a saved name wins a tie."""
    saved = _load()
    taken = {k.lower() for k in saved}
    return {**saved, **{k: v for k, v in KNOWN_SITES.items() if k.lower() not in taken}}


def find(name: str, strict: bool = False) -> tuple[str, str] | None:
    """(shortcut name, target) for a name sir says, if one clearly matches. strict: only the name
    itself, near enough exactly - for words that may really be a search ("godot docs" isn't Google Docs)."""
    if strict:
        name = re.sub(r"\s+", " ", _SITE_FILLER.sub(" ", lu.norm(name))).strip()
    ranked = lu.rank(name, _as_items(_all()))
    if not ranked or ranked[0][0] < (0.9 if strict else lu.MATCH_THRESHOLD):
        return None
    return ranked[0][1]["name"], ranked[0][1]["target"]


async def open_shortcut(args: dict) -> str:
    name = (args.get("name") or "").strip()
    if not name:
        return lu.say("Which website should I open?")
    # "Add a shortcut called Crunchyroll for crunchyroll.com" sometimes arrives here; if sir
    # really asked to add one and gave an address, add it rather than failing to open it.
    import turn_context
    said = turn_context.user_text.get()
    address = _WEB_ADDRESS.search(said)
    if address and _ADD_WORDS.search(said) and find(name) is None:
        return await add_shortcut({"name": name, "address": address.group(0)})
    found = find(name)
    if found is None:
        typed = _clean_web_address(name)  # "open excalidraw.com"
        if typed:
            return await lu.open_in_browser((urlparse(typed).hostname or typed).removeprefix("www."), typed)
        near = lu.suggestions(lu.rank(name, _as_items(_all()))) or "none that are close"
        return lu.say(f"I don't have a shortcut or know a site called {name}. Closest: {near}. "
                      "I can add it if you tell me its web address.")
    label, target = found
    if target.lower().startswith(("http://", "https://")):
        return await lu.open_in_browser(label, target)
    # a shortcut pointed at a local program by editing the file by hand (see the top of this file)
    blocked = lu.launch_blocked_reason("shortcut:" + target)
    if blocked:
        return f"{label}: {blocked}."
    if not os.path.exists(target):
        return f"The shortcut '{label}' points at {target}, which doesn't exist any more."
    os.startfile(target)
    lu.note_launch("shortcut:" + target)
    return lu.say(f"Opening {label}.")


async def add_shortcut(args: dict) -> str:
    name = (args.get("name") or "").strip()
    address = _clean_web_address(args.get("address") or "")
    if not name:
        return "What should the shortcut be called?"
    if address is None:
        return "I need a valid web address for that shortcut, like netflix.com."
    shortcuts = _load()
    shortcuts[name] = address
    _save(shortcuts)
    return lu.say(f"Saved a shortcut called {name} for {address}.")


async def remove_shortcut(args: dict) -> str:
    name = (args.get("name") or "").strip()
    shortcuts = _load()
    ranked = lu.rank(name, _as_items(shortcuts))
    if not name or not ranked or ranked[0][0] < 0.8:
        return f"I don't have a shortcut that clearly matches '{name}'."
    removed = ranked[0][1]["name"]
    del shortcuts[removed]
    _save(shortcuts)
    return f"Removed the {removed} shortcut."


async def list_shortcuts(args: dict) -> str:
    shortcuts = _load()
    mine = ("Saved shortcuts: " + ", ".join(shortcuts) + ". ") if shortcuts else "No shortcuts saved. "
    return mine + "I also know common sites by name, like " + ", ".join(list(KNOWN_SITES)[:8]) + "."


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "open_shortcut",
            "description": (
                "Opens a website by name in sir's browser: his saved shortcuts (Netflix, YouTube, Twitch...), "
                "common sites known by name (GitHub, Excalidraw, Gmail, Google Docs, ChatGPT, Reddit, Wikipedia...) "
                "or a web address. Use this for anything that is a website rather than an installed app. Only "
                "for opening the site itself - to play a particular show or movie on Netflix, always use netflix_play."
            ),
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_shortcut",
            "description": "Saves a new shortcut: a name sir will say, and the website address it opens.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "What sir will call it, e.g. Crunchyroll."},
                    "address": {"type": "string", "description": "The website address, e.g. crunchyroll.com."},
                },
                "required": ["name", "address"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_shortcut",
            "description": "Deletes a saved shortcut by name.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_shortcuts",
            "description": "Lists the saved shortcut names.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

DISPATCH = {
    "open_shortcut": lu.spoken(open_shortcut),
    "add_shortcut": lu.spoken(add_shortcut),
    "remove_shortcut": lu.spoken(remove_shortcut),
    "list_shortcuts": lu.spoken(list_shortcuts),
}
