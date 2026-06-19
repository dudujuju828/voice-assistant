# Voice Assistant

A Windows system-tray co-pilot with a near-zero visual footprint. Hold a global
hotkey to talk, release it when you're done. [Wispr Flow](https://wisprflow.ai)
(bound to the same keys) transcribes your speech, the app captures that text
silently, screenshots your chosen monitor, asks Claude (Opus) via the Claude
Code CLI in a persistent session, and speaks the answer back through ElevenLabs
streaming TTS. The only thing on screen is a tiny dot in the corner.

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the full design.

## Prerequisites

- **Windows** (relies on Win32 APIs for the hotkey, monitor capture, and focus
  handling). Run at **normal integrity — not elevated** (elevation breaks the
  Wispr → input-box typing via UIPI).
- **Python 3.10+**
- **Claude Code CLI** on PATH: `npm i -g @anthropic-ai/claude-code`
- An **ElevenLabs API key** (only for the ElevenLabs TTS provider; the local
  Kokoro provider needs no key — see [Local TTS](#local-tts-offline)).

## Setup

```bash
pip install -r requirements.txt
copy .env.example .env   # then add your ELEVENLABS_API_KEY
python main.py
```

## Usage

1. The app runs in the system tray with no window.
2. Bind Wispr Flow to **Ctrl+Win** (the same hotkey the app listens for).
   Set Wispr to type into the focused app for the default visible text box
   capture method, or choose another capture method in Settings.
3. **Hold Ctrl+Win**, speak your question, then **release**. Wispr stops
   and hands over the text; the app takes it from there silently.
4. The only visible cue is a small dot in the bottom-right corner: red while
   recording, amber while thinking, green while speaking. It's gone when idle.
5. Right-click the tray icon for **Settings** (capture monitor, capture method,
   the **send-screenshot toggle**, Claude model/effort/timeout, voice, and TTS
   quality/timeout), **Reset Claude Session**, **Pause Hotkey**, or **Quit**.
   Turn the screenshot toggle off to use it as a plain voice assistant — no
   screen capture, and no image is sent to Claude.

### Capture methods

- `visible_input` (default) - Wispr types into a small bottom-of-screen text box
  with selected placeholder text. Use this when Wispr needs a visible editable
  field or selected text before it will replace the current contents.
- `clipboard` — Wispr copies the transcription; the app reads the
  clipboard a moment after you release the key. If the clipboard does not
  change during recording, the app ignores the turn instead of reusing stale
  text from the previous request.
- `hidden_input` — Wispr types into an invisible, off-screen box that the app
  reads back. Use this when Wispr needs a focused text field or active
  insertion point before it will emit the transcript.

Set the method and post-release delay in Settings.
Older configs that still have the historical default `clipboard` method are
migrated once to `visible_input`; choosing `clipboard` in Settings afterward is
preserved.

Config is stored at `%APPDATA%\VoiceAssistant\config.json`; the Claude session
id persists there so conversations carry across questions and restarts. The
spoken-reply behaviour (short, plain, no markdown) is set by `SYSTEM_PROMPT` in
`claude_client.py`.
Diagnostics are written to `%APPDATA%\VoiceAssistant\voice-assistant.log`.

### Model and voice settings

Settings includes editable Claude model, ElevenLabs model, and ElevenLabs voice
fields. Claude effort maps to the Claude Code CLI `--effort` option (`low`,
`medium`, `high`, `xhigh`, `max`) and can be left at `default` to omit the
flag. Claude and ElevenLabs timeouts are configurable and bounded. ElevenLabs
stability, similarity, and speed are also bounded before use so bad config
values fall back to safe ranges.

### Local TTS (offline)

You can run text-to-speech locally with [Kokoro](https://github.com/thewh1teagle/kokoro-onnx)
instead of the ElevenLabs API — handy when you're offline or out of credits. No
API key is needed.

1. Install the optional deps: `pip install kokoro-onnx soundfile`.
2. Download the model files into a `models/` folder next to `main.py`:
   - [`kokoro-v1.0.onnx`](https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx) (~325 MB)
   - [`voices-v1.0.bin`](https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin) (~28 MB)
3. In Settings, set **TTS provider** to *Local — Kokoro* and pick a **Local
   voice** (e.g. `af_heart`, `am_adam`, `bf_emma`; the field is editable so any
   Kokoro voice id works).

The model files are large and are git-ignored, so they live only on your
machine. The phonemizer (espeak-ng) ships bundled via `espeakng-loader`, so no
separate system install is required on Windows. The first reply after launch
loads the model (a few seconds); later replies are faster. If the model files
are missing, the app logs a warning and stays silent rather than crashing — so
switch back to the ElevenLabs provider if you haven't downloaded them.

#### GPU acceleration (NVIDIA)

Local TTS runs on the GPU automatically when CUDA is available, which is far
faster (on an RTX 4060, synthesis drops from ~5–6 s on CPU to ~0.3 s once warm).
Set it up by replacing the CPU onnxruntime with the GPU build and adding the
CUDA 13 / cuDNN 9 runtime wheels (no system CUDA toolkit needed — the wheels are
self-contained):

```bash
pip uninstall onnxruntime
pip install onnxruntime-gpu
pip install nvidia-cublas nvidia-cuda-runtime nvidia-cuda-nvrtc \
            nvidia-cufft nvidia-curand nvidia-cusparse nvidia-cudnn-cu13
```

`tts_local.py` registers the wheels' DLL directories, requests the CUDA
execution provider, and falls back to CPU automatically if the GPU libraries
aren't present (logged, never a crash). The log line `Kokoro running on GPU
(CUDAExecutionProvider)` confirms the GPU is in use; check
`voice-assistant.log`.

### Higher-quality local TTS with voice cloning (Chatterbox)

For noticeably more natural speech — and to clone a specific voice — set the
**TTS provider** to *Local — Chatterbox*. [Chatterbox](https://github.com/resemble-ai/chatterbox)
(Resemble AI) is a ~0.5 B model; it's slower than Kokoro (~1–2 s per reply on an
RTX 4060 vs ~0.3 s) but clearly higher quality and can speak in a voice you
provide.

1. Install the deps. For GPU, install the CUDA torch build **first** so pip
   doesn't pull the CPU-only wheel:

   ```bash
   pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124
   pip install chatterbox-tts
   ```

2. Provide a voice to clone (optional). Drop a clean 7–20 s WAV of one speaker at
   `models/voice_sample.wav` and it's used automatically, or set a path under
   **Settings → Cloning voice sample** (the *Browse…* button opens a file
   picker). Leave it blank with no bundled file to use Chatterbox's built-in
   voice.

3. Set **TTS provider** to *Local — Chatterbox* and save.

The ~1 GB model downloads from Hugging Face on first use, then is cached.
`tts_chatterbox.py` auto-detects CUDA (`torch.cuda`) and falls back to CPU,
logging `Chatterbox running on cuda`/`cpu`. Like the Kokoro path, a missing
dependency or model degrades to silence rather than crashing — switch back to
another provider if you haven't installed it. The voice sample is git-ignored,
so it stays on your machine.
