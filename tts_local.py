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

import glob
import logging
import os
import threading
from typing import Optional

import audio_playback

logger = logging.getLogger(__name__)

SAMPLE_RATE_FALLBACK = 24000

_MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
_MODEL_PATH = os.path.join(_MODELS_DIR, "kokoro-v1.0.onnx")
_VOICES_PATH = os.path.join(_MODELS_DIR, "voices-v1.0.bin")

_kokoro = None  # cached Kokoro instance (loaded lazily)
_kokoro_lock = threading.Lock()


def _register_cuda_dll_dirs() -> None:
    """Add pip-installed NVIDIA CUDA/cuDNN DLL folders to the search path.

    onnxruntime's CUDA provider needs the cuBLAS/cuDNN/etc. DLLs on the search
    path to load. The nvidia pip wheels scatter them under site-packages/nvidia
    in layouts that vary by CUDA version (e.g. cu13/bin/x86_64 for CUDA 13,
    <component>/bin for older ones, cudnn/bin for cuDNN), so we register every
    directory under nvidia/ that actually contains a DLL. Best-effort: if the
    wheels aren't installed we silently stay on CPU.
    """
    if not hasattr(os, "add_dll_directory"):  # non-Windows
        return
    try:
        import nvidia
    except Exception:
        return
    for base in getattr(nvidia, "__path__", []):
        for dll in glob.glob(os.path.join(base, "**", "*.dll"), recursive=True):
            try:
                os.add_dll_directory(os.path.dirname(dll))
            except OSError:
                pass


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

        _kokoro = _load_kokoro_gpu() or Kokoro(_MODEL_PATH, _VOICES_PATH)
        return _kokoro


def _load_kokoro_gpu():
    """Try to build a CUDA-backed Kokoro; return None to fall back to CPU.

    Requests the CUDA provider with CPU as a fallback in the same session, so
    onnxruntime drops to CPU automatically if the GPU libraries can't load.
    Logs which provider actually ran so GPU use can be confirmed in the log.
    """
    try:
        import onnxruntime as ort
        from kokoro_onnx import Kokoro

        _register_cuda_dll_dirs()
        if "CUDAExecutionProvider" not in ort.get_available_providers():
            logger.info("Kokoro: CUDA provider unavailable; using CPU.")
            return None

        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        sess = ort.InferenceSession(_MODEL_PATH, providers=providers)
        active = sess.get_providers()
        if "CUDAExecutionProvider" not in active:
            # CUDA libs failed to load; onnxruntime fell back. Use CPU path.
            logger.warning(
                "Kokoro: CUDA libraries missing; running on CPU. Active: %s", active
            )
            return None
        logger.info("Kokoro running on GPU (CUDAExecutionProvider).")
        return Kokoro.from_session(sess, _VOICES_PATH)
    except Exception as exc:
        logger.warning("Kokoro GPU init failed (%s); falling back to CPU.", exc)
        return None


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
    pcm16 = audio_playback.float_to_pcm16(samples, np)
    return audio_playback.play_int16(
        pcm16, int(sample_rate or SAMPLE_RATE_FALLBACK), sd, cancel
    )
