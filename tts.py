"""ElevenLabs streaming text-to-speech playback.

Requests raw PCM (24 kHz, 16-bit mono) so we can stream straight into
sounddevice with no MP3 decoder dependency.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Optional

import requests

try:
    import sounddevice as sd
except Exception:  # pragma: no cover - audio backend may be missing in CI
    sd = None  # type: ignore

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

SAMPLE_RATE = 24000
CHANNELS = 1
REQUEST_TIMEOUT = 30  # seconds to first byte
CHUNK_SIZE = 4096


def _api_key() -> Optional[str]:
    return os.getenv("ELEVENLABS_API_KEY")


def speak(
    text: str,
    voice_id: str,
    model_id: str = "eleven_flash_v2_5",
    stability: float = 0.5,
    similarity_boost: float = 0.75,
    speed: float = 1.0,
    request_timeout: float = REQUEST_TIMEOUT,
    cancel: Optional[threading.Event] = None,
) -> bool:
    """Synthesize ``text`` and play it back, blocking until playback ends.

    Failures are swallowed (logged) so a TTS outage degrades to silence rather
    than crashing the pipeline. Returns True only when playback was attempted
    successfully. Run this off the UI thread (see main.SpeakWorker).

    Pass ``cancel`` (a ``threading.Event``) to support barge-in: when it is set
    mid-playback the stream is aborted immediately and we stop reading chunks.
    """
    text = (text or "").strip()
    if not text:
        return False

    key = _api_key()
    if not key:
        logger.warning("ELEVENLABS_API_KEY not set; skipping speech.")
        return False
    if sd is None:
        logger.warning("sounddevice unavailable; skipping speech.")
        return False

    url = (
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"
        "?optimize_streaming_latency=3&output_format=pcm_24000"
    )
    headers = {
        "xi-api-key": key,
        "Content-Type": "application/json",
        "Accept": "audio/pcm",
    }
    body = {
        "text": text,
        "model_id": model_id,
        "voice_settings": {
            "stability": stability,
            "similarity_boost": similarity_boost,
            "speed": speed,
        },
    }

    try:
        with requests.post(
            url,
            headers=headers,
            json=body,
            stream=True,
            timeout=request_timeout,
        ) as resp:
            if resp.status_code != 200:
                detail = resp.text[:200] if resp.content else ""
                logger.warning("ElevenLabs error %s: %s", resp.status_code, detail)
                return False
            played = _play_stream(resp, cancel)
            if not played:
                logger.warning("ElevenLabs stream contained no playable audio.")
            return played
    except requests.RequestException as exc:
        logger.warning("ElevenLabs request failed: %s", exc)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("TTS playback failed: %s", exc)
    return False


def _play_stream(
    resp: "requests.Response", cancel: Optional[threading.Event] = None
) -> bool:
    """Feed streamed PCM bytes into a sounddevice output stream.

    When ``cancel`` is set (barge-in), stop reading and abort the stream so
    buffered audio is dropped immediately rather than played to the end.
    """
    stream = sd.RawOutputStream(
        samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="int16"
    )
    leftover = b""
    wrote_audio = False
    try:
        stream.start()
        for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
            if cancel is not None and cancel.is_set():
                break
            if not chunk:
                continue
            data = leftover + chunk
            # int16 frames are 2 bytes; hold back any trailing odd byte.
            usable = len(data) - (len(data) % 2)
            if usable:
                stream.write(data[:usable])
                wrote_audio = True
            leftover = data[usable:]
    finally:
        # On barge-in, abort() drops the buffered audio for an instant cut-off;
        # otherwise stop() lets the tail of the reply finish cleanly.
        abort = getattr(stream, "abort", None)
        if cancel is not None and cancel.is_set() and callable(abort):
            abort()
        else:
            stream.stop()
        stream.close()
    return wrote_audio
