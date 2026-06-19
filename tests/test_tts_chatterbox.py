from __future__ import annotations

import sys
import threading
import types
import unittest
from unittest.mock import patch

import tts_chatterbox


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


def _fake_model(samples, sr: int = 24000):
    """A stand-in ChatterboxTTS: records the prompt, returns ``samples``."""
    calls: list[dict] = []

    def generate(text, audio_prompt_path=None, **kwargs):
        calls.append({"text": text, "audio_prompt_path": audio_prompt_path, **kwargs})
        return samples

    return types.SimpleNamespace(generate=generate, sr=sr, calls=calls)


class TTSChatterboxTests(unittest.TestCase):
    def test_empty_text_returns_false(self) -> None:
        self.assertFalse(tts_chatterbox.speak_chatterbox("   "))

    def test_missing_dependency_returns_false(self) -> None:
        with (
            patch("tts_chatterbox.is_available", return_value=False),
            patch("tts_chatterbox.logger.warning"),
        ):
            self.assertFalse(tts_chatterbox.speak_chatterbox("hi"))

    def test_synthesizes_and_plays(self) -> None:
        import numpy as np

        stream = FakeStream()
        model = _fake_model(np.zeros(5000, dtype="float32"))
        with (
            patch("tts_chatterbox.is_available", return_value=True),
            patch("tts_chatterbox._load_model", return_value=model),
            patch("tts_chatterbox._resolve_voice_sample", return_value="ref.wav"),
            patch.dict(sys.modules, {"sounddevice": _fake_sd(stream)}),
        ):
            result = tts_chatterbox.speak_chatterbox("hello", "ref.wav")

        self.assertTrue(result)
        self.assertGreater(stream.writes, 0)
        self.assertTrue(stream.stopped)
        self.assertFalse(stream.aborted)
        # The resolved sample is threaded through to the model.
        self.assertEqual(model.calls[0]["audio_prompt_path"], "ref.wav")

    def test_accepts_2d_tensor_shape(self) -> None:
        # Chatterbox returns shape [1, N]; it must be flattened before playback.
        import numpy as np

        stream = FakeStream()
        model = _fake_model(np.zeros((1, 5000), dtype="float32"))
        with (
            patch("tts_chatterbox.is_available", return_value=True),
            patch("tts_chatterbox._load_model", return_value=model),
            patch("tts_chatterbox._resolve_voice_sample", return_value=None),
            patch.dict(sys.modules, {"sounddevice": _fake_sd(stream)}),
        ):
            self.assertTrue(tts_chatterbox.speak_chatterbox("hello"))
        self.assertGreater(stream.writes, 0)

    def test_cancel_aborts_playback(self) -> None:
        import numpy as np

        cancel = threading.Event()
        stream = CancelOnFirstWriteStream(cancel)
        model = _fake_model(np.zeros(50000, dtype="float32"))
        with (
            patch("tts_chatterbox.is_available", return_value=True),
            patch("tts_chatterbox._load_model", return_value=model),
            patch("tts_chatterbox._resolve_voice_sample", return_value=None),
            patch.dict(sys.modules, {"sounddevice": _fake_sd(stream)}),
        ):
            result = tts_chatterbox.speak_chatterbox("hello", cancel=cancel)

        self.assertTrue(result)  # one chunk played before the cancel landed
        self.assertEqual(stream.writes, 1)
        self.assertTrue(stream.aborted)
        self.assertFalse(stream.stopped)

    def test_synthesis_failure_returns_false(self) -> None:
        def boom(*_a, **_k):
            raise RuntimeError("model exploded")

        model = types.SimpleNamespace(generate=boom, sr=24000)
        with (
            patch("tts_chatterbox.is_available", return_value=True),
            patch("tts_chatterbox._load_model", return_value=model),
            patch("tts_chatterbox._resolve_voice_sample", return_value=None),
            patch("tts_chatterbox.logger.warning"),
        ):
            self.assertFalse(tts_chatterbox.speak_chatterbox("hello"))


class VoiceSampleResolutionTests(unittest.TestCase):
    def test_explicit_existing_path_is_used(self) -> None:
        with patch("tts_chatterbox.os.path.isfile", return_value=True):
            self.assertEqual(
                tts_chatterbox._resolve_voice_sample("C:/me.wav"), "C:/me.wav"
            )

    def test_missing_explicit_path_falls_back_to_bundled(self) -> None:
        # Explicit path absent, bundled sample present.
        def isfile(path: str) -> bool:
            return path == tts_chatterbox._BUNDLED_VOICE_SAMPLE

        with (
            patch("tts_chatterbox.os.path.isfile", side_effect=isfile),
            patch("tts_chatterbox.logger.warning"),
        ):
            self.assertEqual(
                tts_chatterbox._resolve_voice_sample("C:/gone.wav"),
                tts_chatterbox._BUNDLED_VOICE_SAMPLE,
            )

    def test_no_sample_anywhere_uses_builtin_voice(self) -> None:
        with patch("tts_chatterbox.os.path.isfile", return_value=False):
            self.assertIsNone(tts_chatterbox._resolve_voice_sample(""))

    def test_blank_sample_uses_bundled_when_present(self) -> None:
        with patch("tts_chatterbox.os.path.isfile", return_value=True):
            self.assertEqual(
                tts_chatterbox._resolve_voice_sample(None),
                tts_chatterbox._BUNDLED_VOICE_SAMPLE,
            )


if __name__ == "__main__":
    unittest.main()
