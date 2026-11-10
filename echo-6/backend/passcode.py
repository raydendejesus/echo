"""
Sir's secret passcode - stored so that nobody, Echo included, can read it back.

backend/passcode.json (private, never committed) holds only a salted scrypt
fingerprint of the passcode. Opening the file tells you nothing; the only way to
use it is to offer a passcode and see whether it fits.

Whatever sir says is checked the moment it arrives, and the passcode is cut out of
it right there: Echo's conversation memory, phone call history, the Thinking
window, the listener's log and the AI model only ever see "[passcode]". What
survives the check is a single yes/no - "sir said the passcode this turn" - which
protected commands (like shutdown) look at, and which is gone when the turn ends.
"""
import hashlib
import hmac
import json
import os
import re
from pathlib import Path

import launch_utils as lu

FILE = Path(__file__).resolve().parent / "passcode.json"
# Where the first version kept the phrase in plain text; moved into FILE as a
# fingerprint the first time it's seen, then deleted.
_LEGACY_FILE = Path(__file__).resolve().parent / "pc_power.json"
PLACEHOLDER = "[passcode]"
_SCRYPT_N = 2 ** 13  # every sentence sir says gets checked, so the cost per check matters
_WORD = re.compile(r"\w+")


def _key(text: str) -> str:
    # Case, punctuation and spacing don't matter ("Blue llama." == "bluellama"):
    # speech-to-text is inconsistent about all three.
    return lu.norm(text).replace(" ", "")


def _fingerprint(key: str, salt: bytes, n: int = _SCRYPT_N) -> bytes:
    return hashlib.scrypt(key.encode("utf-8"), salt=salt, n=n, r=8, p=1, dklen=32)


def _write(data: dict) -> None:
    FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def set_passcode(passcode: str) -> None:
    key = _key(passcode)
    salt = os.urandom(16)
    _write({"salt": salt.hex(), "fingerprint": _fingerprint(key, salt).hex(), "length": len(key), "n": _SCRYPT_N})
    _LEGACY_FILE.unlink(missing_ok=True)


def _load() -> dict:
    if not FILE.exists() and _LEGACY_FILE.exists():
        try:
            old = json.loads(_LEGACY_FILE.read_text(encoding="utf-8")).get("phrase", "")
        except (OSError, json.JSONDecodeError):
            old = ""
        if _key(old):
            set_passcode(old)
        else:
            _LEGACY_FILE.unlink(missing_ok=True)
    try:
        data = json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if data.get("fingerprint") and data.get("salt") and data.get("length") else {}


def is_set() -> bool:
    return bool(_load())


def scrub(text: str) -> tuple[str, bool]:
    """(text with the passcode cut out, whether it was in there). Only runs of words
    whose combined length matches the passcode's are fingerprinted, so an ordinary
    sentence costs at most a few checks."""
    data = _load()
    if not data or not text:
        return text, False
    salt, target, length = bytes.fromhex(data["salt"]), bytes.fromhex(data["fingerprint"]), int(data["length"])
    n = int(data.get("n", _SCRYPT_N))
    tokens = [(m.start(), m.end(), _key(m.group())) for m in _WORD.finditer(text)]
    found = []
    i = 0
    while i < len(tokens):
        joined = ""
        for j in range(i, len(tokens)):
            joined += tokens[j][2]
            if len(joined) > length:
                break
            if len(joined) == length and hmac.compare_digest(_fingerprint(joined, salt, n), target):
                found.append((tokens[i][0], tokens[j][1]))
                i = j
                break
        i += 1
    if not found:
        return text, False
    out, last = [], 0
    for start, end in found:
        out.append(text[last:start])
        out.append(PLACEHOLDER)
        last = end
    out.append(text[last:])
    return "".join(out), True
