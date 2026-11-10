"""
What Echo is allowed to do on its own, and for how long.

Sir's rule: Echo may always browse the web, think and keep notes ("talk to himself"),
and work on a project sir approved - but only while that approval is less than a day
old. Anything else it wants to do on its own (write or run code, make an app, start a
new project) needs sir's OK first. When sir asks for something directly, that request
IS the approval, for that project, for 24 hours. After that it expires and Echo has to
ask again.

Approving at the PC: the dashboard's Approve button, or saying so. Approving by phone
needs sir's passcode in the same sentence, like shutting the PC down does.

Kept in backend/approvals.json (private).
"""
import json
import time
import uuid
from pathlib import Path

FILE = Path(__file__).resolve().parent / "approvals.json"
APPROVAL_SECONDS = 24 * 60 * 60
MAX_KEPT = 200


def _load() -> list[dict]:
    try:
        data = json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [r for r in data if isinstance(r, dict) and r.get("id")]


def _save(records: list[dict]) -> None:
    FILE.write_text(json.dumps(records[-MAX_KEPT:], indent=2), encoding="utf-8")


def _expire(records: list[dict]) -> list[dict]:
    now = time.time()
    for r in records:
        if r["status"] == "approved" and now - r.get("approved_at", 0) > APPROVAL_SECONDS:
            r["status"] = "expired"
    return records


def all_records() -> list[dict]:
    records = _expire(_load())
    _save(records)
    return records


def pending() -> list[dict]:
    return [r for r in all_records() if r["status"] == "pending"]


def request(what: str, detail: str = "", project: str | None = None) -> dict:
    """Asks sir for permission. The same pending question isn't asked twice."""
    records = all_records()
    for r in records:
        if r["status"] == "pending" and r["what"] == what and r.get("project") == project:
            return r
    record = {"id": uuid.uuid4().hex[:8], "what": what, "detail": detail, "project": project,
              "status": "pending", "asked_at": time.time()}
    records.append(record)
    _save(records)
    return record


def grant(what: str, project: str | None, via: str) -> dict:
    """Sir asked for this himself - that request is the approval."""
    records = all_records()
    record = {"id": uuid.uuid4().hex[:8], "what": what, "detail": "", "project": project, "status": "approved",
              "asked_at": time.time(), "approved_at": time.time(), "via": via}
    records.append(record)
    _save(records)
    return record


def decide(record_id: str, approve: bool, via: str) -> dict | None:
    records = all_records()
    for r in records:
        if r["id"] == record_id and r["status"] == "pending":
            r["status"] = "approved" if approve else "denied"
            r["decided_at"] = time.time()
            if approve:
                r["approved_at"] = time.time()
            r["via"] = via
            _save(records)
            return r
    return None


def find_pending(words: str) -> dict | None:
    """The pending request sir means: by id, by words in it, or the newest one."""
    waiting = pending()
    if not waiting:
        return None
    words = (words or "").strip().lower()
    for r in waiting:
        if words and (words == r["id"] or words in r["what"].lower() or words in (r.get("project") or "").lower()):
            return r
    return waiting[-1]


def approved(project: str) -> dict | None:
    """The live (under a day old) approval for this project, if there is one."""
    live = [r for r in all_records() if r["status"] == "approved" and r.get("project") == project]
    return max(live, key=lambda r: r["approved_at"]) if live else None


def time_left(record: dict) -> float:
    return max(0.0, APPROVAL_SECONDS - (time.time() - record.get("approved_at", 0)))
