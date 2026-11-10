"""
Calendar: sir's own events - a name, the day, a start (and end) time, where, and what it's
about - kept on this PC in backend/calendar.json (private, never committed). Added and changed
from the dashboard's Calendar page or by voice ("put the dentist on Friday at 3 at Main Street
Dental"), and Echo can say what's coming up. An event with a reminder is announced out loud that
many minutes before it starts (and phoned through if Sir Page is on).

Days and times are worked out here, from the words, not by the brain: "tomorrow", "next
Friday", "November 14th", "11/14", "the 20th", "3pm", "half past 2", "noon". The brain doesn't
know today's date by itself, and a small model's date arithmetic can't be trusted.
"""
import json
import re
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

import launch_utils as lu

PLUGIN_NAME = "calendar_events"
TOGGLE_LABEL = "Calendar"
CONFIG_KEY = "calendar_enabled"
ENABLED_BY_DEFAULT = True
VRAM_COST = "no local model"
SIR_CALLS = True

FILE = Path(__file__).resolve().parent.parent / "calendar.json"
MAX_EVENTS = 2000
DEFAULT_REMIND_MINUTES = 30
FIELDS = ("title", "date", "time", "end_time", "location", "details", "remind_minutes")

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
_NUMBER_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
                 "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}
_MONTH_RE = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"


# --- words -> a day and a time -------------------------------------------------------------

def _num(word: str) -> int | None:
    return int(word) if word.isdigit() else _NUMBER_WORDS.get(word)


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def parse_day(said: str, today: date | None = None) -> date | None:
    """The day in what was said, or None if it names none."""
    today = today or date.today()
    s = " " + re.sub(r"[,]", " ", (said or "").lower()) + " "
    s = re.sub(r"\s+", " ", s)
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        return _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    if re.search(r"\bday after tomorrow\b", s):
        return today + timedelta(days=2)
    if re.search(r"\b(tomorrow|tmrw|tmr)\b", s):
        return today + timedelta(days=1)
    if re.search(r"\b(today|tonight|this (morning|afternoon|evening))\b", s):
        return today
    m = re.search(r"\bin (\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten) (day|week|month)s?\b", s)
    if m and _num(m.group(1)) is not None:
        n, unit = _num(m.group(1)), m.group(2)
        if unit == "day":
            return today + timedelta(days=n)
        if unit == "week":
            return today + timedelta(weeks=n)
        month = today.month - 1 + n
        y, mo = today.year + month // 12, month % 12 + 1
        return _safe_date(y, mo, min(today.day, 28)) if not _safe_date(y, mo, today.day) else _safe_date(y, mo, today.day)
    m = re.search(rf"\b{_MONTH_RE} (\d{{1,2}})(?:st|nd|rd|th)?\b(?: (\d{{4}}))?", s) or None
    day_first = None
    if not m:
        day_first = re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)? (?:of )?{_MONTH_RE}(?: (\d{{4}}))?", s)
    if m or day_first:
        if m:
            mon, d, y = m.group(1)[:3], int(m.group(2)), m.group(3)
        else:
            d, mon, y = int(day_first.group(1)), day_first.group(2)[:3], day_first.group(3)
        found = _safe_date(int(y) if y else today.year, _MONTHS[mon], d)
        if found and not y and found < today:
            found = _safe_date(today.year + 1, _MONTHS[mon], d)
        return found
    m = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", s)
    if m:
        y = int(m.group(3)) if m.group(3) else today.year
        y = y + 2000 if y < 100 else y
        found = _safe_date(y, int(m.group(1)), int(m.group(2)))
        if found and not m.group(3) and found < today:
            found = _safe_date(y + 1, int(m.group(1)), int(m.group(2)))
        return found
    m = re.search(r"\b(this |next |on |coming )?(mon|tues|tue|wednes|wed|thurs|thu|fri|satur|sat|sun)(day)?\b", s)
    if m:
        stem = m.group(2)[:3]
        wd = next(i for i, name in enumerate(_WEEKDAYS) if name.startswith(stem))
        ahead = (wd - today.weekday()) % 7
        if m.group(1) and m.group(1).strip() == "next" and ahead == 0:
            ahead = 7
        return today + timedelta(days=ahead)
    m = re.search(r"\bthe (\d{1,2})(?:st|nd|rd|th)\b", s)
    if m:
        d = int(m.group(1))
        found = _safe_date(today.year, today.month, d)
        if found is None or found < today:
            nxt = today.replace(day=1) + timedelta(days=32)
            found = _safe_date(nxt.year, nxt.month, d)
        return found
    return None


