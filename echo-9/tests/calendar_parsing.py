"""
The calendar works out days and times from sir's own words (backend/plugins/calendar_events.py) -
checked here against a fixed "today", Tuesday, November 10, 2026. Also the event file round trip
and reminders coming due, in a temporary file.

Run:  venv\\Scripts\\python.exe tests\\calendar_parsing.py
"""
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from plugins import calendar_events as cal  # noqa: E402

TODAY = date(2026, 11, 10)  # a Tuesday
results = []


def check(name, got, want):
    results.append(got == want)
    print(f"{'PASS' if got == want else 'FAIL'}  {name}" + ("" if got == want else f"  (got {got}, wanted {want})"))


DAYS = {
    "today": date(2026, 11, 10),
    "tonight": date(2026, 11, 10),
    "tomorrow": date(2026, 11, 11),
    "the day after tomorrow": date(2026, 11, 12),
    "friday": date(2026, 11, 13),
    "on Friday at 3": date(2026, 11, 13),
    "this friday": date(2026, 11, 13),
    "tuesday": date(2026, 11, 10),
    "next tuesday": date(2026, 11, 17),
    "monday": date(2026, 11, 16),
    "november 14th": date(2026, 11, 14),
    "Nov 14": date(2026, 11, 14),
    "the 14th of november": date(2026, 11, 14),
    "march 3": date(2027, 3, 3),          # already past this year -> next year
    "december 25, 2026": date(2026, 12, 25),
    "11/20": date(2026, 11, 20),
    "1/5": date(2027, 1, 5),
    "2026-12-01": date(2026, 12, 1),
    "in 3 days": date(2026, 11, 13),
    "in two weeks": date(2026, 11, 24),
    "the 20th": date(2026, 11, 20),
    "the 2nd": date(2026, 12, 2),
    "sometime": None,
}
for said, want in DAYS.items():
    check(f"day: {said!r}", cal.parse_day(said, TODAY), want)

TIMES = {
    "3pm": "15:00", "3 pm": "15:00", "3:30 p.m.": "15:30", "15:00": "15:00", "at 3": "15:00",
    "at 9": "09:00", "9 in the morning": None, "at 9 in the morning": "09:00", "10am": "10:00",
    "12pm": "12:00", "12am": "00:00", "noon": "12:00", "midnight": "00:00", "half past 2": "14:30",
    "quarter to 5": "16:45", "tonight at 8": "20:00", "": None, "friday": None,
}
for said, want in TIMES.items():
    check(f"time: {said!r}", cal.parse_time(said), want)

check("spoken time 15:00", cal.spoken_time("15:00"), "3 PM")
check("spoken time 09:30", cal.spoken_time("09:30"), "9:30 AM")
check("spoken day: tomorrow", cal.spoken_day(date(2026, 11, 11), TODAY), "tomorrow")
check("spoken day: Friday", cal.spoken_day(date(2026, 11, 13), TODAY), "Friday, November 13")

# the file, in a temporary place
cal.FILE = Path(tempfile.mkdtemp(prefix="echo_cal_")) / "calendar.json"
e = cal.add({"title": "Dentist", "date": "2026-11-13", "time": "15:00", "location": "Main Street Dental",
             "details": "cleaning", "remind_minutes": 30})
check("added and loaded back", [x["title"] for x in cal.load()], ["Dentist"])
check("described", cal.describe(e, TODAY), "Dentist Friday, November 13 at 3 PM at Main Street Dental (cleaning)")
try:
    cal.add({"title": "", "date": "2026-11-13"})
    check("an event without a name is refused", False, True)
except ValueError:
    check("an event without a name is refused", True, True)
try:
    cal.add({"title": "x", "date": "2026-13-40"})
    check("a date that doesn't exist is refused", False, True)
except ValueError:
    check("a date that doesn't exist is refused", True, True)
cal.update(e["id"], {"time": "16:00"})
check("changed time", cal.load()[0]["time"], "16:00")
check("not due an hour early", cal.due_reminders(datetime(2026, 11, 13, 15, 0)), [])
due = cal.due_reminders(datetime(2026, 11, 13, 15, 31))
check("due 29 minutes before", [x["title"] for x in due], ["Dentist"])
check("reminder text", cal.reminder_text(due[0], datetime(2026, 11, 13, 15, 31)),
      "Sir, a reminder: Dentist in 29 minutes, at Main Street Dental.")
check("said only once", cal.due_reminders(datetime(2026, 11, 13, 15, 40)), [])
cal.update(e["id"], {"time": "17:00"})
check("moving it re-arms the reminder", [x["title"] for x in cal.due_reminders(datetime(2026, 11, 13, 16, 45))], ["Dentist"])
cal.remove(e["id"])
check("removed", cal.load(), [])

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
