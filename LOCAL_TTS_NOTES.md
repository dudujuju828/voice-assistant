# Local TTS — request & plan

## Request (from user)
ElevenLabs credits ran out (now topped up again, so the API still works for
now). Want the option to run TTS **locally** instead of the ElevenLabs API:
Claude's reply text → local voice model → play the audio. Add a **setting** to
choose between the ElevenLabs API and local inference.

Chosen local engine: **Kokoro** (in-app, runs on the RTX 4060 / CPU).
(Note: "Qwen" mentioned earlier is an LLM, not a TTS model — unrelated.)

## Plan
- Add a `tts.provider` setting: `elevenlabs` (default) | `local`.
- New `tts_local.py` backend: load Kokoro once, synthesize, play via sounddevice
  with the same barge-in/cancel support as the ElevenLabs path.
- `tts.py` / `SpeakWorker` dispatch on the provider.
- Settings dialog: provider dropdown + local voice picker.
- Tests for the provider config + dispatch (Kokoro itself mocked).

## Constraints
- Do **not** restart or interrupt the running app while the user is using it.
- No audio playback during verification (would interrupt the user).

## Status — DONE
- [x] Kokoro installed (`kokoro-onnx`, `soundfile`) + model files in `models/`
      (git-ignored; download links in README)
- [x] Backend `tts_local.py` (lazy load, chunked playback, cancel/abort)
- [x] Provider dispatch in `SpeakWorker` (`tts.provider`: elevenlabs | local)
- [x] Settings UI: TTS provider dropdown + local voice picker
- [x] Tests (config + dispatch + tts_local, Kokoro mocked) — 77 passing
- [x] README "Local TTS (offline)" section

Verified Kokoro synthesizes (af_heart, 24 kHz): cold load ~9.5s, first synth
~5.7s, faster after. Not played aloud during dev so as not to interrupt use.