def parse_time(said: str, prefer_morning: bool = False) -> str | None:
    """'3pm', '3:30 pm', '15:00', 'half past 2', 'noon' -> 'HH:MM' (24-hour), or None. A bare
    "at 3" is the afternoon - unless prefer_morning (an alarm: "6:30" means the morning)."""
    s = " " + (said or "").lower().replace(".", "") + " "
    if re.search(r"\bnoon|midday\b", s):
        return "12:00"
    if re.search(r"\bmidnight\b", s):
        return "00:00"
    evening = bool(re.search(r"\b(tonight|evening|night|afternoon)\b", s))
    morning = bool(re.search(r"\bmorning\b", s))
    m = re.search(r"\bhalf past (\d{1,2}|\w+)\b", s) or re.search(r"\bquarter (past|to) (\d{1,2}|\w+)\b", s)
    hour = minute = None
    ampm = None
    if m and m.re.pattern.startswith(r"\bhalf"):
        hour, minute = _num(m.group(1)), 30
    elif m:
        hour = _num(m.group(2))
        minute = 15 if m.group(1) == "past" else 45
        if hour is not None and m.group(1) == "to":
            hour -= 1
    if hour is None:
        m = re.search(r"(?<![\d/-])(\d{1,2})(?::(\d{2}))?\s*(am|pm|a m|p m)\b", s) \
            or re.search(r"(?<![\d/-])(\d{1,2}):(\d{2})(?![\d/])()", s) \
            or re.search(r"\bat (\d{1,2})()()\b(?![:/\d])", s)
        if not m:
            return None
        hour, minute = int(m.group(1)), int(m.group(2) or 0)
        ampm = (m.group(3) or "").replace(" ", "") or None
    if hour is None or not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    after = re.search(rf"\b{hour}(:\d\d)?\s*(am|pm|a m|p m)\b", s)
    ampm = ampm or (after.group(2).replace(" ", "") if after else None)
    if ampm == "pm" and hour < 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    elif ampm is None and hour <= 12:
        if evening and hour < 12:
            hour += 12
        elif not morning and not prefer_morning and 1 <= hour <= 7:
            hour += 12  # "at 3" means the afternoon
    return f"{hour:02d}:{minute:02d}"


# --- the stored events ------------------------------------------------------------------------

def load() -> list[dict]:
    try:
        data = json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    events = [e for e in data if isinstance(e, dict) and e.get("id") and e.get("title") and e.get("date")]
    return sorted(events, key=lambda e: (e["date"], e.get("time") or "99:99"))


def _save(events: list[dict]) -> None:
    FILE.write_text(json.dumps(events[-MAX_EVENTS:], indent=2), encoding="utf-8")


def _clean(fields: dict) -> dict:
    """Validates an event's fields (from the dashboard or a tool) - raises ValueError."""
    out = {}
    if "title" in fields:
        out["title"] = " ".join(str(fields["title"] or "").split())[:120]
        if not out["title"]:
            raise ValueError("An event needs a name.")
    if "date" in fields:
        d = str(fields["date"] or "").strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) or not _safe_date(*map(int, d.split("-"))):
            raise ValueError("The date should look like 2026-11-14.")
        out["date"] = d
    for key in ("time", "end_time"):
        if key in fields:
            t = str(fields[key] or "").strip()
            if t and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", t):
                raise ValueError("Times should look like 15:30.")
            out[key] = t
    for key, limit in (("location", 200), ("details", 2000)):
        if key in fields:
            out[key] = str(fields[key] or "").strip()[:limit]
    if "remind_minutes" in fields:
        r = fields["remind_minutes"]
        out["remind_minutes"] = None if r in (None, "", False) else max(0, min(int(r), 7 * 24 * 60))
    return out


def add(fields: dict) -> dict:
    event = {"title": "", "date": "", "time": "", "end_time": "", "location": "", "details": "",
             "remind_minutes": None, **_clean(fields)}
    if not event["title"] or not event["date"]:
        raise ValueError("An event needs a name and a date.")
    event.update(id=uuid.uuid4().hex[:10], created_at=time.time(), reminded=False)
    _save(load() + [event])
    return event


