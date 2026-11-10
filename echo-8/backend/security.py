from __future__ import annotations

import asyncio
import ctypes
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import config as config_module

if TYPE_CHECKING:  # annotations only - OpenCV and NumPy load the first time a camera feature runs
    import numpy as np

SECURITY_DIR = Path(__file__).resolve().parent / "security"
KNOWN_FACES_DIR = SECURITY_DIR / "known_faces"
LOGS_DIR = SECURITY_DIR / "logs"
CHECK_INTERVAL_SECONDS = 15
ENROLL_FRAME_COUNT = 8

_face_app = None


def _get_face_app():
    global _face_app
    if _face_app is None:
        import onnxruntime as ort
        from insightface.app import FaceAnalysis
        available = ort.get_available_providers()
        providers = [p for p in ("CUDAExecutionProvider", "CPUExecutionProvider") if p in available] or ["CPUExecutionProvider"]
        app = FaceAnalysis(name="buffalo_l", providers=providers)
        app.prepare(ctx_id=0, det_size=(640, 640))
        _face_app = app
    return _face_app


def _capture_frame(warmup_frames: int = 5) -> np.ndarray | None:
    """Insta360's autofocus/exposure needs a moment after the device opens, so the
    first frame or two are often dark/blurry - discard a few before taking the real one."""
    import cv2

    cap = cv2.VideoCapture(0)
    try:
        frame = None
        for _ in range(warmup_frames + 1):
            ok, frame = cap.read()
            if not ok:
                return None
            time.sleep(0.05)
        return frame
    finally:
        cap.release()


def _largest_face_embedding(frame: np.ndarray):
    faces = _get_face_app().get(frame)
    if not faces:
        return None
    faces.sort(key=lambda f: (f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1]), reverse=True)
    return faces[0].normed_embedding


def is_locked() -> bool:
    handle = ctypes.windll.user32.OpenInputDesktop(0, False, 0)
    if not handle:
        return True
    ctypes.windll.user32.CloseDesktop(handle)
    return False


def lock_workstation() -> None:
    ctypes.windll.user32.LockWorkStation()


_known_cache: tuple[tuple, list] | None = None


def _load_known_embeddings() -> list[np.ndarray]:
    """Read from disk only when the set of reference photos changed, not on every 15-second check."""
    import numpy as np

    global _known_cache
    KNOWN_FACES_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(KNOWN_FACES_DIR.glob("*.npy"))
    signature = tuple((p.name, p.stat().st_mtime_ns) for p in files)
    if _known_cache is None or _known_cache[0] != signature:
        _known_cache = (signature, [np.load(p) for p in files])
    return _known_cache[1]


def _save_known_embedding(embedding: np.ndarray, frame: np.ndarray) -> None:
    import cv2
    import numpy as np

    KNOWN_FACES_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    np.save(KNOWN_FACES_DIR / f"{stamp}.npy", embedding)
    cv2.imwrite(str(KNOWN_FACES_DIR / f"{stamp}.jpg"), frame)


def _best_similarity(embedding: np.ndarray, known: list[np.ndarray]) -> float:
    if not known:
        return -1.0
    import numpy as np

    return float(np.max(np.stack(known) @ embedding))


def _log_event(message: str, frame: np.ndarray | None = None) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    with open(LOGS_DIR / "events.log", "a", encoding="utf-8") as f:
        f.write(f"{stamp} {message}\n")
    if frame is not None:
        import cv2

        cv2.imwrite(str(LOGS_DIR / f"{stamp}_unrecognized.jpg"), frame)


def enroll() -> dict:
    captured = 0
    for _ in range(ENROLL_FRAME_COUNT):
        frame = _capture_frame()
        if frame is not None:
            embedding = _largest_face_embedding(frame)
            if embedding is not None:
                _save_known_embedding(embedding, frame)
                captured += 1
        time.sleep(0.5)
    return {"captured": captured, "requested": ENROLL_FRAME_COUNT}


def remember_me() -> dict:
    """One frame of sir, added to his reference photos - Echo knows him better each time."""
    frame = _capture_frame()
    if frame is None:
        return {"saved": False, "why": "the camera didn't give a picture (it may be busy - try again)"}
    embedding = _largest_face_embedding(frame)
    if embedding is None:
        return {"saved": False, "why": "I couldn't see a face - look at the camera"}
    _save_known_embedding(embedding, frame)
    _log_event("Sir added a reference photo from the dashboard.")
    return {"saved": True, "count": known_face_count()}


def faces_in(frame: np.ndarray, threshold: float) -> list[dict]:
    """Every face in the frame: where it is, and whether it's sir."""
    known = _load_known_embeddings()
    out = []
    for face in _get_face_app().get(frame):
        score = _best_similarity(face.normed_embedding, known)
        x1, y1, x2, y2 = (int(v) for v in face.bbox)
        out.append({"label": "sir" if score >= threshold else "someone I don't know", "box": [x1, y1, x2, y2],
                    "score": round(score, 2), "you": score >= threshold})
    return out


def capture_frame() -> np.ndarray | None:
    return _capture_frame()


def recent_reference_photos(limit: int = 12) -> list[str]:
    KNOWN_FACES_DIR.mkdir(parents=True, exist_ok=True)
    return [p.name for p in sorted(KNOWN_FACES_DIR.glob("*.jpg"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]]


def known_face_count() -> int:
    KNOWN_FACES_DIR.mkdir(parents=True, exist_ok=True)
    return len(list(KNOWN_FACES_DIR.glob("*.npy")))


async def guard_loop() -> None:
    was_locked = False
    while True:
        cfg = config_module.load_config()
        if not cfg.get("desk_guard_enabled"):
            was_locked = False
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)
            continue

        locked_now = await asyncio.to_thread(is_locked)

        if was_locked and not locked_now:
            await asyncio.sleep(1.5)
            frame = await asyncio.to_thread(_capture_frame)
            if frame is not None:
                embedding = await asyncio.to_thread(_largest_face_embedding, frame)
                if embedding is not None:
                    await asyncio.to_thread(_save_known_embedding, embedding, frame)
                    _log_event("Real Windows unlock detected - captured new trusted reference photo.")

        if locked_now:
            was_locked = True
        else:
            frame = await asyncio.to_thread(_capture_frame)
            if frame is not None:
                embedding = await asyncio.to_thread(_largest_face_embedding, frame)
                if embedding is not None:
                    known = await asyncio.to_thread(_load_known_embeddings)
                    score = _best_similarity(embedding, known)
                    threshold = cfg.get("desk_guard_threshold", 0.65)
                    if score < threshold:
                        _log_event(f"Unrecognized face (similarity {score:.2f} < {threshold}) - locking.", frame)
                        await asyncio.to_thread(lock_workstation)
                        was_locked = True
                        import pager  # local: pager -> telephony, which security must not pull in at import time

                        asyncio.create_task(pager.page_owner(
                            "deskguard",
                            "Sir, someone I didn't recognize was at your computer, so I've locked it.",
                        ))

        await asyncio.sleep(CHECK_INTERVAL_SECONDS)