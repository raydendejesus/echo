"""
Builds a few real projects with Echo's real brain and real sandbox, and checks the results
are right - not just "done". Uses a temporary project list, so nothing shows up in yours,
and deletes the test projects afterwards. Needs Ollama running.

    venv\\Scripts\\python.exe tests\\agent_projects.py
"""
import asyncio
import re
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
import httpx  # noqa: E402

import agent  # noqa: E402
import approvals  # noqa: E402
import http_pool  # noqa: E402
import sandbox  # noqa: E402

TMP = Path(tempfile.mkdtemp())
agent.FILE = TMP / "projects.json"
approvals.FILE = TMP / "approvals.json"
MODEL, URL = "echo", "http://127.0.0.1:11434/api/chat"

CASES = [
    ("Selftest Primes", "Write a Python script that prints the first 20 prime numbers and their sum.",
     lambda slug: "639" in sandbox.run(slug, "main.py")["output"]),
    ("Selftest Clock", "Make a big digital clock page that shows the time and today's date, updating every second.",
     lambda slug: re.search(r"setInterval|requestAnimationFrame|setTimeout", sandbox.safe_path(slug, "index.html").read_text(encoding="utf-8"))),
    ("Selftest Timer", "Make me a workout interval timer app: 30 seconds work, 10 seconds rest, 8 rounds, with big numbers and a start button.",
     lambda slug: "<button" in sandbox.safe_path(slug, "index.html").read_text(encoding="utf-8")),
]


async def build(title: str, goal: str) -> dict:
    project = agent.start(title, goal, asked_by_sir=True)
    while project["status"] == "working" and project["steps"] < 16:
        await agent.step(project, MODEL, URL)
        agent._update(project)
        project = agent.get(project["id"])
    return project


async def main() -> None:
    async with httpx.AsyncClient() as client:
        http_pool.client = lambda: client
        passed = 0
        for title, goal, check in CASES:
            started = time.time()
            project = await build(title, goal)
            try:
                right = project["status"] == "done" and bool(check(project["slug"]))
            except Exception as exc:  # noqa: BLE001
                right = False
                print("   check error:", exc)
            passed += right
            trail = " > ".join(e["kind"] for e in project["log"])
            print(f"{'PASS' if right else 'FAIL'}  {title}: {project['steps']} steps, {time.time() - started:.0f}s | {trail}")
            sandbox.delete_project(project["slug"])
        # approvals: a day-old approval stops writing
        project = agent.start("Selftest Expiry", "write hello.txt", asked_by_sir=True)
        records = approvals._load()
        for r in records:
            r["approved_at"] -= 25 * 3600
        approvals._save(records)
        blocked = not agent._gate(project)
        print(f"{'PASS' if blocked else 'FAIL'}  approval older than a day stops the work and asks again")
        sandbox.delete_project(project["slug"])
        print(f"\n{passed + blocked}/{len(CASES) + 1} passed")


asyncio.run(main())
