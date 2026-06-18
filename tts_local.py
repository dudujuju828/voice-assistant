"""Local text-to-speech via Kokoro (kokoro-onnx).

An offline alternative to the ElevenLabs API for when there are no credits or no
network. The model (~300 MB) is loaded once on first use and cached. Kokoro
returns the whole clip, which we stream into sounddevice in small chunks so
barge-in can still abort playback mid-sentence.

Heavy imports (kokoro_onnx, numpy) are deferred into the functions so the rest
of the app — and the test suite — can import this module without Kokoro or its
model files installed. A missing model degrades to silence (logged), never a
crash.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Optional

logger = logging.getLogger(__name__)

SAMPLE_RATE_FALLBACK = 24000
CHUNK_FRAMES = 2048  # int16 frames per write — small enough for snappy barge-in

_MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
_MODEL_PATH = os.path.join(_MODELS_DIR, "kokoro-v1.0.onnx")
_VOICES_PATH = os.path.join(_MODELS_DIR, "voices-v1.0.bin")

_kokoro = None  # cached Kokoro instance (loaded lazily)
_kokoro_lock = threading.Lock()


def is_available() -> bool:
    """True if the Kokoro model files are present on disk."""
    return os.path.isfile(_MODEL_PATH) and os.path.isfile(_VOICES_PATH)


def _load_kokoro():
    """Load and cache the Kokoro model (first call is slow; ~300 MB)."""
    global _kokoro
    if _kokoro is not None:
        return _kokoro
    with _kokoro_lock:
        if _kokoro is not None:
            return _kokoro
        # Point phonemizer at the bundled espeak-ng so Windows needs no system
        # install. Best-effort — newer kokoro-onnx wires this up itself.
        try:
            import espeakng_loader
            from phonemizer.backend.espeak.wrapper import EspeakWrapper

            EspeakWrapper.set_library(espeakng_loader.get_library_path())
            EspeakWrapper.set_data_path(espeakng_loader.get_data_path())
        except Exception:
            pass
        from kokoro_onnx import Kokoro

        _kokoro = Kokoro(_MODEL_PATH, _VOICES_PATH)
        return _kokoro


def speak_local(
    text: str,
    voice: str,
    speed: float = 1.0,
    cancel: Optional[threading.Event] = None,
) -> bool:
    """Synthesize ``text`` with Kokoro and play it, blocking until done.

    Returns True only if audio was played. Failures are logged and return False
    so a local-TTS problem degrades to silence rather than crashing the
    pipeline. Run off the UI thread (see main.SpeakWorker); ``cancel`` aborts
    playback for barge-in.
    """
    text = (text or "").strip()
    if not text:
        return False
    if not is_available():
        logger.warning(
            "Kokoro model files not found in %s; cannot use local TTS.", _MODELS_DIR
        )
        return False
    try:
        import numpy as np

        import sounddevice as sd
    except Exception as exc:
        logger.warning("Local TTS dependencies unavailable: %s", exc)
        return False

    # British voices (bf_/bm_) sound right with en-gb; everything else en-us.
    lang = "en-gb" if voice[:1].lower() == "b" else "en-us"
    try:
        kokoro = _load_kokoro()
        samples, sample_rate = kokoro.create(
            text, voice=voice, speed=float(speed), lang=lang
        )
    except Exception as exc:
        logger.warning("Kokoro synthesis failed: %s", exc)
        return False

    if samples is None or len(samples) == 0:
        logger.warning("Kokoro produced no audio.")
        return False

    # float32 [-1, 1] -> int16 PCM, then stream in chunks so cancel can abort.
    pcm = np.clip(np.asarray(samples, dtype="float32"), -1.0, 1.0)
    pcm16 = (pcm * 32767.0).astype("<i2").tobytes()
    return _play_int16(pcm16, int(sample_rate or SAMPLE_RATE_FALLBACK), sd, cancel)


def _play_int16(data: bytes, sample_rate: int, sd, cancel) -> bool:
    """Stream int16 PCM bytes into sounddevice, honouring a cancel event."""
    stream = sd.RawOutputStream(samplerate=sample_rate, channels=1, dtype="int16")
    stream.start()
    bytes_per_chunk = CHUNK_FRAMES * 2  # 2 bytes per int16 frame
    wrote_audio = False
    try:
        for offset in range(0, len(data), bytes_per_chunk):
            if cancel is not None and cancel.is_set():
                break
            chunk = data[offset : offset + bytes_per_chunk]
            if chunk:
                stream.write(chunk)
                wrote_audio = True
    finally:
        # On barge-in, abort() drops buffered audio for an instant cut-off.
        abort = getattr(stream, "abort", None)
        if cancel is not None and cancel.is_set() and callable(abort):
            abort()
        else:
            stream.stop()
        stream.close()
    return wrote_audio
