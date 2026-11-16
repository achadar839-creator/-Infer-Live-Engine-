cd ~/Infer-Live-Engine || exit 1

cat << 'EOF' > patch.py
from pathlib import Path
from datetime import datetime
import shutil
import sys

ROOT = Path(__file__).resolve().parent
BACKUP = ROOT / ("backup_core_" + datetime.now().strftime("%Y%m%d_%H%M%S"))

FILES = {}

# ============================================================
# 1. APP.PY
# ============================================================

FILES["app.py"] = r'''
import os
import secrets
import logging
from pathlib import Path

from fastapi import (
    FastAPI, HTTPException, UploadFile,
    File, Form, Depends, Header
)
from fastapi.responses import FileResponse
from dotenv import load_dotenv

from database import init_db
from models import Stream
from streamer import stream_manager

BASE_DIR = Path(__file__).resolve().parent

load_dotenv(BASE_DIR / ".env")

STORAGE_ROOT = Path(
    os.getenv("STORAGE_DIR", str(BASE_DIR))
).resolve()

UPLOAD_DIR = STORAGE_ROOT / "uploads"
DATA_DIR = STORAGE_ROOT / "data"
LOG_DIR = STORAGE_ROOT / "logs"

for directory in (UPLOAD_DIR, DATA_DIR, LOG_DIR):
    directory.mkdir(parents=True, exist_ok=True)

API_TOKEN = os.getenv("API_TOKEN", "").strip()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("infer-live")

app = FastAPI(
    title="Infer Live Engine",
    version="2.0.0"
)

ALLOWED_EXTENSIONS = {
    ".mp4", ".mkv", ".mov", ".webm",
    ".jpg", ".jpeg", ".png"
}


@app.on_event("startup")
def startup():
    init_db()
    logger.info("Infer Live Engine started")


def authenticate(x_api_key: str = Header(default="")):

    if not API_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="API_TOKEN is not configured"
        )

    if not secrets.compare_digest(x_api_key, API_TOKEN):
        raise HTTPException(
            status_code=401,
            detail="Invalid API Token"
        )

    return True


@app.get("/")
def dashboard():
    return FileResponse(
        BASE_DIR / "frontend" / "dashboard.html"
    )


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "service": "Infer Live Engine"
    }


@app.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    auth=Depends(authenticate)
):

    extension = Path(file.filename or "").suffix.lower()

    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="Unsupported file format"
        )

    filename = secrets.token_hex(16) + extension
    destination = UPLOAD_DIR / filename

    try:
        with destination.open("wb") as output:
            while True:
                chunk = await file.read(1024 * 1024)

                if not chunk:
                    break

                output.write(chunk)

    except Exception:
        destination.unlink(missing_ok=True)
        raise

    finally:
        await file.close()

    return {
        "success": True,
        "file_path": str(destination),
        "filename": filename
    }


@app.post("/start")
def start_stream(
    video_path: str = Form(...),
    stream_key: str = Form(...),
    custom_text: str = Form(""),
    layout: str = Form("landscape"),
    thumbnail: str = Form(""),
    feat_vis: str = Form("false"),
    feat_pan: str = Form("false"),
    feat_wea: str = Form("false"),
    feat_gli: str = Form("false"),
    feat_hos: str = Form("false"),
    auth=Depends(authenticate)
):

    if not video_path.strip():
        raise HTTPException(
            status_code=400,
            detail="Please upload a video first"
        )

    if not stream_key.strip():
        raise HTTPException(
            status_code=400,
            detail="YouTube Stream Key is required"
        )

    if layout not in ("landscape", "shorts"):
        raise HTTPException(
            status_code=400,
            detail="Invalid stream layout"
        )

    video = Path(video_path).resolve()

    # Only allow uploaded videos from persistent storage.
    if video.parent != UPLOAD_DIR.resolve():
        raise HTTPException(
            status_code=403,
            detail="Invalid video storage path"
        )

    if not video.is_file():
        raise HTTPException(
            status_code=404,
            detail="Uploaded video not found"
        )

    if video.suffix.lower() not in {
        ".mp4", ".mkv", ".mov", ".webm"
    }:
        raise HTTPException(
            status_code=400,
            detail="Selected file is not a video"
        )

    config = {
        "video_path": str(video),
        "stream_key": stream_key.strip(),
        "custom_text": custom_text,
        "layout": layout,
        "thumbnail": thumbnail,
        "feat_vis": feat_vis,
        "feat_pan": feat_pan,
        "feat_wea": feat_wea,
        "feat_gli": feat_gli,
        "feat_hos": feat_hos
    }

    success = stream_manager.start(config)

    if not success:
        raise HTTPException(
            status_code=409,
            detail="Streaming engine is already running"
        )

    return {
        "success": True,
        "message": "Infer Live Engine started!",
        "layout": layout
    }


@app.post("/stop")
def stop_stream(auth=Depends(authenticate)):

    stream_manager.stop()

    return {
        "success": True,
        "message": "Streaming stop requested"
    }


@app.get("/status")
def stream_status(auth=Depends(authenticate)):
    return stream_manager.status()


@app.post("/update-text")
def update_text(
    custom_text: str = Form(...),
    auth=Depends(authenticate)
):

    stream_manager.update_text(custom_text)

    return {
        "success": True,
        "message": "Ticker updated!"
    }
'''

