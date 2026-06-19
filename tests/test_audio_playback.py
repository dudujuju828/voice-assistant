from __future__ import annotations

import threading
import types
import unittest

import numpy as np

import audio_playback


class FakeStream:
    def __init__(self) -> None:
        self.writes = 0
        self.aborted = False
        self.stopped = False

    def start(self) -> None:
        return None

    def write(self, data: bytes) -> None:
        self.writes += 1

    def abort(self) -> None:
        self.aborted = True

    def stop(self) -> None:
        self.stopped = True

    def close(self) -> None:
        return None


def _fake_sd(stream: FakeStream) -> types.SimpleNamespace:
    return types.SimpleNamespace(RawOutputStream=lambda **_kwargs: stream)


class FloatToPcm16Tests(unittest.TestCase):
    def test_clips_and_scales_to_int16(self) -> None:
        samples = np.array([0.0, 1.0, -1.0, 2.0, -2.0], dtype="float32")
        pcm = np.frombuffer(audio_playback.float_to_pcm16(samples, np), dtype="<i2")
        # Out-of-range values clip to the [-1, 1] peak before scaling.
        self.assertEqual(list(pcm), [0, 32767, -32767, 32767, -32767])

    def test_emits_two_bytes_per_sample(self) -> None:
        samples = np.zeros(10, dtype="float32")
        self.assertEqual(len(audio_playback.float_to_pcm16(samples, np)), 20)


class PlayInt16Tests(unittest.TestCase):
    def test_streams_all_chunks_then_stops(self) -> None:
        stream = FakeStream()
        data = b"\x00\x00" * (audio_playback.CHUNK_FRAMES * 3)
        played = audio_playback.play_int16(data, 24000, _fake_sd(stream), None)

        self.assertTrue(played)
        self.assertEqual(stream.writes, 3)
        self.assertTrue(stream.stopped)
        self.assertFalse(stream.aborted)

    def test_cancel_aborts_before_writing(self) -> None:
        stream = FakeStream()
        cancel = threading.Event()
        cancel.set()
        data = b"\x00\x00" * (audio_playback.CHUNK_FRAMES * 3)
        played = audio_playback.play_int16(data, 24000, _fake_sd(stream), cancel)

        self.assertFalse(played)  # nothing written
        self.assertEqual(stream.writes, 0)
        self.assertTrue(stream.aborted)
        self.assertFalse(stream.stopped)


if __name__ == "__main__":
    unittest.main()
