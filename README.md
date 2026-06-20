# Voice Assistant

A Windows system-tray co-pilot with a near-zero visual footprint. Hold a global
hotkey to talk, release it when you're done. [Wispr Flow](https://wisprflow.ai)
(bound to the same keys) transcribes your speech, the app captures that text
silently, screenshots your chosen monitor, asks Claude via the Claude Code CLI
in a persistent session, and speaks the answer back. The only thing on screen is
a tiny dot in the corner.

Text-to-speech can run through the **ElevenLabs API**, or fully **locally** with
[Kokoro](https://github.com/thewh1teagle/kokoro-onnx) (fast) or
[Chatterbox](https://github.com/resemble-ai/chatterbox) (higher quality, can
clone your own voice). See [Local TTS](#local-tts-offline).

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the full design.

> [!WARNING]
> **This assistant runs Claude with `--dangerously-skip-permissions`.** It is a
> hands-free, headless agent, so Claude executes tool calls — including running
> shell commands and reading/writing files on your machine — without asking for
> confirmation. That is what lets you ask it to actually *do* things by voice,
> but it means anything you say is acted on with your full user permissions. Run
> it only on a machine you trust, and read
> [`claude_client.py`](claude_client.py) if you want to change this. To require
> approvals, remove the `--dangerously-skip-permissions` flag there.

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
   the **send-screenshot toggle**, Claude model/effort/timeout, the TTS provider
   and voice, TTS quality/timeout, the **codebase path** for coding mode, and the
   **transcript** toggle/port), **Open Transcript**, **Reset Claude Session**,
   **Pause Hotkey**, **Restart Voice Assistant**, or **Quit**. Turn the screenshot
   toggle off to use it as a plain voice assistant — no screen capture, and no
   image is sent to Claude. **Open Transcript** opens the live conversation page
   (see [Live transcript page](#live-transcript-page)) in your browser.
   **Restart** relaunches the app in a fresh process (handy after changing
   settings); Settings also has a **Save & Restart** button.

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
Diagnostics are written to `%APPDATA%\VoiceAssistant\voice-assistant.log`, and
conversation transcripts to `%APPDATA%\VoiceAssistant\transcripts\` (one JSON
file per conversation — see [Live transcript page](#live-transcript-page)).

### Model and voice settings

Settings includes editable Claude model, ElevenLabs model, and ElevenLabs voice
fields. Claude effort maps to the Claude Code CLI `--effort` option (`low`,
`medium`, `high`, `xhigh`, `max`) and can be left at `default` to omit the
flag. Claude and ElevenLabs timeouts are configurable and bounded. ElevenLabs
stability, similarity, and speed are also bounded before use so bad config
values fall back to safe ranges. A **TTS provider** dropdown switches between the
ElevenLabs API and the local Kokoro/Chatterbox engines, with a local-voice
picker and a voice-sample field for Chatterbox cloning — see
[Local TTS](#local-tts-offline).

### Agentic browsing (Claude-driven Chrome)

The assistant can open a real Chrome window and **browse the web for you** —
search, open pages, click, and read — while you watch it happen. The app launches
a Chrome window (with remote debugging) on your chosen monitor and owns it, and
each browsing turn Claude drives it with browser tools from
[Playwright MCP](https://github.com/microsoft/playwright-mcp), attached over the
Chrome DevTools Protocol. Because the app owns the window, it stays open between
turns for you to read.

**Prerequisites:** [Node.js](https://nodejs.org) on PATH (the server runs via
`npx`) and Google Chrome installed. Nothing to `pip install`.

**Enable it** in Settings → *Agentic browsing*. You can also choose:
- **Browser window** — headed (default, so you can watch) or hidden.
- **Browser monitor** — which display the window opens on (*Auto* picks a
  secondary screen so it sits next to you).
- **Browse turn timeout** — browsing turns run longer than a normal reply.

**Use it by voice.** Normal turns are untouched and stay snappy — browsing only
kicks in when you ask for it. Say something like *"open the browser and find the
cppreference page for lock_guard"* or *"search the web for …"* and it opens
Chrome on your chosen monitor and navigates there, then gives a short spoken
summary. The window **stays open** so you can read it, and you stay in browsing
mode for follow-ups like *"scroll down"* or *"click the first result"*. Say
*"close the browser"* or *"stop browsing"* to end it. The trigger phrases live in
[`browser_mcp.py`](browser_mcp.py).

**Fast path for C++ docs.** C++ reference lookups skip the slow agentic loop
(snapshot → reason → click → read). Alongside the Playwright tools, each browsing
turn also gets one dedicated tool, `open_cppreference`, backed by a tiny stdlib
stdio MCP server ([`cppreference_mcp.py`](cppreference_mcp.py)). Ask for any C++
symbol or concept — *"show me lock_guard"*, *"open the docs for std::sort"*,
*"what's the RAII mutex wrapper"* — and Claude passes the rough symbol straight
to that tool. cppreference runs on MediaWiki, so its go-search endpoint resolves
the loose symbol to the exact page **server-side** (you never need the
non-obvious path, e.g. `lock_guard` lives under `/w/cpp/thread/lock_guard`); the
tool does a single navigate of the same owned Chrome — no snapshot, no read-back
— so it stays fast even on a small model. It reuses the live CDP connection via
Playwright when that's installed, and otherwise drives the CDP endpoint directly
with no extra `pip install`. The window stays open and you stay in browsing mode,
so follow-ups like *"scroll down"* still work.

The first browse is slow (it fetches the Playwright MCP package via `npx` and
launches Chrome); later ones reuse the same window. The browser uses a persistent
profile, so logins stick. Agentic browsing works best with a capable model
(Sonnet or Opus) — small models sometimes answer from memory instead of actually
browsing — but the C++ fast path above stays quick on any model.
Because the assistant runs Claude with skip-permissions (see the warning above),
a browsing agent can click and submit forms on your behalf — keep it to sites you
trust.

### Live transcript page

Every turn — what you say and what the assistant replies — is recorded, and you
can watch the conversation as a **live local web page**. Pick **Open Transcript**
from the tray menu (or browse to the URL printed in the log) and the page opens
in your browser. It is served from a tiny built-in web server on **127.0.0.1
only**, so nothing leaves your machine, and it needs nothing to `pip install` —
it is pure standard library, like the browsing/MCP pieces.

The page is **live**: it reacts in real time to the same states as the tray dot.
You'll see it switch to *Listening…* (red) when you hold the hotkey, *Thinking…*
(amber) with your words streaming in as Wispr types them, then *Speaking…*
(green) as the reply lands — using Server-Sent Events, so there's no polling. It
also gives you:

- **Browsable history** — a sidebar of every past conversation (across sessions
  and restarts), newest first, each labelled and timestamped. Click one to read
  it; click the live one (or *Back to live*) to follow along again.
- **Download** — save any conversation as **Markdown**, plain **text**, or
  **JSON** from the buttons in the header.

A *conversation* maps to one Claude session, so it carries across turns and
restarts exactly like the session does; **Reset Claude Session** (or a coding
turn, which uses its own session) starts a new one. Transcripts live as one JSON
file per conversation under `%APPDATA%\VoiceAssistant\transcripts\`. Turn the
whole feature off, or change the port, in Settings → *Transcript* (port `0` picks
a free port automatically). The store and server are in
[`transcript.py`](transcript.py) and [`transcript_server.py`](transcript_server.py);
the page itself is [`ui/transcript.html`](ui/transcript.html).

### Coding mode (run Claude Code against a codebase)

Point the assistant at a project folder and it can **edit that codebase by
voice**. Set a **Codebase path** and tick **Coding mode** in Settings; then a
turn that sounds like a coding or file-editing request — *"edit the cpp file"*,
*"fix the bug in the parser"*, *"add a function that…"*, *"refactor the auth
module"* — runs Claude Code with that folder as its **working directory /
project**, so it reads and edits the files there directly and then speaks a short
summary of what it changed.

Coding turns use their **own Claude session**, separate from the casual voice
conversation, so the two never mix — and they show up as their own (clearly
labelled) thread on the transcript page. **Normal, non-coding turns are
completely unaffected**: the routing is a cheap, deterministic phrase match (see
[`coding.py`](coding.py)), so plain voice use never touches your codebase, and a
coding turn only fires when the mode is enabled *and* the configured folder
exists. Coding turns honour the **Claude timeout** in Settings (raise it for big
edits), and — like browsing — they run with skip-permissions, so the assistant
edits files on your behalf without asking; keep the codebase path on a project
you're happy to have edited. Browsing takes precedence when a turn is both, so
*"look up…"* still browses.

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
