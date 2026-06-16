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
- An **ElevenLabs API key**

## Setup

```bash
pip install -r requirements.txt
copy .env.example .env   # then add your ELEVENLABS_API_KEY
python main.py
```

## Usage

1. The app runs in the system tray with no window.
2. Bind Wispr Flow to **Ctrl+Win** (the same hotkey the app listens for).
   Set Wispr to copy its transcription to the clipboard (the default capture
   method) or to type it into the focused app (choose `Hidden input` in
   Settings).
3. **Hold Ctrl+Win**, speak your question, then **release**. Wispr stops
   and hands over the text; the app takes it from there silently.
4. The only visible cue is a small dot in the bottom-right corner: red while
   recording, amber while thinking, green while speaking. It's gone when idle.
5. Right-click the tray icon for **Settings** (capture monitor, capture method,
   Claude model/effort, voice, and TTS quality),
   **Pause Hotkey**, or **Quit**.

### Capture methods

- `clipboard` (default) — Wispr copies the transcription; the app reads the
  clipboard a moment after you release the key. If the clipboard does not
  change during recording, the app ignores the turn instead of reusing stale
  text from the previous request.
- `hidden_input` — Wispr types into an invisible, off-screen box that the app
  reads back. Use this when Wispr needs a focused text field or active
  insertion point before it will emit the transcript.
- `visible_input` - Wispr types into a small bottom-of-screen text box with
  selected placeholder text. Use this when Wispr needs a visible editable field
  or selected text before it will replace the current contents.

Set the method in Settings. The post-release delay still lives in `capture` in
the config file.

Config is stored at `%APPDATA%\VoiceAssistant\config.json`; the Claude session
id persists there so conversations carry across questions and restarts. The
spoken-reply behaviour (short, plain, no markdown) is set by `SYSTEM_PROMPT` in
`claude_client.py`.
Diagnostics are written to `%APPDATA%\VoiceAssistant\voice-assistant.log`.

### Model and voice settings

Settings includes editable Claude and ElevenLabs model fields. Claude effort
maps to the Claude Code CLI `--effort` option (`low`, `medium`, `high`,
`xhigh`, `max`) and can be left at `default` to omit the flag. ElevenLabs
stability, similarity, and speed are bounded before use so bad config values
fall back to safe ranges.
