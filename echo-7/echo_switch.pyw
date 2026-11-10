"""
Echo Switch - a tiny on/off switch for Echo that a phone can press (an NFC tag
tap running an iPhone Shortcut, for example).

It is NOT Echo: it loads no AI and uses almost nothing, so it can run from login
while Echo itself stays off until asked. It only starts and stops Echo.

  GET  /status   -> {"echo": "on"|"off"}
  GET  /apps     -> sir's projects, with what Echo built - viewable from his phone
  POST /toggle   -> turns Echo off if it's on, on if it's off
  POST /on, /off

Every request needs the secret key (header "X-Echo-Key", or ?key=) from
echo_switch_key.txt, which is private and never committed. Only this PC's own
network, the home network and Tailscale may connect.
"""
import html
import ipaddress
import os
import json
import secrets
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
KEY_FILE = HERE / "echo_switch_key.txt"
PORT = 8766
NO_WINDOW = 0x08000000
# Two taps in a row (or a phone retrying) mustn't flip Echo on and straight back off.
MIN_SECONDS_BETWEEN_SWITCHES = 15
ALLOWED_NETWORKS = [ipaddress.ip_network(n) for n in (
    "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10",
)]
ECHO_PROCESSES = r"ECHO_TRAY|backend\\server\.py|voicelab\.py|ComfyUI_windows_portable|ComfyUI\\main\.py|run_nvidia_gpu"

_lock = threading.Lock()
_last_switch = 0.0


def _key() -> str:
    if not KEY_FILE.exists():
        KEY_FILE.write_text(secrets.token_urlsafe(24), encoding="utf-8")
    return KEY_FILE.read_text(encoding="utf-8").strip()


REFUSALS_FILE = HERE / "echo_switch_refusals.txt"


def _note_refusal(client, why: str) -> None:
    """Why a request was turned away - never the key itself. Kept short."""
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {client}  {why}\n"
    try:
        lines = REFUSALS_FILE.read_text(encoding="utf-8").splitlines(keepends=True)[-19:] if REFUSALS_FILE.exists() else []
        REFUSALS_FILE.write_text("".join(lines) + line, encoding="utf-8")
    except OSError:
        pass


def _powershell(script: str, timeout: int = 60) -> str:
    return subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW,
    ).stdout


def echo_running() -> bool:
    out = _powershell(
        "@(Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'pythonw*' -and "
        "$_.CommandLine -match 'ECHO_TRAY' }).Count"
    )
    return out.strip() not in ("", "0")


def start_echo() -> None:
    pythonw = HERE / "venv" / "Scripts" / "pythonw.exe"
    if pythonw.exists():
        subprocess.Popen([str(pythonw), str(HERE / "ECHO_TRAY.pyw")], cwd=str(HERE), creationflags=NO_WINDOW)
    else:
        subprocess.Popen(["cmd", "/c", str(HERE / "ECHO.bat")], cwd=str(HERE), creationflags=NO_WINDOW)


def stop_echo() -> None:
    try:  # free the GPU first, while the model server is still answering
        req = urllib.request.Request(
            "http://127.0.0.1:11434/api/generate", data=b'{"model":"echo","keep_alive":0}',
            headers={"Content-Type": "application/json"}, method="POST",
        )
        urllib.request.urlopen(req, timeout=15).read()
    except Exception:  # noqa: BLE001 - Ollama may simply not be running
        pass
    # The tray first, so it can't restart the server, then everything it started.
    kill = (
        "$p = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match '%s' -and $_.ProcessId -ne $PID -and "
        "$_.CommandLine -notmatch 'mcp_watcher|echo_switch' }; "
        "$p | Where-Object { $_.CommandLine -match 'ECHO_TRAY' } | ForEach-Object { taskkill /PID $_.ProcessId /T /F | Out-Null }; "
        "Start-Sleep 2; "
        "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match '%s' -and $_.ProcessId -ne $PID -and "
        "$_.CommandLine -notmatch 'mcp_watcher|echo_switch' } | ForEach-Object { taskkill /PID $_.ProcessId /T /F | Out-Null }"
    ) % (ECHO_PROCESSES, ECHO_PROCESSES)
    _powershell(kill)


def switch(want: str) -> dict:
    global _last_switch
    with _lock:
        running = echo_running()
        target = (not running) if want == "toggle" else (want == "on")
        if target == running:
            return {"echo": "on" if running else "off", "changed": False}
        if time.time() - _last_switch < MIN_SECONDS_BETWEEN_SWITCHES:
            return {"echo": "on" if running else "off", "changed": False, "note": "switched a moment ago - ignoring"}
        _last_switch = time.time()
        start_echo() if target else stop_echo()
        return {"echo": "on" if target else "off", "changed": True}


# --- sir's projects, viewable remotely (read-only) ------------------------------------
SANDBOX_ROOT = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "AppData" / "LocalLow" / "Echo" / "sandbox" / "projects"
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css", ".json": "application/json",
         ".md": "text/plain; charset=utf-8", ".txt": "text/plain; charset=utf-8", ".py": "text/plain; charset=utf-8",
         ".png": "image/png", ".svg": "image/svg+xml"}


