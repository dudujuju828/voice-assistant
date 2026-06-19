"""Local text-to-speech via Chatterbox (Resemble AI).

A higher-quality offline alternative to the Kokoro backend: a ~0.5B model that
clones a voice from a short reference clip. Runs on the RTX 4060 (CUDA) with an
automatic CPU fallback. Like the Kokoro path, the whole clip is synthesized and
then streamed into sounddevice in small chunks (see ``audio_playback``) so a
barge-in can abort playback mid-sentence.

Heavy imports (torch, chatterbox) are deferred into the functions so the rest of
the app — and the test suite — can import this module without Chatterbox or its
weights installed. A missing dependency or model degrades to silence (logged),
never a crash.

Voice cloning: callers pass a path to a reference WAV. When none is given we
fall back to a bundled ``models/voice_sample.wav`` if present, else Chatterbox's
built-in voice. A 7–20 s clean clip of one speaker works best; the model
resamples/downmixes internally, so format doesn't matter.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Optional

import audio_playback

logger = logging.getLogger(__name__)

SAMPLE_RATE_FALLBACK = 24000

# Chatterbox's recommended neutral defaults. Higher exaggeration = more
# expressive/dramatic; lower cfg_weight can help pacing on fast reference voices.
DEFAULT_EXAGGERATION = 0.5
DEFAULT_CFG_WEIGHT = 0.5

_MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
# Used automatically when the caller doesn't specify a voice sample, so a user's
# own voice "just works" once dropped here (git-ignored, like the model files).
_BUNDLED_VOICE_SAMPLE = os.path.join(_MODELS_DIR, "voice_sample.wav")

_model = None  # cached ChatterboxTTS instance (loaded lazily)
_model_lock = threading.Lock()


def is_available() -> bool:
    """True if the Chatterbox package is importable."""
    try:
        import chatterbox.tts  # noqa: F401
    except Exception:
        return False
    return True


def _select_device() -> str:
    """Prefer CUDA (RTX 4060) when torch reports it; otherwise CPU."""
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


def _load_model():
    """Load and cache the Chatterbox model (first call downloads ~1 GB of weights)."""
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:
            return _model
        from chatterbox.tts import ChatterboxTTS

        device = _select_device()
        _model = ChatterboxTTS.from_pretrained(device=device)
        logger.info("Chatterbox running on %s.", device)
        return _model


def _resolve_voice_sample(voice_sample: Optional[str]) -> Optional[str]:
    """Pick the reference WAV: explicit path -> bundled sample -> built-in voice."""
    candidate = (voice_sample or "").strip()
    if candidate:
        if os.path.isfile(candidate):
            return candidate
        logger.warning(
            "Chatterbox voice sample not found: %s; trying the bundled sample.",
            candidate,
        )
    if os.path.isfile(_BUNDLED_VOICE_SAMPLE):
        return _BUNDLED_VOICE_SAMPLE
    return None  # Chatterbox uses its built-in voice when prompt is None.


def speak_chatterbox(
    text: str,
    voice_sample: Optional[str] = None,
    exaggeration: float = DEFAULT_EXAGGERATION,
    cfg_weight: float = DEFAULT_CFG_WEIGHT,
    cancel: Optional[threading.Event] = None,
) -> bool:
    """Synthesize ``text`` with Chatterbox and play it, blocking until done.

    Returns True only if audio was played. Failures are logged and return False
    so a local-TTS problem degrades to silence rather than crashing the pipeline.
    Run off the UI thread (see main.SpeakWorker); ``cancel`` aborts playback for
    barge-in.
    """
    text = (text or "").strip()
    if not text:
        return False
    if not is_available():
        logger.warning(
            "Chatterbox is not installed; cannot use it for local TTS. "
            "Install with: pip install chatterbox-tts"
        )
        return False
    try:
        import numpy as np

        import sounddevice as sd
    except Exception as exc:
        logger.warning("Local TTS dependencies unavailable: %s", exc)
        return False

    prompt = _resolve_voice_sample(voice_sample)
    try:
        model = _load_model()
        wav = model.generate(
            text,
            audio_prompt_path=prompt,
            exaggeration=float(exaggeration),
            cfg_weight=float(cfg_weight),
        )
    except Exception as exc:
        logger.warning("Chatterbox synthesis failed: %s", exc)
        return False

    samples = _tensor_to_float_array(wav, np)
    if samples is None or len(samples) == 0:
        logger.warning("Chatterbox produced no audio.")
        return False

    sample_rate = int(getattr(model, "sr", SAMPLE_RATE_FALLBACK) or SAMPLE_RATE_FALLBACK)
    pcm16 = audio_playback.float_to_pcm16(samples, np)
    return audio_playback.play_int16(pcm16, sample_rate, sd, cancel)


def _tensor_to_float_array(wav, np):
    """Flatten a Chatterbox wav (a torch tensor, shape [1, N]) to 1-D float32.

    Uses duck typing so this module never has to import torch: a tensor exposes
    ``detach``/``cpu``/``numpy``; a plain array (used in tests) skips straight to
    ``np.asarray``.
    """
    arr = wav
    for method in ("detach", "cpu", "numpy"):
        fn = getattr(arr, method, None)
        if callable(fn):
            arr = fn()
    return np.asarray(arr, dtype="float32").reshape(-1)
