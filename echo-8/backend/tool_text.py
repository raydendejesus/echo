"""
Reading tool calls the brain wrote as text instead of making them, and spotting replies
that are code rather than something to say. Shared by the chat, phone calls and projects.
"""
import json
import re


_TOOL_CALL_TAG = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.S)


def _tool_calls_in_text(content: str, known: set[str]) -> list[dict]:
    """The brain sometimes writes its tool call as text - '{"name": "tv_seek", "arguments": {...}}',
    or Hermes' own <tool_call> tags - instead of making it. Read it back as the call it meant,
    so sir gets the action rather than hearing JSON read aloud. Only names of tools actually
    offered this turn count."""
    text = (content or "").strip()
    if not text or ("{" not in text):
        return []
    chunks = _TOOL_CALL_TAG.findall(text) or [re.sub(r"^```(?:json)?|```$", "", text).strip()]
    calls = []
    for chunk in chunks:
        try:
            data = json.loads(chunk)
        except (json.JSONDecodeError, ValueError):
            return []
        for item in data if isinstance(data, list) else [data]:
            if not isinstance(item, dict):
                return []
            name = item.get("name") or (item.get("function") or {}).get("name")
            args = item.get("arguments", item.get("parameters", (item.get("function") or {}).get("arguments", {})))
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except (json.JSONDecodeError, ValueError):
                    return []
            if name not in known or not isinstance(args, dict):
                return []
            calls.append({"function": {"name": name, "arguments": args}})
    return calls


def _looks_like_code(content: str) -> bool:
    """A reply that is JSON / a tool call the brain made up - never something to read aloud."""
    text = re.sub(r"^```(?:json)?|```$", "", (content or "").strip()).strip()
    if text.startswith("<tool_call>"):
        return True
    if not text.startswith(("{", "[")):
        return False
    try:
        json.loads(text)
        return True
    except (json.JSONDecodeError, ValueError):
        return text.startswith("{\"") and ("\"name\"" in text or "\"arguments\"" in text)


_NOT_A_TOOL_NUDGE = (
    "(That reply was code, not an action or an answer - it wasn't run and sir didn't hear it. "
    "Either call one of the functions you actually have, exactly as listed, or answer sir in plain spoken words.)"
)