def update(event_id: str, fields: dict) -> dict:
    changes = _clean({k: v for k, v in fields.items() if k in FIELDS})
    events = load()
    for e in events:
        if e["id"] == event_id:
            e.update(changes)
            if {"date", "time", "remind_minutes"} & changes.keys():
                e["reminded"] = False
            _save(events)
            return e
    raise KeyError(event_id)


def remove(event_id: str) -> bool:
    events = load()
    kept = [e for e in events if e["id"] != event_id]
    _save(kept)
    return len(kept) != len(events)


def between(start: date, end: date) -> list[dict]:
    a, b = start.isoformat(), end.isoformat()
    return [e for e in load() if a <= e["date"] <= b]


# --- saying it -------------------------------------------------------------------------------

def spoken_day(d: date, today: date | None = None) -> str:
    today = today or date.today()
    if d == today:
        return "today"
    if d == today + timedelta(days=1):
        return "tomorrow"
    label = f"{_WEEKDAYS[d.weekday()].title()}, {d:%B} {d.day}"
    return label + (f", {d.year}" if d.year != today.year else "")


def spoken_time(t: str) -> str:
    if not t:
        return ""
    h, m = map(int, t.split(":"))
    suffix = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return f"{h12}:{m:02d} {suffix}" if m else f"{h12} {suffix}"


def describe(e: dict, today: date | None = None, with_day: bool = True) -> str:
    d = date.fromisoformat(e["date"])
    parts = [e["title"]]
    if with_day:
        parts.append(spoken_day(d, today))
    if e.get("time"):
        parts.append("at " + spoken_time(e["time"]) + (f" until {spoken_time(e['end_time'])}" if e.get("end_time") else ""))
    if e.get("location"):
        parts.append("at " + e["location"])
    text = " ".join(parts)
    if e.get("details"):
        text += f" ({e['details']})"
    return text


def now_line() -> str:
    """What day and time it is - the brain has no clock of its own."""
    now = datetime.now()
    return f"Right now it is {_WEEKDAYS[now.weekday()].title()}, {now:%B} {now.day}, {now.year}, {spoken_time(f'{now:%H:%M}')}."


def as_prompt_block() -> str:
    today = date.today()
    soon = between(today, today + timedelta(days=7))
    if not soon:
        return "Sir's calendar: nothing in the next 7 days."
    lines = "\n".join(f"- {describe(e, today)}" for e in soon[:12])
    return f"Sir's calendar, the next 7 days:\n{lines}"


# --- reminders ----------------------------------------------------------------------------------

def due_reminders(now: datetime | None = None) -> list[dict]:
    """Events whose reminder time has come (and that haven't started more than 10 minutes ago).
    Each is marked reminded, so it's announced once."""
    now = now or datetime.now()
    events, due = load(), []
    for e in events:
        if e.get("reminded") or e.get("remind_minutes") is None:
            continue
        start = datetime.fromisoformat(f"{e['date']}T{e.get('time') or '09:00'}")
        if start - timedelta(minutes=e["remind_minutes"]) <= now <= start + timedelta(minutes=10):
            e["reminded"] = True
            due.append(e)
        elif now > start + timedelta(minutes=10):
            e["reminded"] = True  # missed while Echo was off - too late to be useful
    if any(e.get("reminded") for e in events):
        _save(events)
    return due


def reminder_text(e: dict, now: datetime | None = None) -> str:
    now = now or datetime.now()
    start = datetime.fromisoformat(f"{e['date']}T{e.get('time') or '09:00'}")
    minutes = round((start - now).total_seconds() / 60)
    when = "now" if minutes <= 1 else (f"in {minutes} minutes" if minutes < 90 else f"at {spoken_time(e.get('time') or '09:00')}")
    where = f", at {e['location']}" if e.get("location") else ""
    return f"Sir, a reminder: {e['title']} {when}{where}."


# --- tools ---------------------------------------------------------------------------------------

def _user_words() -> str:
    try:
        import turn_context
        return turn_context.user_text.get() or ""
    except (ImportError, LookupError):
        return ""


