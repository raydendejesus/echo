"""
Who gets Echo's own brain, and who gets Echo Mini (backend/brain.py) - checked without any real
model. With Echo Mini on: sir always gets Echo's own brain and everyone else gets Echo Mini;
while a heavy graphics job has the GPU, sir gets Echo Mini too. With Echo Mini off (two full
Echos) or not installed, everyone gets Echo's own brain.

Run:  venv\\Scripts\\python.exe tests\\brain_priority.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import brain  # noqa: E402

ON = {brain.CONFIG_KEY: True}
OFF = {brain.CONFIG_KEY: False}
results = []
unloaded = []


def check(name, got, want):
    results.append(got == want)
    print(f"{'PASS' if got == want else 'FAIL'}  {name}" + ("" if got == want else f"  (got {got}, wanted {want})"))


def reset(ready=True):
    brain._in_use.update(sir=0, guest=0, background=0)
    brain._sir_seen = -1e9
    brain._lent = 0

    async def is_ready():
        return ready
    brain.helper_ready = is_ready


async def fake_unload(model):
    unloaded.append(model)
brain._unload = fake_unload


async def model_for(cfg, who):
    async with brain.for_conversation(cfg, who) as (model, _):
        return model


async def main():
    reset()
    check("sir gets Echo's own brain", await model_for(ON, "sir"), brain.MAIN)
    check("a caller gets Echo Mini, even with nobody else talking", await model_for(ON, "guest"), brain.HELPER)

    reset()
    async with brain.for_conversation(ON, "guest"):
        check("sir calling while a caller is talking gets Echo's own brain", await model_for(ON, "sir"), brain.MAIN)

    reset()
    async with brain.for_conversation(ON, "sir"):
        check("a caller while sir is talking gets Echo Mini", await model_for(ON, "guest"), brain.HELPER)
        check("...and a second caller too", await model_for(ON, "guest"), brain.HELPER)

    reset()
    async with brain.hold():
        check("sir during a project step gets Echo's own brain", await model_for(ON, "sir"), brain.MAIN)

    reset()
    check("Echo Mini off (two full Echos): a caller gets Echo's own brain", await model_for(OFF, "guest"), brain.MAIN)
    check("Echo Mini off: one conversation slot becomes two", (brain.slots_for(ON), brain.slots_for(OFF)), (1, 2))

    reset(ready=False)
    check("Echo Mini not installed yet: a caller gets Echo's own brain", await model_for(ON, "guest"), brain.MAIN)

    reset()
    async with brain.lend_gpu():
        check("a 3D job unloads Echo's own brain first", unloaded[-1:], [brain.MAIN])
        check("during a 3D job sir gets Echo Mini", await model_for(ON, "sir"), brain.HELPER)
        check("during a 3D job the GPU counts as lent", brain.gpu_lent(), True)
    check("after the 3D job sir gets Echo's own brain again", await model_for(ON, "sir"), brain.MAIN)

    reset()
    async with brain.for_conversation(ON, "sir"):
        pass
    async with brain.for_conversation(OFF, "guest"):
        pass
    check("everything is released afterwards", sum(brain._in_use.values()), 0)

    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
