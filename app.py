import os, secrets
from pathlib import Path
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Depends, Header
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from database import init_db, get_db
from streamer import stream_manager
from dotenv import load_dotenv

load_dotenv()
app = FastAPI(title="Infer Live Engine (Pro Edition)")
BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
API_TOKEN = os.getenv("API_TOKEN", "chandan_live_99")
ALLOWED_EXTENSIONS = {".mp4", ".mkv", ".mov", ".webm", ".jpg", ".png", ".jpeg"}

@app.on_event("startup")
def startup():
    init_db()

def authenticate(x_api_key: str = Header(default="")):
    if not secrets.compare_digest(x_api_key, API_TOKEN):
        raise HTTPException(status_code=401, detail="Unauthorized")

@app.get("/")
def dashboard():
    return FileResponse(BASE_DIR / "frontend" / "dashboard.html")

@app.post("/upload")
async def upload_file(file: UploadFile = File(...), auth=Depends(authenticate)):
    extension = Path(file.filename or "").suffix.lower()
    if extension not in ALLOWED_EXTENSIONS: raise HTTPException(status_code=400)
    filename = f"{secrets.token_hex(16)}{extension}"
    destination = UPLOAD_DIR / filename
    with destination.open("wb") as output:
        while chunk := await file.read(1024 * 1024): output.write(chunk)
    return {"success": True, "file_path": str(destination)}

@app.post("/start")
def start_stream(
    video_path: str = Form(...), stream_key: str = Form(...), custom_text: str = Form(""),
    layout: str = Form("landscape"), thumbnail: str = Form(""),
    feat_vis: str = Form("false"), feat_pan: str = Form("false"), feat_wea: str = Form("false"),
    feat_gli: str = Form("false"), feat_hos: str = Form("false"),
    auth=Depends(authenticate)
):
    video = Path(video_path).resolve()
    if not video.is_file(): raise HTTPException(status_code=404)
    config = {
        "video_path": str(video), "stream_key": stream_key.strip(), "custom_text": custom_text,
        "layout": layout, "thumbnail": thumbnail,
        "feat_vis": feat_vis, "feat_pan": feat_pan, "feat_wea": feat_wea, 
        "feat_gli": feat_gli, "feat_hos": feat_hos
    }
    success = stream_manager.start(config)
    if not success: raise HTTPException(status_code=409, detail="Stream running")
    return {"success": True, "message": f"Engine Started in {layout.upper()} mode!"}

@app.post("/stop")
def stop_stream(auth=Depends(authenticate)):
    stream_manager.stop()
    return {"success": True, "message": "Streaming stopped"}

@app.get("/status")
def stream_status(auth=Depends(authenticate)):
    return stream_manager.status()

@app.post("/update-text")
def update_text(custom_text: str = Form(...), auth=Depends(authenticate)):
    stream_manager.update_text(custom_text)
    return {"success": True}
