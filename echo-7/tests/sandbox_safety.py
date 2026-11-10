"""
Checks the sandbox really boxes code in: it runs, can write in its own folder, and is
blocked from changing sir's files or Echo's own; runaway code is killed; memory is capped.

    venv\\Scripts\\python.exe tests\\sandbox_safety.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
import sandbox as sb  # noqa: E402

PROJECT = "safety-selftest"
outside = Path(os.environ["USERPROFILE"]) / "echo_sandbox_escape_test.txt"
echo_file = Path(__file__).resolve().parent.parent / "backend" / "config.py"

checks = []


def check(ok, label):
    checks.append(ok)
    print(("PASS " if ok else "FAIL ") + label)


sb.write_file(PROJECT, "hello.py", f'''
import os, sys
print("hello from the sandbox, python", sys.version.split()[0])
open("inside.txt", "w").write("ok")
print("inside-write:", os.path.exists("inside.txt"))
try:
    open(r"{outside}", "w").write("escaped")
    print("outside-write: ALLOWED")
except Exception as e:
    print("outside-write: blocked", type(e).__name__)
try:
    with open(r"{echo_file}", "a") as f:
        f.write("")
    print("echo-write: ALLOWED")
except Exception as e:
    print("echo-write: blocked", type(e).__name__)
''')
sb.write_file(PROJECT, "hello.js", "require('fs').writeFileSync('n.txt','ok'); console.log('node', process.version, 'ok');")
sb.write_file(PROJECT, "forever.py", "while True:\n    pass\n")
sb.write_file(PROJECT, "hog.py", "x = bytearray(3 * 1024 ** 3)\nprint('got 3 GB')\n")

r = sb.run(PROJECT, "hello.py")
print("   ", r)
check("hello from the sandbox" in r["output"], "python code runs")
check("inside-write: True" in r["output"], "it can write in its own project folder")
check("outside-write: blocked" in r["output"] and not outside.exists(), "it CAN'T write to your files")
check("echo-write: blocked" in r["output"], "it CAN'T change Echo's own files")
r = sb.run(PROJECT, "hello.js")
print("   ", r)
check("node" in r["output"] and "ok" in r["output"], "javascript (node) code runs")
r = sb.run(PROJECT, "forever.py", timeout=4)
check(r["timed_out"] and r["seconds"] < 10, f"runaway code is stopped ({r['seconds']}s)")
r = sb.run(PROJECT, "hog.py")
check("got 3 GB" not in r["output"], "memory is capped (a 3 GB grab fails)")
try:
    sb.write_file(PROJECT, "../../escape.txt", "x")
    check(False, "files can't be written outside the project")
except ValueError:
    check(True, "files can't be written outside the project")
outside.unlink(missing_ok=True)
sb.delete_project(PROJECT)
print(f"\n{sum(checks)}/{len(checks)} safety checks passed")