# ============================================================
# 2. STREAMER.PY
# ============================================================

FILES["streamer.py"] = r'''
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
'''

# ============================================================
# 3. DASHBOARD.HTML
# ============================================================

FILES["frontend/dashboard.html"] = r'''
<!DOCTYPE html>
<html lang="en">

<head>

<meta charset="UTF-8">

<meta name="viewport"
content="width=device-width, initial-scale=1.0">

<title>Infer Live Engine</title>

<style>

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    background: #080b12;
    color: white;
    font-family: Arial, sans-serif;
}

.container {
    max-width: 900px;
    margin: 30px auto;
    padding: 20px;
}

h1 {
    color: #00e5a0;
    font-size: 30px;
}

.card {
    background: #111827;
    border: 1px solid #263347;
    border-radius: 12px;
    padding: 25px;
    margin-bottom: 20px;
}

label {
    display: block;
    margin: 15px 0 8px;
    color: #b8c4d8;
    font-weight: bold;
}

input, textarea, select {
    width: 100%;
    background: #080d18;
    border: 1px solid #34445c;
    border-radius: 8px;
    padding: 14px;
    color: white;
    outline: none;
}

input:focus, textarea:focus, select:focus {
    border-color: #00e5a0;
}

button {
    border: none;
    border-radius: 8px;
    padding: 14px 20px;
    cursor: pointer;
    font-weight: bold;
    margin-top: 15px;
}

button:disabled {
    opacity: 0.5;
    cursor: not-allowed;
}

.start {
    background: #00e5a0;
    color: black;
}

.stop {
    background: #ff4757;
    color: white;
}

.update {
    background: #2e86de;
    color: white;
}

.f-row {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 12px;
    background: #080d18;
    padding: 14px;
    border-radius: 8px;
}

.f-row input {
    width: 20px;
    height: 20px;
}

#toast {
    display: none;
    position: fixed;
    top: 15px;
    left: 50%;
    transform: translateX(-50%);
    background: #00e5a0;
    color: black;
    padding: 14px 20px;
    border-radius: 8px;
    font-weight: bold;
    z-index: 9999;
    max-width: 90%;
}

.path-row {
    display: flex;
    gap: 8px;
}

.path-row input {
    min-width: 0;
}

#statusBox {
    background: #080d18;
    border-radius: 8px;
    padding: 18px;
    line-height: 1.8;
    overflow-wrap: anywhere;
}

@media(max-width:600px) {

    .container {
        padding: 12px;
    }

    .card {
        padding: 18px;
    }

    .buttons {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
    }

}

</style>

</head>

<body>

<div id="toast"></div>

<div class="container">

<h1>INFER LIVE ENGINE</h1>

<p style="color:#b8c4d8;">
24/7 YouTube AI Broadcasting Station
</p>

<div class="card">

<h2>🔐 Security</h2>

<label>API Token</label>

<input
type="password"
id="apiToken"
placeholder="Enter API Token"
autocomplete="off"
>

<button class="update" onclick="saveToken()">
SAVE TOKEN
</button>

</div>

<div class="card">

<h2>🎥 Media & Format</h2>

<label>Stream Layout</label>

<select id="layout">

<option value="landscape">
Standard Desktop (16:9)
</option>

<option value="shorts">
YouTube Shorts / Mobile (9:16)
</option>

</select>

<label>Upload Video</label>

<input
type="file"
id="video"
accept="video/mp4,video/x-matroska,video/quicktime,video/webm"
>

<div class="path-row">

<input
type="text"
id="videoPath"
readonly
placeholder="Upload video first"
>

<button onclick="clearFile()">
✖
</button>

</div>

<label>Custom Thumbnail (Optional)</label>

<input
type="file"
id="thumb"
accept="image/*"
>

<p style="color:#9ca3af;font-size:12px;">
Thumbnail upload is retained for future integration.
RTMP streaming does not set the YouTube video thumbnail.
</p>

<label>YouTube Stream Key</label>

<input
type="password"
id="streamKey"
placeholder="Enter YouTube Stream Key"
autocomplete="off"
>

</div>

<div class="card">

<h2 style="color:#00e5a0;">
🔥 The 5 Infinity Stones
</h2>

<div class="f-row">
<input type="checkbox" id="f_vis">
<span>🎶 Live Beat Pulse</span>
</div>

<div class="f-row">
<input type="checkbox" id="f_pan">
<span>🎥 Cinematic Auto-Pan</span>
</div>

<div class="f-row">
<input type="checkbox" id="f_wea">
<span>⛈️ Real-Time Vibe Sync</span>
</div>

<div class="f-row">
<input type="checkbox" id="f_gli">
<span>👾 The Glitch Bounty</span>
</div>

<div class="f-row">
<input type="checkbox" id="f_hos">
<span>🤖 AI Ghost Host</span>
</div>

<p style="color:#9ca3af;font-size:12px;">
These controls are reserved for the next feature integration.
</p>

</div>

<div class="card">

<h2>💬 Controls & Status</h2>

<textarea
id="customText"
placeholder="Write your scrolling message..."
rows="3"
></textarea>

<div class="buttons">

<button
class="start"
id="startButton"
onclick="startStream()"
>
START ENGINE
</button>

<button
class="stop"
id="stopButton"
onclick="stopStream()"
>
STOP
</button>

<button
class="update"
onclick="updateText()"
>
UPDATE TICKER
</button>

</div>

<div id="statusBox" style="margin-top:20px;">

Status:
<strong id="status" style="color:#ff4757;">
OFFLINE
</strong>

<div id="details"></div>

</div>

</div>

</div>

<script>

const $ = id => document.getElementById(id);

function showToast(message) {

    const toast = $("toast");

    toast.textContent = String(message);

    toast.style.display = "block";

    clearTimeout(window.toastTimer);

    window.toastTimer = setTimeout(() => {
        toast.style.display = "none";
    }, 3500);

}

$("apiToken").value =
    sessionStorage.getItem("apiToken") || "";

function saveToken() {

    const token = $("apiToken").value.trim();

    if (!token) {
        showToast("Please enter API Token");
        return;
    }

    sessionStorage.setItem("apiToken", token);

    showToast("API Token Saved");
}

function headers() {

    return {
        "X-API-Key":
            sessionStorage.getItem("apiToken") || ""
    };

}

async function apiRequest(url, options = {}) {

    const response = await fetch(url, {
        ...options,
        headers: {
            ...headers(),
            ...(options.headers || {})
        }
    });

    const text = await response.text();

    let data = {};

    try {
        data = text ? JSON.parse(text) : {};
    } catch {
        throw new Error(
            "Server returned an invalid response"
        );
    }

    if (!response.ok) {

        let message = data.detail || "Request failed";

        if (Array.isArray(message)) {
            message = message.map(x => x.msg).join(", ");
        }

        throw new Error(String(message));
    }

    return data;
}

async function uploadFile(inputId, pathId) {

    const file = $(inputId).files[0];

    if (!file) return null;

    const form = new FormData();

    form.append("file", file);

    showToast("Uploading file...");

    try {

        const data = await apiRequest("/upload", {
            method: "POST",
            body: form
        });

        $(pathId).value = data.file_path;

        showToast("Upload Successful!");

        return data.file_path;

    } catch(error) {

        $(pathId).value = "";

        showToast(error.message);

        $(inputId).value = "";

        return null;
    }

}

$("video").addEventListener("change", async () => {

    $("videoPath").value = "";

    await uploadFile("video", "videoPath");

});

$("thumb").addEventListener("change", async () => {

    await uploadFile("thumb", "thumbPath");

});

function clearFile() {

    $("video").value = "";
    $("videoPath").value = "";

    showToast("Video selection cleared");

}

async function startStream() {

    const videoPath = $("videoPath").value.trim();

    const streamKey = $("streamKey").value.trim();

    if (!videoPath) {
        showToast("Please upload a video first");
        return;
    }

    if (!streamKey) {
        showToast("Please enter YouTube Stream Key");
        return;
    }

    const form = new FormData();

    form.append("video_path", videoPath);
    form.append("stream_key", streamKey);

    form.append(
        "custom_text",
        $("customText").value
    );

    form.append(
        "layout",
        $("layout").value
    );

    form.append(
        "feat_vis",
        String($("f_vis").checked)
    );

    form.append(
        "feat_pan",
        String($("f_pan").checked)
    );

    form.append(
        "feat_wea",
        String($("f_wea").checked)
    );

    form.append(
        "feat_gli",
        String($("f_gli").checked)
    );

    form.append(
        "feat_hos",
        String($("f_hos").checked)
    );

    $("startButton").disabled = true;

    showToast("Starting Streaming Engine...");

    try {

        const data = await apiRequest("/start", {
            method: "POST",
            body: form
        });

        showToast(data.message);

        await checkStatus();

    } catch(error) {

        showToast(error.message);

    } finally {

        $("startButton").disabled = false;

    }

}

async function stopStream() {

    try {

        const data = await apiRequest("/stop", {
            method: "POST"
        });

        showToast(data.message);

        await checkStatus();

    } catch(error) {

        showToast(error.message);

    }

}

async function updateText() {

    const form = new FormData();

    form.append(
        "custom_text",
        $("customText").value
    );

    try {

        const data = await apiRequest(
            "/update-text",
            {
                method: "POST",
                body: form
            }
        );

        showToast(data.message);

    } catch(error) {

        showToast(error.message);

    }

}

async function checkStatus() {

    try {

        const data = await apiRequest("/status");

        const status = $("status");

        if (data.ffmpeg_active) {

            status.textContent = "LIVE 🔥";
            status.style.color = "#00e5a0";

        } else if (data.running) {

            status.textContent = "STARTING / RECONNECTING";
            status.style.color = "#fbbf24";

        } else {

            status.textContent = "OFFLINE";
            status.style.color = "#ff4757";

        }

        $("details").textContent =
            "FFmpeg: " +
            (data.ffmpeg_active ? "ACTIVE" : "INACTIVE") +
            " | Uptime: " +
            data.uptime_seconds +
            "s | Restarts: " +
            data.restart_count +
            (data.last_error
                ? " | Error: " + data.last_error
                : "");

    } catch(error) {

        $("status").textContent = "CONNECTION ERROR";

        $("status").style.color = "#ff4757";

        $("details").textContent = error.message;

    }

}

setInterval(checkStatus, 5000);

checkStatus();

</script>

</body>
</html>
'''

