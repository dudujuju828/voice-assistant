# Voice Assistant

A Windows system-tray co-pilot. Press a global hotkey, speak your question
(typed into a focused box by [Wispr Flow](https://wisprflow.ai) or the
keyboard), and the app screenshots your chosen monitor, asks Claude (Opus) via
the Claude Code CLI in a persistent session, and speaks the answer back through
ElevenLabs streaming TTS — with a non-intrusive status overlay throughout.

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

1. The app runs in the system tray.
2. Press **Ctrl+Alt+Space** (default hotkey).
3. Speak (Wispr types into the box) or type, then press **Enter**.
4. Watch the overlay: 🎤 Listening → 🤔 Thinking → 🔊 Speaking.
5. Right-click the tray icon for **Settings** (capture monitor, voice),
   **Pause Hotkey**, or **Quit**.

Config is stored at `%APPDATA%\VoiceAssistant\config.json`; the Claude session
id persists there so conversations carry across questions and restarts.
