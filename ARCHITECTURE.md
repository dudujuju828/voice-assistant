# Voice Assistant — API & Library Manifest

> Windows tray co-pilot: global hotkey → Wispr types speech into overlay input → capture chosen monitor → Claude (Opus) turn → ElevenLabs streaming TTS. Python + PySide6.

## ⚠️ Integrity-level correction
Run the app at **normal (medium) integrity — NOT elevated.**
- Full-monitor capture works fine without elevation.
- Elevation triggers UIPI: a non-elevated Wispr Flow cannot type into an elevated input box. Running normal integrity is required for the Wispr→input-box flow.

---

## LIBRARY MANIFEST

```bash
pip install PySide6 mss pywin32 requests sounddevice python-dotenv
```

| Package | pip | Why |
|---|---|---|
| **PySide6** | `pip install PySide6` | GUI framework (LGPL, unlike PyQt6). Tray, overlay, input window, event loop. Qt6 is per-monitor-V2 DPI-aware by default. |
| **mss** | `pip install mss` | Fast multi-monitor screen capture by pixel bounds. Cleaner than BitBlt; returns raw BGRA. |
| **pywin32** | `pip install pywin32` | `win32gui`/`win32api`/`win32con` for `RegisterHotKey`, monitor enumeration (`EnumDisplayMonitors`), friendly device names, `SetForegroundWindow`, window-style flags. |
| **requests** | `pip install requests` | ElevenLabs REST calls (streaming via `stream=True`). |
| **sounddevice** | `pip install sounddevice` | Play streamed PCM/MP3 audio from ElevenLabs with low latency. (Alt: `pip install pyaudio`.) |
| **python-dotenv** | `pip install python-dotenv` | Load `ELEVENLABS_API_KEY` etc. from `.env`. |

**Optional (better Claude integration):** `pip install claude-agent-sdk` — typed async Python wrapper over the Claude Code CLI. Avoids manual subprocess/JSON parsing. Still requires the `claude` CLI installed (it shells out to it).

**Bundle Claude Code? → No, assume installed.** It's a Node-based CLI with its own auth/update lifecycle; vendoring it into a PyInstaller build is fragile. Instead: on first run, check `shutil.which("claude")`; if missing, show a tray notification linking to install docs and disable the hotkey. Document `npm i -g @anthropic-ai/claude-code` (or the native installer) as a prerequisite.

---

## WINDOWS API REFERENCE

### Push-to-talk hotkey (user32 low-level keyboard hook)
We need both key *down* and key *up*, so `RegisterHotKey` (down-only,
`WM_HOTKEY`) doesn't fit. Instead we install a low-level keyboard hook:
- **`SetWindowsHookExW(WH_KEYBOARD_LL=13, proc, hMod, 0)`** — global hook; the
  callback runs on the installing thread, which must pump Win32 messages (Qt's
  event loop does this). `hMod` = `GetModuleHandleW(None)`.
- The callback receives `WM_KEYDOWN/KEYUP/SYSKEYDOWN/SYSKEYUP` with a
  `KBDLLHOOKSTRUCT` (we read `vkCode`). When the trigger key (Win) goes down
  *and* the modifiers are held (checked with `GetAsyncKeyState`), we emit
  `pressed`; when the trigger key goes up we emit `released`. Auto-repeat is
  ignored via an "already active" flag.
- **`CallNextHookEx`** must be called and the proc must return fast — a slow
  hook stalls all keyboard input. So `pressed`/`released` are connected with
  `Qt.QueuedConnection` and the real work happens back on the event loop.
- **`UnhookWindowsHookEx(hook)`** on shutdown / pause.

Semantics: hold the combo → Wispr (bound to the same keys) records → release →
we capture the transcript, screenshot, and run a Claude turn.

### Capturing the transcript silently (no visible input box)
There is **no on-screen input window**. Two capture methods (config
`capture.method`):

1. **`clipboard`** (default) — Wispr is set to copy its transcription. After the
   key is released we wait `capture.delay_ms` (default 500 ms) for Wispr to
   finish, then read `QApplication.clipboard().text()`.
2. **`hidden_input`** — an invisible, off-screen `QLineEdit` (`hidden_input.py`)
   that briefly grabs focus on press via `SetForegroundWindow` so Wispr's
   keystrokes land there; we read and clear it after release.

