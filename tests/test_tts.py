from __future__ import annotations

import unittest
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


class TTSTests(unittest.TestCase):
    def test_speak_sends_configured_voice_settings(self) -> None:
        with (
            patch.dict("os.environ", {"ELEVENLABS_API_KEY": "key"}),
            patch.object(tts, "sd", FakeSoundDevice()),
            patch("tts.requests.post", return_value=FakeResponse()) as post,
        ):
            tts.speak(
                "hello",
                "voice-id",
                "eleven_multilingual_v2",
                stability=0.2,
                similarity_boost=0.9,
                speed=1.1,
                request_timeout=12,
            )

        body = post.call_args.kwargs["json"]
        self.assertEqual(post.call_args.kwargs["timeout"], 12)
        self.assertEqual(body["model_id"], "eleven_multilingual_v2")
        self.assertEqual(
            body["voice_settings"],
            {"stability": 0.2, "similarity_boost": 0.9, "speed": 1.1},
        )


if __name__ == "__main__":
    unittest.main()
