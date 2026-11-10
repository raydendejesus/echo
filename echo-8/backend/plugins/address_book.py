"""
Address book: places sir names himself - "home", "gym", "favorite restaurant" - so he can
ask for directions or an address by that name. Kept in backend/address_book.json, which is
private (never committed). Directions open in Google Maps in the default browser; no account
or key needed.
"""
import json
import os
import time
from pathlib import Path
from urllib.parse import quote_plus

import launch_utils as lu

PLUGIN_NAME = "address_book"
TOGGLE_LABEL = "Address Book (named places, directions)"
CONFIG_KEY = "address_book_enabled"
ENABLED_BY_DEFAULT = True
VRAM_COST = "no local model"
SIR_CALLS = True

FILE = Path(__file__).resolve().parent.parent / "address_book.json"


def load() -> list[dict]:
    try:
        data = json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [e for e in data if isinstance(e, dict) and e.get("name") and e.get("address")]


def _save(entries: list[dict]) -> None:
    FILE.write_text(json.dumps(entries, indent=2), encoding="utf-8")


def add(name: str, address: str) -> list[dict]:
    name, address = name.strip(), " ".join(address.split())
    entries = [e for e in load() if e["name"].lower() != name.lower()]
    entries.append({"name": name, "address": address, "saved_at": time.time()})
    _save(entries)
    return entries


def remove(name: str) -> list[dict]:
    entries = [e for e in load() if e["name"].lower() != name.strip().lower()]
    _save(entries)
    return entries


def find(name: str) -> dict | None:
    """'the gym', 'my gym', 'gym' all find "gym"."""
    query = lu.norm(name)
    for prefix in ("the ", "my "):
        if query.startswith(prefix):
            query = query[len(prefix):]
    ranked = lu.rank(query, load())
    return ranked[0][1] if ranked and ranked[0][0] >= lu.MATCH_THRESHOLD else None


def as_prompt_block() -> str:
    entries = load()
    if not entries:
        return ""
    lines = "\n".join(f"- {e['name']}: {e['address']}" for e in entries)
    return f"Address book (places sir has named):\n{lines}"


def _resolve(place: str) -> tuple[str, str]:
    """(what to say, what to give Maps) - a saved name if it matches, else the place as said."""
    entry = find(place)
    return (entry["name"], entry["address"]) if entry else (place, place)


async def save_address(args: dict) -> str:
    name, address = (args.get("name") or "").strip(), (args.get("address") or "").strip()
    if not name or not address:
        return lu.say("I need both a name for the place and its address.")
    add(name, address)
    return lu.say(f"Saved {name} as {address}.")


async def get_address(args: dict) -> str:
    entry = find(args.get("name") or "")
    if entry is None:
        saved = ", ".join(e["name"] for e in load()) or "nothing yet"
        return lu.say(f"I don't have a place called {args.get('name')} saved. Saved places: {saved}.")
    return lu.say(f"{entry['name']} is at {entry['address']}.")


async def list_addresses(args: dict) -> str:
    entries = load()
    if not entries:
        return lu.say("Your address book is empty. Tell me a name and an address to save one.")
    return lu.say("Saved places: " + "; ".join(f"{e['name']}, {e['address']}" for e in entries) + ".")


async def forget_address(args: dict) -> str:
    entry = find(args.get("name") or "")
    if entry is None:
        return lu.say(f"There's no saved place called {args.get('name')}.")
    remove(entry["name"])
    return lu.say(f"Removed {entry['name']} from your address book.")


async def directions(args: dict) -> str:
    to_label, to_addr = _resolve(args.get("to") or "")
    if not to_addr:
        return lu.say("Where to?")
    url = f"https://www.google.com/maps/dir/?api=1&destination={quote_plus(to_addr)}"
    from_place = (args.get("from") or "").strip()
    from_label = ""
    if from_place:
        from_label, from_addr = _resolve(from_place)
        url += f"&origin={quote_plus(from_addr)}"
    mode = (args.get("mode") or "").lower()
    if mode in ("driving", "walking", "bicycling", "transit"):
        url += f"&travelmode={mode}"
    blocked = lu.launch_blocked_reason(url)
    if blocked:
        return lu.say(f"Directions: {blocked}.")
    os.startfile(url)
    lu.note_launch(url)
    return lu.say(f"Directions to {to_label}{f' from {from_label}' if from_label else ''} are up in Google Maps.")


SCHEMAS = [
    {"type": "function", "function": {
        "name": "save_address",
        "description": "Saves a place in sir's address book under a name he chooses (home, gym, favorite restaurant...).",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string"}, "address": {"type": "string", "description": "The address exactly as sir gave it."}},
            "required": ["name", "address"]}}},
    {"type": "function", "function": {
        "name": "get_address",
        "description": "Looks up a saved place's address by its name (\"what's the gym's address\").",
        "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "list_addresses",
        "description": "Lists every place in sir's address book.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "forget_address",
        "description": "Removes a place from the address book.",
        "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "directions",
        "description": ("Opens Google Maps directions on the PC. 'to' and 'from' can be saved place names (gym, home) "
                        "or any address/place. Leave 'from' empty to start from where he is."),
        "parameters": {"type": "object", "properties": {
            "to": {"type": "string"}, "from": {"type": "string"},
            "mode": {"type": "string", "enum": ["driving", "walking", "bicycling", "transit"]}},
            "required": ["to"]}}},
]

DISPATCH = {
    "save_address": save_address,
    "get_address": get_address,
    "list_addresses": list_addresses,
    "forget_address": forget_address,
    "directions": directions,
}
