"""
The sandbox Echo writes and runs its own code in.

Each project gets a folder under O:\\JarvisAI\\sandbox\\projects\\<name>. Code Echo
runs there is started:

- at LOW INTEGRITY - the trust level browsers give web pages. Windows itself then
  refuses to let it change or delete anything of sir's (his files are all at the
  normal, higher level); the only place it can write is the low-trust LocalLow folder
  its projects live in.
- inside a Windows JOB OBJECT that caps it at MEMORY_LIMIT_MB of memory, a handful of
  processes, and TIME_LIMIT_SECONDS; when the time is up (or the run ends) Windows
  kills everything it started, children included.
- with a bare environment (no tokens, keys or paths from Echo's own environment).

It can still read files and reach the internet like any normal program - full
isolation needs a virtual machine (Windows Sandbox), which needs virtualization
switched on in the BIOS. Web apps and animations Echo makes don't run here at all:
they're shown on the dashboard inside the browser's own sandboxed frame.
"""
import ctypes
import os
import re
import shutil
import subprocess
import time
from ctypes import wintypes
from pathlib import Path

# Windows' own low-trust folder (AppData\LocalLow) - everything in it is already marked
# low-integrity, so the boxed-in code can write there and nowhere else.
ROOT = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "AppData" / "LocalLow" / "Echo" / "sandbox" / "projects"
TIME_LIMIT_SECONDS = 60
MEMORY_LIMIT_MB = 1024
MAX_PROCESSES = 8
MAX_FILE_BYTES = 2_000_000
OUTPUT_FILE = "_run_output.txt"
RUNNERS = {".py": "python", ".js": "node"}

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_adv = ctypes.WinDLL("advapi32", use_last_error=True)

TOKEN_ALL = 0x0001 | 0x0002 | 0x0008 | 0x0080  # assign primary, duplicate, query, adjust default
SE_GROUP_INTEGRITY = 0x20
TokenIntegrityLevel = 25
LOW_INTEGRITY_SID = "S-1-16-4096"
CREATE_SUSPENDED = 0x4
CREATE_NO_WINDOW = 0x08000000
CREATE_UNICODE_ENVIRONMENT = 0x400
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
JobObjectExtendedLimitInformation = 9
JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x100
JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x8
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
WAIT_TIMEOUT = 0x102


class SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]


class TOKEN_MANDATORY_LABEL(ctypes.Structure):
    _fields_ = [("Label", SID_AND_ATTRIBUTES)]


class STARTUPINFO(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR), ("lpDesktop", wintypes.LPWSTR),
                ("lpTitle", wintypes.LPWSTR), ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
                ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD), ("dwXCountChars", wintypes.DWORD),
                ("dwYCountChars", wintypes.DWORD), ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD), ("lpReserved2", ctypes.c_void_p),
                ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in ("r", "w", "o", "rt", "wt", "ot")]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION), ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


_k32.GetCurrentProcess.restype = wintypes.HANDLE
_k32.CreateJobObjectW.restype = wintypes.HANDLE
_k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
_k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
_k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
_k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
_k32.ResumeThread.argtypes = [wintypes.HANDLE]
_k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_k32.WaitForSingleObject.restype = wintypes.DWORD
_k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_k32.LocalFree.argtypes = [ctypes.c_void_p]
_adv.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
_adv.DuplicateTokenEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                  ctypes.POINTER(wintypes.HANDLE)]
_adv.ConvertStringSidToSidW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
_adv.GetLengthSid.argtypes = [ctypes.c_void_p]
_adv.SetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
_adv.CreateProcessAsUserW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p,
                                      ctypes.c_void_p, wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p,
                                      wintypes.LPCWSTR, ctypes.POINTER(STARTUPINFO),
                                      ctypes.POINTER(PROCESS_INFORMATION)]


# --- project folders --------------------------------------------------------------

def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return (s or "project")[:40]


def project_dir(name: str) -> Path:
    path = ROOT / slug(name)
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_path(project: str, relative: str) -> Path:
    """A file inside the project folder - never outside it (no '..', no absolute paths)."""
    base = project_dir(project).resolve()
    target = (base / relative.replace("\\", "/").lstrip("/")).resolve()
    if base != target and base not in target.parents:
        raise ValueError(f"{relative} is outside the project folder")
    return target


