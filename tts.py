"""ElevenLabs streaming text-to-speech playback.

Requests raw PCM (24 kHz, 16-bit mono) so we can stream straight into
sounddevice with no MP3 decoder dependency.
"""
from __future__ import annotations

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
) -> None:
    """Synthesize ``text`` and play it back, blocking until playback ends.

    Failures are swallowed (logged) so a TTS outage degrades to silence rather
    than crashing the pipeline. Run this off the UI thread (see SpeakWorker).
    """
    text = (text or "").strip()
    if not text:
        return

    key = _api_key()
    if not key:
        print("[tts] ELEVENLABS_API_KEY not set; skipping speech.")
        return
    if sd is None:
        print("[tts] sounddevice unavailable; skipping speech.")
        return

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
            url, headers=headers, json=body, stream=True, timeout=REQUEST_TIMEOUT
        ) as resp:
            if resp.status_code != 200:
                detail = resp.text[:200] if resp.content else ""
                print(f"[tts] ElevenLabs error {resp.status_code}: {detail}")
                return
            _play_stream(resp)
    except requests.RequestException as exc:
        print(f"[tts] request failed: {exc}")
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[tts] playback failed: {exc}")


def _play_stream(resp: "requests.Response") -> None:
    """Feed streamed PCM bytes into a sounddevice output stream."""
    stream = sd.RawOutputStream(
        samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="int16"
    )
    stream.start()
    leftover = b""
    try:
        for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
            if not chunk:
                continue
            data = leftover + chunk
            # int16 frames are 2 bytes; hold back any trailing odd byte.
            usable = len(data) - (len(data) % 2)
            if usable:
                stream.write(data[:usable])
            leftover = data[usable:]
    finally:
        stream.stop()
        stream.close()


class SpeakWorker(threading.Thread):
    """Background thread wrapper so playback never blocks the Qt loop."""

    def __init__(
        self,
        text: str,
        voice_id: str,
        model_id: str = "eleven_flash_v2_5",
        stability: float = 0.5,
        similarity_boost: float = 0.75,
        speed: float = 1.0,
        on_done=None,
    ) -> None:
        super().__init__(daemon=True)
        self._text = text
        self._voice_id = voice_id
        self._model_id = model_id
        self._stability = stability
        self._similarity_boost = similarity_boost
        self._speed = speed
        self._on_done = on_done

    def run(self) -> None:
        try:
            speak(
                self._text,
                self._voice_id,
                self._model_id,
                self._stability,
                self._similarity_boost,
                self._speed,
            )
        finally:
            if self._on_done:
                self._on_done()
