# Voice Input Plan

Local voice typing for this PC (X11 + i3, Intel CPU, no CUDA).

## How it works

1. A daemon starts at login and loads the faster-whisper model once (CPU, int8).
2. A hotkey in i3 runs `voice-input toggle`.
3. First press: start recording from the mic. A notification says "Listening".
4. Second press: stop recording, transcribe, then type the text into the active window with `xdotool type`.
5. Language is auto-detected, so English, Chinese, or mixed speech all work.

## Files

- `voice_input.py` - daemon and `toggle` command in one file (about 60 lines).
- `requirements.txt` - `faster-whisper`, `sounddevice`, `numpy`.
- `voice-input.service` - systemd user service for the daemon.
- `README.md` - setup and i3 binding.

## Details

- Toggle: the daemon writes its PID to `$XDG_RUNTIME_DIR/voice-input.pid`. `toggle` sends `SIGUSR1` to it.
- Recording: `sounddevice.InputStream`, 16 kHz mono, collect chunks in memory.
- Model: `small` by default, set by env var `VOICE_MODEL` (`base` is faster, `medium` is more accurate).
- Transcribe: `vad_filter=True` to cut silence; join segments into one string.
- Typing: `xdotool type --clearmodifiers --delay 0 -- <text>`. If typing CJK text fails in some apps, fall back to copying to the clipboard (`xclip`) and sending `ctrl+v`.
- Feedback: `notify-send` for "Listening", "Working", and errors.

## Steps

1. Create a venv and install `requirements.txt`.
2. Write `voice_input.py`; test with `python voice_input.py daemon` and `python voice_input.py toggle`.
3. Add the systemd user service and enable it.
4. Add to i3 config: `bindsym $mod+v exec --no-startup-id ~/projects/cassc/voice-input/.venv/bin/python ~/projects/cassc/voice-input/voice_input.py toggle`.
5. Check speed of `small` vs `base` on this CPU; pick the default.

## Later (not now)

- Push-to-talk (hold key) instead of toggle.
- Fix common words with a custom `initial_prompt`.
