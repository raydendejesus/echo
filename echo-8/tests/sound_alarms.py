"""
White noise, chimes and alarms (backend/plugins/speaker_sounds.py) - checked without playing
anything: the sounds are made and measured, and alarms come due in a temporary file.

Run:  venv\\Scripts\\python.exe tests\\sound_alarms.py
"""
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from plugins import calendar_events  # noqa: E402
from plugins import speaker_sounds as s  # noqa: E402

results = []


def check(name, got, want):
    results.append(got == want)
    print(f"{'PASS' if got == want else 'FAIL'}  {name}" + ("" if got == want else f"  (got {got}, wanted {want})"))


def low_share(arr):
    """How much of the sound's energy is below 500 Hz."""
    spec = np.abs(np.fft.rfft(arr[:, 0].astype(float))) ** 2
    f = np.fft.rfftfreq(len(arr), 1 / 48000)
    return spec[f < 500].sum() / spec.sum()


white, pink, brown = (s.noise(k, seconds=4) for k in ("white", "pink", "brown"))
check("noise is stereo 16-bit", (white.shape[1], white.dtype), (2, np.dtype("int16")))
check("noise is loud enough but never clips", 20000 < np.abs(white).max() <= 32767, True)
check("pink is deeper than white, brown deeper than pink", low_share(white) < low_share(pink) < low_share(brown), True)
check("chime is under two seconds", len(s.chime()) / 48000 < 2, True)
check("an alarm's '6:30' is the morning", calendar_events.parse_time("6:30", prefer_morning=True), "06:30")
check("an alarm's '7pm' stays the evening", calendar_events.parse_time("7pm", prefer_morning=True), "19:00")

s.ALARMS_FILE = Path(tempfile.mkdtemp(prefix="echo_alarm_")) / "alarms.json"
once = s.add_alarm("07:00", "2026-11-11", "", "gym")
daily = s.add_alarm("06:30", "", "weekdays", "")
check("two alarms saved", len(s.load_alarms()), 2)
check("nothing due the night before", s.due_alarms(datetime(2026, 11, 10, 23, 0)), [])
due = s.due_alarms(datetime(2026, 11, 11, 6, 30, 20))  # a Wednesday
check("the weekday alarm rings Wednesday at 6:30", [a["id"] for a in due], [daily["id"]])
check("...only once that day", s.due_alarms(datetime(2026, 11, 11, 6, 31)), [])
check("the one-off rings at 7", [a["id"] for a in s.due_alarms(datetime(2026, 11, 11, 7, 0, 30))], [once["id"]])
check("...and is gone afterwards", [a["id"] for a in s.load_alarms()], [daily["id"]])
check("the weekday alarm stays quiet on Saturday", s.due_alarms(datetime(2026, 11, 14, 6, 30)), [])
check("wake-up words", s.wake_up_text({"label": "gym"}, datetime(2026, 11, 11, 7, 0)).split(" Today")[0],
      "Good morning, sir. It's 7 AM. Your alarm: gym.")
check("alarm described", s.describe_alarm(daily), "6:30 AM every weekday")
try:
    s.add_alarm("25:00")
    check("a time that doesn't exist is refused", False, True)
except ValueError:
    check("a time that doesn't exist is refused", True, True)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
