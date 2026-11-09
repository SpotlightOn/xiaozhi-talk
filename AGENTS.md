# Project notes for coding agents

Desktop chat client for Xiaozhi servers.  Python 3.10+, PySide6 (Qt 6) and the
`xiaozhi-sdk` package, `src/` layout, no build step.  All code, comments and UI
strings are English — never German (see "Language" below).

## Layout

| Path | Purpose |
| --- | --- |
| `src/xiaozhi_talk/app.py` | `main()`, QApplication setup, theme, desktop file name |
| `src/xiaozhi_talk/client.py` | `ChatClient` + `Events`: asyncio client around the SDK, no Qt |
| `src/xiaozhi_talk/bridge.py` | `ClientBridge`: runs the client in a thread, exposes Qt signals |
| `src/xiaozhi_talk/ui/main_window.py` | window, menus, voice state machine (manual + hands-free) |
| `src/xiaozhi_talk/ui/composer.py` | text box, Send, mic button, Auto-speak/Mute row |
| `src/xiaozhi_talk/ui/chat_view.py` | message bubbles |
| `src/xiaozhi_talk/ui/accounts_dialog.py` | accounts and settings (incl. input device) |
| `src/xiaozhi_talk/audio.py` | `VoicePlayer`: TTS playback through `QAudioSink` |
| `src/xiaozhi_talk/audio_input.py` | `MicrophoneCapture`: `QAudioSource` → 60 ms PCM frames |
| `src/xiaozhi_talk/voice.py` | frame geometry, tail silence, `is_speech()` |
| `src/xiaozhi_talk/storage/` | `Vault`, `Account`, `Settings`, keyring/Fernet backends |
| `tests/mock_server.py` | in-process websocket server used by the tests |

## Commands

```bash
.venv/bin/python -m pytest           # tests (offscreen, temp config)
ruff check src tests                 # lint
ruff format src tests                # format
grep -rnP "[äöüßÄÖÜ]" src tests README.md   # must stay empty
./run.sh                             # start the app (creates .venv on first use)
./install.sh [--uninstall]           # user install into XDG dirs
```

## How the voice path works (do not re-invent it)

* Frames are 24 kHz mono int16, **exactly 60 ms / 2880 bytes**; the SDK
  resamples to 16 kHz and Opus-encodes them.
* A turn only ends when the audio stream goes quiet: `stop_speech()` appends
  `TAIL_FRAMES` (2.04 s) of silence.  Shorter tails are not transcribed.
* Frames are sent in real time (one per `FRAME_MS`); bursts confuse the VAD.
* Manual mode: click Speak → `start_speech()`, click again → `stop_speech()`.
* Auto-speak (hands-free) lives in `main_window.py`: the microphone stays open,
  `_hands_free_frame()` watches the level (`voice.is_speech`), 3 loud frames call
  `start_recording()`, 34 quiet frames call `stop_recording(keep_capture=True)`.
  Speech during a reply aborts the reply first (barge-in).  The client API is
  the same as manual mode — do not stream continuously, the server then never
  finalises the turn.
* Playback is stereo (mono is often routed to the left channel only); the player
  duplicates the samples.
* `auto_speak` implies `play_audio`.

## Gotchas

* **One session per Device-Id**: a second client kicks the first after a few
  seconds.  Close any running app before testing against the real server.
* The server accepts text only for wake words: long `listen/detect` texts return
  an `alert` error.
* Keepalive: `ChatClient` sends `listen/start` after ~20 s without traffic;
  suppressed while `_busy` (turn running).
* Config lives in `$XIAOZHI_TALK_CONFIG_DIR` or `$XDG_CONFIG_HOME/xiaozhi-talk`;
  tests must use `CredentialStore(tmp_path, prefer_keyring=False)`.
* Qt translates its own strings from the locale: `localization.use_english_qt_strings()`
  runs from the package `__init__` before PySide6 is imported.
* Visual runs need `DISPLAY=:1`; `XIAOZHI_TALK_LOG_LEVEL=DEBUG` for details.

## Language

English only in code, identifiers, comments, UI strings, presets, docs and
commit messages.  German is allowed solely in front-end content (message text
typed by a user, test audio), never in the repository.