def _day_from(args: dict) -> date | None:
    """The day from the tool's own date field, else from what sir actually said. A date the brain
    wrote that's already past (an old year from its training) is checked against sir's words."""
    today = date.today()
    found = parse_day(f"{args.get('date') or ''} {args.get('time') or ''}", today)
    if found is None or found < today:
        from_words = parse_day(_user_words(), today)
        if from_words is not None:
            return from_words
    return found


def _find(said: str, events: list[dict]) -> list[dict]:
    said = (said or "").strip()
    if not said:
        return []
    exact = [e for e in events if e["title"].lower() == said.lower()]
    if exact:
        return exact
    for e in events:
        e["name"] = e["title"]
    ranked = lu.rank(said, events)
    best = [e for s, e in ranked if s >= lu.MATCH_THRESHOLD and s >= ranked[0][0] - 0.05]
    for e in events:
        e.pop("name", None)
    return best


def _which(args: dict) -> list[dict]:
    """The event sir means: by the name the brain passed, else one whose name sir said."""
    events = load()
    found = _find(args.get("which") or "", events)
    if not found:
        said = f" {lu.norm(_user_words())} "
        found = [e for e in events if lu.norm(e["title"]) and f" {lu.norm(e['title'])} " in said]
    return _upcoming_first(found)


def _upcoming_first(events: list[dict]) -> list[dict]:
    today = date.today().isoformat()
    return [e for e in events if e["date"] >= today] + [e for e in events if e["date"] < today][::-1]


async def calendar_add_event(args: dict) -> str:
    title = (args.get("title") or "").strip()
    if not title:
        return lu.say("What should I call the event, sir?")
    day = _day_from(args)
    if day is None:
        return lu.say(f"What day is {title}, sir?")
    if day < date.today():
        return lu.say(f"{spoken_day(day)} has already passed, sir - which day did you mean for {title}?")
    words = f"{args.get('time') or ''}"
    start = parse_time(words) or (parse_time(_user_words()) if not args.get("time") and re.search(r"\d|noon|midnight|half past|quarter", _user_words(), re.I) else None)
    end = parse_time(args.get("end_time") or "")
    remind = args.get("remind_minutes")
    try:
        event = add({"title": title, "date": day.isoformat(), "time": start or "", "end_time": end or "",
                     "location": args.get("location") or "", "details": args.get("details") or "",
                     "remind_minutes": remind if remind not in (None, "") else (DEFAULT_REMIND_MINUTES if start else None)})
    except ValueError as exc:
        return lu.say(str(exc))
    note = f" I'll remind you {event['remind_minutes']} minutes before." if event.get("remind_minutes") else ""
    return lu.say(f"Added to your calendar: {describe(event)}.{note}")


def _range_for(when: str) -> tuple[date, date, str]:
    today = date.today()
    w = (when or "").lower()
    if re.search(r"\bnext week\b", w):
        start = today + timedelta(days=7 - today.weekday())
        return start, start + timedelta(days=6), "next week"
    if re.search(r"\b(this week|week)\b", w):
        return today, today + timedelta(days=6 - today.weekday()), "this week"
    if re.search(r"\b(this month|month)\b", w):
        nxt = today.replace(day=1) + timedelta(days=32)
        return today, nxt.replace(day=1) - timedelta(days=1), "this month"
    if re.search(r"\b(all|everything|anything coming|upcoming|coming up|soon)\b", w) or not w.strip():
        return today, today + timedelta(days=30), "the next 30 days"
    day = parse_day(w, today)
    if day:
        return day, day, spoken_day(day)
    return today, today + timedelta(days=30), "the next 30 days"


async def calendar_events(args: dict) -> str:
    start, end, label = _range_for(args.get("when") or "")
    found = between(start, end)
    if not found:
        return lu.say(f"Nothing on your calendar {label if label in ('today', 'tomorrow') else 'for ' + label}, sir.")
    single = start == end
    items = [describe(e, with_day=not single) for e in found[:15]]
    more = f" And {len(found) - 15} more." if len(found) > 15 else ""
    head = f"{label[0].upper()}{label[1:]}" if single else f"For {label}"
    return lu.say(f"{head}: " + "; ".join(items) + "." + more)


