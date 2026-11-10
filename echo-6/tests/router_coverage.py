"""
Checks the tool router never hides the tool a request needs.

For every request in tests/sim_world.py (and a list of extra everyday phrasings below), the
tools offered must include at least one tool the request should use. No brain, no network -
just the router's word matching, so it runs in a second.

    venv\\Scripts\\python.exe tests\\router_coverage.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tests"))

import tool_router  # noqa: E402

EXTRA = [
    ("how much did I spend this week?", ["get_spending_summary"]),
    ("who is calling me?", ["hang_up_call", "call_phone_number"]),
    ("what am I looking at?", ["view_camera", "view_screen"]),
    ("what's the weather tomorrow?", ["web_search"]),
    ("who won the game last night?", ["web_search"]),
    ("what is my gpu temperature?", ["pc_stats"]),
    ("how are you running?", ["echo_self_report"]),
    ("what is running on my computer?", ["list_running_apps"]),
    ("when is my next meeting?", ["list_calendar_events"]),
    ("what's on my screen?", ["view_screen"]),
    ("what is playing on spotify?", ["spotify_now_playing"]),
    ("is the burnt peanut live?", ["tv_youtube_creator"]),
    ("what is the address of the gym?", ["get_address"]),
    ("how much does a tesla cost?", ["web_search"]),
    ("what did I tell you about my sister?", ["check_knowledge", "remember"]),
    ("who wrote the hobbit?", ["web_search"]),
    ("what are you waiting on me for?", ["list_requests"]),
    ("is my pc running hot?", ["pc_stats"]),
]


def all_tool_names() -> list[dict]:
    """Every tool name the router knows, plus the basics, as minimal schemas."""
    names = set(tool_router.GROUP_OF) | {"web_search", "fetch_webpage", "remember", "check_knowledge", "save_knowledge"}
    return [{"function": {"name": n}} for n in sorted(names)]


def main() -> int:
    import sim_world

    schemas = all_tool_names()
    cases = []
    for sc in sim_world.SCENARIOS:
        wanted = [t for t in sc["tools"] if not t.startswith("_")]
        if wanted:
            cases.append((sc["say"], wanted))
    cases += [([said], wanted) for said, wanted in EXTRA]
    bad = 0
    for lines, wanted in cases:
        earlier = []
        offered = set()
        for line in lines:
            offered = {s["function"]["name"] for s in tool_router.select(schemas, line, earlier[-2:])}
            earlier.append(line)
        if not offered & set(wanted):
            bad += 1
            print(f"MISSING  {lines[-1]!r}: wanted one of {wanted}; groups {sorted(tool_router.groups_for(lines[-1]))}")
    print(f"{len(cases) - bad}/{len(cases)} requests get the tool they need")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
