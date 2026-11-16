import os
import time
import shutil
import logging
import subprocess
import threading

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

STORAGE_ROOT = Path(
    os.getenv("STORAGE_DIR", str(BASE_DIR))
).resolve()

DATA_DIR = STORAGE_ROOT / "data"
LOG_DIR = STORAGE_ROOT / "logs"

DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

TICKER_FILE = DATA_DIR / "ticker.txt"
GLITCH_FILE = DATA_DIR / "glitch.txt"
HOST_FILE = DATA_DIR / "host.txt"

for path in (TICKER_FILE, GLITCH_FILE, HOST_FILE):
    if not path.exists():
        path.write_text("", encoding="utf-8")

logger = logging.getLogger("infer-live.streamer")


class StreamManager:

    def __init__(self):

        self.process = None
        self.running = False

        self.lock = threading.RLock()
        self.stop_event = threading.Event()

        self.config = {}

        self.started_at = None
        self.last_error = None
        self.restart_count = 0

        self.ticker_file = TICKER_FILE

    def update_text(self, text):

        safe_text = str(text).replace("\x00", "")

        temporary = self.ticker_file.with_suffix(".tmp")

        temporary.write_text(
            safe_text,
            encoding="utf-8"
        )

        temporary.replace(self.ticker_file)

    def build_filter(self):

        filters = []

        layout = self.config.get(
            "layout", "landscape"
        )

        # Landscape 16:9
        if layout == "landscape":

            filters.append(
                "scale=1920:1080:"
                "force_original_aspect_ratio=decrease"
            )

            filters.append(
                "pad=1920:1080:(ow-iw)/2:(oh-ih)/2"
            )

        # Shorts 9:16
        elif layout == "shorts":

            filters.append(
                "scale=1080:1920:"
                "force_original_aspect_ratio=decrease"
            )

            filters.append(
                "pad=1080:1920:(ow-iw)/2:(oh-ih)/2"
            )

        filters.append("setsar=1")

        # Live Ticker
        ticker_path = str(
            self.ticker_file.resolve()
        ).replace("\\", "/")

        ticker_path = ticker_path.replace(
            "'", "\\'"
        )

        filters.append(
            "drawtext="
            f"textfile='{ticker_path}':"
            "reload=1:"
            "fontcolor=yellow:"
            "fontsize=28:"
            "x=20:"
            "y=h-th-30:"
            "box=1:"
            "boxcolor=black@0.6"
        )

        return ",".join(filters)

    def start(self, config):

        with self.lock:

            if self.running:
                return False

            if not shutil.which("ffmpeg"):
                self.last_error = "FFmpeg executable not found"
                return False

            self.config = config.copy()

            self.stop_event.clear()

            self.started_at = time.time()
            self.last_error = None
            self.restart_count = 0

            self.running = True

            self.update_text(
                self.config.get("custom_text", "")
            )

            thread = threading.Thread(
                target=self._worker,
                daemon=True,
                name="InferLiveWorker"
            )

            thread.start()

            return True

    def _build_command(self):

        stream_key = self.config["stream_key"]

        video_path = self.config["video_path"]

        output_url = (
            "rtmp://a.rtmp.youtube.com/live2/"
            + stream_key
        )

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "warning",

            "-stream_loop", "-1",
            "-re",

            "-i", video_path,

            "-map", "0:v:0",
            "-map", "0:a:0?",

            "-vf", self.build_filter(),

            "-c:v", "libx264",
            "-preset", "veryfast",
            "-tune", "zerolatency",

            "-pix_fmt", "yuv420p",
            "-r", "30",

            "-b:v", "4500k",
            "-maxrate", "4500k",
            "-bufsize", "9000k",

            "-c:a", "aac",
            "-b:a", "128k",
            "-ar", "44100",

            "-f", "flv",
            output_url
        ]

        return cmd

    def _worker(self):

        try:

            while not self.stop_event.is_set():

                process = None

                try:

                    cmd = self._build_command()

                    log_path = LOG_DIR / "ffmpeg.log"

                    with log_path.open("ab") as log_file:

                        process = subprocess.Popen(
                            cmd,
                            stdout=subprocess.DEVNULL,
                            stderr=log_file,
                            start_new_session=True
                        )

                    with self.lock:
                        self.process = process

                    logger.info(
                        "FFmpeg process started: PID %s",
                        process.pid
                    )

                    while not self.stop_event.is_set():

                        return_code = process.poll()

                        if return_code is not None:
                            break

                        time.sleep(2)

                    if self.stop_event.is_set():

                        if process.poll() is None:

                            process.terminate()

                            try:
                                process.wait(timeout=10)

                            except subprocess.TimeoutExpired:
                                process.kill()
                                process.wait(timeout=5)

                        break

                    return_code = process.poll()

                    self.last_error = (
                        f"FFmpeg exited with code {return_code}"
                    )

                    logger.error(self.last_error)

                    self.restart_count += 1

                    # Retry after unexpected FFmpeg exit.
                    self.stop_event.wait(5)

                except Exception as exc:

                    self.last_error = str(exc)

                    logger.exception(
                        "Streaming worker error"
                    )

                    self.restart_count += 1

                    self.stop_event.wait(5)

                finally:

                    with self.lock:

                        if self.process is process:
                            self.process = None

                    if process is not None and process.poll() is None:

                        try:
                            process.kill()
                            process.wait(timeout=5)
                        except Exception:
                            pass

        finally:

            with self.lock:

                self.process = None
                self.running = False
                self.started_at = None

            logger.info("Streaming worker stopped")

    def stop(self):

        self.stop_event.set()

        with self.lock:
            process = self.process

        if process is not None and process.poll() is None:

            try:
                process.terminate()
            except Exception:
                pass

    def status(self):

        with self.lock:

            active = (
                self.process is not None
                and self.process.poll() is None
            )

            uptime = 0

            if self.started_at is not None:
                uptime = int(
                    time.time() - self.started_at
                )

            return {
                "running": self.running,
                "ffmpeg_active": active,
                "uptime_seconds": uptime,
                "restart_count": self.restart_count,
                "last_error": self.last_error,
                "layout": self.config.get("layout")
            }


stream_manager = StreamManager()
