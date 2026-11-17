import os
import time
import shutil
import logging
import subprocess
import threading
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
STORAGE_ROOT = Path(os.getenv("STORAGE_DIR", str(BASE_DIR))).resolve()

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
        self.glitch_file = GLITCH_FILE
        self.host_file = HOST_FILE

    def update_text(self, text):
        safe_text = str(text).replace("\x00", "")
        temporary = self.ticker_file.with_suffix(".tmp")
        temporary.write_text(safe_text, encoding="utf-8")
        temporary.replace(self.ticker_file)

    def _has_audio(self, video_path: str) -> bool:
        if not shutil.which("ffprobe"):
            return False
        try:
            res = subprocess.run(
                [
                    "ffprobe", "-v", "error",
                    "-select_streams", "a:0",
                    "-show_entries", "stream=codec_type",
                    "-of", "csv=p=0", video_path
                ],
                capture_output=True,
                text=True,
                timeout=5
            )
            return "audio" in res.stdout.lower()
        except Exception:
            return False

    def build_filter(self):
        filters = []
        layout = self.config.get("layout", "shorts")

        if layout == "landscape":
            filters += [
                "scale=1280:720:force_original_aspect_ratio=decrease",
                "pad=1280:720:(ow-iw)/2:(oh-ih)/2",
                "format=yuv420p"
            ]
        else:
            filters += [
                "scale=720:1280:force_original_aspect_ratio=decrease",
                "pad=720:1280:(ow-iw)/2:(oh-ih)/2",
                "format=yuv420p"
            ]

        filters.append("setsar=1")

        if self.config.get("feat_wea") == "true":
            filters.append("eq=brightness=0.02:saturation=1.1")
        if self.config.get("feat_vis") == "true":
            filters.append("hue=s=1.15")

        if self.config.get("stone_1") == "true" or self.config.get("feat_1") == "true" or self.config.get("stone_pulse") == "true":
            filters.append("eq=brightness='0.03*sin(t*3)'")
        if self.config.get("stone_2") == "true" or self.config.get("feat_2") == "true" or self.config.get("stone_vibe") == "true":
            filters.append("eq=contrast=1.15:saturation=1.2")
        if self.config.get("stone_3") == "true" or self.config.get("feat_3") == "true" or self.config.get("stone_glitch") == "true":
            filters.append("rgbashift=rh=2:bv=2")
        
        if self.config.get("stone_4") == "true" or self.config.get("feat_4") == "true" or self.config.get("stone_ghost") == "true":
            bs = chr(92)
            t_path = str(self.ticker_file).replace(bs, "/").replace(":", bs + ":")
            filters.append(f"drawtext=textfile='{t_path}':reload=1:fontcolor=cyan:fontsize=36:x=30:y=30:box=1:boxcolor=black@0.4")
            
        if self.config.get("stone_5") == "true" or self.config.get("feat_5") == "true" or self.config.get("stone_pan") == "true":
            filters.append("zoompan=z='min(zoom+0.0005,1.05)':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'")

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

            self.update_text(self.config.get("custom_text", ""))

            thread = threading.Thread(
                target=self._worker,
                daemon=True,
                name="InferLiveWorker"
            )
            thread.start()
            return True

    def _build_command(self):
        stream_key = self.config["stream_key"].strip()
        video_path = self.config["video_path"]

        if stream_key.startswith("rtmp://") or stream_key.startswith("rtmps://"):
            output_url = stream_key
        else:
            output_url = f"rtmp://a.rtmp.youtube.com/live2/{stream_key}"

        has_audio = self._has_audio(video_path)

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "warning",
            "-fflags", "+genpts+igndts",
            "-stream_loop", "-1",
            "-re",
            "-i", video_path,
        ]

        if not has_audio:
            cmd += [
                "-f", "lavfi",
                "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"
            ]

        cmd += [
            "-map", "0:v:0",
            "-map", "0:a:0" if has_audio else "1:a:0",
            "-vf", self.build_filter(),
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-tune", "zerolatency",
            "-pix_fmt", "yuv420p",
            "-r", "30",
            "-g", "60",
            "-keyint_min", "60",
            "-sc_threshold", "0",
            "-b:v", "2500k",
            "-maxrate", "2500k",
            "-bufsize", "5000k",
            "-c:a", "aac",
            "-b:a", "128k",
            "-ar", "44100",
            "-f", "flv",
            "-flvflags", "no_duration_filesize",
            output_url
        ]
        return cmd

    def _read_last_log_line(self, log_path: Path) -> str:
        try:
            if not log_path.exists():
                return ""
            lines = [
                ln.strip()
                for ln in log_path.read_text(encoding="utf-8", errors="ignore").splitlines()
                if ln.strip()
            ]
            return lines[-1][-180:] if lines else ""
        except Exception:
            return ""

    def _worker(self):
        log_path = LOG_DIR / "ffmpeg.log"
        try:
            while not self.stop_event.is_set():
                process = None
                try:
                    log_path.write_text("", encoding="utf-8")
                    cmd = self._build_command()

                    with log_path.open("ab") as log_file:
                        process = subprocess.Popen(
                            cmd,
                            stdout=subprocess.DEVNULL,
                            stderr=log_file,
                            start_new_session=True
                        )

                    with self.lock:
                        self.process = process

                    logger.info("FFmpeg process started: PID %s", process.pid)

                    while not self.stop_event.is_set():
                        return_code = process.poll()
                        if return_code is not None:
                            break
                        time.sleep(2)

                    if self.stop_event.is_set():
                        if process.poll() is None:
                            process.terminate()
                            try:
                                process.wait(timeout=8)
                            except subprocess.TimeoutExpired:
                                process.kill()
                                process.wait(timeout=4)
                        break

                    return_code = process.poll()
                    detail = self._read_last_log_line(log_path)
                    self.last_error = f"Exit {return_code}: {detail}" if detail else f"FFmpeg exited ({return_code})"
                    logger.error(self.last_error)
                    self.restart_count += 1
                    self.stop_event.wait(4)

                except Exception as exc:
                    self.last_error = str(exc)
                    logger.exception("Streaming worker error")
                    self.restart_count += 1
                    self.stop_event.wait(4)

                finally:
                    with self.lock:
                        if self.process is process:
                            self.process = None
                    if process is not None and process.poll() is None:
                        try:
                            process.kill()
                            process.wait(timeout=4)
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
            active = self.process is not None and self.process.poll() is None
            uptime = int(time.time() - self.started_at) if self.started_at is not None else 0
            return {
                "running": self.running,
                "ffmpeg_active": active,
                "uptime_seconds": uptime,
                "restart_count": self.restart_count,
                "last_error": self.last_error,
                "layout": self.config.get("layout", "shorts"),
                "active_user": self.config.get("user_email", ""),
                "video_path": self.config.get("video_path", "")
            }


stream_manager = StreamManager()
