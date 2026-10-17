# voice-input

Local voice typing for X11 + i3. Tray icon + hotkey. Uses SenseVoice (sherpa-onnx) on CPU. Chinese, English, Japanese, Korean, Cantonese.

## Install

Needs X11, `uv`, `xdotool`, `xclip`, `notify-send`, PipeWire or PulseAudio.

    uv tool install git+https://github.com/cassc/voice-input      # or, from a clone: uv tool install .

The model (~230 MB) downloads to `~/.cache/voice-input` on first start.

Add to `~/.config/i3/config`:

    exec --no-startup-id voice-input
    bindsym $mod+grave exec --no-startup-id voice-input toggle

Update after code changes: `uv tool install --reinstall .`

## Start

The i3 `exec` line starts it at login. To start or restart it by hand:

    pkill -f bin/voice-input; setsid -f voice-input

Check it runs: `pgrep -af voice-input`. A second start prints "already running".

## Use

Click the tray icon or press `$mod+grave` to start. A sound plays when the mic is ready: talk then. Press again to stop (another sound).

While you talk, a popup shows a live guess of the current phrase, and it improves as you speak. After each pause (0.5 s), the phrase is pasted into the focused window (Shift+Insert). Your clipboard is restored after.

Icon colors: dark grey = loading model, grey = ready, pink = mic starting (wait), red = listening (talk now), yellow = working.

To quit: `pkill -f bin/voice-input`. (There is no right-click menu: pystray has none on X11.)

## Env vars

- `VOICE_MIC` - sounddevice input, default `pulse` (the PipeWire default source).
- `VOICE_SOUND=0` - no ready/stop sounds.
