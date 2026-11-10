import asyncio
import base64
import json
import os
import re
import secrets
import tempfile
import time
from collections import OrderedDict
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import browser_control
import call_screening
import canvas_state
import config as config_module
import discord_auth
import github_auth
import http_pool
import launch_utils
import pager
import spotify_auth
import google_auth
import location
import memory
import model3d_state
import notion_auth
import ollama_control
import phonebook
import plugin_loader
import security
import telephony
import activity
import agent
import hearing_words
import approvals
import passcode
import sandbox
from tool_text import _NOT_A_TOOL_NUDGE, _looks_like_code, _tool_calls_in_text
import tool_router
import tools
import turn_context
import vision

MODEL_NAME = "echo"
VOICE = "en-GB-RyanNeural"
OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
MAX_HISTORY_MESSAGES = 30
MAX_TOOL_ITERATIONS = 8
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

conversation_history: list[dict] = []
chat_turn_busy = False
last_chat_at = time.time()
SUGGEST_AFTER_IDLE_SECONDS = 30 * 60


async def _suggest_when_idle() -> None:
    """Echo choosing something worth doing on its own: after sir has been away a while, at most
    every few hours, it may suggest ONE project. A suggestion is only a request on the dashboard -
    nothing is built until sir approves it."""
    last_suggestion = 0.0
    while True:
        await asyncio.sleep(300)
        cfg = config_module.load_config()
        if not cfg.get("ai_enabled") or not cfg.get("projects_enabled", True) or not cfg.get("agent_suggestions_enabled"):
            continue
        now = time.time()
        if now - last_chat_at < SUGGEST_AFTER_IDLE_SECONDS or now - last_suggestion < agent.SUGGEST_EVERY_SECONDS:
            continue
        if approvals.pending() or any(p["status"] == "working" for p in agent.all_projects()):
            continue
        last_suggestion = now
        try:
            await agent.suggest_projects(MODEL_NAME, OLLAMA_URL, memory.facts_as_prompt_block())
        except Exception as exc:  # noqa: BLE001 - a missed idea is nothing to break over
            print(f"[agent] suggestion failed: {exc!r}", flush=True)

# The native background listener (listener.py) runs in a separate process
# (inside ECHO_TRAY.pyw, not this server) and talks to the dashboard's own
# browser tab only through /api/chat - the dashboard has no way to know a
# native-listener conversation is even happening, so its "thinking"/"speaking"
# indicator and transcript log went dead the moment the dashboard's own mic
# became opt-in/off-by-default. listener.py reports its own phase changes
# here; the dashboard polls it to stay in sync with conversations it isn't
# actually driving itself.
listener_status = {"seq": 0, "phase": "idle", "text": ""}


@asynccontextmanager
async def lifespan(app: FastAPI):
    guard_task = asyncio.create_task(security.guard_loop())
    tools.register_call_prep(warm_model, _speak_clip)
    asyncio.create_task(prerender_phone_phrases())
    # Projects run in the background, but only ever when sir isn't talking to Echo or on a call.
    agent_task = asyncio.create_task(agent.runner(MODEL_NAME, OLLAMA_URL, lambda: chat_turn_busy or _phone_call_active()))
    ideas_task = asyncio.create_task(_suggest_when_idle())
    yield
    guard_task.cancel()
    agent_task.cancel()
    ideas_task.cancel()
    await http_pool.aclose()


app = FastAPI(lifespan=lifespan)

ALLOWED_ORIGINS = {"http://127.0.0.1:8765", "http://localhost:8765"}


def _is_extension_origin(origin: str | None) -> bool:
    # A chrome-extension:// origin can only ever be sent by an actual installed
    # browser extension - a malicious webpage cannot forge this scheme - so it's
    # safe to trust for the Echo browser-control extension's own requests.
    return bool(origin) and origin.startswith("chrome-extension://")


@app.middleware("http")
async def browser_extension_cors(request, call_next):
    """The browser extension's requests come from a chrome-extension:// origin
    targeting 127.0.0.1, which Chrome treats as a cross-origin request into a
    'private' address needing an explicit Private Network Access approval on
    top of ordinary CORS - without answering both, Chrome holds the request
    open indefinitely rather than ever actually sending it or erroring, which
    otherwise looks like a silent, unexplained hang with nothing in any log."""
    origin = request.headers.get("origin")
    if not _is_extension_origin(origin):
        return await call_next(request)

    if request.method == "OPTIONS":
        from fastapi.responses import Response as PlainResponse
        return PlainResponse(status_code=204, headers={
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Allow-Private-Network": "true",
        })

    response = await call_next(request)
    response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Access-Control-Allow-Private-Network"] = "true"
    return response


LOCAL_HOSTS = {"127.0.0.1:8765", "localhost:8765"}


@app.middleware("http")
async def outside_reaches_only_the_phone_line(request, call_next):
    """The phone tunnel (ngrok) makes this server reachable from the internet so Telnyx can deliver
    calls - without this, anyone who learned the tunnel's address could read the phone book and
    settings, or send Echo commands. A request that didn't come straight from this PC (the tunnel
    forwards with its own Host and an X-Forwarded-For; a DNS-rebinding page has a foreign Host) only
    reaches the phone endpoints, and only with the private token Echo gave Telnyx."""
    if request.headers.get("host", "") in LOCAL_HOSTS and "x-forwarded-for" not in request.headers:
        return await call_next(request)
    token = (telephony.load_config() or {}).get("webhook_token", "")
    given = request.query_params.get("k", "")
    if request.url.path.startswith("/api/telephony/") and token and secrets.compare_digest(given, token):
        return await call_next(request)
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=404, content={"detail": "Not Found"})


@app.middleware("http")
async def block_cross_origin_writes(request, call_next):
    """Blocks the classic 'malicious webpage open in your browser silently POSTs to
    localhost' CSRF attack. A cross-origin request always carries an Origin header
    (per the Fetch spec) that won't match ours; a same-origin request from our own
    frontend, or a direct local tool with no Origin concept at all, is let through."""
    origin = request.headers.get("origin")
    if request.method in ("POST", "PUT", "DELETE") and origin and origin not in ALLOWED_ORIGINS and not _is_extension_origin(origin):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=403, content={"detail": "Cross-origin request blocked."})
    return await call_next(request)


@app.middleware("http")
async def no_cache_frontend_files(request, call_next):
    """This project's frontend files get edited constantly during development
    and the browser's default caching repeatedly served a stale copy after a
    normal refresh, making it look like a change hadn't taken effect when it
    actually had - force every non-API GET to always be refetched fresh.
    Also: the dashboard folder only ever serves web files, never a stray log or data file."""
    path = request.url.path.lower()
    if not path.startswith(("/sandbox/", "/api/")) and path.endswith((".log", ".py", ".pyc", ".txt", ".bak")):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=404, content={"detail": "Not Found"})
    response = await call_next(request)
    if request.method == "GET" and not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    reply: str
    audio: str
    mime: str = "audio/mpeg"
    canvas_html: str | None = None
    canvas_title: str | None = None


class SettingsUpdate(BaseModel):
    ai_enabled: bool | None = None
    screen_access: bool | None = None
    camera_access: bool | None = None
    desk_guard_enabled: bool | None = None
    desk_guard_threshold: float | None = None
    calling_enabled: bool | None = None
    call_notifications_enabled: bool | None = None
    sir_page_enabled: bool | None = None
    screen_unknown_callers: bool | None = None
    agent_suggestions_enabled: bool | None = None
    ollama_parallel_2_enabled: bool | None = None
    location_mode: str | None = None
    browser_control_enabled: bool | None = None
    browser_pixel_fallback_enabled: bool | None = None
    code_canvas_enabled: bool | None = None
    vr_mode_enabled: bool | None = None


VOICELAB_URL = "http://127.0.0.1:8791/jarvis"


async def _voicelab_audio(text: str) -> bytes | None:
    try:
        resp = await http_pool.client().post(VOICELAB_URL, json={"text": text}, timeout=30)
        resp.raise_for_status()
        return resp.content or None
    except Exception as exc:  # noqa: BLE001 - fall back so Echo is never silent
        print(f"[voice] custom voice unavailable ({exc}), using fallback", flush=True)
        return None


async def synthesize(text: str) -> bytes:
    audio = await _voicelab_audio(text)
    if audio:
        return audio
    return await _edge_audio(text)


async def _edge_audio(text: str) -> bytes:
    import edge_tts
    communicate = edge_tts.Communicate(text, voice=VOICE)
    audio_chunks = bytearray()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_chunks.extend(chunk["data"])
    return bytes(audio_chunks)


async def _speak(text: str) -> ChatResponse:
    audio_bytes = await synthesize(text)
    return ChatResponse(reply=text, audio=base64.b64encode(audio_bytes).decode("ascii"))