def _projects() -> list[dict]:
    try:
        return json.loads((HERE / "backend" / "projects.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _esc(text: str) -> str:
    return html.escape(str(text or ""))


def gallery_page(key: str) -> bytes:
    rows = []
    for p in reversed(_projects()[-20:]):
        status = {"working": "working on it", "waiting_approval": "needs your OK", "done": "done"}.get(p.get("status"), p.get("status"))
        links = []
        if p.get("preview"):
            links.append(f'<a href="/apps/{_esc(p["slug"])}/{_esc(p["preview"])}?key={_esc(key)}">Open it</a>')
        folder = SANDBOX_ROOT / p.get("slug", "")
        for name in ("report.md", "main.py", "main.js", "index.html"):
            if (folder / name).exists() and name != p.get("preview"):
                links.append(f'<a href="/apps/{_esc(p["slug"])}/{name}?key={_esc(key)}">{name}</a>')
        log = "".join(f"<li>{_esc(e.get('kind'))}: {_esc(str(e.get('text'))[:160])}</li>" for e in p.get("log", [])[-6:])
        rows.append(f"<section><h2>{_esc(p.get('title'))}</h2><p class=s>{_esc(status)} - {p.get('steps', 0)} steps</p>"
                    f"<p>{_esc(p.get('summary'))}</p><p>{' - '.join(links)}</p><details><summary>What Echo did</summary><ul>{log}</ul></details></section>")
    body = "".join(rows) or "<p>No projects yet. Ask Echo to make or research something.</p>"
    page = ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>Echo projects</title><style>body{font:16px system-ui;margin:0;padding:16px;background:#101312;color:#e4e9e6}"
            "a{color:#5ee08a}section{border-bottom:1px solid #2a312d;padding:10px 0}.s{color:#98a39d;margin:0}"
            "h2{margin:0 0 4px;font-size:1.15rem}li{color:#98a39d;font-size:.85rem}</style>"
            f"<h1>Echo's projects</h1>{body}")
    return page.encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, data: bytes, content_type: str, sandboxed: bool = False) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if sandboxed:
            # What Echo built runs boxed in, here too: its scripts can't reach anything else.
            self.send_header("Content-Security-Policy", "sandbox allow-scripts allow-modals allow-pointer-lock")
            self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _app_file(self, rel: str) -> None:
        parts = rel.split("/", 1)
        if len(parts) != 2:
            self._reply(404, {"error": "no such file"})
            return
        base = (SANDBOX_ROOT / parts[0]).resolve()
        target = (base / parts[1]).resolve()
        if SANDBOX_ROOT.resolve() not in base.parents or base not in target.parents or not target.is_file():
            self._reply(404, {"error": "no such file"})
            return
        self._send(200, target.read_bytes(), TYPES.get(target.suffix.lower(), "text/plain; charset=utf-8"), sandboxed=True)

    def _reply(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _allowed(self) -> bool:
        try:
            client = ipaddress.ip_address(self.client_address[0])
        except ValueError:
            return False
        if not any(client in net for net in ALLOWED_NETWORKS):
            _note_refusal(client, "address isn't on the home network or Tailscale")
            return False
        url = urlparse(self.path)
        given = (self.headers.get("X-Echo-Key") or (parse_qs(url.query).get("key") or [""])[0]).strip()
        if secrets.compare_digest(given, _key()):
            return True
        if not given:
            headers = ", ".join(self.headers.keys())
            _note_refusal(client, f"no key sent (headers it did send: {headers}; path: {url.path})")
        else:
            right = _key()
            same = sum(1 for a, b in zip(given, right) if a == b)
            _note_refusal(client, f"wrong key: {len(given)} characters (should be {len(right)}), "
                                  f"{same} in the right place")
        return False

    def _handle(self, method: str) -> None:
        if not self._allowed():
            self._reply(403, {"error": "not allowed"})
            return
        path = urlparse(self.path).path.rstrip("/")
        if path == "/status":
            self._reply(200, {"echo": "on" if echo_running() else "off"})
        elif method == "GET" and path == "/apps":
            key = (parse_qs(urlparse(self.path).query).get("key") or [self.headers.get("X-Echo-Key", "")])[0]
            self._send(200, gallery_page(key), "text/html; charset=utf-8")
        elif method == "GET" and path.startswith("/apps/"):
            self._app_file(path[len("/apps/"):])
        elif method == "POST" and path in ("/toggle", "/on", "/off"):
            self._reply(200, switch(path[1:]))
        else:
            self._reply(404, {"error": "use /status, /toggle, /on, /off or /apps"})

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def log_message(self, *args):  # quiet: no console, no log file
        pass


def main() -> None:
    _key()
    try:
        server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    except OSError:
        sys.exit(0)  # already running
    server.serve_forever()


if __name__ == "__main__":
    main()
