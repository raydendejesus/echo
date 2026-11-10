import json
from pathlib import Path

import plugin_loader

CONFIG_FILE = Path(__file__).resolve().parent / "config.json"

DEFAULTS = {
    "ai_enabled": True,
    "screen_access": False,
    "camera_access": False,
    "desk_guard_enabled": False,
    "desk_guard_threshold": 0.65,
    "calling_enabled": False,
    "call_notifications_enabled": False,
    "sir_page_enabled": False,
    # Numbers not in the phone book: take a message instead of hanging up straight away.
    "screen_unknown_callers": True,
    # Echo may suggest projects of its own when sir has been away a while. A suggestion
    # is only a request on the dashboard - nothing is built until he approves it.
    "agent_suggestions_enabled": True,
    "ollama_parallel_2_enabled": False,
    "location_mode": "off",
    "browser_control_enabled": False,
    "browser_pixel_fallback_enabled": False,
    "code_canvas_enabled": False,
    "vr_mode_enabled": False,
}
# A plugin's toggle gets a default the moment its file is dropped into
# backend/plugins/ - no manual edit here needed.
DEFAULTS.update(plugin_loader.config_defaults())


# The listener and the dashboard ask for the settings many times a second; re-read the file only
# when it actually changed (any process's write changes its modified time or size).
_cache: tuple[tuple[int, int], dict] | None = None


def load_config() -> dict:
    global _cache
    if not CONFIG_FILE.exists():
        save_config(DEFAULTS)
        return dict(DEFAULTS)
    try:
        stat = CONFIG_FILE.stat()
        key = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        key = None
    if key is not None and _cache is not None and _cache[0] == key:
        return dict(_cache[1])
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {**DEFAULTS}  # mid-write or damaged: defaults this once, never cached
    merged = {**DEFAULTS, **data}
    if key is not None:
        _cache = (key, merged)
    return dict(merged)


def save_config(config: dict) -> None:
    global _cache
    CONFIG_FILE.write_text(json.dumps(config, indent=2), encoding="utf-8")
    _cache = None