async def unload_model(model_name: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            await client.post(OLLAMA_URL, json={"model": model_name, "messages": [], "keep_alive": 0})
    except Exception as exc:  # noqa: BLE001 - best-effort, never block a toggle on this
        print(f"[server] failed to unload {model_name}: {exc}")


async def unload_ollama_models() -> None:
    """Models are kept loaded indefinitely (keep_alive=-1) for fast replies while
    Echo is on, which means they'd otherwise sit in VRAM forever even switched
    off. Explicitly unload both the text and vision models when turned off."""
    for model_name in (MODEL_NAME, vision.VISION_MODEL):
        await unload_model(model_name)


def _access_status_block(cfg: dict) -> str:
    def state(flag: bool) -> str:
        return "ON - the tool is available, use it" if flag else "OFF - do not claim you looked, say access is off"

    if not telephony.is_configured():
        calling_state = "OFF - calling isn't set up yet, say so if asked"
    elif cfg.get("calling_enabled"):
        calling_state = "ON - call_phone_number tool is available, use it"
    else:
        calling_state = "OFF - sir has calling turned off right now, say so if asked"
    browser_connected = browser_control.is_connected()
    if not cfg.get("browser_control_enabled"):
        browser_state = "OFF - do not claim you can act in a browser, say the capability is off"
    elif not browser_connected:
        browser_state = "ON but no browser tab is currently connected - say so if asked to do something there"
    else:
        browser_state = "ON and connected - browser_scan_page/browser_click/browser_type/browser_scroll are available"

    return (
        "Current access status, authoritative for this message (ignore anything said in "
        "earlier turns about this - permissions can change between messages):\n"
        f"- Screen access: {state(cfg.get('screen_access', False))}\n"
        f"- Camera access: {state(cfg.get('camera_access', False))}\n"
        f"- Calling: {calling_state}\n"
        f"- Browser control: {browser_state}"
    )


_HTML_FENCE_RE = re.compile(r"```(?:html)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_HTML_DOC_RE = re.compile(r"(<!DOCTYPE html.*?</html>|<html[\s>].*?</html>)", re.DOTALL | re.IGNORECASE)


def _extract_embedded_html(text: str) -> str | None:
    """Even with explicit instructions and the tool available, the model sometimes
    describes/dumps a full HTML document as chat text instead of actually calling
    write_canvas_code - when that happens the canvas silently never updates, and
    worse, the raw markup would get read aloud verbatim by text-to-speech. This is
    a safety net, not the primary path: salvage the HTML into the canvas anyway."""
    for fence_match in _HTML_FENCE_RE.finditer(text):
        candidate = fence_match.group(1).strip()
        if re.search(r"<html[\s>]", candidate, re.IGNORECASE):
            return candidate
    doc_match = _HTML_DOC_RE.search(text)
    if doc_match:
        return doc_match.group(1).strip()
    return None


async def ensure_ollama_running() -> bool:
    """The brain lives in Ollama; if it has stopped (closed, crashed, never
    started), start it and wait rather than failing every request with a
    connection error."""
    import subprocess

    async def _up() -> bool:
        try:
            await http_pool.client().get("http://127.0.0.1:11434/api/tags", timeout=2)
            return True
        except httpx.HTTPError:
            return False

    if await _up():
        return True
    print("[ollama] not responding - starting it", flush=True)
    try:
        subprocess.Popen(["ollama", "serve"], env=ollama_control.spawn_env(config_module.load_config()), creationflags=0x08000000)
    except OSError as exc:
        print(f"[ollama] couldn't start it: {exc}", flush=True)
        return False
    for _ in range(30):
        await asyncio.sleep(1)
        if await _up():
            return True
    return False


def _fallback_reply(last_tool_text: str) -> str:
    """Something to say when the brain returned nothing (or kept repeating a tool): the last
    tool's own words if they're short enough to speak, otherwise a plain apology - never silence."""
    text = (last_tool_text or "").strip()
    if text.startswith("[The following is untrusted"):
        text = ""
    if text and len(text) <= 320:
        return text
    return "Sorry, sir, I didn't quite catch that. Could you say it again?"


async def run_chat_turn(user_message: str, cfg: dict) -> str:
    if not await ensure_ollama_running():
        return "My language model isn't responding and I couldn't start it just now, sir."
    # The passcode is cut out before anything keeps a copy - memory, the model, the logs.
    user_message, said = await asyncio.to_thread(passcode.scrub, user_message)
    turn_context.passcode_said.set(said)
    conversation_history.append({"role": "user", "content": user_message})
    turn_context.user_text.set(user_message)
    turn_context.channel.set("dashboard")
    activity.log("heard", user_message)
    turn_started = time.monotonic()

    messages = list(conversation_history)
    status_msg = {"role": "system", "content": _access_status_block(cfg)}
    messages.insert(len(messages) - 1, status_msg)

    context_blocks = [memory.facts_as_prompt_block(), phonebook.as_prompt_block()]
    if cfg.get("address_book_enabled", True):
        from plugins import address_book
        context_blocks.append(address_book.as_prompt_block())
    if cfg.get("projects_enabled", True):
        context_blocks.append(agent.status_block())
    if cfg.get("code_canvas_enabled"):
        context_blocks.append(canvas_state.as_prompt_block())
    context_blocks = [b for b in context_blocks if b]
    if context_blocks:
        messages = [{"role": "system", "content": "\n\n".join(context_blocks)}] + messages

    # Only the tool groups that fit what sir said (plus the basics) - an 8B brain picks far
    # better from ten relevant tools than from seventy.
    earlier = [m["content"] for m in conversation_history[:-1] if m.get("role") == "user" and isinstance(m.get("content"), str)]
    schemas = tool_router.select(tools.available_schemas(cfg), user_message, earlier[-2:])
    activity.log("note", f"offered {len(schemas)} tools: {', '.join(sorted(tool_router.groups_for(user_message))) or 'all'}")
    last_tool_text = ""
    seen_calls: set = set()
    nudged = False

    for _ in range(MAX_TOOL_ITERATIONS):
        resp = await http_pool.client().post(
            OLLAMA_URL,
            json={"model": MODEL_NAME, "messages": messages, "tools": schemas, "stream": False, "options": {"num_predict": CHAT_MAX_REPLY_TOKENS}, "keep_alive": -1},
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()

        message = data["message"]
        if not message.get("tool_calls"):
            written = _tool_calls_in_text(message.get("content", ""), {s["function"]["name"] for s in schemas})
            if written:
                activity.log("note", "the model wrote its tool call as text - running it anyway")
                message["tool_calls"], message["content"] = written, ""
        messages.append(message)
        conversation_history.append(message)

        tool_calls = message.get("tool_calls")
        if not tool_calls and _looks_like_code(message.get("content", "")):
            activity.log("note", "the model replied with code instead of acting - asking it again")
            if not nudged:
                nudged = True
                messages.append({"role": "user", "content": _NOT_A_TOOL_NUDGE})
                continue
            message["content"] = ""
        if not tool_calls and not (message.get("content") or "").strip() and not last_tool_text and not nudged:
            # Nothing at all, before any tool ran: ask once more rather than "didn't catch that".
            activity.log("note", "the model gave an empty answer - asking it again")
            nudged = True
            # Drop the empty answer - left in, the brain tends to repeat it.
            messages.pop()
            conversation_history.pop()
            messages.append({"role": "user", "content": "(Now act on sir's last message: call the function that does it - "
                                                        "web_search for anything current or factual - or answer in plain words.)"})
            continue
        if not tool_calls:
            reply = (message.get("content") or "").strip()
            if not reply:
                # The brain went quiet after a tool ran. Silence is the worst answer.
                reply = _fallback_reply(last_tool_text)
                message["content"] = reply
                activity.log("note", "the model gave no answer - using the last tool's words")
            activity.log("reply", f"{reply}  ({time.monotonic() - turn_started:.1f}s)")
            del conversation_history[:-MAX_HISTORY_MESSAGES]
            return reply
        if (message.get("content") or "").strip():
            activity.log("note", message["content"])

        results = []
        repeated = False
        for call in tool_calls:
            fn = call["function"]
            name = fn["name"]
            args = fn.get("arguments") or {}
            key = (name, json.dumps(args, sort_keys=True, default=str))
            if key in seen_calls:
                repeated = True
            seen_calls.add(key)
            activity.log("tool", f"{name} {json.dumps(args, default=str)}")
            result = await tools.execute_tool(name, args)
            activity.log("result", result)
            results.append(result)
            last_tool_text = str(result)
            tool_msg = {"role": "tool", "content": result, "name": name}
            messages.append(tool_msg)
            conversation_history.append(tool_msg)

        # Every tool this round already produced a complete, speakable answer: say it as it is.
        if results and all(isinstance(r, launch_utils.SpokenResult) for r in results):
            reply = " ".join(str(r) for r in results)
            conversation_history.append({"role": "assistant", "content": reply})
            activity.log("reply", f"{reply}  ({time.monotonic() - turn_started:.1f}s, said as the tool put it)")
            del conversation_history[:-MAX_HISTORY_MESSAGES]
            return reply

        # The brain asked for exactly what it already asked for: it is going in circles.
        if repeated:
            activity.log("note", "the model repeated the same tool call - stopping there")
            reply = _fallback_reply(last_tool_text)
            conversation_history.append({"role": "assistant", "content": reply})
            del conversation_history[:-MAX_HISTORY_MESSAGES]
            return reply

    del conversation_history[:-MAX_HISTORY_MESSAGES]
    return _fallback_reply(last_tool_text) if last_tool_text else "I've gone through several steps but couldn't reach a final answer just now, sir."


@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    cfg = config_module.load_config()
    if not cfg.get("ai_enabled"):
        # Properly silent, not a spoken "I'm off" - an off switch that still
        # visibly reacts to you doesn't feel like it's actually off.
        return ChatResponse(reply="", audio="")

    global chat_turn_busy, last_chat_at
    last_chat_at = time.time()
    if chat_turn_busy:
        # The browser dashboard's own speech recognition and the native
        # background listener can both pick up the same spoken utterance at
        # once, each firing its own independent /api/chat call - without this,
        # that produced two full, separately-executed replies (two overlapping
        # voices, and for browser-control requests, duplicated real actions).
        # Only ever run one turn at a time; silently drop whichever second
        # copy of the same utterance shows up while the first is in progress.
        return ChatResponse(reply="", audio="")

    chat_turn_busy = True
    try:
        canvas_before = canvas_state.get_updated_at()
        reply_text = await run_chat_turn(req.message, cfg)

        if cfg.get("code_canvas_enabled") and canvas_state.get_updated_at() == canvas_before:
            embedded_html = _extract_embedded_html(reply_text)
            if embedded_html:
                canvas_state.set_content(embedded_html, canvas_state.get().get("title") or "")
                reply_text = "I've updated the canvas - take a look."

        response = await _speak(reply_text)
        if canvas_state.get_updated_at() != canvas_before:
            canvas = canvas_state.get()
            response.canvas_html = canvas["html"]
            response.canvas_title = canvas.get("title") or None
        return response
    finally:
        chat_turn_busy = False


@app.get("/api/canvas")
async def get_canvas() -> dict:
    return canvas_state.get()


@app.get("/api/3d_model")
async def get_3d_model() -> dict:
    return model3d_state.get()


class ListenerEvent(BaseModel):
    phase: str
    text: str = ""


@app.post("/api/listener/event")
async def report_listener_event(ev: ListenerEvent) -> dict:
    global listener_status
    listener_status = {"seq": listener_status["seq"] + 1, "phase": ev.phase, "text": ev.text}
    return {"ok": True}


@app.get("/api/listener/event")
async def get_listener_event() -> dict:
    return listener_status


class MicReport(BaseModel):
    device: str
    level: float = 0.0
    note: str = ""


mic_status: dict = {"device": "", "level": 0.0, "note": "", "at": 0.0}


@app.post("/api/listener/mic")
async def report_mic(report: MicReport) -> dict:
    global mic_status
    if report.device != mic_status.get("device") and mic_status.get("device"):
        activity.log("note", f"mic switched to {report.device}")
    mic_status = {"device": report.device, "level": report.level, "note": report.note, "at": time.time()}
    return {"ok": True}


@app.get("/api/listener/mic")
async def get_mic() -> dict:
    # "age" lets the dashboard tell a quiet mic from a listener that isn't running.
    return {**mic_status, "age": time.time() - mic_status["at"] if mic_status["at"] else None}


@app.get("/api/activity")
async def get_activity(after: int = 0) -> list[dict]:
    return activity.since(after)


PHONE_MAX_HISTORY = 20

# Ollama runs one request at a time on this model, so any single reply that never stops
# holds everyone else (the next caller, your own voice command) in line behind it - and in
# practice a runaway reply only ended when the 2-minute client timeout killed it. Every
# reply is therefore bounded. A phone reply is a couple of sentences, so its cap is small;
# the dashboard cap is large enough for a whole page of code but still finite.
PHONE_MAX_REPLY_TOKENS = 220
CHAT_MAX_REPLY_TOKENS = 8000
PHONE_BRAIN_TIMEOUT_SECONDS = 30


def _trim_to_last_sentence(text: str) -> str:
    """A reply that hit its length cap is cut back to the last complete sentence."""
    cut = max(text.rfind(". "), text.rfind("! "), text.rfind("? "), text.rfind(".\n"))
    if text.rstrip().endswith((".", "!", "?")):
        return text
    return text[: cut + 1] if cut > 0 else text


async def run_phone_turn(call_sid: str, user_text: str, system_prompt: str, phone_tools: list[dict]) -> str:
    history = telephony.call_histories.setdefault(call_sid, [])
    user_text, said = await asyncio.to_thread(passcode.scrub, user_text)
    turn_context.passcode_said.set(said)
    history.append({"role": "user", "content": user_text})
    messages = [{"role": "system", "content": system_prompt}] + history
    if len(phone_tools) > 12:  # a sir call: narrow the full set the same way as at the desk
        earlier = [m["content"] for m in history[:-1] if m.get("role") == "user" and isinstance(m.get("content"), str)]
        phone_tools = tool_router.select(phone_tools, user_text, earlier[-2:])
    allowed_tool_names = {t["function"]["name"] for t in phone_tools}
    turn_context.user_text.set(user_text)
    turn_context.channel.set("phone")
    activity.log("heard", user_text, "phone")
    turn_started = time.monotonic()

    for _ in range(MAX_TOOL_ITERATIONS):
        resp = await http_pool.client().post(
            OLLAMA_URL,
            json={"model": MODEL_NAME, "messages": messages, "tools": phone_tools, "stream": False, "options": {"num_predict": PHONE_MAX_REPLY_TOKENS}, "keep_alive": -1},
            timeout=PHONE_BRAIN_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        data = resp.json()

        message = data["message"]
        if not message.get("tool_calls"):
            written = _tool_calls_in_text(message.get("content", ""), allowed_tool_names)
            if written:
                message["tool_calls"], message["content"] = written, ""
        if data.get("done_reason") == "length" and not message.get("tool_calls"):
            message["content"] = _trim_to_last_sentence(message.get("content", ""))
        messages.append(message)
        history.append(message)

        tool_calls = message.get("tool_calls")
        if not tool_calls and _looks_like_code(message.get("content", "")):
            messages.append({"role": "user", "content": _NOT_A_TOOL_NUDGE})
            message["content"] = ""
            continue
        if not tool_calls:
            del history[:-PHONE_MAX_HISTORY]
            reply = (message.get("content") or "").strip() or "Sorry, I didn't quite catch that. Could you say it again?"
            activity.log("reply", f"{reply}  ({time.monotonic() - turn_started:.1f}s)", "phone")
            return reply

        results = []
        for call in tool_calls:
            fn = call["function"]
            name = fn["name"]
            args = fn.get("arguments") or {}
            # execute_tool dispatches any tool the server knows about, not only
            # the ones offered on this call - so a caller (or a confused model)
            # naming one that wasn't offered must be refused here.
            activity.log("tool", f"{name} {json.dumps(args, default=str)}", "phone")
            if name not in allowed_tool_names:
                result = "That isn't available during a phone call."
            else:
                result = await tools.execute_tool(name, args)
            activity.log("result", result, "phone")
            results.append(result)
            tool_msg = {"role": "tool", "content": result, "name": name}
            messages.append(tool_msg)
            history.append(tool_msg)

        # Same as on the dashboard: a tool that already produced the answer is said as-is.
        if results and all(isinstance(r, launch_utils.SpokenResult) for r in results):
            reply = " ".join(str(r) for r in results)
            history.append({"role": "assistant", "content": reply})
            activity.log("reply", f"{reply}  ({time.monotonic() - turn_started:.1f}s)", "phone")
            del history[:-PHONE_MAX_HISTORY]
            return reply

    del history[:-PHONE_MAX_HISTORY]
    return "I'm having some trouble completing that thought. Let's pick this up another time, goodbye."


_phrase_audio: "OrderedDict[str, bytes]" = OrderedDict()
MAX_CACHED_PHRASES = 64


async def _speech_audio(text: str) -> bytes:
    """Voice synthesis with a small cache: the fixed phrases on a call (hellos, "didn't catch
    that") are rendered once, ahead of time, so they play instantly. Only audio from the
    custom voice is cached - a fallback-voice render is never kept."""
    cached = _phrase_audio.get(text)
    if cached is not None:
        _phrase_audio.move_to_end(text)
        return cached
    audio = await _voicelab_audio(text)
    if audio:
        if len(text) <= 160:
            _phrase_audio[text] = audio
            while len(_phrase_audio) > MAX_CACHED_PHRASES:
                _phrase_audio.popitem(last=False)
        return audio
    return await _edge_audio(text)


async def _speak_clip(text: str) -> str:
    return telephony.register_audio_clip(await _speech_audio(text))


async def warm_model() -> None:
    """Load the brain into memory ahead of when it's needed - a call about to be placed, or
    one just answered - so the first reply isn't a cold start."""
    try:
        await http_pool.client().post(
            OLLAMA_URL, json={"model": MODEL_NAME, "messages": [], "keep_alive": -1}, timeout=120
        )
    except Exception as exc:  # noqa: BLE001 - purely an optimization
        print(f"[ollama] warm-up failed: {exc}", flush=True)


def _inbound_opening(role: str, caller_name: str | None) -> str:
    if role == "guest":
        return f"Hi {caller_name}, this is Echo, {telephony.OWNER_NICKNAME_TO_OTHERS}'s AI assistant. How can I help you?"
    if caller_name and caller_name.strip().lower() != "sir":
        return f"Hi {caller_name}, this is Echo. How can I help you?"
    return "Hello, sir. How can I help you?"


FIXED_CALL_PHRASES = (
    call_screening.OPENING,
    call_screening.ASK_NAME,
    call_screening.GOODBYE,
    "Sorry, I didn't catch that. Could you say it again?",
    "I haven't heard anything in a while, so I'll let you go. Goodbye.",
    "Sorry, I lost track of this call. Please call back. Goodbye.",
    "I'm sorry, I have to end this call now. Goodbye.",
    "I'm sorry, I can't continue this call. Goodbye.",
)


async def prerender_phone_phrases() -> None:
    """Render every phrase a call can start with ahead of time (at startup, and again when
    the phone book changes), so answering a call never waits on voice synthesis."""
    phrases = list(FIXED_CALL_PHRASES)
    cfg = telephony.load_config()
    if cfg and cfg.get("owner_number"):
        phrases.append(_inbound_opening("owner", phonebook.name_for_number(cfg["owner_number"])))
    for entry in phonebook.load_entries():
        if entry.get("is_sir"):
            phrases.append(_inbound_opening("sir", entry["name"]))
        elif entry.get("can_call_echo"):
            phrases.append(_inbound_opening("guest", entry["name"]))
    for phrase in phrases:
        try:
            await _speech_audio(phrase)
        except Exception:  # noqa: BLE001 - best effort
            pass


@app.post("/api/telephony/voice")
async def telephony_voice(request: Request) -> Response:
    cfg = telephony.load_config()
    form = await request.form()
    call_sid = form.get("CallSid", "")

    if cfg is None:
        return Response(
            content="<?xml version=\"1.0\" encoding=\"UTF-8\"?><Response><Say>Echo calling is not fully configured yet. Goodbye.</Say><Hangup/></Response>",
            media_type="application/xml",
        )

    if not config_module.load_config().get("calling_enabled"):
        return Response(
            content="<?xml version=\"1.0\" encoding=\"UTF-8\"?><Response><Say>Calling is currently turned off. Goodbye.</Say><Hangup/></Response>",
            media_type="application/xml",
        )

    from_number = form.get("From", "")
    policy = phonebook.caller_policy(from_number, cfg.get("owner_number"))
    if (
        policy["role"] == "unknown"
        and config_module.load_config().get("screen_unknown_callers")
        and call_screening.allowed_to_screen(from_number)
    ):
        # Not in the phone book: take a message (who and why), nothing more.
        telephony.call_context[call_sid] = {
            "direction": "inbound",
            "role": "unknown",
            "caller_number": from_number,
            "started_at": time.time(),
            "screen_turns": 0,
            "screen_text": [],
        }
        telephony.call_last_response_at[call_sid] = time.time()
        telephony.trim_call_state()
        activity.log("note", f"screening a call from an unknown number, {from_number}", "phone")
        asyncio.create_task(warm_model())
        clip_id = await _speak_clip(call_screening.OPENING)
        return Response(content=telephony.gather_texml(cfg, clip_id), media_type="application/xml")
    if policy["role"] in ("blocked", "unknown"):
        phonebook.record_attempt(from_number, policy["name"], policy["reason"])
        print(f"[telephony] turned away an inbound call from {from_number}: {policy['reason']}", flush=True)
        if policy["name"]:
            note = f"Sir, {policy['name']} just tried to call me, but their access is switched off, so I turned them away."
        else:
            note = f"Sir, an unknown number, {pager.spoken_number(from_number)}, just tried to call me, and I turned them away."
        asyncio.create_task(pager.page_owner(f"blocked:{from_number}", note))
        return Response(
            content="<?xml version=\"1.0\" encoding=\"UTF-8\"?><Response><Say>Sorry, this line isn't available to you. Goodbye.</Say><Hangup/></Response>",
            media_type="application/xml",
        )

    caller_name = policy["name"] or phonebook.name_for_number(from_number)
    telephony.call_context[call_sid] = {
        "direction": "inbound",
        "role": policy["role"],
        "caller_name": caller_name,
        "caller_number": from_number,
        "started_at": time.time(),
    }
    telephony.call_last_response_at[call_sid] = time.time()
    telephony.trim_call_state()
    if policy["role"] == "guest":
        asyncio.create_task(
            pager.page_owner(f"guest:{caller_name}", f"Sir, {caller_name} is on a call with me right now.")
        )
    asyncio.create_task(warm_model())
    opening = _inbound_opening(policy["role"], caller_name)
    clip_id = await _speak_clip(opening)
    return Response(content=telephony.gather_texml(cfg, clip_id), media_type="application/xml")


@app.post("/api/telephony/outbound_voice")
async def telephony_outbound_voice(request: Request) -> Response:
    cfg = telephony.load_config()
    form = await request.form()
    call_sid = form.get("CallSid", "")
    contact_name = request.query_params.get("contact", "")
    opening_message = request.query_params.get("msg", "")
    greeting = request.query_params.get("greeting", "").strip()

    telephony.call_context[call_sid] = {
        "direction": "outbound",
        "contact_name": contact_name,
        "opening_message": opening_message,
    }
    telephony.call_last_response_at[call_sid] = time.time()
    telephony.trim_call_state()

    dialed_number = form.get("To", "")
    calling_owner = cfg and dialed_number and dialed_number == cfg.get("owner_number")

    opening = telephony.outbound_opening(contact_name, opening_message, greeting, bool(calling_owner))
    # Recorded as something Echo already said, so the model doesn't re-introduce
    # itself again on the first real reply - it otherwise has no way of knowing
    # this opening line already happened.
    telephony.call_histories.setdefault(call_sid, []).append({"role": "assistant", "content": opening})

    # The hello was rendered before the call was placed; only fall back to rendering it
    # now if that clip is missing.
    prepared = request.query_params.get("clip", "")
    clip_id = prepared if prepared and telephony.get_audio_clip(prepared) else await _speak_clip(opening)
    asyncio.create_task(warm_model())
    return Response(content=telephony.gather_texml(cfg, clip_id), media_type="application/xml")


_HANGUP_XML = '<?xml version="1.0" encoding="UTF-8"?><Response><Hangup/></Response>'
HANGUP_WATCH_INTERVAL_SECONDS = 0.5
STATUS_POLL_AFTER_SECONDS = 2.5


async def _watch_for_hangup(request: Request, cfg: dict | None, call_sid: str) -> None:
    """Returns once the caller is known to be gone. Signals, cheapest first: a status
    webhook already said so; the phone network dropped our webhook connection (only a
    hint - confirmed with Telnyx before acting, since a half-closed socket isn't a
    hang-up); or, if a reply is taking a while, a throttled look at Telnyx's call record."""
    started = time.monotonic()
    while True:
        await asyncio.sleep(HANGUP_WATCH_INTERVAL_SECONDS)
        if telephony.call_has_ended(call_sid):
            return
        suspected = False
        try:
            suspected = await request.is_disconnected()
        except Exception:  # noqa: BLE001 - only a hint
            pass
        waited = time.monotonic() - started
        if cfg is not None and (suspected or waited >= STATUS_POLL_AFTER_SECONDS):
            status = await telephony.fetch_call_status(cfg, call_sid, force=suspected)
            if status in telephony.TERMINAL_CALL_STATUSES:
                telephony.mark_call_ended(call_sid)
                return


async def _reply_clip_unless_hung_up(
    request: Request, cfg: dict | None, call_sid: str, speech_text: str, system_prompt: str, phone_tools: list[dict]
) -> str | None:
    """Works out the reply and renders it to audio - and stops dead if the caller hangs up
    first, returning None. Cancelling drops the request to the brain, which aborts the
    generation, so a dead call never keeps anyone else waiting."""

    async def produce() -> str:
        try:
            reply_text = await run_phone_turn(call_sid, speech_text, system_prompt, phone_tools)
        except httpx.TimeoutException:
            print(f"[telephony] the brain took over {PHONE_BRAIN_TIMEOUT_SECONDS}s on a phone turn - apologising instead", flush=True)
            reply_text = "Sorry, I'm taking too long to think about that. Could you ask me again?"
        return await _speak_clip(reply_text)

    work = asyncio.create_task(produce())
    telephony.inflight_work.setdefault(call_sid, set()).add(work)
    watcher = asyncio.create_task(_watch_for_hangup(request, cfg, call_sid))
    try:
        await asyncio.wait({work, watcher}, return_when=asyncio.FIRST_COMPLETED)
        if work.done() and not work.cancelled():
            return work.result()
        work.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await work
        return None
    except asyncio.CancelledError:
        work.cancel()
        raise
    finally:
        watcher.cancel()
        telephony.inflight_work.get(call_sid, set()).discard(work)


@app.post("/api/telephony/gather")
async def telephony_gather(request: Request) -> Response:
    cfg = telephony.load_config()
    form = await request.form()
    call_sid = form.get("CallSid", "")
    speech_text = form.get("SpeechResult", "")

    if telephony.call_has_ended(call_sid):
        telephony.forget_call(call_sid)
        return Response(content=_HANGUP_XML, media_type="application/xml")

    context = telephony.call_context.get(call_sid)
    if context is None:
        # The server restarted mid-call, so who this is was lost. Never assume
        # the owner - end it rather than hand an unknown caller owner access.
        clip_id = await _speak_clip("Sorry, I lost track of this call. Please call back. Goodbye.")
        return Response(content=telephony.say_and_hangup_texml(cfg, clip_id), media_type="application/xml")

    if context.get("role") == "unknown":
        return await _screen_turn(cfg, call_sid, context, speech_text)

    dialed_number = form.get("To", "")
    is_owner = cfg and dialed_number and dialed_number == cfg.get("owner_number")

    # Who's on the line is re-checked every turn, so flipping a phone book switch
    # (call access, sir) takes effect mid-call.
    if context["direction"] == "inbound":
        role = phonebook.caller_policy(context.get("caller_number", ""), cfg.get("owner_number") if cfg else None)["role"]
    elif phonebook.is_sir_number(dialed_number):
        role = "sir"
    else:
        role = "owner" if is_owner else "contact"

    if role == "blocked":
        clip_id = await _speak_clip("I'm sorry, I can't continue this call. Goodbye.")
        return Response(content=telephony.say_and_hangup_texml(cfg, clip_id), media_type="application/xml")
    if role == "sir":
        system_prompt = telephony.PHONE_PERSONA_SIR + "\n\n" + agent.status_block()
        phone_tools = tools.phone_tools_sir(config_module.load_config())
    elif role == "contact":
        system_prompt = telephony.phone_persona_outbound(
            context.get("contact_name"), context.get("opening_message", "")
        )
        phone_tools = tools.PHONE_TOOLS_OUTBOUND
    elif role == "guest":
        # An allowed contact, capped in length.
        if time.time() - context.get("started_at", time.time()) > telephony.GUEST_MAX_CALL_SECONDS:
            clip_id = await _speak_clip("I'm sorry, I have to end this call now. Goodbye.")
            return Response(content=telephony.say_and_hangup_texml(cfg, clip_id), media_type="application/xml")
        system_prompt = telephony.phone_persona_guest(context.get("caller_name") or "the caller")
        phone_tools = tools.PHONE_TOOLS_GUEST
    else:
        system_prompt = telephony.PHONE_PERSONA_INBOUND
        phone_tools = tools.PHONE_TOOLS_INBOUND

    last_response = telephony.call_last_response_at.get(call_sid, time.time())
    silent_for = time.time() - last_response

    if not speech_text:
        if silent_for > telephony.MAX_SILENCE_SECONDS:
            clip_id = await _speak_clip("I haven't heard anything in a while, so I'll let you go. Goodbye.")
            return Response(content=telephony.say_and_hangup_texml(cfg, clip_id), media_type="application/xml")
        clip_id = await _speak_clip("Sorry, I didn't catch that. Could you say it again?")
        return Response(content=telephony.gather_texml(cfg, clip_id), media_type="application/xml")

    telephony.call_last_response_at[call_sid] = time.time()
    clip_id = await _reply_clip_unless_hung_up(request, cfg, call_sid, speech_text, system_prompt, phone_tools)
    if clip_id is None:
        print(f"[telephony] caller hung up mid-reply - stopped generating ({call_sid[:8]})", flush=True)
        telephony.forget_call(call_sid)
        return Response(content=_HANGUP_XML, media_type="application/xml")
    return Response(content=telephony.gather_texml(cfg, clip_id), media_type="application/xml")


async def _screen_turn(cfg: dict | None, call_sid: str, context: dict, speech_text: str) -> Response:
    """One turn of screening an unknown caller: no tools, just "who is this and what's it about"."""
    if speech_text:
        speech_text = passcode.scrub(speech_text)[0]
        context["screen_text"].append(speech_text)
        activity.log("heard", speech_text, "phone")
    context["screen_turns"] += 1
    out_of_time = (
        context["screen_turns"] >= call_screening.MAX_TURNS
        or time.time() - context["started_at"] > call_screening.MAX_SCREEN_SECONDS
    )

    if not context["screen_text"]:
        if out_of_time:
            await _finish_screening(context)
            clip_id = await _speak_clip("I didn't catch anything, so I'll let you go. Goodbye.")
            return Response(content=telephony.say_and_hangup_texml(cfg, clip_id), media_type="application/xml")
        clip_id = await _speak_clip("Sorry, I didn't catch that. Who's calling, and what's it about?")
        return Response(content=telephony.gather_texml(cfg, clip_id), media_type="application/xml")

    context["info"] = await call_screening.extract(" ".join(context["screen_text"]), MODEL_NAME, OLLAMA_URL)
    if not context["info"].get("name") and not out_of_time and not context["info"]["scam"]:
        clip_id = await _speak_clip(call_screening.ASK_NAME)
        return Response(content=telephony.gather_texml(cfg, clip_id), media_type="application/xml")

    await _finish_screening(context)
    clip_id = await _speak_clip(call_screening.GOODBYE)
    return Response(content=telephony.say_and_hangup_texml(cfg, clip_id), media_type="application/xml")


async def _finish_screening(context: dict) -> None:
    """Save the caller (calling off, scam-marked if it sounded like one) and tell sir. Runs once
    per call - at the goodbye, or when the caller hangs up first."""
    if context.get("screen_done"):
        return
    context["screen_done"] = True
    number = context["caller_number"]
    info = context.get("info")
    if info is None and context["screen_text"]:
        info = await call_screening.extract(" ".join(context["screen_text"]), MODEL_NAME, OLLAMA_URL)
    info = info or {"name": None, "reason": None, "scam": False, "scam_why": None}
    name = call_screening.save_caller(number, info)
    reason = info.get("reason") if isinstance(info.get("reason"), str) and info.get("reason") else ""
    # "Confirming an appointment" -> "about confirming an appointment", but "IRS ..." stays "IRS".
    if reason[:2].istitle():
        reason = reason[:1].lower() + reason[1:]
    about = f" about {reason.rstrip('.')}" if reason else ""

    if info["scam"]:
        who = f"a caller who said they were {name}" if name and not name.startswith("Scam caller") else f"an unknown number, {pager.spoken_number(number)},"
        message = f"Sir, {who} rang me{about}, and it sounded like a scam, so I didn't let them in."
        if name:
            message += " I've marked them as a scam in your phone book."
    elif name:
        message = f"Sir, a new caller, {name}, rang me{about}. I added them to your phone book with calling switched off."
    else:
        message = f"Sir, an unknown number, {pager.spoken_number(number)}, called me{about}, but didn't say who they were."

    phonebook.record_attempt(number, name, "screened - suspected scam" if info["scam"] else f"screened{about}")
    activity.log("note", message, "phone")
    print(f"[screening] {message}", flush=True)
    asyncio.create_task(pager.page_owner(f"blocked:{number}", message))


UNANSWERED_STATUSES = {"busy", "no-answer", "failed", "canceled"}
NOT_HUMAN_ANSWERED_BY = {"machine", "fax", "unknown"}
notified_call_sids: set[str] = set()


@app.post("/api/telephony/status")
async def telephony_status(request: Request) -> Response:
    form = await request.form()
    call_sid = form.get("CallSid", "")
    call_status = form.get("CallStatus", "")
    answered_by = form.get("AnsweredBy", "")
    contact = request.query_params.get("contact", "")

    if call_status.lower() in telephony.TERMINAL_CALL_STATUSES:
        screened = telephony.call_context.get(call_sid)
        if screened and screened.get("role") == "unknown" and not screened.get("screen_done"):
            # They hung up before the goodbye - still save what they said and tell sir.
            asyncio.create_task(_finish_screening(screened))
        # The other party is gone: stop anything still being generated for them.
        telephony.mark_call_ended(call_sid)
        if not telephony.inflight_work.get(call_sid):
            telephony.forget_call(call_sid)

    if call_status in ("completed", *UNANSWERED_STATUSES):
        stale_keys = [k for k, v in telephony.active_outbound_calls.items() if v == call_sid]
        for k in stale_keys:
            del telephony.active_outbound_calls[k]

    unanswered = call_status in UNANSWERED_STATUSES or answered_by in NOT_HUMAN_ANSWERED_BY
    notifications_on = config_module.load_config().get("call_notifications_enabled", False)

    if unanswered and notifications_on and call_sid and call_sid not in notified_call_sids:
        notified_call_sids.add(call_sid)
        who = contact or form.get("To", "the number")
        reason = "went to voicemail" if answered_by in NOT_HUMAN_ANSWERED_BY else f"wasn't answered ({call_status})"
        note = f"Tried calling {who} on sir's behalf, but it {reason}."
        memory.add_fact(note)

        cfg = telephony.load_config()
        if cfg:
            message = f"Sir, I tried calling {who} for you, but {reason.replace('sir', 'you')}."
            try:
                await telephony.notify_owner(cfg, message)
            except Exception as exc:  # noqa: BLE001 - never let a notification failure break the webhook
                print(f"[status] failed to notify owner: {exc}")

    return Response(content="", status_code=200)


@app.post("/api/telephony/notify_voice")
async def telephony_notify_voice(request: Request) -> Response:
    cfg = telephony.load_config()
    message = request.query_params.get("msg", "I have an update for you, sir.")
    clip_id = await _speak_clip(message)
    return Response(content=telephony.say_and_hangup_texml(cfg, clip_id), media_type="application/xml")


@app.get("/api/telephony/audio/{clip_id}")
async def telephony_audio(clip_id: str) -> Response:
    audio_bytes = telephony.get_audio_clip(clip_id)
    if audio_bytes is None:
        raise HTTPException(status_code=404, detail="Clip not found")
    return Response(content=audio_bytes, media_type="audio/mpeg")


class PhonebookEntry(BaseModel):
    name: str
    number: str


@app.get("/api/phonebook")
async def get_phonebook() -> list[dict]:
    return phonebook.load_entries()


@app.post("/api/phonebook")
async def add_phonebook_entry(entry: PhonebookEntry) -> list[dict]:
    result = phonebook.add_entry(entry.name, entry.number)
    asyncio.create_task(prerender_phone_phrases())
    return result


@app.delete("/api/phonebook/{name}")
async def delete_phonebook_entry(name: str) -> list[dict]:
    return phonebook.delete_entry(name)


class CallAccessUpdate(BaseModel):
    allowed: bool


@app.post("/api/phonebook/{name}/call_access")
async def set_phonebook_call_access(name: str, update: CallAccessUpdate) -> list[dict]:
    entries = phonebook.set_call_access(name, update.allowed)
    if entries is None:
        raise HTTPException(status_code=404, detail=f"No contact named '{name}'.")
    asyncio.create_task(prerender_phone_phrases())
    return entries


@app.get("/api/setup")
async def get_setup() -> list[dict]:
    import setup_check
    return await asyncio.to_thread(setup_check.checklist)


@app.post("/api/setup/fix/{fix}")
async def run_setup_fix(fix: str) -> dict:
    import setup_check
    if fix not in ("sandbox", "adb", "brain", "vision"):
        raise HTTPException(status_code=404, detail="Nothing to fix by that name.")
    return {"status": setup_check.start_fix(fix)}


class HearingWord(BaseModel):
    word: str


@app.get("/api/hearing_words")
async def get_hearing_words() -> dict:
    return {"custom": hearing_words.custom_words(), "all": hearing_words.all_words()}


@app.post("/api/hearing_words")
async def add_hearing_word(entry: HearingWord) -> dict:
    if not entry.word.strip():
        raise HTTPException(status_code=400, detail="Type a word or name.")
    hearing_words.add_custom(entry.word)
    return await get_hearing_words()


@app.delete("/api/hearing_words/{word}")
async def delete_hearing_word(word: str) -> dict:
    hearing_words.remove_custom(word)
    return await get_hearing_words()


class AddressEntry(BaseModel):
    name: str
    address: str


@app.get("/api/addresses")
async def get_addresses() -> list[dict]:
    from plugins import address_book
    return address_book.load()


@app.post("/api/addresses")
async def add_address(entry: AddressEntry) -> list[dict]:
    from plugins import address_book
    if not entry.name.strip() or not entry.address.strip():
        raise HTTPException(status_code=400, detail="A place needs a name and an address.")
    return address_book.add(entry.name, entry.address)


@app.delete("/api/addresses/{name}")
async def delete_address(name: str) -> list[dict]:
    from plugins import address_book
    return address_book.remove(name)


# --- projects and approvals -------------------------------------------------------

def _project_view(p: dict, full: bool = False) -> dict:
    view = {k: p.get(k) for k in ("id", "title", "goal", "status", "steps", "preview", "summary", "created_at", "slug")}
    view["log"] = p.get("log", [])[-(200 if full else 8):]
    view["notes"] = p.get("notes", [])[-20:] if full else []
    view["files"] = sandbox.list_files(p["slug"]) if full else []
    live = approvals.approved(p["id"])
    view["approval_hours_left"] = round(approvals.time_left(live) / 3600, 1) if live else 0
    return view


@app.get("/api/projects")
async def list_projects_api() -> list[dict]:
    return [_project_view(p) for p in reversed(agent.all_projects())]


@app.get("/api/projects/{project_id}")
async def get_project_api(project_id: str) -> dict:
    project = agent.get(project_id)
    if project is None or project["id"] != project_id:
        raise HTTPException(status_code=404, detail="No such project.")
    return _project_view(project, full=True)


@app.post("/api/projects/{project_id}/stop")
async def stop_project_api(project_id: str) -> dict:
    project = agent.get(project_id)
    if project is None or project["id"] != project_id:
        raise HTTPException(status_code=404, detail="No such project.")
    agent.stop(project, "stopped from the dashboard")
    return _project_view(project)


@app.get("/api/approvals")
async def list_approvals_api() -> list[dict]:
    return list(reversed(approvals.all_records()[-30:]))


class ApprovalDecision(BaseModel):
    approve: bool


@app.post("/api/approvals/{record_id}")
async def decide_approval_api(record_id: str, decision: ApprovalDecision) -> dict:
    record = approvals.decide(record_id, decision.approve, via="dashboard")
    if record is None:
        raise HTTPException(status_code=404, detail="That request isn't waiting any more.")
    if decision.approve and record.get("project"):
        agent.resume_if_approved(record["project"])
    activity.log("note", f"{'approved' if decision.approve else 'declined'} on the dashboard: {record['what']}")
    return record


# What Echo builds is shown in a locked-down frame: the files are served with a sandbox
# policy, so a page Echo made can run its own scripts but can't reach the dashboard,
# Echo's API, cookies, or anything else on this machine's origin.
SANDBOXED_PAGE_HEADERS = {
    "Content-Security-Policy": "sandbox allow-scripts allow-modals allow-pointer-lock",
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
}


@app.get("/sandbox/{slug}/{path:path}")
async def sandbox_file(slug: str, path: str) -> Response:
    from fastapi.responses import FileResponse
    try:
        target = sandbox.safe_path(slug, path or "index.html")
    except ValueError:
        raise HTTPException(status_code=404)
    if not target.is_file() or target.name.startswith("_run_output"):
        raise HTTPException(status_code=404)
    return FileResponse(target, headers=SANDBOXED_PAGE_HEADERS)


class ScamUpdate(BaseModel):
    scam: bool


@app.post("/api/phonebook/{name}/scam")
async def set_phonebook_scam(name: str, update: ScamUpdate) -> list[dict]:
    entries = phonebook.set_scam(name, update.scam)
    if entries is None:
        raise HTTPException(status_code=404, detail=f"No contact named '{name}'.")
    return entries


class SirUpdate(BaseModel):
    is_sir: bool


@app.post("/api/phonebook/{name}/sir")
async def set_phonebook_sir(name: str, update: SirUpdate) -> list[dict]:
    entries = phonebook.set_sir(name, update.is_sir)
    if entries is None:
        raise HTTPException(status_code=404, detail=f"No contact named '{name}'.")
    asyncio.create_task(prerender_phone_phrases())
    return entries


class ShutdownPhrase(BaseModel):
    phrase: str


@app.get("/api/pc_power/phrase")
async def get_shutdown_phrase_status() -> dict:
    # Only whether one is set - the phrase itself never leaves the server.
    return {"set": passcode.is_set()}


@app.post("/api/pc_power/phrase")
async def set_shutdown_phrase(update: ShutdownPhrase) -> dict:
    if len(launch_utils.norm(update.phrase).replace(" ", "")) < 6:
        raise HTTPException(status_code=400, detail="Use a passcode of at least 6 letters - a couple of words works well.")
    passcode.set_passcode(update.phrase)
    return {"set": True}


@app.get("/api/phonebook/blocked_calls")
async def get_blocked_calls() -> list[dict]:
    return phonebook.recent_attempts()


@app.post("/api/upload", response_model=ChatResponse)
async def upload(file: UploadFile = File(...)) -> ChatResponse:
    data = await file.read()
    content_type = file.content_type or ""

    if content_type.startswith("image/"):
        description = await vision.describe_image(data)
    elif content_type.startswith("video/"):
        suffix = Path(file.filename or "upload.mp4").suffix
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        try:
            description = await vision.summarize_video(tmp_path)
        finally:
            os.unlink(tmp_path)
    else:
        return await _speak("I can only look at images or videos, sir.")

    conversation_history.append({"role": "user", "content": f"[Uploaded a {content_type} file for you to look at.]"})
    conversation_history.append({"role": "assistant", "content": description})
    del conversation_history[:-MAX_HISTORY_MESSAGES]

    return await _speak(description)


@app.post("/api/security/enroll")
async def enroll_face() -> dict:
    return await asyncio.to_thread(security.enroll)


@app.get("/api/security/status")
async def security_status() -> dict:
    return {"known_faces": security.known_face_count()}


class LocationUpdate(BaseModel):
    latitude: float
    longitude: float
    label: str | None = None


@app.post("/api/location")
async def report_location(update: LocationUpdate) -> dict:
    cfg = config_module.load_config()
    if cfg.get("location_mode", "off") == "off":
        raise HTTPException(status_code=403, detail="Location access is currently off.")
    location.set_location(update.latitude, update.longitude, update.label)
    return {"ok": True}


class BrowserResultReport(BaseModel):
    ok: bool
    data: dict | None = None
    error: str | None = None


@app.get("/api/browser/poll")
async def browser_poll() -> dict:
    """Long-polled continuously by the Echo browser extension's background
    script. Blocks until a tool call queues an action, or returns empty after
    a while so the extension can simply poll again."""
    command = await browser_control.wait_for_command()
    return {"command": command}


@app.post("/api/browser/report")
async def browser_report(report: BrowserResultReport) -> dict:
    """Called by the extension once it's finished (or failed) executing the
    action it was just given."""
    browser_control.submit_result(report.model_dump())
    return {"ok": True}


@app.get("/api/settings")
async def get_settings() -> dict:
    return config_module.load_config()


def _phone_call_active() -> bool:
    """True while a call is being worked on or was spoken to within the last 45 seconds."""
    if any(telephony.inflight_work.values()):
        return True
    now = time.time()
    return any(
        now - last < 45
        for sid, last in telephony.call_last_response_at.items()
        if not telephony.call_has_ended(sid)
    )


@app.get("/api/ollama/status")
async def get_ollama_status() -> dict:
    cfg = config_module.load_config()
    return {
        "restarting": ollama_control.status["restarting"],
        "error": ollama_control.status["error"],
        "slots": await asyncio.to_thread(ollama_control.current_slots),
        "wanted": ollama_control.SLOTS_WHEN_ON if cfg.get("ollama_parallel_2_enabled") else 1,
    }


ACCESS_KEYS_AFFECTING_HISTORY = ("screen_access", "camera_access", "calling_enabled", "browser_control_enabled", "code_canvas_enabled")
VALID_LOCATION_MODES = {"off", "pc", "phone"}


@app.post("/api/settings")
async def set_settings(update: SettingsUpdate) -> dict:
    cfg = config_module.load_config()
    changes = update.model_dump(exclude_none=True)

    if changes.get("desk_guard_enabled") and security.known_face_count() == 0:
        raise HTTPException(status_code=400, detail="Enroll at least one face before enabling Desk Guard.")

    if "location_mode" in changes and changes["location_mode"] not in VALID_LOCATION_MODES:
        raise HTTPException(status_code=400, detail="location_mode must be one of: off, pc, phone.")

    if changes.get("browser_pixel_fallback_enabled") and not cfg.get("browser_control_enabled") and not changes.get("browser_control_enabled"):
        raise HTTPException(status_code=400, detail="Turn on browser control before enabling its pixel fallback.")

    restart_ollama_with = None
    if "ollama_parallel_2_enabled" in changes:
        wanted = ollama_control.SLOTS_WHEN_ON if changes["ollama_parallel_2_enabled"] else 1
        if wanted != ollama_control.current_slots():
            if ollama_control.status["restarting"]:
                raise HTTPException(status_code=409, detail="Ollama is already restarting - wait for it to finish.")
            if _phone_call_active():
                raise HTTPException(
                    status_code=409,
                    detail="A phone call is in progress - restarting Ollama now would cut it off. Try again when it ends.",
                )
            restart_ollama_with = wanted

    access_changed = any(cfg.get(k) != changes[k] for k in ACCESS_KEYS_AFFECTING_HISTORY if k in changes)
    turning_off = changes.get("ai_enabled") is False and cfg.get("ai_enabled") is not False

    cfg.update(changes)
    if "ollama_parallel_2_enabled" in changes:
        cfg["ollama_parallel_previous"] = ollama_control.persist_user_env(
            bool(changes["ollama_parallel_2_enabled"]), cfg.get("ollama_parallel_previous")
        )
    config_module.save_config(cfg)

    if restart_ollama_with is not None:
        asyncio.create_task(ollama_control.restart(restart_ollama_with, cfg, warm_model))

    if access_changed:
        # Old turns discussing "access is off" would otherwise bias the model into
        # contradicting the new state - a fresh state deserves a fresh conversation.
        conversation_history.clear()

    if turning_off:
        await unload_ollama_models()
    elif not cfg.get("screen_access") and not cfg.get("camera_access"):
        # The vision model is shared by both screen and camera tools and was
        # kept loaded indefinitely for speed - only safe to drop it once
        # *neither* toggle needs it anymore, otherwise the other one breaks.
        await unload_model(vision.VISION_MODEL)

    return cfg


@app.get("/api/plugins")
async def list_plugins() -> list[dict]:
    cfg = config_module.load_config()
    result = []
    for meta in plugin_loader.plugin_metadata():
        enabled = True if meta["always_on"] else bool(cfg.get(meta["config_key"]))
        result.append({**meta, "enabled": enabled})
    return result


class PluginToggle(BaseModel):
    enabled: bool


@app.post("/api/plugins/{plugin_name}/toggle")
async def toggle_plugin(plugin_name: str, update: PluginToggle) -> dict:
    meta = next((m for m in plugin_loader.plugin_metadata() if m["name"] == plugin_name), None)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"No plugin named '{plugin_name}'.")
    if meta["always_on"]:
        raise HTTPException(status_code=400, detail=f"'{plugin_name}' has no toggle - it's always on.")

    cfg = config_module.load_config()
    cfg[meta["config_key"]] = update.enabled
    config_module.save_config(cfg)
    if not update.enabled:
        plugin_loader.call_on_disable(plugin_name)
    # Same reasoning as ACCESS_KEYS_AFFECTING_HISTORY above - stale context
    # about a plugin's old on/off state shouldn't bias the next reply.
    conversation_history.clear()
    return {"ok": True, "name": plugin_name, "enabled": update.enabled}


@app.get("/api/connections")
async def list_connections() -> list[dict]:
    return [
        {
            "name": "google",
            "label": "Google (Gmail / Calendar / Drive)",
            "auth_style": "oauth",
            "configured": google_auth.is_configured(),
            "connected": google_auth.is_connected(),
        },
        {
            "name": "github",
            "label": "GitHub",
            "auth_style": "oauth",
            "configured": github_auth.is_configured(),
            "connected": github_auth.is_connected(),
        },
        {
            "name": "spotify",
            "label": "Spotify",
            "auth_style": "oauth",
            "configured": spotify_auth.is_configured(),
            "connected": spotify_auth.is_connected(),
        },
        {
            "name": "discord",
            "label": "Discord",
            "auth_style": "token",
            "configured": True,
            "connected": discord_auth.is_connected(),
            "invite_url": discord_auth.build_invite_url(),
        },
        {
            "name": "notion",
            "label": "Notion",
            "auth_style": "token",
            "configured": True,
            "connected": notion_auth.is_connected(),
        },
    ]


class OAuthClientCredentials(BaseModel):
    client_id: str
    client_secret: str


@app.post("/api/connections/google/credentials")
async def google_save_credentials(update: OAuthClientCredentials) -> dict:
    if not update.client_id.strip() or not update.client_secret.strip():
        raise HTTPException(status_code=400, detail="Both the Client ID and Client secret are required.")
    google_auth.save_client_config(update.client_id.strip(), update.client_secret.strip())
    return {"ok": True}


@app.post("/api/connections/github/credentials")
async def github_save_credentials(update: OAuthClientCredentials) -> dict:
    if not update.client_id.strip() or not update.client_secret.strip():
        raise HTTPException(status_code=400, detail="Both the Client ID and Client secret are required.")
    github_auth.save_client_config(update.client_id.strip(), update.client_secret.strip())
    return {"ok": True}


@app.post("/api/connections/spotify/credentials")
async def spotify_save_credentials(update: OAuthClientCredentials) -> dict:
    if not update.client_id.strip() or not update.client_secret.strip():
        raise HTTPException(status_code=400, detail="Both the Client ID and Client secret are required.")
    problem = await spotify_auth.check_credentials(update.client_id.strip(), update.client_secret.strip())
    if problem == spotify_auth.UNREACHABLE:
        raise HTTPException(status_code=502, detail="Couldn't reach Spotify to check those - check your internet connection and try again.")
    if problem:
        raise HTTPException(status_code=400, detail=problem)
    spotify_auth.save_client_config(update.client_id.strip(), update.client_secret.strip())
    return {"ok": True}


@app.get("/api/connections/spotify/start")
async def spotify_connect_start() -> Response:
    from fastapi.responses import RedirectResponse
    url = spotify_auth.build_auth_url()
    if url is None:
        raise HTTPException(
            status_code=400,
            detail="Spotify isn't configured yet - see backend/spotify_oauth_config.example.json.",
        )
    return RedirectResponse(url)


@app.get("/api/connections/spotify/callback")
async def spotify_connect_callback(request: Request) -> Response:
    from fastapi.responses import RedirectResponse
    code = request.query_params.get("code")
    error = request.query_params.get("error")
    if error:
        return RedirectResponse(f"/?spotify_connect=error&detail={error}")
    if not code:
        return RedirectResponse("/?spotify_connect=error&detail=no_code")
    try:
        await spotify_auth.exchange_code(code)
    except Exception as exc:  # noqa: BLE001 - surface to the dashboard, not a raw 500
        return RedirectResponse(f"/?spotify_connect=error&detail={exc}")
    return RedirectResponse("/?spotify_connect=success")


@app.post("/api/connections/spotify/disconnect")
async def spotify_disconnect() -> dict:
    spotify_auth.disconnect()
    return {"ok": True}


@app.get("/api/connections/google/start")
async def google_connect_start() -> Response:
    from fastapi.responses import RedirectResponse
    url = google_auth.build_auth_url()
    if url is None:
        raise HTTPException(
            status_code=400,
            detail="Google OAuth isn't configured yet - see backend/google_oauth_config.example.json.",
        )
    return RedirectResponse(url)


@app.get("/api/connections/google/callback")
async def google_connect_callback(request: Request) -> Response:
    from fastapi.responses import RedirectResponse
    code = request.query_params.get("code")
    error = request.query_params.get("error")
    if error:
        return RedirectResponse(f"/?google_connect=error&detail={error}")
    if not code:
        return RedirectResponse("/?google_connect=error&detail=no_code")
    try:
        await google_auth.exchange_code(code)
    except Exception as exc:  # noqa: BLE001 - surface to the dashboard, not a raw 500
        return RedirectResponse(f"/?google_connect=error&detail={exc}")
    return RedirectResponse("/?google_connect=success")


@app.post("/api/connections/google/disconnect")
async def google_disconnect() -> dict:
    google_auth.disconnect()
    return {"ok": True}


@app.get("/api/connections/github/start")
async def github_connect_start() -> Response:
    from fastapi.responses import RedirectResponse
    url = github_auth.build_auth_url()
    if url is None:
        raise HTTPException(
            status_code=400,
            detail="GitHub OAuth isn't configured yet - see backend/github_oauth_config.example.json.",
        )
    return RedirectResponse(url)


@app.get("/api/connections/github/callback")
async def github_connect_callback(request: Request) -> Response:
    from fastapi.responses import RedirectResponse
    code = request.query_params.get("code")
    error = request.query_params.get("error")
    if error:
        return RedirectResponse(f"/?github_connect=error&detail={error}")
    if not code:
        return RedirectResponse("/?github_connect=error&detail=no_code")
    try:
        await github_auth.exchange_code(code)
    except Exception as exc:  # noqa: BLE001 - surface to the dashboard, not a raw 500
        return RedirectResponse(f"/?github_connect=error&detail={exc}")
    return RedirectResponse("/?github_connect=success")


@app.post("/api/connections/github/disconnect")
async def github_disconnect() -> dict:
    github_auth.disconnect()
    return {"ok": True}


class DiscordTokenSubmit(BaseModel):
    bot_token: str
    client_id: str | None = None


@app.post("/api/connections/discord/token")
async def discord_save_token(update: DiscordTokenSubmit) -> dict:
    if not update.bot_token.strip():
        raise HTTPException(status_code=400, detail="Bot token can't be empty.")
    discord_auth.save_token(update.bot_token.strip(), update.client_id.strip() if update.client_id else None)
    return {"ok": True}


@app.post("/api/connections/discord/disconnect")
async def discord_disconnect() -> dict:
    discord_auth.disconnect()
    return {"ok": True}


class NotionTokenSubmit(BaseModel):
    integration_secret: str


@app.post("/api/connections/notion/token")
async def notion_save_token(update: NotionTokenSubmit) -> dict:
    if not update.integration_secret.strip():
        raise HTTPException(status_code=400, detail="Integration secret can't be empty.")
    notion_auth.save_token(update.integration_secret.strip())
    return {"ok": True}


@app.post("/api/connections/notion/disconnect")
async def notion_disconnect() -> dict:
    notion_auth.disconnect()
    return {"ok": True}


GENERATED_3D_MODELS_DIR = Path(__file__).resolve().parent / "plugins_data" / "generated_3d_models"
GENERATED_3D_MODELS_DIR.mkdir(parents=True, exist_ok=True)


# --- the Camera section, usage log, project code ------------------------------------

@app.get("/api/usage")
async def get_usage() -> list[dict]:
    import usage
    return usage.recent()


def _camera_allowed() -> None:
    if not config_module.load_config().get("camera_access"):
        raise HTTPException(status_code=403, detail="Camera access is switched off - turn on 'See the camera' in Switches.")


@app.get("/api/camera/status")
async def camera_status() -> dict:
    cfg = config_module.load_config()
    return {"access": bool(cfg.get("camera_access")), "known_faces": security.known_face_count(),
            "photos": security.recent_reference_photos()}


@app.get("/api/camera/ref/{name}")
async def camera_reference_photo(name: str) -> Response:
    from fastapi.responses import FileResponse
    if not re.fullmatch(r"[\w.]+\.jpg", name):
        raise HTTPException(status_code=404)
    path = security.KNOWN_FACES_DIR / name
    if not path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(path, headers={"Cache-Control": "no-store"})


@app.post("/api/camera/remember")
async def camera_remember() -> dict:
    _camera_allowed()
    return await asyncio.to_thread(security.remember_me)


class CameraLook(BaseModel):
    labels: bool = True
    describe: bool = True


_GROUNDING_PROMPT = (
    "Find every distinct thing you can see in this image - people, objects, furniture, devices, pets. "
    'Reply with JSON only: {"objects": [{"label": "short name", "bbox_2d": [x1, y1, x2, y2]}]} '
    "using pixel coordinates of this image."
)


@app.post("/api/camera/look")
async def camera_look(req: CameraLook) -> dict:
    """A frame from the camera with what Echo makes of it: faces (and whether one is sir),
    labelled boxes around the things in view, and a short description."""
    _camera_allowed()
    import cv2

    frame = await asyncio.to_thread(security.capture_frame)
    if frame is None:
        raise HTTPException(status_code=503, detail="The camera didn't give a picture - it may be busy. Try again.")
    # One known size, so the vision model's coordinates map straight back onto the picture.
    h, w = frame.shape[:2]
    scale = 640 / w
    frame = cv2.resize(frame, (640, int(h * scale)))
    h, w = frame.shape[:2]
    ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    image = jpg.tobytes()
    result = {"image": "data:image/jpeg;base64," + base64.b64encode(image).decode("ascii"), "width": w, "height": h,
              "faces": [], "objects": [], "description": ""}
    threshold = config_module.load_config().get("desk_guard_threshold", 0.65)
    try:
        result["faces"] = await asyncio.to_thread(security.faces_in, frame, threshold)
    except Exception as exc:  # noqa: BLE001 - face recognition is optional
        print(f"[camera] face check failed: {exc!r}", flush=True)
    jobs = []
    if req.labels:
        jobs.append(_ground_objects(image, w, h))
    if req.describe:
        jobs.append(vision.describe_image(image, "Describe what this webcam sees in two sentences."))
    done = await asyncio.gather(*jobs, return_exceptions=True)
    if req.labels and not isinstance(done[0], Exception):
        result["objects"] = done[0]
    if req.describe and not isinstance(done[-1], Exception):
        result["description"] = done[-1]
    return result


async def _ground_objects(image: bytes, w: int, h: int) -> list[dict]:
    resp = await http_pool.client().post(OLLAMA_URL, json={
        "model": vision.VISION_MODEL, "stream": False, "format": "json", "keep_alive": "10m",
        "options": {"temperature": 0, "num_predict": 600},
        "messages": [{"role": "user", "content": _GROUNDING_PROMPT, "images": [base64.b64encode(image).decode("ascii")]}],
    }, timeout=120)
    resp.raise_for_status()
    try:
        data = json.loads(resp.json()["message"]["content"])
    except (json.JSONDecodeError, KeyError):
        return []
    items = data.get("objects", data) if isinstance(data, dict) else data
    out = []
    for item in items if isinstance(items, list) else []:
        box = item.get("bbox_2d") or item.get("box") if isinstance(item, dict) else None
        if not (isinstance(box, list) and len(box) == 4 and all(isinstance(v, (int, float)) for v in box)):
            continue
        x1, y1, x2, y2 = (max(0, min(int(v), lim)) for v, lim in zip(box, (w, h, w, h)))
        if x2 - x1 < 6 or y2 - y1 < 6:
            continue
        out.append({"label": str(item.get("label", "thing"))[:40], "box": [x1, y1, x2, y2]})
    return out[:25]


@app.get("/api/projects/{project_id}/file")
async def project_file(project_id: str, path: str) -> dict:
    """A project's file as text, for the dashboard's Code view."""
    project = agent.get(project_id)
    if project is None or project["id"] != project_id:
        raise HTTPException(status_code=404, detail="No such project.")
    try:
        target = sandbox.safe_path(project["slug"], path)
    except ValueError:
        raise HTTPException(status_code=404)
    if not target.is_file():
        raise HTTPException(status_code=404)
    return {"path": path, "text": target.read_text(encoding="utf-8", errors="replace")[:200_000]}

# --- the new dashboard: live status, appearance, galleries ------------------------

APPEARANCE_FILE = Path(__file__).resolve().parent / "appearance.json"
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
# Short plain names (a theme like "Midnight Gold", a season like "christmas-2026") - letters,
# digits, spaces, dashes and underscores only, so nothing stored here can carry markup.
_PLAIN_NAME = re.compile(r"^[A-Za-z0-9 _-]{1,40}$")


@app.get("/api/appearance")
async def get_appearance() -> dict:
    try:
        return json.loads(APPEARANCE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


@app.post("/api/appearance")
async def save_appearance(request: Request) -> dict:
    """Colors and orb settings for the dashboard. Only plain values are kept: #rrggbb colors,
    numbers, true/false and short plain names - nothing that could inject anything into the page."""
    body = await request.json()
    saved = await get_appearance()

    def clean(value):
        if isinstance(value, str) and (_HEX.match(value) or _PLAIN_NAME.match(value)):
            return value
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and -1000 <= value <= 1000:
            return value
        if isinstance(value, dict):
            return {str(k)[:60]: clean(v) for k, v in value.items() if clean(v) is not None}
        return None

    for key, value in (body or {}).items():
        if value is None:
            saved.pop(str(key), None)
            continue
        cleaned = clean(value)
        if cleaned is not None:
            saved[str(key)[:60]] = cleaned
    APPEARANCE_FILE.write_text(json.dumps(saved, indent=2), encoding="utf-8")
    return saved


# Seasonal mode: the song each season may play. The page can only name a season - never a song
# of its own - so this can't be used to play or open anything else.
SEASON_SONGS = {
    "christmas": "All I Want for Christmas Is You Mariah Carey",
    "halloween": "Thriller Michael Jackson",
    "newyear": "Auld Lang Syne",
    "valentines": "Can't Help Falling in Love Elvis Presley",
}


def _spotify_ready() -> bool:
    return bool(config_module.load_config().get("spotify_control_enabled")) and spotify_auth.is_connected()


@app.get("/api/seasonal/music")
async def seasonal_music_ready() -> dict:
    return {"spotify": _spotify_ready()}


@app.post("/api/seasonal/music")
async def seasonal_music(request: Request) -> dict:
    season = str((await request.json()).get("season", ""))
    song = SEASON_SONGS.get(season)
    if not song:
        return {"played": False, "said": "No song for that season."}
    if not _spotify_ready():
        return {"played": False, "said": "Spotify isn't connected - connect it on the Connections page to add music."}
    import usage
    from plugins import spotify_control

    said = str(await spotify_control.spotify_play({"query": song, "kind": "track"}))
    usage.record("spotify_play", {"query": song, "why": f"{season} mode"}, said, "dashboard")
    activity.log("note", f"{season} mode: {said}")
    return {"played": said.lower().startswith("playing"), "said": said}


GENERATED_IMAGES_DIR = Path(__file__).resolve().parent / "plugins_data" / "generated_images"


@app.get("/api/images")
async def list_images() -> list[dict]:
    if not GENERATED_IMAGES_DIR.exists():
        return []
    items = []
    for png in sorted(GENERATED_IMAGES_DIR.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)[:80]:
        prompt_file = png.with_name(png.stem + "_prompt.txt")
        prompt = prompt_file.read_text(encoding="utf-8", errors="ignore")[:300] if prompt_file.exists() else ""
        items.append({"file": png.name, "url": f"/generated_images/{png.name}", "prompt": prompt, "at": png.stat().st_mtime})
    return items


@app.get("/api/models3d")
async def list_models3d() -> list[dict]:
    folder = Path(GENERATED_3D_MODELS_DIR)
    if not folder.exists():
        return []
    return [{"file": f.name, "url": f"/3d_models/{f.name}", "at": f.stat().st_mtime}
            for f in sorted(folder.glob("*.glb"), key=lambda p: p.stat().st_mtime, reverse=True)[:60]]


_live_cache: dict[str, tuple[float, object]] = {}


async def _cached(key: str, seconds: float, fetch):
    hit = _live_cache.get(key)
    if hit and time.time() - hit[0] < seconds:
        return hit[1]
    try:
        value = await fetch()
    except Exception:  # noqa: BLE001 - a status line is never worth an error
        value = None
    _live_cache[key] = (time.time(), value)
    return value


async def _spotify_now() -> dict | None:
    token = await spotify_auth.get_valid_token()
    if not token:
        return None
    resp = await http_pool.client().get("https://api.spotify.com/v1/me/player",
                                        headers={"Authorization": f"Bearer {token}"}, timeout=4)
    if resp.status_code != 200 or not resp.content:
        return {"playing": False, "track": "", "artist": ""}
    data = resp.json()
    item = data.get("item") or {}
    return {"playing": bool(data.get("is_playing")), "track": item.get("name", ""),
            "artist": ", ".join(a.get("name", "") for a in item.get("artists", [])),
            "device": (data.get("device") or {}).get("name", "")}


async def _tv_now() -> dict | None:
    from plugins import tv_control

    def look():
        cast = tv_control._get_cast()
        if cast is None or not cast.status:
            return None
        mc = cast.media_controller
        st = mc.status
        return {"name": cast.cast_info.friendly_name, "app": cast.status.display_name or "home screen",
                "title": (st.title if st and st.title else ""), "state": (st.player_state if st else "")}

    return await asyncio.wait_for(asyncio.to_thread(look), timeout=6)


async def _pc_now() -> dict | None:
    from plugins import pc_stats

    def read():
        g = pc_stats._gpu()
        mem = pc_stats._memory()
        low = [d["drive"] for d in pc_stats._drives() if d["total_gb"] >= 32 and (d["free_gb"] < 50 or d["percent_used"] > 90)]
        return {"cpu": pc_stats._cpu_percent(), "ram_used": mem["used_gb"], "ram_total": mem["total_gb"], "low_drives": low,
                "gpu": g and {"temp": g["temp_c"], "used_gb": g["vram_used_gb"], "total_gb": g["vram_total_gb"],
                              "busy": g["busy_percent"], "power": g["power_w"]}}

    return await asyncio.to_thread(read)


def _last_request() -> dict | None:
    events = list(activity._events)
    for i in range(len(events) - 1, -1, -1):
        if events[i]["kind"] == "heard":
            after = events[i + 1:]
            reply = next((e["text"] for e in after if e["kind"] == "reply"), "")
            tools_used = [e["text"].split(" ")[0] for e in after if e["kind"] == "tool"]
            return {"heard": events[i]["text"], "reply": reply, "tools": tools_used[:4],
                    "channel": events[i]["channel"], "at": events[i]["at"]}
    return None


@app.get("/api/live")
async def live_status() -> dict:
    """Everything the dashboard's right-hand Live panel shows, in one call."""
    cfg = config_module.load_config()
    mic_age = time.time() - mic_status["at"] if mic_status.get("at") else None
    spotify = None
    if cfg.get("spotify_control_enabled") and spotify_auth.is_connected():
        spotify = await _cached("spotify", 5, _spotify_now)
    tv = None
    if cfg.get("tv_control_enabled"):
        tv = await _cached("tv", 10, _tv_now)
    calls = [c for c in telephony.call_context.values()
             if time.time() - c.get("started_at", time.time()) < 3 * 60 * 60]
    projects = agent.all_projects()
    canvas = canvas_state.get()
    model3d = model3d_state.get()
    return {
        "echo": {"enabled": bool(cfg.get("ai_enabled")), "phase": listener_status.get("phase", "idle"),
                 "busy": chat_turn_busy},
        "mic": {"device": mic_status.get("device", ""), "level": mic_status.get("level", 0),
                "alive": mic_age is not None and mic_age < 6},
        "last": _last_request(),
        "spotify": spotify,
        "tv": tv,
        "phone": {"configured": telephony.is_configured(), "enabled": bool(cfg.get("calling_enabled")),
                  "in_call": [c.get("caller_name") or c.get("contact_name") or "someone" for c in calls],
                  "sir_page": bool(cfg.get("sir_page_enabled")), "screening": bool(cfg.get("screen_unknown_callers"))},
        "projects": [{"id": p["id"], "title": p["title"], "status": p["status"], "steps": p["steps"]}
                     for p in projects[-4:]][::-1],
        "approvals": len(approvals.pending()),
        "pc": (pc := await _cached("pc", 10, _pc_now)),
        "gpu": pc and pc["gpu"],
        "canvas": canvas.get("title") or ("something" if canvas.get("html") else ""),
        "model3d": model3d.get("title") or (model3d.get("filename") or ""),
    }

app.mount("/3d_models", StaticFiles(directory=GENERATED_3D_MODELS_DIR), name="3d_models")
GENERATED_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/generated_images", StaticFiles(directory=GENERATED_IMAGES_DIR), name="generated_images")

app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8765)