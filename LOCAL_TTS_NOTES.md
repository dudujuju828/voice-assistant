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

## Higher-quality model: Chatterbox + voice cloning — code DONE
Request: swap the local model to something higher quality and clone the user's
own voice (sample at C:\Users\max\Documents\Audacity\myvoice.wav, 14s).

- Engine: **Chatterbox** (Resemble AI, ~0.5B, MIT). Quality > Kokoro; slower
  (~1–2s/reply on the 4060 vs ~0.3s). torch-based, so it adds a torch/torchaudio
  stack alongside onnxruntime (they coexist — torch bundles its own CUDA cu12,
  onnxruntime uses the cu13 wheels).
- New provider `tts.provider = "chatterbox"` (3rd option beside elevenlabs/local).
- Backend `tts_chatterbox.py`: lazy ChatterboxTTS load, torch.cuda device select +
  CPU fallback, generate -> flatten tensor -> shared audio_playback. Same
  cancel/abort barge-in as Kokoro.
- Shared `audio_playback.py`: float_to_pcm16 + play_int16, extracted from
  tts_local so both local backends stream identically (no duplication).
- Voice cloning: config `tts.voice_sample` (path). Empty -> auto-use bundled
  `models/voice_sample.wav` (the user's clip, copied there, git-ignored) ->
  else Chatterbox built-in voice. Settings has a path field + Browse button.
- SpeakWorker dispatches chatterbox; main passes config.tts_voice_sample.
- Tests: test_tts_chatterbox + test_audio_playback + config/main_state additions
  (Chatterbox mocked). 93 passing.

### TODO to actually run it
- Install deps into the .venv (heavy, ~2.5GB torch + ~1GB model on first use):
    pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124
    pip install chatterbox-tts
- Verify kokoro-onnx/onnxruntime still import after (numpy/dep shuffle risk).
- First real synth not yet run (would download model + play audio; avoided
  during the user's active session per the no-playback constraint).
