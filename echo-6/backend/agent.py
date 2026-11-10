"""
Echo working on its own - projects it carries out in the background, step by step.

A project is a goal ("make me a workout timer app", "find the best 4K TV under $500 and
compare them"). Echo plans it, then builds it (an app/game/animation page, a script, or a
research report), checks its own work in the sandbox - compiling the page's scripts or
running the program - feeds any errors back to itself to fix, and finishes with a short
spoken summary for sir. It can also suggest projects of its own.

What it may do on its own is limited by approvals.py: web browsing and notes are always
fine; writing files, running code and showing an app need a live (under a day old)
approval for that project. When the approval runs out mid-project, the project
pauses and Echo asks again. The only person it can phone is sir himself (via Sir Page) -
there is no tool here for calling anyone else.

Echo always gives way to sir: a step only runs when no conversation or phone call is in
progress, so working on a project never slows down talking to it.
"""
import asyncio
import json
import re
import time
import uuid
from pathlib import Path

import activity
import approvals
import http_pool
import sandbox

FILE = Path(__file__).resolve().parent / "projects.json"
MAX_STEPS = 40
STEP_PAUSE_SECONDS = 2.0
STEP_MAX_TOKENS = 3000
LOG_SHOWN_TO_MODEL = 12
SUGGEST_EVERY_SECONDS = 6 * 60 * 60

_wake = asyncio.Event()


# --- storage ------------------------------------------------------------------------

def _load() -> list[dict]:
    try:
        data = json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [p for p in data if isinstance(p, dict) and p.get("id")]


def _save(projects: list[dict]) -> None:
    FILE.write_text(json.dumps(projects[-50:], indent=2), encoding="utf-8")


def all_projects() -> list[dict]:
    return _load()


def get(project_id_or_name: str) -> dict | None:
    key = (project_id_or_name or "").strip().lower()
    projects = _load()
    for p in projects:
        if p["id"] == key:
            return p
    matches = [p for p in projects if key and (key in p["title"].lower() or key == p["slug"])]
    return matches[-1] if matches else (projects[-1] if not key and projects else None)


def _update(project: dict) -> None:
    projects = _load()
    for i, p in enumerate(projects):
        if p["id"] == project["id"]:
            projects[i] = project
            break
    else:
        projects.append(project)
    _save(projects)


def _log(project: dict, kind: str, text: str) -> None:
    project.setdefault("log", []).append({"at": time.time(), "kind": kind, "text": text[:1500]})
    project["log"] = project["log"][-200:]
    activity.log("note" if kind != "error" else "error", f"[project: {project['title']}] {kind}: {text[:300]}")


# --- starting, stopping ---------------------------------------------------------------

def start(title: str, goal: str, asked_by_sir: bool) -> dict:
    """A new project. When sir asked for it, that is its approval (for 24 hours); a project
    Echo thought of itself waits for sir's OK."""
    slug = sandbox.slug(title)
    project = {"id": uuid.uuid4().hex[:8], "title": title.strip()[:80], "slug": slug, "goal": goal.strip()[:2000],
               "status": "working" if asked_by_sir else "waiting_approval", "created_at": time.time(),
               "steps": 0, "log": [], "preview": None, "summary": ""}
    sandbox.project_dir(slug)
    if asked_by_sir:
        approvals.grant(f"work on: {project['title']}", project["id"], via="sir asked")
        _log(project, "start", f"Started because sir asked: {goal[:300]}")
    else:
        approvals.request(f"Start a project: {project['title']}", goal, project["id"])
        _log(project, "suggested", goal[:300])
    _update(project)
    _wake.set()
    return project


def stop(project: dict, why: str = "sir stopped it") -> None:
    project["status"] = "stopped"
    _log(project, "stopped", why)
    _update(project)


def resume_if_approved(project_id: str) -> None:
    project = get(project_id)
    if project and project["status"] == "waiting_approval" and approvals.approved(project_id):
        project["status"] = "working"
        _log(project, "approved", "sir approved - carrying on")
        _update(project)
        _wake.set()


def _notify_sir(project: dict, message: str) -> None:
    """Tell sir something needs him: on the dashboard always, and by phone - sir only - if
    Sir Page is on. Echo never phones anyone else on its own."""
    activity.log("note", message)
    try:
        import pager
        asyncio.get_running_loop().create_task(pager.page_owner(f"agent:{project['id']}", message))
    except Exception:  # noqa: BLE001
        pass