# ============================================================
# APPLY PATCH SAFELY
# ============================================================

print("\n========== INFER LIVE ENGINE PATCH ==========\n")

if not (ROOT / "app.py").exists():
    print("ERROR: app.py not found")
    sys.exit(1)

if not (ROOT / "streamer.py").exists():
    print("ERROR: streamer.py not found")
    sys.exit(1)

if not (ROOT / "frontend" / "dashboard.html").exists():
    print("ERROR: dashboard.html not found")
    sys.exit(1)

BACKUP.mkdir(parents=True, exist_ok=True)

try:

    for filename, content in FILES.items():

        destination = ROOT / filename

        destination.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        backup_file = BACKUP / filename

        shutil.copy2(
            destination,
            backup_file
        )

        destination.write_text(
            content.strip() + "\n",
            encoding="utf-8"
        )

        print("[UPDATED]", filename)

    print("\n========== PYTHON VALIDATION ==========\n")

    import ast

    for filename in ("app.py", "streamer.py"):

        ast.parse(
            (ROOT / filename).read_text(
                encoding="utf-8"
            )
        )

        print("[SYNTAX OK]", filename)

    print("\n========== PATCH SUCCESS ==========")
    print("Backup:", BACKUP)

except Exception as error:

    print("\nPATCH FAILED:", error)

    print("Restoring original files...")

    for filename in FILES:

        original = BACKUP / filename

        if original.exists():

            shutil.copy2(
                original,
                ROOT / filename
            )

    print("Original files restored.")

    sys.exit(1)

EOF

python3 patch.py || exit 1

echo
echo "========== GIT STATUS =========="

git status --short

echo
echo "========== COMMIT AND PUSH =========="

