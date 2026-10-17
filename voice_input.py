import os
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


def notify(msg):
    subprocess.run(["notify-send", "-t", "1500", "Voice input", msg])


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
        self.chunks = []
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
        for f in ("model.int8.onnx", "tokens.txt"):
            if not (MODEL_DIR / f).exists():
                notify(f"Downloading {f}")
                urllib.request.urlretrieve(MODEL_URL + f, MODEL_DIR / f"{f}.part")
                (MODEL_DIR / f"{f}.part").rename(MODEL_DIR / f)
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

        self.chunks = []
        self.stream = sd.InputStream(samplerate=RATE, channels=1, dtype="float32", device=MIC,
                                     callback=self.on_audio)
        self.set_state("starting")
        self.stream.start()

    def on_audio(self, data, *_):
        # Bluetooth mics send silence for ~1 s while switching to headset mode.
        if self.state == "starting" and data.any():
            threading.Thread(target=self.set_state, args=("listening",)).start()
        self.chunks.append(data.copy())

    def stop(self):
        time.sleep(0.3)
        self.stream.stop()
        self.stream.close()
        self.set_state("working")
        threading.Thread(target=self.transcribe, daemon=True).start()

    def transcribe(self):
        import numpy as np

        try:
            audio = np.concatenate(self.chunks).flatten() if self.chunks else np.zeros(0, "float32")
            if len(audio) < RATE / 2:
                return
            if abs(audio).max() < 0.01:
                notify("Mic gave only silence")
                return
            stream = self.model.create_stream()
            stream.accept_waveform(RATE, audio)
            self.model.decode_stream(stream)
            text = stream.result.text.strip()
            # Silence comes back as "." or similar.
            if any(c.isalnum() for c in text):
                type_text(text)
        except Exception as e:
            notify(f"Error: {e}")
        finally:
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