# --- the work itself: one clear job per step ----------------------------------------
# A small local model is good at writing one thing well when told exactly what to write,
# and poor at juggling a box of tools. So each step asks it for ONE piece of plain text -
# a plan, a whole file, a fixed file, a report - and Echo's own code does the rest: saving,
# running in the sandbox, checking for errors, feeding errors back, showing the result.

WEB_WORDS = ("app", "game", "animation", "website", "web page", "webpage", "page", "site", "visual", "interactive",
             "calculator", "timer", "tracker", "dashboard", "html", "draw", "screensaver", "quiz", "clock")
SCRIPT_WORDS = ("script", "python", "program", "automate", "automation", "command line", "calculate", "convert",
                "csv", "parse", "simulate", "javascript file", "node")
RESEARCH_WORDS = ("research", "find out", "compare", "look into", "best ", "which ", "review", "summarize",
                  "summary of", "report", "pros and cons", "options for", "recommend")
MAX_FIXES = 3
CODE_BLOCK = re.compile(r"```[a-zA-Z0-9]*\s*\n(.*?)```", re.S)
SYNTAX_CHECKER = r"""
const fs = require('fs'), vm = require('vm');
const html = fs.readFileSync(process.argv[2], 'utf8');
const re = /<script\b([^>]*)>([\s\S]*?)<\/script>/gi;
let m, n = 0, bad = 0;
while ((m = re.exec(html))) {
  if (/src\s*=/.test(m[1]) || /type\s*=\s*["']?(module|text\/template)/i.test(m[1])) continue;
  n++;
  try { new vm.Script(m[2], { filename: 'script' + n + '.js' }); }
  catch (e) {
    bad++;
    // Where it broke, in the page's own line numbers, with the lines around it.
    const at = /script\d+\.js:(\d+)/.exec(e.stack || '');
    const before = html.slice(0, m.index + m[0].indexOf('>') + 1).split('\n').length - 1;
    const lines = html.split('\n');
    const line = at ? before + parseInt(at[1], 10) : 0;
    let context = '';
    for (let i = Math.max(1, line - 3); line && i <= Math.min(lines.length, line + 2); i++)
      context += (i === line ? '>> ' : '   ') + i + ': ' + lines[i - 1] + '\n';
    console.log('ERROR on line ' + line + ': ' + e.message + '\n' + context);
  }
}
if (!/<html|<body|<canvas|<div/i.test(html)) { console.log('PAGE ERROR: this does not look like an HTML page'); bad++; }
console.log(bad ? 'FAILED' : 'OK ' + n + ' scripts');
"""


def classify(goal: str) -> str:
    g = goal.lower()
    if any(w in g for w in RESEARCH_WORDS) and not any(w in g for w in ("app", "game", "script")):
        return "research"
    if any(w in g for w in SCRIPT_WORDS) and not any(w in g for w in ("app", "game", "animation", "website", "page")):
        return "script"
    return "web"


async def _ask(model: str, url: str, prompt: str, max_tokens: int = STEP_MAX_TOKENS, as_json: bool = False) -> str:
    body = {"model": model, "messages": [{"role": "system", "content": WORKER_SYSTEM}, {"role": "user", "content": prompt}],
            "stream": False, "keep_alive": -1, "options": {"num_predict": max_tokens, "temperature": 0.4}}
    if as_json:
        body["format"] = "json"
    resp = await http_pool.client().post(url, json=body, timeout=300)
    resp.raise_for_status()
    return (resp.json()["message"].get("content") or "").strip()


WORKER_SYSTEM = (
    "You are Echo, building something for sir on your own. Do exactly the one job you are given, completely. "
    "When asked for a file, reply with ONLY that whole file inside one code block - no explanations, no "
    "placeholders, no 'add your code here'. Visuals are drawn with CSS, SVG or <canvas>; never link outside images."
)


def _extract_code(reply: str) -> str:
    blocks = CODE_BLOCK.findall(reply)
    if blocks:
        return max(blocks, key=len).strip() + "\n"
    return reply.strip() + "\n"


def _gate(project: dict) -> bool:
    """Writing, running and showing need a live approval; if it ran out, pause and ask again."""
    if approvals.approved(project["id"]):
        return True
    project["status"] = "waiting_approval"
    approvals.request(f"Keep working on: {project['title']}",
                      "The approval for this project is more than a day old (or was never given).", project["id"])
    _log(project, "paused", "approval is more than a day old - asked sir again")
    return False


