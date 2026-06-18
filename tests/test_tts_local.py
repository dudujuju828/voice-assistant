from __future__ import annotations

import sys
import threading
import types
import unittest
from unittest.mock import patch

import tts_local


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


class CancelOnFirstWriteStream(FakeStream):
    def __init__(self, cancel: threading.Event) -> None:
        super().__init__()
        self._cancel = cancel

    def write(self, data: bytes) -> None:
        super().write(data)
        self._cancel.set()


def _fake_sd(stream: FakeStream) -> types.SimpleNamespace:
    return types.SimpleNamespace(RawOutputStream=lambda **_kwargs: stream)


class TTSLocalTests(unittest.TestCase):
    def test_empty_text_returns_false(self) -> None:
        self.assertFalse(tts_local.speak_local("   ", "af_heart"))

    def test_missing_model_returns_false(self) -> None:
        with (
            patch("tts_local.is_available", return_value=False),
            patch("tts_local.logger.warning"),
        ):
            self.assertFalse(tts_local.speak_local("hi", "af_heart"))

    def test_synthesizes_and_plays(self) -> None:
        import numpy as np

        stream = FakeStream()
        kokoro = types.SimpleNamespace(
            create=lambda *a, **k: (np.zeros(5000, dtype="float32"), 24000)
        )
        with (
            patch("tts_local.is_available", return_value=True),
            patch("tts_local._load_kokoro", return_value=kokoro),
            patch.dict(sys.modules, {"sounddevice": _fake_sd(stream)}),
        ):
            result = tts_local.speak_local("hello", "af_heart")

        self.assertTrue(result)
        self.assertGreater(stream.writes, 0)
        self.assertTrue(stream.stopped)
        self.assertFalse(stream.aborted)

    def test_cancel_aborts_playback(self) -> None:
        import numpy as np

        cancel = threading.Event()
        stream = CancelOnFirstWriteStream(cancel)
        kokoro = types.SimpleNamespace(
            create=lambda *a, **k: (np.zeros(50000, dtype="float32"), 24000)
        )
        with (
            patch("tts_local.is_available", return_value=True),
            patch("tts_local._load_kokoro", return_value=kokoro),
            patch.dict(sys.modules, {"sounddevice": _fake_sd(stream)}),
        ):
            result = tts_local.speak_local("hello", "af_heart", cancel=cancel)

        self.assertTrue(result)  # one chunk played before the cancel landed
        self.assertEqual(stream.writes, 1)
        self.assertTrue(stream.aborted)
        self.assertFalse(stream.stopped)

    def test_synthesis_failure_returns_false(self) -> None:
        def boom(*_a, **_k):
            raise RuntimeError("model exploded")

        kokoro = types.SimpleNamespace(create=boom)
        with (
            patch("tts_local.is_available", return_value=True),
            patch("tts_local._load_kokoro", return_value=kokoro),
            patch("tts_local.logger.warning"),
        ):
            self.assertFalse(tts_local.speak_local("hello", "af_heart"))


if __name__ == "__main__":
    unittest.main()
