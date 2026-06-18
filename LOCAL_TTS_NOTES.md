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

## GPU acceleration — DONE
- onnxruntime-gpu 1.27 (needs CUDA 13 + cuDNN 9). Supplied via pip wheels:
  nvidia-cublas / cuda-runtime / cuda-nvrtc / cufft / curand / cusparse (CUDA 13,
  unsuffixed names) + nvidia-cudnn-cu13. No system CUDA toolkit; driver 581.80.
- CUDA 13 wheels land under nvidia/cu13/bin/x86_64 (+ nvidia/cudnn/bin), so
  _register_cuda_dll_dirs() recursively adds every nvidia dir containing a DLL.
- tts_local._load_kokoro_gpu builds an InferenceSession with
  [CUDA, CPU] providers via Kokoro.from_session; auto CPU fallback + logging.
- Measured on RTX 4060: load 2.3s, synth ~0.3s warm (was ~5.7s CPU) — ~18x.
