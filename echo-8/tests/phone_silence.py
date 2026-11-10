"""
Phone calls that nobody is talking on (backend/server.py's call webhooks) - checked against the
real handlers with a fake phone line; no call is placed and nothing is played.

- A call answered by a voicemail box is hung up straight away, not talked to.
- Silence gets "Sorry, I didn't catch that" at most twice, then a goodbye and a hang-up. (A
  voicemail box once got "didn't catch that" over and over until it cut off.)

Run:  venv\\Scripts\\python.exe tests\\phone_silence.py
"""
import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
results = []


def check(name, got, want):
    results.append(got == want)
    print(f"{'PASS' if got == want else 'FAIL'}  {name}" + ("" if got == want else f"  (got {got!r}, wanted {want!r})"))


async def main():
    tmp = Path(tempfile.mkdtemp(prefix="echo_phone_"))
    skip = shutil.ignore_patterns("__pycache__", "*.log", "generated_*")
    shutil.copytree(ROOT / "backend", tmp / "backend", ignore=skip)
    shutil.copytree(ROOT / "frontend", tmp / "frontend", ignore=skip)
    os.chdir(tmp / "backend")
    sys.path.insert(0, str(tmp / "backend"))
    try:
        import httpx
        import server
        import telephony

        owner = "+13305550100"
        cfg = {"owner_number": owner, "telnyx_number": "+13305559999", "public_base_url": "https://sim.invalid",
               "account_sid": "sim", "api_key": "sim", "application_sid": "sim", "webhook_token": ""}
        telephony.load_config = lambda: cfg

        async def quiet_clip(text):
            return telephony.register_audio_clip(b"sim")
        server._speak_clip = quiet_clip

        async def no_warm(who="sir"):
            return None
        server.warm_model = no_warm

        transport = httpx.ASGITransport(app=server.app, client=("127.0.0.1", 5000))
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client:
            r = await client.post("/api/telephony/outbound_voice?contact=&msg=test",
                                  data={"CallSid": "vm-1", "To": owner, "AnsweredBy": "machine_start"})
            check("voicemail answered: hung up", "<Hangup/>" in r.text and "<Gather" not in r.text, True)
            check("voicemail answered: nothing said to it", "<Play>" in r.text, False)

            r = await client.post("/api/telephony/outbound_voice?contact=&msg=test",
                                  data={"CallSid": "hu-1", "To": owner, "AnsweredBy": "human"})
            check("a person answered: Echo speaks and listens", "<Play>" in r.text and "<Gather" in r.text, True)
            replies = []
            for _ in range(3):
                r = await client.post("/api/telephony/gather", data={"CallSid": "hu-1", "To": owner, "SpeechResult": ""})
                replies.append("hangup" if "<Hangup/>" in r.text else "asks again" if "<Gather" in r.text else r.text[:60])
            check("silence: asks again twice, then hangs up", replies, ["asks again", "asks again", "hangup"])

            # Telnyx's usual way: the detection result arrives as a status update
            ended = []

            async def fake_end(cfg_, sid):
                ended.append(sid)
            telephony.end_call = fake_end
            r = await client.post("/api/telephony/outbound_voice?contact=&msg=test", data={"CallSid": "vm-2", "To": owner})
            r = await client.post("/api/telephony/status?contact=", data={"CallSid": "vm-2", "CallStatus": "in-progress",
                                                                          "AnsweredBy": "machine_end_beep"})
            await asyncio.sleep(0.1)
            check("voicemail reported by status: the call is ended", ended, ["vm-2"])
            check("voicemail reported by status: told to hang up", "<Hangup/>" in r.text, True)
            r = await client.post("/api/telephony/gather", data={"CallSid": "vm-2", "To": owner, "SpeechResult": "leave a message"})
            check("...and its greeting is never answered", "<Hangup/>" in r.text and "<Play>" not in r.text, True)
            r = await client.post("/api/telephony/status?contact=", data={"CallSid": "hu-2", "CallStatus": "in-progress",
                                                                          "AnsweredBy": "human"})
            check("a person answering is left alone", (r.text, "hu-2" in ended), ("", False))
    finally:
        os.chdir(ROOT)
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


asyncio.run(main())