The hotkey no longer needs to grant foreground rights to a visible box. The
only on-screen element is the **StatusOverlay**, which must **never** steal
focus:
- Window flags: `Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowTransparentForInput`.
- Win32 ex-style: `WS_EX_NOACTIVATE (0x08000000) | WS_EX_TOOLWINDOW | WS_EX_TRANSPARENT`, applied via `win32gui.SetWindowLong(hwnd, GWL_EXSTYLE, ...)` after `show()`.
- It is a tiny ~40 px corner dot at low opacity: red recording, amber
  processing, green speaking, gone when idle. No text, no emojis.

Relevant calls: `SetForegroundWindow`, `SetWindowLong`/`GetWindowLong` with `GWL_EXSTYLE`, `ShowWindow`. Constants in `win32con`.

### Monitor enumeration (detect all displays + bounds)
- **`win32api.EnumDisplayMonitors()`** → list of `(hMonitor, hdc, (l,t,r,b))`.
- **`win32api.GetMonitorInfo(hMonitor)`** → `{'Monitor': (l,t,r,b), 'Work': (...), 'Flags': 1=primary, 'Device': '\\\\.\\DISPLAY1'}`.
- Map `Device` → friendly name via `win32api.EnumDisplayDevices(device, 0).DeviceString` for the settings UI ("Dell U2720Q", etc.).
- Store the chosen monitor's `Monitor` rect (virtual-screen coords) in config.

### DPI awareness
- **`ctypes.windll.user32.SetProcessDpiAwarenessContext(-4)`** (`DPI_AWARE_PER_MONITOR_AWARE_V2`) at process start, **before** creating the QApplication. Qt6 sets this itself, but calling it explicitly guarantees mss bounds == physical pixels.
- With per-monitor-V2, `EnumDisplayMonitors` rects and `mss` capture are both in physical pixels → 1:1, no scaling math needed.

### Capture
- Use **mss**, not BitBlt — feed it the stored monitor rect:
  ```python
  with mss.mss() as sct:
      shot = sct.grab({"left": l, "top": t, "width": r-l, "height": b-t})
  ```
- Save PNG via `mss.tools.to_png(shot.rgb, shot.size, output=path)`, then hand the path to Claude.
- (BitBlt alternative if mss ever misbehaves: `GetDC(0)` → `CreateCompatibleDC` → `BitBlt(SRCCOPY)` over the monitor rect.)

### System tray
- `QSystemTrayIcon` + `QMenu` (Settings / Pause hotkey / Quit). `showMessage()` for "Claude not installed" etc. No raw Win32 needed.

---

## EXTERNAL API ENDPOINTS

### ElevenLabs — streaming TTS
- **`POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream`**
- Headers: `xi-api-key: <key>`, `Content-Type: application/json`, `Accept: audio/mpeg`.
- Body:
  ```json
  {
    "text": "...Claude's reply...",
    "model_id": "eleven_flash_v2_5",
    "voice_settings": { "stability": 0.5, "similarity_boost": 0.75 }
  }
  ```
- `model_id`: `eleven_flash_v2_5` for lowest latency (~75ms); `eleven_turbo_v2_5` for quality/latency balance.
- Query param `?optimize_streaming_latency=3` shaves first-byte latency.
- Consume with `requests.post(..., stream=True)` → iterate `resp.iter_content(chunk_size=4096)` → feed an MP3 decoder / sounddevice. For raw PCM playback add `?output_format=pcm_24000` and stream straight into `sounddevice.RawOutputStream`.
- List voices: `GET https://api.elevenlabs.io/v1/voices` (populate settings dropdown).

### Claude Code CLI (subprocess, per-turn spawn)
Confirmed pattern — spawn one process per turn, do **not** hold an interactive stdin pipe.

```
claude -p "<prompt>" \
  --resume <session-id> \
  --model opus \
  --output-format json \
  --add-dir <screenshot-dir>
```

| Flag | Purpose |
|---|---|
| `-p, --print` | Non-interactive: run one turn, print result, exit. |
| `--resume <id>` | Continue the persistent session (carries conversation memory). |
| `--model opus` | Use Opus (`claude-opus-4-8`). |
| `--output-format json` | Machine-parseable result (`{"result": "...", "session_id": "..."}`). Use `stream-json` if you want token-by-token to start TTS earlier. |
| `--add-dir <dir>` | Grant read access to the screenshot folder so Claude can open the image. |