def write_file(project: str, relative: str, content: str) -> Path:
    if len(content.encode("utf-8")) > MAX_FILE_BYTES:
        raise ValueError("file too big for the sandbox")
    path = safe_path(project, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def list_files(project: str) -> list[str]:
    base = project_dir(project)
    return sorted(str(p.relative_to(base)).replace("\\", "/") for p in base.rglob("*")
                  if p.is_file() and not p.name.startswith("_run_output") and "__pycache__" not in p.parts)


def delete_project(project: str) -> None:
    path = ROOT / slug(project)
    if path.exists() and ROOT in path.resolve().parents:
        shutil.rmtree(path, ignore_errors=True)


# --- running code -----------------------------------------------------------------

def _low_integrity_token() -> wintypes.HANDLE:
    own = wintypes.HANDLE()
    if not _adv.OpenProcessToken(_k32.GetCurrentProcess(), TOKEN_ALL, ctypes.byref(own)):
        raise OSError(ctypes.get_last_error(), "OpenProcessToken")
    low = wintypes.HANDLE()
    ok = _adv.DuplicateTokenEx(own, 0x02000000, None, 2, 1, ctypes.byref(low))  # MAXIMUM_ALLOWED, impersonation, primary
    _k32.CloseHandle(own)
    if not ok:
        raise OSError(ctypes.get_last_error(), "DuplicateTokenEx")
    sid = ctypes.c_void_p()
    _adv.ConvertStringSidToSidW(LOW_INTEGRITY_SID, ctypes.byref(sid))
    label = TOKEN_MANDATORY_LABEL(SID_AND_ATTRIBUTES(sid, SE_GROUP_INTEGRITY))
    ok = _adv.SetTokenInformation(low, TokenIntegrityLevel, ctypes.byref(label),
                                  ctypes.sizeof(label) + _adv.GetLengthSid(sid))
    _k32.LocalFree(sid)
    if not ok:
        raise OSError(ctypes.get_last_error(), "SetTokenInformation")
    return low


def _limited_job() -> wintypes.HANDLE:
    job = _k32.CreateJobObjectW(None, None)
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = (JOB_OBJECT_LIMIT_PROCESS_MEMORY | JOB_OBJECT_LIMIT_ACTIVE_PROCESS
                                             | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
    info.BasicLimitInformation.ActiveProcessLimit = MAX_PROCESSES
    info.ProcessMemoryLimit = MEMORY_LIMIT_MB * 1024 * 1024
    _k32.SetInformationJobObject(job, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info))
    return job


def _environment(folder: Path) -> ctypes.Array:
    keep = ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    tools = [str(SANDBOX_PYTHON.parent)] + [str(Path(shutil.which("node")).parent)] if shutil.which("node") else [str(SANDBOX_PYTHON.parent)]
    env["PATH"] = ";".join(tools + [os.path.join(os.environ.get("SYSTEMROOT", r"C:\Windows"), "System32")])
    env["TEMP"] = env["TMP"] = str(folder)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    block = "\0".join(f"{k}={v}" for k, v in sorted(env.items())) + "\0\0"
    return ctypes.create_unicode_buffer(block)


# The sandbox's own standalone Python (python.org's "embeddable" build). Not Echo's own
# Python: that one is a Microsoft Store app, and Windows starts Store apps *outside* the job
# object - so its time and memory limits would never reach the code.
SANDBOX_PYTHON = Path(__file__).resolve().parent.parent / "tools" / "sandbox-python" / "python.exe"


def _interpreter(runner: str) -> str:
    if runner == "python":
        if not SANDBOX_PYTHON.exists():
            raise FileNotFoundError("the sandbox's Python isn't installed (tools/sandbox-python) - run the setup check")
        return str(SANDBOX_PYTHON)
    found = shutil.which(runner)
    if not found:
        raise FileNotFoundError(f"{runner} isn't installed")
    return found


def run(project: str, relative: str, args: list[str] | None = None, timeout: int = TIME_LIMIT_SECONDS) -> dict:
    """Runs a .py or .js file from the project, boxed in. Returns {exit_code, output, timed_out, seconds}."""
    script = safe_path(project, relative)
    runner = RUNNERS.get(script.suffix.lower())
    if runner is None:
        raise ValueError(f"I can only run {', '.join(RUNNERS)} files in the sandbox")
    if not script.exists():
        raise FileNotFoundError(relative)
    folder = project_dir(project)
    for old_output in folder.glob("_run_output*.txt"):
        try:
            old_output.unlink()
        except OSError:
            pass  # a killed run can hold its file for a moment; it's cleared next time
    out_file = folder / f"_run_output_{int(time.time() * 1000)}.txt"
    exe = _interpreter(runner)
    quoted = " ".join(f'"{a}"' for a in [exe, str(script), *(args or [])])
    cmdline = ctypes.create_unicode_buffer(f'cmd.exe /d /c "{quoted} > "{out_file}" 2>&1"')

    token, job = _low_integrity_token(), _limited_job()
    si, pi = STARTUPINFO(), PROCESS_INFORMATION()
    si.cb = ctypes.sizeof(si)
    flags = CREATE_SUSPENDED | CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT
    started = time.time()
    try:
        if not _adv.CreateProcessAsUserW(token, None, cmdline, None, None, False, flags, _environment(folder),
                                         str(folder), ctypes.byref(si), ctypes.byref(pi)):
            raise OSError(ctypes.get_last_error(), "CreateProcessAsUserW")
        _k32.AssignProcessToJobObject(job, pi.hProcess)
        _k32.ResumeThread(pi.hThread)
        timed_out = _k32.WaitForSingleObject(pi.hProcess, int(timeout * 1000)) == WAIT_TIMEOUT
        if timed_out:
            _k32.TerminateJobObject(job, 1)
            _k32.WaitForSingleObject(pi.hProcess, 5000)
        code = wintypes.DWORD()
        _k32.GetExitCodeProcess(pi.hProcess, ctypes.byref(code))
    finally:
        for h in (pi.hThread, pi.hProcess, token):
            if h:
                _k32.CloseHandle(h)
        _k32.CloseHandle(job)  # kills anything still running in the box
    try:
        output = out_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        output = ""
    return {"exit_code": code.value, "output": output[-6000:], "timed_out": timed_out,
            "seconds": round(time.time() - started, 2)}
