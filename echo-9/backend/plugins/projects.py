"""
Projects and approvals, from the chat or by phone: "make me an app that...", "how's the
project going?", "stop that", "what are you waiting on?", "approve it".

Asking for a project is the approval for it (24 hours). Approving something Echo asked
for by phone needs sir's passcode in the same sentence - at the PC it doesn't.
"""
import re

import approvals
import launch_utils as lu
import turn_context

PLUGIN_NAME = "projects"
TOGGLE_LABEL = "Projects (Echo works on things by itself)"
CONFIG_KEY = "projects_enabled"
ENABLED_BY_DEFAULT = True
VRAM_COST = "uses the brain when idle"
SIR_CALLS = True


def _agent():
    import agent
    return agent


def _status_line(p: dict) -> str:
    words = {"working": "in progress", "waiting_approval": "waiting for your OK", "done": "done",
             "stopped": "stopped"}.get(p["status"], p["status"])
    last = next((e["text"] for e in reversed(p.get("log", [])) if e["kind"] in ("tool", "thought", "start")), "")
    tail = f" Latest: {last[:140]}." if last and p["status"] == "working" else ""
    if p["status"] == "done" and p.get("summary"):
        tail = f" {p['summary'][:300]}"
    return f"{p['title']} is {words} after {p['steps']} steps.{tail}"


def _title_from(goal: str) -> str:
    import re
    words = re.sub(r"^(hey |ok )?(echo|jarvis)[,!. ]*", "", goal.strip(), flags=re.I)
    words = re.sub(r"^(can you |could you |please |make me |build me |make |build |create |write )+", "", words, flags=re.I)
    words = re.sub(r"^(a |an |the )", "", words, flags=re.I).strip(" .!?")
    return (words[:1].upper() + words[1:40]).strip() or "Project"


async def start_project(args: dict) -> str:
    goal = (args.get("goal") or "").strip()
    title = (args.get("title") or "").strip() or _title_from(goal)
    if not goal:
        return lu.say("What should the project do?")
    project = _agent().start(title, goal, asked_by_sir=True)
    where = "on the dashboard's Projects tab" if turn_context.channel.get() != "phone" else "on the dashboard when you're back"
    return lu.say(f"On it, sir - I'm working on {project['title']} in the background. You'll see it {where}, "
                  "and I'll tell you when it's done. You've approved it for the next 24 hours.")


async def project_status(args: dict) -> str:
    project = _agent().get(args.get("name") or "")
    if project is None:
        return lu.say("There aren't any projects yet.")
    return lu.say(_status_line(project))


async def list_projects(args: dict) -> str:
    projects = _agent().all_projects()[-6:]
    if not projects:
        return lu.say("No projects yet. Ask me to make or research something and I'll work on it in the background.")
    return lu.say(" ".join(_status_line(p) for p in reversed(projects)))


async def stop_project(args: dict) -> str:
    project = _agent().get(args.get("name") or "")
    if project is None or project["status"] in ("done", "stopped"):
        return lu.say("There's no project running to stop.")
    _agent().stop(project)
    return lu.say(f"Stopped {project['title']}.")


async def list_requests(args: dict) -> str:
    waiting = approvals.pending()
    if not waiting:
        return lu.say("I'm not waiting on anything from you, sir.")
    return lu.say("Waiting for your OK on: " + "; ".join(r["what"] for r in waiting[-5:]) + ".")


_SAYS_YES = re.compile(r"\b(approve[ds]?|go ahead|yes|yeah|yep|sure|ok(ay)?|do it|green light|sounds good|let'?s do it|"
                       r"fine|allow(ed)?|you can|permission|start it|build it)\b", re.I)
_SAYS_NO = re.compile(r"\b(deny|denied|decline[ds]?|no|nope|don'?t|do not|reject(ed)?|cancel|skip (it|that)|"
                      r"never ?mind|not now|stop)\b", re.I)


async def _decide(args: dict, approve: bool) -> str:
    record = approvals.find_pending(args.get("which") or "")
    if record is None:
        return lu.say("There's nothing waiting for your approval.")
    # Only ever on sir's own say-so: in testing, the brain once "declined" a waiting request
    # because sir had just said a wrong passcode. His words must actually approve or decline.
    said = turn_context.user_text.get() or ""
    if not (_SAYS_YES if approve else _SAYS_NO).search(said):
        return lu.say(f"Just to be sure - do you want me to approve or decline {record['what']}?")
    on_phone = turn_context.channel.get() == "phone"
    if approve and on_phone and not turn_context.passcode_said.get():
        return lu.say("To approve that by phone, say your passcode along with the approval.")
    approvals.decide(record["id"], approve, via="phone with passcode" if on_phone else "at the PC")
    if approve and record.get("project"):
        _agent().resume_if_approved(record["project"])
    verb = "Approved" if approve else "Declined"
    return lu.say(f"{verb}: {record['what']}." + (" It's good for 24 hours." if approve else ""))


async def approve_request(args: dict) -> str:
    return await _decide(args, True)


async def deny_request(args: dict) -> str:
    return await _decide(args, False)


SCHEMAS = [
    {"type": "function", "function": {
        "name": "start_project",
        "description": ("Starts a bigger job Echo works on by itself in the background: building an app, game, website "
                        "or animation, writing and testing a script, or a research write-up. Use when sir asks you to "
                        "make/build/code/research something that takes several steps. For a quick one-screen demo "
                        "use write_canvas_code instead."),
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string", "description": "A short name."},
            "goal": {"type": "string", "description": "What sir wants, in full detail, as he said it."}},
            "required": ["goal"]}}},
    {"type": "function", "function": {
        "name": "project_status", "description": "How a project is going (the latest one if no name).",
        "parameters": {"type": "object", "properties": {"name": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "list_projects", "description": "Lists Echo's projects and where each is up to.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "stop_project", "description": "Stops a project Echo is working on.",
        "parameters": {"type": "object", "properties": {"name": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "list_requests", "description": "What Echo is waiting for sir's approval on.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "approve_request",
        "description": "Sir approves something Echo asked permission for ('yes, go ahead', 'approve it'). By phone he must say his passcode too.",
        "parameters": {"type": "object", "properties": {"which": {"type": "string", "description": "Words from the request, or empty for the latest."}}}}},
    {"type": "function", "function": {
        "name": "deny_request", "description": "Sir says no to something Echo asked permission for.",
        "parameters": {"type": "object", "properties": {"which": {"type": "string"}}}}},
]

DISPATCH = {
    "start_project": start_project,
    "project_status": project_status,
    "list_projects": list_projects,
    "stop_project": stop_project,
    "list_requests": list_requests,
    "approve_request": approve_request,
    "deny_request": deny_request,
}
