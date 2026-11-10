"""
GitHub page: repos sir saves under his own names ("echo", "idk"), from the dashboard's GitHub
page or by voice - so "check the echo GitHub" reads that repo (what's in it, the newest commits,
the README) and "go to the echo GitHub" opens it in his browser. Public repos need no GitHub
account. Kept in backend/github_repos.json, which is private (never committed).
"""
import json
import re
import time
from pathlib import Path

import launch_utils as lu

PLUGIN_NAME = "github_repos"
TOGGLE_LABEL = "GitHub page (saved repos)"
CONFIG_KEY = "github_repos_enabled"
ENABLED_BY_DEFAULT = True
VRAM_COST = "no local model"

FILE = Path(__file__).resolve().parent.parent / "github_repos.json"
MAX_REPOS = 100
_GITHUB_URL = re.compile(r"^(?:https?://)?(?:www\.)?github\.com/([\w.-]+)(/[\w.-]+)?(/[^\s]*)?$", re.I)


def clean_url(url: str) -> str | None:
    """A github.com address as typed or said, made whole - or None if it isn't one."""
    m = _GITHUB_URL.match((url or "").strip().rstrip("/"))
    if not m:
        return None
    owner, repo, rest = m.group(1), (m.group(2) or "").removesuffix(".git"), m.group(3) or ""
    return f"https://github.com/{owner}{repo}{rest}"


def load() -> list[dict]:
    try:
        data = json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [e for e in data if isinstance(e, dict) and e.get("name") and e.get("url")]


def _save(entries: list[dict]) -> None:
    FILE.write_text(json.dumps(entries[-MAX_REPOS:], indent=2), encoding="utf-8")


def add(name: str, url: str) -> list[dict]:
    name, clean = " ".join((name or "").split())[:60], clean_url(url)
    if not name or not clean:
        raise ValueError("A saved repo needs a name and a github.com address.")
    entries = [e for e in load() if e["name"].lower() != name.lower()]
    entries.append({"name": name, "url": clean, "saved_at": time.time()})
    _save(entries)
    return load()


def remove(name: str) -> list[dict]:
    _save([e for e in load() if e["name"].lower() != (name or "").strip().lower()])
    return load()


_FILLER = re.compile(r"\b(the|my|a|github|git hub|repo|repository|page|one)\b")


def find(said: str) -> dict | None:
    """"the echo GitHub", "my idk repo", "echo" all find the repo saved as "echo"."""
    entries = load()
    if not entries:
        return None
    query = re.sub(r"\s+", " ", _FILLER.sub(" ", lu.norm(said))).strip() or lu.norm(said)
    ranked = lu.rank(query, entries)
    if ranked and ranked[0][0] >= lu.MATCH_THRESHOLD:
        return ranked[0][1]
    # "raydendejesus/jarvis", or just the repo's own name, works too
    for e in entries:
        tail = lu.norm(e["url"].removeprefix("https://github.com/"))
        if query and (query == tail or query == tail.split(" ")[-1]):
            return e
    return None


def mentioned_in(text: str) -> dict | None:
    """A saved repo named in a whole sentence ("check the echo github and tell me...")."""
    said = f" {lu.norm(text)} "
    for e in sorted(load(), key=lambda e: -len(e["name"])):
        if f" {lu.norm(e['name'])} " in said:
            return e
    return None


def as_prompt_block() -> str:
    entries = load()
    if not entries:
        return ""
    lines = "\n".join(f"- {e['name']}: {e['url']}" for e in entries)
    return f"Saved GitHub repos (sir's names for them):\n{lines}"


def _resolve(said: str) -> dict | None:
    entry = find(said)
    if entry:
        return entry
    url = clean_url(said)
    return {"name": url.removeprefix("https://github.com/"), "url": url} if url else None


def _not_found(said: str) -> str:
    saved = ", ".join(e["name"] for e in load()) or "none yet - add one on the dashboard's GitHub page"
    return lu.say(f"I don't have a GitHub repo called {said} saved. Saved ones: {saved}.")


async def check_github_repo(args: dict) -> str:
    said = (args.get("name") or "").strip()
    entry = _resolve(said)
    if entry is None:
        return _not_found(said)
    import tools
    return await tools.fetch_webpage(entry["url"])


async def open_github_repo(args: dict) -> str:
    said = (args.get("name") or "").strip()
    if not said:
        import turn_context
        entry = mentioned_in(turn_context.user_text.get() or "")
    else:
        entry = _resolve(said)
    if entry is None and not re.sub(r"\s+", " ", _FILLER.sub(" ", lu.norm(said))).strip():
        # just "open GitHub" / "my GitHub" - no repo named, so GitHub itself
        return await lu.open_in_browser("GitHub", "https://github.com")
    if entry is None:
        return _not_found(said)
    return await lu.open_in_browser(f"the {entry['name']} GitHub", entry["url"])


async def save_github_repo(args: dict) -> str:
    name, url = (args.get("name") or "").strip(), (args.get("url") or "").strip()
    try:
        add(name, url)
    except ValueError as exc:
        return lu.say(str(exc))
    return lu.say(f"Saved {clean_url(url)} as the {name} GitHub.")


async def list_github_repos(args: dict) -> str:
    entries = load()
    if not entries:
        return lu.say("No GitHub repos are saved yet. Add one on the dashboard's GitHub page, or tell me a name and its address.")
    return lu.say("Saved GitHub repos: " + "; ".join(f"{e['name']}, {e['url'].removeprefix('https://')}" for e in entries) + ".")


async def forget_github_repo(args: dict) -> str:
    entry = find(args.get("name") or "")
    if entry is None:
        return lu.say(f"There's no saved GitHub repo called {args.get('name')}.")
    remove(entry["name"])
    return lu.say(f"Removed the {entry['name']} GitHub.")


_NAME = {"type": "string", "description": "The repo's saved name (like 'echo'), or a github.com address"}
SCHEMAS = [
    {"type": "function", "function": {
        "name": "check_github_repo",
        "description": ("Reads one of sir's GitHub repos - what's in it, its newest commits and its README - to answer "
                        "questions about it ('check the echo GitHub', 'what's new on my idk repo')."),
        "parameters": {"type": "object", "properties": {"name": _NAME}, "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "open_github_repo",
        "description": ("Opens one of sir's GitHub repos in his browser so he can see it ('go to the echo GitHub') - "
                        "or GitHub itself when he names no repo ('open GitHub')."),
        "parameters": {"type": "object", "properties": {"name": _NAME}, "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "save_github_repo",
        "description": "Saves a GitHub repo under a name sir chooses, for checking or opening it by that name later.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string"}, "url": {"type": "string", "description": "The github.com address exactly as sir gave it"}},
            "required": ["name", "url"]}}},
    {"type": "function", "function": {
        "name": "list_github_repos",
        "description": "Lists the GitHub repos sir has saved.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "forget_github_repo",
        "description": "Removes a saved GitHub repo.",
        "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}},
]

DISPATCH = {
    "check_github_repo": check_github_repo,
    "open_github_repo": open_github_repo,
    "save_github_repo": save_github_repo,
    "list_github_repos": list_github_repos,
    "forget_github_repo": forget_github_repo,
}
