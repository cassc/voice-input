import os
import queue
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

PID_FILE = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "voice-input.pid"
RATE = 16000
MIC = os.environ.get("VOICE_MIC", "pulse")
MODEL_DIR = Path.home() / ".cache/voice-input/sense-voice"
MODEL_URL = "https://huggingface.co/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/resolve/main/"
VAD_URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx"
SOUNDS = Path("/usr/share/sounds/freedesktop/stereo")
WINDOW = 512
COLORS = {"loading": "#555555", "idle": "#9a9a9a", "starting": "#e09090", "listening": "#e04040", "working": "#e0b030"}


def toggle_running():
    try:
        os.kill(int(PID_FILE.read_text()), signal.SIGUSR1)
    except (FileNotFoundError, ProcessLookupError, ValueError):
        sys.exit("voice-input is not running")


def already_running():
    try:
        os.kill(int(PID_FILE.read_text()), 0)
        return True
    except (FileNotFoundError, ProcessLookupError, ValueError):
        return False


def notify(msg, replace=None, timeout=1500):
    cmd = ["notify-send", "-p", "-t", str(timeout), "Voice input", msg]
    if replace:
        cmd[1:1] = ["-r", replace]
    return subprocess.run(cmd, capture_output=True, text=True).stdout.strip()


def play(name):
    if os.environ.get("VOICE_SOUND") != "0" and (SOUNDS / name).exists():
        subprocess.Popen(["pw-play", str(SOUNDS / name)])


def type_text(text):
    # xdotool type drops Chinese characters, so paste instead.
    # Shift+Insert works in GUI apps and terminals (alacritty reads primary).
    old = subprocess.run(["xclip", "-o", "-selection", "clipboard"], capture_output=True).stdout
    for sel in ("clipboard", "primary"):
        subprocess.run(["xclip", "-i", "-selection", sel], input=text.encode())
    subprocess.run(["xdotool", "key", "--clearmodifiers", "shift+Insert"])
    time.sleep(0.3)
    subprocess.run(["xclip", "-i", "-selection", "clipboard"], input=old)


