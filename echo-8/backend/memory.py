import json
from datetime import datetime, timezone
from pathlib import Path

MEMORY_DIR = Path(__file__).resolve().parent / "memory"
MEMORY_FILE = MEMORY_DIR / "facts.json"
MAX_FACTS = 300


def _ensure_file() -> None:
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    if not MEMORY_FILE.exists():
        MEMORY_FILE.write_text("[]", encoding="utf-8")


def load_facts() -> list[dict]:
    _ensure_file()
    try:
        return json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _when(stamp: str) -> str:
    """The day a fact was saved, in sir's own time - so Echo can say "you told me in October",
    never "you just said"."""
    try:
        saved = datetime.fromisoformat(stamp).astimezone()
    except (TypeError, ValueError):
        return ""
    now = datetime.now().astimezone()
    days = (now.date() - saved.date()).days
    if days == 0:
        return "today"
    if days == 1:
        return "yesterday"
    return f"{saved:%b} {saved.day}" + (f", {saved.year}" if saved.year != now.year else "")


def facts_as_prompt_block() -> str:
    facts = load_facts()
    if not facts:
        return ""
    lines = "\n".join(f"- {f['text']}" + (f" (saved {_when(f.get('timestamp', ''))})" if _when(f.get("timestamp", "")) else "")
                      for f in facts)
    return f"Known facts about sir, remembered from previous conversations (with the day each was saved):\n{lines}"


def add_fact(text: str) -> str:
    facts = load_facts()
    facts.append({"text": text, "timestamp": datetime.now(timezone.utc).isoformat()})
    facts = facts[-MAX_FACTS:]
    _ensure_file()
    MEMORY_FILE.write_text(json.dumps(facts, indent=2), encoding="utf-8")
    return f"Remembered: {text}"