def _check_web(project: dict) -> str:
    """'' when the page's scripts compile; otherwise the errors, word for word."""
    sandbox.write_file(project["slug"], "_check.js", SYNTAX_CHECKER)
    result = sandbox.run(project["slug"], "_check.js", [str(sandbox.safe_path(project["slug"], project["main_file"]))], timeout=20)
    sandbox.safe_path(project["slug"], "_check.js").unlink(missing_ok=True)
    out = result["output"].strip()
    return out if "FAILED" in out else ""


def _count_printed_items(evidence: str, printed: str) -> int | None:
    """How many items a quoted piece of program output lists - a printed [a, b, c] list, or one
    number per line - but only if that list really appears in what the program printed."""
    flat = lambda s: re.sub(r"\s+", "", s)  # noqa: E731
    bracket = re.search(r"\[([^\[\]]+)\]", evidence)
    if bracket and flat(bracket.group(0)) in flat(printed):
        return len([x for x in bracket.group(1).split(",") if x.strip()])
    numbers = [line.strip() for line in evidence.splitlines() if re.fullmatch(r"-?\d+(\.\d+)?", line.strip())]
    if len(numbers) >= 2 and flat("\n".join(numbers)) in flat(printed):
        return len(numbers)
    return None


async def _review(project: dict, model: str, url: str) -> str:
    """Error-free isn't the same as right. Read the work against each thing sir asked for -
    and for a program, its real output - and name anything missing or wrong ('' if none)."""
    if project.get("reviews", 0) >= MAX_FIXES:
        return ""
    project["reviews"] = project.get("reviews", 0) + 1
    code = sandbox.safe_path(project["slug"], project["main_file"]).read_text(encoding="utf-8")[:12000]
    output = ""
    if project.get("kind") == "script":
        output = f"\n\nWhen run, it printed exactly this:\n```\n{project.get('last_output', '')[:2500]}\n```"
    # A checklist, not "does it look right?": a small brain waved through "primes up to 20" for
    # "the first 20 primes" until it had to quote the evidence and count it.
    raw = await _ask(model, url, (
        f"Sir asked for: {project['goal']}\n\nHere is what was built ({project['main_file']}):\n```\n{code}\n```{output}\n\n"
        "Check it like a strict reviewer. Go through each thing sir asked for, one at a time. For each one, copy the "
        "exact evidence from the output (or the code, for a page) that shows it is done. Whenever sir gave a number - "
        "how many items, how long, how many rounds - count what is actually there, one by one, and compare. Also "
        "trace the code for things that look done but don't work: a value overwritten every frame, a button that "
        "changes nothing visible, a wrong total (work the numbers out yourself). Reply as JSON: "
        '{"checks": [{"asked": "...", "evidence": "...", "count_asked": null, "count_found": null, "ok": true}], '
        '"problems": ["each real problem, specific"]}'), 900, as_json=True)
    try:
        data = json.loads(raw)
        found = [p for p in data.get("problems", []) if isinstance(p, str) and p.strip()]
        for c in data.get("checks", []):
            if not isinstance(c, dict):
                continue
            asked, got = c.get("count_asked"), c.get("count_found")
            # The brain quotes the right evidence but can still claim the wrong count ("8 primes" -> "20"),
            # so when its evidence is a list really printed by the program, count it here instead.
            counted = _count_printed_items(str(c.get("evidence", "")), project.get("last_output", ""))
            if counted is not None:
                got = counted
            miscounted = isinstance(asked, (int, float)) and isinstance(got, (int, float)) and asked != got
            if (c.get("ok") is False or miscounted) and not any(str(c.get("asked", ""))[:30] in p for p in found):
                found.append(f"{c.get('asked', 'a requirement')}: not met"
                             + (f" - asked for {asked}, found {got}" if miscounted else ""))
    except (json.JSONDecodeError, AttributeError):
        return ""
    return "\n".join(f"- {p}" for p in found[:6])


