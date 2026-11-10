"""
The TurboQuant engine's plumbing (backend/turbo_engine.py, backend/brain_api.py) - checked
without running the engine: Ollama-style requests are translated to the engine's (OpenAI) form
and back, and only Echo's own brain is routed to the engine - never the vision model or Echo Mini,
and never when the engine isn't answering.

Run:  venv\\Scripts\\python.exe tests\\turbo_engine_routing.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import brain_api  # noqa: E402
import turbo_engine as te  # noqa: E402

results = []


def check(name, got, want):
    results.append(got == want)
    print(f"{'PASS' if got == want else 'FAIL'}  {name}" + ("" if got == want else f"  (got {got!r}, wanted {want!r})"))


ollama_request = {
    "model": "echo", "stream": False, "format": "json", "options": {"num_predict": 300},
    "tools": [{"type": "function", "function": {"name": "web_search", "parameters": {"type": "object", "properties": {}}}}],
    "messages": [
        {"role": "system", "content": "You are Echo."},
        {"role": "user", "content": "weather and time?"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "web_search", "arguments": {"query": "weather"}}},
            {"function": {"name": "get_time", "arguments": {}}}]},
        {"role": "tool", "content": "sunny", "name": "web_search"},
        {"role": "tool", "content": "3 PM", "name": "get_time"},
        {"role": "system", "content": "(3 hours passed.)"},
        {"role": "user", "content": "thanks"},
    ],
}
body = te.to_engine(ollama_request)
calls = body["messages"][2]["tool_calls"]
check("tool calls get ids", [c["id"] for c in calls], ["call_1", "call_2"])
check("arguments become JSON text", calls[0]["function"]["arguments"], '{"query": "weather"}')
check("each tool result points at its call", [m.get("tool_call_id") for m in body["messages"][3:5]], ["call_1", "call_2"])
check("system notes mid-conversation are kept", body["messages"][5], {"role": "system", "content": "(3 hours passed.)"})
check("reply length limit carried", body["max_tokens"], 300)
check("Echo's temperature when none is given", body["temperature"], 0.6)
check("JSON answers asked for the engine's way", body["response_format"], {"type": "json_object"})
check("tools passed through", body["tools"], ollama_request["tools"])

answer = te.from_engine({"choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", "content": None,
                         "tool_calls": [{"id": "x", "type": "function", "function": {"name": "web_search", "arguments": '{"query": "news"}'}}]}}],
                         "usage": {"prompt_tokens": 812, "completion_tokens": 19}})
check("engine tool call read back Ollama's way", answer["message"]["tool_calls"], [{"function": {"name": "web_search", "arguments": {"query": "news"}}}])
check("empty content becomes ''", answer["message"]["content"], "")
check("token counts carried", (answer["prompt_eval_count"], answer["eval_count"]), (812, 19))
cut = te.from_engine({"choices": [{"finish_reason": "length", "message": {"content": "a long"}}]})
check("a reply cut short says so", cut["done_reason"], "length")
bad = te.from_engine({"choices": [{"message": {"tool_calls": [{"function": {"name": "x", "arguments": "{oops"}}]}}]})
check("broken arguments don't crash", bad["message"]["tool_calls"][0]["function"]["arguments"], {})


async def routing():
    sent = []

    class FakeClient:
        async def post(self, url, json, timeout):
            sent.append(("ollama", json["model"]))
            return "ollama-response"

    async def engine_chat(payload, timeout=120):
        sent.append(("engine", payload["model"]))
        return {"message": {"role": "assistant", "content": "hi"}}

    up = {"value": True}

    async def serving(cfg):
        return up["value"]
    te.serving, te.chat = serving, engine_chat
    client = FakeClient()
    url = "http://127.0.0.1:11434/api/chat"
    msgs = [{"role": "user", "content": "hi"}]

    r = await brain_api.post(url, json={"model": "echo", "messages": msgs}, client=client)
    check("Echo's brain goes to the engine", (sent[-1], r.json()["message"]["content"]), (("engine", "echo"), "hi"))
    await brain_api.post(url, json={"model": "echo-mini", "messages": msgs}, client=client)
    check("Echo Mini stays on Ollama", sent[-1], ("ollama", "echo-mini"))
    await brain_api.post(url, json={"model": "qwen2.5vl:7b", "messages": msgs}, client=client)
    check("the vision model stays on Ollama", sent[-1], ("ollama", "qwen2.5vl:7b"))
    before = len(sent)
    r = await brain_api.post(url, json={"model": "echo", "messages": [], "keep_alive": -1}, client=client)
    check("a warm-up isn't sent anywhere (the engine keeps the brain loaded)", (len(sent), r.json()["done"]), (before, True))
    up["value"] = False
    await brain_api.post(url, json={"model": "echo", "messages": msgs}, client=client)
    check("engine not answering: Echo's brain falls back to Ollama", sent[-1], ("ollama", "echo"))


asyncio.run(routing())
args = te.args.__code__.co_consts
check("the engine only listens on this PC", "127.0.0.1" in args, True)
check("the fork's recommended cache pair", (te.CACHE_K, te.CACHE_V), ("q8_0", "turbo3"))

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
