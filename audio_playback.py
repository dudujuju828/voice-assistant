"""Shared PCM playback for the local TTS backends (Kokoro, Chatterbox).

Both local backends synthesize a whole clip, convert it to 16-bit PCM, and
stream it into sounddevice in small chunks so a barge-in cancel can abort
playback mid-sentence. That conversion + streaming lives here so each backend
stays thin and they can't drift apart.

numpy and sounddevice are passed in by the caller rather than imported here, so
importing a TTS backend never forces the audio stack to load and the callers
keep their own "dependency missing -> degrade to silence" handling.
"""
from __future__ import annotations

import threading
from typing import Optional

CHUNK_FRAMES = 2048  # int16 frames per write — small enough for snappy barge-in


def float_to_pcm16(samples, np) -> bytes:
    """Convert float32 [-1, 1] samples to little-endian int16 PCM bytes."""
    pcm = np.clip(np.asarray(samples, dtype="float32"), -1.0, 1.0)
    return (pcm * 32767.0).astype("<i2").tobytes()


def play_int16(
    data: bytes,
    sample_rate: int,
    sd,
    cancel: Optional[threading.Event],
) -> bool:
    """Stream int16 PCM bytes into sounddevice, honouring a cancel event.

    Returns True only if at least one chunk was written. On barge-in (cancel set)
    ``abort()`` drops buffered audio for an instant cut-off; otherwise the stream
    drains normally.
    """
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
        abort = getattr(stream, "abort", None)
        if cancel is not None and cancel.is_set() and callable(abort):
            abort()
        else:
            stream.stop()
        stream.close()
    return wrote_audio