async def step(project: dict, model: str, url: str) -> None:
    project["steps"] += 1
    kind = project.setdefault("kind", classify(project["goal"]))
    phase = project.setdefault("phase", "plan")
    goal = project["goal"]

    if phase == "plan":
        plan = await _ask(model, url, f"Goal: {goal}\n\nWrite a short plan: 3 to 6 bullet points of what you'll build "
                                      "or find, and how. Plain text, no code.", 400)
        project.setdefault("notes", []).append(plan)
        _log(project, "plan", plan)
        project["phase"] = "research" if kind == "research" else "build"
        return

    if phase == "research":
        raw = await _ask(model, url, f"Goal: {goal}\n\nGive 2 or 3 web searches that would answer this. "
                                     'Reply as JSON: {"searches": ["...", "..."]}', 200, as_json=True)
        try:
            searches = [q for q in json.loads(raw).get("searches", []) if isinstance(q, str)][:3]
        except (json.JSONDecodeError, AttributeError):
            searches = [goal]
        import tools
        found = []
        for q in searches or [goal]:
            text = str(await tools.web_search(q))
            found.append(f"Search: {q}\n{text[:2500]}")
            _log(project, "search", q)
        project["research"] = "\n\n".join(found)[:9000]
        project["phase"] = "write_report"
        return

    if phase == "write_report":
        report = await _ask(model, url, f"Goal: {goal}\n\nWhat the searches found:\n{project.get('research', '')}\n\n"
                                        "Write a clear report for sir in Markdown: a short answer first, then the "
                                        "details, with the sources' names. Only state what the findings support.", 2500)
        if not _gate(project):
            return
        sandbox.write_file(project["slug"], "report.md", report)
        project["summary"] = (await _ask(model, url, f"In two spoken sentences, tell sir the answer from this report:\n{report[:3000]}", 160))
        project["status"], project["phase"] = "done", "done"
        _log(project, "done", project["summary"])
        return

    if phase == "build":
        if not _gate(project):
            return
        plan = "\n".join(project.get("notes", [])[-1:])
        if kind == "web":
            project["main_file"] = "index.html"
            reply = await _ask(model, url, f"Goal: {goal}\nPlan:\n{plan}\n\nWrite the COMPLETE, working index.html: one "
                                           "file with all CSS in <style> and all JavaScript in <script>. Reply with only "
                                           "the file in one ```html code block.")
        else:
            ext = ".js" if "javascript" in goal.lower() or "node" in goal.lower() else ".py"
            project["main_file"] = "main" + ext
            lang = "JavaScript (Node.js, built-in modules only)" if ext == ".js" else "Python 3 (standard library only)"
            reply = await _ask(model, url, f"Goal: {goal}\nPlan:\n{plan}\n\nWrite the COMPLETE program in {lang}. It must run "
                                           "on its own with no input from the keyboard and print its results. Reply with "
                                           "only the file in one code block.")
        code = _extract_code(reply)
        sandbox.write_file(project["slug"], project["main_file"], code)
        _log(project, "wrote", f"{project['main_file']} ({len(code)} characters)")
        project["phase"], project["fixes"] = "check", 0
        return

    if phase == "check":
        if not _gate(project):
            return
        if not sandbox.safe_path(project["slug"], project["main_file"]).exists():
            # The file is gone (deleted, or the folder was cleared): build it again.
            _log(project, "missing", f"{project['main_file']} wasn't there - writing it again")
            project["phase"] = "build"
            return
        if kind == "web":
            problems = await asyncio.to_thread(_check_web, project)
        else:
            result = await asyncio.to_thread(sandbox.run, project["slug"], project["main_file"])
            project["last_output"] = result["output"][-3000:]
            failed = result["exit_code"] != 0 or result["timed_out"] or "Traceback" in result["output"]
            problems = (("It ran for over a minute and was stopped.\n" if result["timed_out"] else "")
                        + result["output"][-3000:]) if failed else ""
            _log(project, "ran", f"exit code {result['exit_code']}: {result['output'][-400:]}")
        if not problems:
            _log(project, "checked", "no errors")
            problems = await _review(project, model, url)
            if not problems:
                project["phase"] = "show"
                _log(project, "reviewed", "does everything that was asked")
                return
            _log(project, "review", problems[:600])
        _log(project, "errors", problems[:800])
        if project.get("fixes", 0) >= MAX_FIXES:
            project["status"], project["phase"] = "done", "done"
            project["summary"] = (f"I built {project['title']}, but it still has errors I couldn't fix after "
                                  f"{MAX_FIXES} tries - it's in the Projects tab if you want to look.")
            _log(project, "done", project["summary"])
            return
        project["fixes"] = project.get("fixes", 0) + 1
        current = sandbox.safe_path(project["slug"], project["main_file"]).read_text(encoding="utf-8")
        if project["fixes"] < MAX_FIXES:
            reply = await _ask(model, url, f"Goal: {goal}\n\nThis is {project['main_file']}:\n```\n{current[:12000]}\n```\n\n"
                                           f"It has these problems (the line marked >> is where it breaks):\n{problems[:2000]}\n\n"
                                           "Fix exactly those problems - rewrite the broken lines correctly. Reply with only "
                                           "the COMPLETE corrected file in one code block.")
        else:
            # Patching the same mistake keeps failing: start over, simpler.
            reply = await _ask(model, url, f"Goal: {goal}\n\nAn earlier attempt kept failing with: {problems[:600]}\n\n"
                                           f"Write {project['main_file']} again from scratch, as simply as possible: plain "
                                           "string concatenation instead of template literals, short functions, no clever "
                                           "tricks. Reply with only the COMPLETE file in one code block.")
        code = _extract_code(reply)
        sandbox.write_file(project["slug"], project["main_file"], code)
        _log(project, "fixed", f"rewrote {project['main_file']} (fix {project['fixes']})")
        return

    if phase == "show":
        if kind == "web":
            project["preview"] = project["main_file"]
            what = f"It's showing on the dashboard's Projects tab."
        else:
            what = f"It ran in the sandbox and printed: {project.get('last_output', '').strip()[:200]}"
        summary = await _ask(model, url, f"In one or two spoken sentences, tell sir what you made for: {goal}. "
                                         "Don't mention files or code blocks.", 120)
        project["summary"] = f"{summary} {what}".strip()
        project["status"], project["phase"] = "done", "done"
        _log(project, "done", project["summary"])
        return