**First turn** (no session yet): run without `--resume`; parse `session_id` from the JSON result and persist it. **Subsequent turns:** `--resume <that id>`.

**Subprocess best practice on Windows:**
- Use `asyncio.create_subprocess_exec` (or `subprocess.run` in a QThread to keep the Qt loop alive).
- Resolve the binary: `shutil.which("claude")` — on Windows it's typically `claude.cmd` (a shim), so pass the full path and `shell=False`.
- Reference the screenshot in the prompt by absolute path: `"Here is my screen: <path>. <user question>"`.
- **Error handling = "start fresh":** if exit code ≠ 0 or stderr contains `No conversation found` / session errors, clear the stored `session_id` and retry once without `--resume`. (Mirrors the global session-reuse rule.)
- Timeout the subprocess (e.g. 120s) and surface failures via the StatusOverlay ("Error").

---

## CONFIG PERSISTENCE

`%APPDATA%\VoiceAssistant\config.json` (via `os.getenv("APPDATA")`):

```json
{
  "capture_monitor_device": "\\\\.\\DISPLAY2",
  "hotkey": { "mods": ["ctrl"], "vk": "Win" },
  "elevenlabs": { "voice_id": "...", "model_id": "eleven_flash_v2_5" },
  "claude": { "session_id": null, "model": "opus" }
}
```
- Store monitor by **`Device` name**, not index/rect — indices reshuffle when displays are plugged/unplugged; re-resolve bounds at runtime via `EnumDisplayMonitors`. If the saved device is gone, fall back to primary and re-prompt in settings.
- `session_id` lives here so it survives restarts; null = fresh session next turn.
- Secrets (`ELEVENLABS_API_KEY`) go in `.env` / Windows Credential Manager, **not** this file.

---

## MVP FILE STRUCTURE

```
voice-assistant/
├── main.py                # entrypoint: SetProcessDpiAwareness → QApplication → wire it up
├── config.py              # load/save %APPDATA%\config.json, defaults
├── hotkey.py              # WH_KEYBOARD_LL hook → push-to-talk pressed/released
├── hidden_input.py        # invisible off-screen QLineEdit (hidden_input capture)
├── monitors.py            # EnumDisplayMonitors, friendly names, resolve device→rect
├── capture.py             # mss grab of chosen monitor → PNG path
├── claude_client.py       # subprocess spawn, SYSTEM_PROMPT, --resume, json parse, session mgmt
├── tts.py                 # ElevenLabs streaming POST → sounddevice playback
├── ui/
│   ├── tray.py            # QSystemTrayIcon + menu
│   ├── status_overlay.py  # non-activating click-through corner dot (no text)
│   └── settings.py        # monitor dropdown, hotkey, voice picker
├── .env                   # ELEVENLABS_API_KEY=...
└── requirements.txt
```

---

## ARCHITECTURE SUMMARY (updated flow)

```
[Tray running, normal integrity]
        │
   hold hotkey (WH_KEYBOARD_LL: trigger down + mods held → "pressed"); Wispr records
        │   (Wispr is bound to the same keys)
        ▼
[StatusOverlay: red dot — recording]   (WS_EX_NOACTIVATE — never steals focus)
        │
   user speaks, then releases the hotkey → Wispr stops and hands over the text
        │
   wait capture.delay_ms → read transcript (clipboard / hidden_input)
        ├──► capture.py: mss grab of chosen monitor → screenshot.png
        │
        ▼
[StatusOverlay: amber dot — processing]
        │
   claude_client.py: claude -p "<text> + <png>" --append-system-prompt SYSTEM_PROMPT --resume <id> --model opus
        │   (parse JSON result + session_id; on error → clear id, retry fresh)
        ▼
[StatusOverlay: green dot — speaking]
        │
   tts.py: POST /v1/text-to-speech/{voice}/stream → stream chunks → sounddevice
        │
        ▼
[StatusOverlay hides → back to idle tray]
```

### Build order (MVP)
1. Tray + push-to-talk hook + idle. 2. Silent capture (clipboard / hidden input) works with Wispr. 3. Monitor enumerate + settings + capture. 4. Claude subprocess turn (text only, then with screenshot). 5. ElevenLabs streaming playback. 6. StatusOverlay dot polish.
```
```
