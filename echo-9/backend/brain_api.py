"""
The one door every request to Echo's brain goes through.

Requests are written Ollama's way (/api/chat). With the TurboQuant engine switched on and
answering (turbo_engine.py), requests for Echo's own brain ("echo") go to the engine instead;
everything else - and Echo's brain too whenever the engine isn't answering - goes to Ollama as
always. Callers get back something that reads like the httpx response they already handle.
"""
import httpx

import http_pool
import turbo_engine

MAIN = "echo"


class _Answer:
    """The engine's answer, shaped like the httpx response the callers already read."""
    status_code = 200

    def __init__(self, data: dict):
        self._data = data

    def raise_for_status(self) -> "_Answer":
        return self

    def json(self) -> dict:
        return self._data


async def post(url: str, json: dict, timeout: float = 120, client: httpx.AsyncClient | None = None):
    import config as config_module  # here, not at the top: config loads the plugins, which load tools
    if (json.get("model") == MAIN and url.endswith("/api/chat")
            and await turbo_engine.serving(config_module.load_config())):
        if not json.get("messages"):
            # a warm-up or an unload: the engine keeps the brain loaded while it runs
            return _Answer({"model": MAIN, "message": {"role": "assistant", "content": ""}, "done": True})
        return _Answer(await turbo_engine.chat(json, timeout))
    return await (client or http_pool.client()).post(url, json=json, timeout=timeout)
