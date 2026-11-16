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


@app.post("/upload_disabled")
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