def status_block() -> str:
    """The real state of projects and approvals, given to Echo with every message - so "how's the
    project going?" is answered from fact, never invented."""
    import approvals as _approvals
    projects = _load()[-5:]
    waiting = _approvals.pending()
    lines = []
    for p in reversed(projects):
        words = {"working": "in progress", "waiting_approval": "paused, waiting for sir's OK", "done": "done",
                 "stopped": "stopped"}.get(p["status"], p["status"])
        extra = f" - {p['summary'][:160]}" if p["status"] == "done" and p.get("summary") else ""
        lines.append(f"- {p['title']}: {words}, {p['steps']} steps{extra}")
    if not lines:
        lines.append("- none (no background project has been started)")
    asks = "; ".join(r["what"] for r in waiting[-5:]) or "nothing"
    return ("Background projects (the real, current state - never claim progress that isn't listed here):\n"
            + "\n".join(lines) + f"\nWaiting for sir's approval on: {asks}")


async def runner(model: str, url: str, sir_is_busy) -> None:
    """Runs forever inside the server: works on projects whenever sir isn't using Echo."""
    while True:
        try:
            await asyncio.wait_for(_wake.wait(), timeout=STEP_PAUSE_SECONDS * 5)
        except asyncio.TimeoutError:
            pass
        _wake.clear()
        for project in [p for p in _load() if p["status"] == "working"]:
            if sir_is_busy():
                break
            if project["steps"] >= MAX_STEPS:
                project["status"] = "stopped"
                _log(project, "stopped", f"reached the {MAX_STEPS}-step limit")
                _update(project)
                _notify_sir(project, f"Sir, I've stopped working on {project['title']} - it hit my step limit.")
                continue
            try:
                await step(project, model, url)
            except Exception as exc:  # noqa: BLE001 - one bad step must not kill the loop
                _log(project, "error", repr(exc))
            _update(project)
            if project["status"] == "done":
                _notify_sir(project, f"Sir, {project['title']} is done. {project['summary']}")
            elif project["status"] == "waiting_approval":
                _notify_sir(project, f"Sir, {project['title']} needs your OK to carry on.")
            await asyncio.sleep(STEP_PAUSE_SECONDS)
            _wake.set()  # keep going while there is work


async def suggest_projects(model: str, url: str, facts: str) -> dict | None:
    """Echo choosing something worth doing by itself - only ever a suggestion until sir approves it."""
    prompt = ("From what you know about sir below, suggest ONE small, genuinely useful project you could do for him "
              "on your own in the background (a tool, an app, a bit of research). Reply as JSON: "
              '{"title": "...", "goal": "..."} - or {} if nothing is worth doing.\n\n' + facts[:3000])
    try:
        resp = await http_pool.client().post(url, json={"model": model, "messages": [{"role": "user", "content": prompt}],
                                                        "format": "json", "stream": False, "keep_alive": -1,
                                                        "options": {"num_predict": 200}}, timeout=60)
        idea = json.loads(resp.json()["message"]["content"])
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(idea, dict) or not idea.get("title") or not idea.get("goal"):
        return None
    return start(str(idea["title"]), str(idea["goal"]), asked_by_sir=False)