async def calendar_change_event(args: dict) -> str:
    matches = _which(args)
    if not matches:
        return lu.say(f"I couldn't find {args.get('which') or 'that'} on your calendar, sir.")
    event = matches[0]  # the soonest; it's named in the reply, so sir can correct it
    changes: dict = {}
    if args.get("title") and lu.norm(args["title"]) != lu.norm(event["title"]):
        changes["title"] = args["title"]
    if args.get("date") and parse_day(args["date"]) is None and parse_time(args["date"]):
        args = {**args, "time": args.get("time") or args["date"], "date": ""}  # "4pm" given as the day
    if args.get("date"):
        day = parse_day(args["date"]) or parse_day(_user_words())
        if day is None:
            return lu.say(f"Which day should {event['title']} move to, sir?")
        changes["date"] = day.isoformat()
    if not args.get("date") and not args.get("time") and not changes:
        # "move the dentist to 4pm" with nothing filled in - the words say it
        words = _user_words()
        if parse_day(words):
            changes["date"] = parse_day(words).isoformat()
        if re.search(r"\d|noon|midnight|half past|quarter", words, re.I) and parse_time(words):
            changes["time"] = parse_time(words)
    if args.get("time"):
        t = parse_time(args["time"])
        if t is None:
            return lu.say(f"What time should {event['title']} be, sir?")
        changes["time"] = t
    if args.get("end_time"):
        changes["end_time"] = parse_time(args["end_time"]) or ""
    for key in ("location", "details", "remind_minutes"):
        if args.get(key) not in (None, ""):
            changes[key] = args[key]
    if not changes:
        return lu.say(f"What should I change about {event['title']}, sir?")
    try:
        event = update(event["id"], changes)
    except (ValueError, KeyError) as exc:
        return lu.say(str(exc))
    return lu.say(f"Changed. It's now {describe(event)}.")


async def calendar_remove_event(args: dict) -> str:
    matches = _which(args)
    if not matches:
        return lu.say(f"I couldn't find {args.get('which') or 'that'} on your calendar, sir.")
    event = matches[0]
    remove(event["id"])
    return lu.say(f"Removed {describe(event)} from your calendar.")


_WHEN_DAY = "The day exactly as sir said it ('tomorrow', 'Friday', 'November 14th', '11/14')"
SCHEMAS = [
    {"type": "function", "function": {
        "name": "calendar_add_event",
        "description": ("Adds an event to sir's own calendar on this PC. Use the words sir said for the day and time - "
                        "they're worked out exactly here. Only fill location, details and end time if sir said them."),
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string", "description": "What the event is called, e.g. 'Dentist'"},
            "date": {"type": "string", "description": _WHEN_DAY},
            "time": {"type": "string", "description": "Start time as sir said it ('3pm', 'half past 2'), if he did"},
            "end_time": {"type": "string", "description": "End time, only if sir said one"},
            "location": {"type": "string", "description": "Where, only if sir said"},
            "details": {"type": "string", "description": "What it's about, only if sir said"},
            "remind_minutes": {"type": "integer", "description": "Minutes before to remind him, only if he asked for a specific reminder"},
        }, "required": ["title", "date"]}}},
    {"type": "function", "function": {
        "name": "calendar_events",
        "description": "What's on sir's own calendar for a day or a stretch of time ('today', 'Friday', 'this week', 'next week', 'coming up').",
        "parameters": {"type": "object", "properties": {
            "when": {"type": "string", "description": "The day or range as sir said it; empty for what's coming up"}}}}},
    {"type": "function", "function": {
        "name": "calendar_change_event",
        "description": "Changes an event on sir's calendar - its day, time, place, name, notes or reminder. Only pass what changes.",
        "parameters": {"type": "object", "properties": {
            "which": {"type": "string", "description": "The event's name as sir said it"},
            "title": {"type": "string"}, "date": {"type": "string", "description": _WHEN_DAY},
            "time": {"type": "string"}, "end_time": {"type": "string"}, "location": {"type": "string"},
            "details": {"type": "string"}, "remind_minutes": {"type": "integer"},
        }, "required": ["which"]}}},
    {"type": "function", "function": {
        "name": "calendar_remove_event",
        "description": "Removes an event from sir's calendar.",
        "parameters": {"type": "object", "properties": {
            "which": {"type": "string", "description": "The event's name as sir said it"}}, "required": ["which"]}}},
]

DISPATCH = {
    "calendar_add_event": calendar_add_event,
    "calendar_events": calendar_events,
    "calendar_change_event": calendar_change_event,
    "calendar_remove_event": calendar_remove_event,
}
