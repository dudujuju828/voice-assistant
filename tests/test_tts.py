from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import tts


class FakeResponse:
    status_code = 200
    content = b""
    text = ""

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args) -> None:
        return None

    def iter_content(self, chunk_size: int):
        return iter((b"\x00\x00",))


class EmptyResponse(FakeResponse):
    def iter_content(self, chunk_size: int):
        return iter(())


class FakeStream:
    def start(self) -> None:
        return None

    def write(self, data: bytes) -> None:
        return None

    def stop(self) -> None:
        return None

    def close(self) -> None:
        return None


class FakeSoundDevice:
    def RawOutputStream(self, **_kwargs) -> FakeStream:  # noqa: N802
        return FakeStream()


class MultiChunkResponse(FakeResponse):
    def iter_content(self, chunk_size: int):
        return iter((b"\x00\x00", b"\x00\x00", b"\x00\x00"))


class CancelOnFirstWriteStream(FakeStream):
    """Stream that trips the cancel event the moment the first chunk plays."""

    def __init__(self, cancel: threading.Event) -> None:
        self._cancel = cancel
        self.writes = 0
        self.aborted = False
        self.stopped = False

    def write(self, data: bytes) -> None:
        self.writes += 1
        self._cancel.set()

    def abort(self) -> None:
        self.aborted = True

    def stop(self) -> None:
        self.stopped = True


class TTSTests(unittest.TestCase):
    def test_speak_sends_configured_voice_settings(self) -> None:
        with (
            patch.dict("os.environ", {"ELEVENLABS_API_KEY": "key"}),
            patch.object(tts, "sd", FakeSoundDevice()),
            patch("tts.requests.post", return_value=FakeResponse()) as post,
        ):
            result = tts.speak(
                "hello",
                "voice-id",
                "eleven_multilingual_v2",
                stability=0.2,
                similarity_boost=0.9,
                speed=1.1,
                request_timeout=12,
            )

        self.assertTrue(result)
        body = post.call_args.kwargs["json"]
        self.assertEqual(post.call_args.kwargs["timeout"], 12)
        self.assertEqual(body["model_id"], "eleven_multilingual_v2")
        self.assertEqual(
            body["voice_settings"],
            {"stability": 0.2, "similarity_boost": 0.9, "speed": 1.1},
        )

    def test_speak_returns_false_without_api_key(self) -> None:
        with (
            patch.dict("os.environ", {}, clear=True),
            patch.object(tts, "sd", FakeSoundDevice()),
            patch("tts.logger.warning"),
        ):
            self.assertFalse(tts.speak("hello", "voice-id"))

    def test_speak_returns_false_for_empty_audio_stream(self) -> None:
        with (
            patch.dict("os.environ", {"ELEVENLABS_API_KEY": "key"}),
            patch.object(tts, "sd", FakeSoundDevice()),
            patch("tts.requests.post", return_value=EmptyResponse()),
            patch("tts.logger.warning"),
        ):
            self.assertFalse(tts.speak("hello", "voice-id"))

    def test_speak_aborts_playback_when_cancelled(self) -> None:
        # Barge-in: once cancel is set mid-stream we stop reading further chunks
        # and abort() the stream (drop buffered audio) instead of letting it
        # drain via stop().
        cancel = threading.Event()
        stream = CancelOnFirstWriteStream(cancel)
        fake_sd = SimpleNamespace(RawOutputStream=lambda **_kwargs: stream)
        with (
            patch.dict("os.environ", {"ELEVENLABS_API_KEY": "key"}),
            patch.object(tts, "sd", fake_sd),
            patch("tts.requests.post", return_value=MultiChunkResponse()),
        ):
            result = tts.speak("hello", "voice-id", cancel=cancel)

        self.assertTrue(result)  # one chunk played before the cancel landed
        self.assertEqual(stream.writes, 1)  # stopped after the first chunk
        self.assertTrue(stream.aborted)
        self.assertFalse(stream.stopped)


if __name__ == "__main__":
    unittest.main()