class App:
    def __init__(self):
        self.model = None
        self.state = "loading"
        self.vad = None
        self.audio = None
        self.stream = None
        self.lock = threading.Lock()

    def icon_image(self):
        from PIL import Image, ImageDraw

        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        c = COLORS[self.state]
        d.rounded_rectangle((22, 6, 42, 40), radius=10, fill=c)
        d.arc((14, 20, 50, 48), 0, 180, fill=c, width=4)
        d.line((32, 48, 32, 58), fill=c, width=4)
        return img

    def set_state(self, state):
        self.state = state
        self.icon.icon = self.icon_image()
        self.icon.title = f"Voice input: {state}"

    def load_model(self):
        import sherpa_onnx

        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        files = {f: MODEL_URL + f for f in ("model.int8.onnx", "tokens.txt")}
        files["silero_vad.onnx"] = VAD_URL
        for f, url in files.items():
            if not (MODEL_DIR / f).exists():
                notify(f"Downloading {f}")
                urllib.request.urlretrieve(url, MODEL_DIR / f"{f}.part")
                (MODEL_DIR / f"{f}.part").rename(MODEL_DIR / f)
        silero = sherpa_onnx.SileroVadModelConfig(model=str(MODEL_DIR / "silero_vad.onnx"), min_silence_duration=0.5,
                                                  window_size=WINDOW)
        self.vad = sherpa_onnx.VoiceActivityDetector(sherpa_onnx.VadModelConfig(silero_vad=silero, sample_rate=RATE))
        self.model = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(MODEL_DIR / "model.int8.onnx"), tokens=str(MODEL_DIR / "tokens.txt"),
            num_threads=4, use_itn=True)
        self.set_state("idle")

    def toggle(self, *_):
        with self.lock:
            if self.state == "idle":
                try:
                    self.start()
                except Exception as e:
                    notify(f"Mic error: {e}")
            elif self.state in ("starting", "listening"):
                self.stop()

    def start(self):
        import sounddevice as sd

        self.audio = queue.Queue()
        self.vad.reset()
        self.stream = sd.InputStream(samplerate=RATE, channels=1, dtype="float32", device=MIC,
                                     callback=self.on_audio)
        self.set_state("starting")
        self.stream.start()
        threading.Thread(target=self.listen, daemon=True).start()

    def on_audio(self, data, *_):
        # Bluetooth mics send silence for ~1 s while switching to headset mode.
        if self.state == "starting" and data.any():
            self.state = "listening"
            threading.Thread(target=self.set_state, args=("listening",)).start()
            play("message.oga")
        self.audio.put(data[:, 0].copy())

    def stop(self):
        time.sleep(0.3)
        self.stream.stop()
        self.stream.close()
        play("complete.oga")
        self.set_state("working")
        self.audio.put(None)

    def decode(self, samples):
        stream = self.model.create_stream()
        stream.accept_waveform(RATE, samples)
        self.model.decode_stream(stream)
        return stream.result.text.strip()

    def paste(self, text):
        # Silence comes back as "." or similar.
        if not any(c.isalnum() for c in text):
            return
        if self.pasted and text[0].isascii() and not self.pasted[-1].isspace():
            text = " " + text
        type_text(text)
        self.pasted = text

    def phrase_done(self):
        found = not self.vad.empty()
        while not self.vad.empty():
            self.vad.pop()
        return found

    def listen(self):
        """Paste each phrase after a pause, and show a live guess of the current phrase in a popup."""
        import numpy as np

        buf, phrase, peak, shown, popup = np.zeros(0, "float32"), [], 0.0, 0.0, None
        self.pasted = ""
        try:
            while (data := self.audio.get()) is not None:
                peak = max(peak, float(abs(data).max()))
                buf = np.concatenate([buf, data])
                phrase.append(data)
                while len(buf) >= WINDOW:
                    self.vad.accept_waveform(buf[:WINDOW])
                    buf = buf[WINDOW:]
                # Decode all audio since the last paste: the VAD segment cuts the first sound.
                if self.phrase_done():
                    self.paste(self.decode(np.concatenate(phrase)))
                    phrase = []
                elif self.vad.is_speech_detected() and time.time() - shown > 0.5:
                    popup = notify(self.decode(np.concatenate(phrase)) or "...", popup, 10000)
                    shown = time.time()
            self.vad.flush()
            if self.phrase_done():
                self.paste(self.decode(np.concatenate(phrase)))
            if peak < 0.01:
                notify(f"Mic gave only silence (peak {peak:.4f})")
        except Exception as e:
            notify(f"Error: {e}")
        finally:
            if popup:
                notify("", popup, 1)
            self.set_state("idle")

    def quit(self, *_):
        PID_FILE.unlink(missing_ok=True)
        self.icon.stop()

    def run(self):
        import pystray
        from pystray import MenuItem as Item

        menu = pystray.Menu(
            Item(lambda _: "Stop" if self.state == "listening" else "Start", self.toggle, default=True),
            Item("Quit", self.quit),
        )
        self.icon = pystray.Icon("voice-input", self.icon_image(), "Voice input: loading", menu)
        # pystray's X11 backend forgets to listen for clicks, so left-click does nothing.
        from Xlib import X
        self.icon._window.change_attributes(event_mask=X.ExposureMask | X.StructureNotifyMask | X.ButtonPressMask)
        PID_FILE.write_text(str(os.getpid()))
        signal.signal(signal.SIGUSR1, lambda *_: threading.Thread(target=self.toggle).start())
        signal.signal(signal.SIGTERM, lambda *_: self.quit())
        threading.Thread(target=self.load_model, daemon=True).start()
        self.icon.run()


def main():
    if sys.argv[1:] == ["toggle"]:
        toggle_running()
    elif already_running():
        sys.exit("voice-input is already running")
    else:
        App().run()


if __name__ == "__main__":
    main()
