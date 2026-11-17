import os
import json
import time
import secrets
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Header
from fastapi.responses import FileResponse
from dotenv import load_dotenv

from database import init_db
from streamer import stream_manager

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

STORAGE_ROOT = Path(os.getenv("STORAGE_DIR", str(BASE_DIR))).resolve()
UPLOAD_DIR = STORAGE_ROOT / "uploads"
DATA_DIR = STORAGE_ROOT / "data"
LOG_DIR = STORAGE_ROOT / "logs"

for directory in (UPLOAD_DIR, DATA_DIR, LOG_DIR):
    directory.mkdir(parents=True, exist_ok=True)

USERS_FILE = DATA_DIR / "users_db.json"

ADMIN_EMAILS = {
    email.strip().lower()
    for email in os.getenv(
        "ADMIN_EMAILS",
        "achadar839@gmail.com,chandan@gmail.com,monighco@gmail.com"
    ).split(",")
    if email.strip()
}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("infer-live")

app = FastAPI(title="Infer Live Engine", version="3.0.0")

ALLOWED_EXTENSIONS = {".mp4", ".mkv", ".mov", ".webm", ".jpg", ".jpeg", ".png"}


def load_users_db():
    if USERS_FILE.exists():
        try:
            return json.loads(USERS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_users_db(data):
    USERS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_or_create_user(email: str, name: str = ""):
    email = email.strip().lower()
    if not email:
        email = "guest@gmail.com"
    db = load_users_db()
    is_admin = (email in ADMIN_EMAILS) or (len(db) == 0)
    if email not in db:
        db[email] = {
            "email": email,
            "name": name or email.split("@")[0],
            "is_admin": is_admin,
            "joined_at": int(time.time()),
            "last_seen": int(time.time()),
            "video_path": "",
            "video_name": "",
            "stream_key": "",
            "layout": "shorts",
            "custom_text": "",
            "feat_vis": "true",
            "feat_pan": "true",
            "feat_wea": "true",
            "feat_gli": "true",
            "feat_hos": "true"
        }
    else:
        db[email]["last_seen"] = int(time.time())
        if name:
            db[email]["name"] = name
        if email in ADMIN_EMAILS:
            db[email]["is_admin"] = True

    # Auto-Fallback: If saved video_path was lost after container restart, pick latest available video
    saved_vp = db[email].get("video_path", "")
    if not saved_vp or not Path(saved_vp).is_file():
        videos = sorted(
            [p for p in UPLOAD_DIR.glob("*") if p.suffix.lower() in {".mp4", ".mkv", ".mov", ".webm"}],
            key=lambda x: x.stat().st_mtime,
            reverse=True
        )
        if videos:
            db[email]["video_path"] = str(videos[0])
            db[email]["video_name"] = videos[0].name
        else:
            db[email]["video_path"] = ""
            db[email]["video_name"] = ""

    save_users_db(db)
    return db[email]


@app.on_event("startup")
def startup():
    init_db()
    logger.info("Infer Live Engine 3.0 started")


@app.get("/")
def dashboard():
    return FileResponse(BASE_DIR / "frontend" / "dashboard.html")


@app.get("/health")
def health():
    return {"status": "healthy", "service": "Infer Live Engine 3.0"}


@app.post("/auth/google")
def google_login(
    email: str = Form(...),
    name: str = Form("")
):
    user = get_or_create_user(email, name)
    return {
        "success": True,
        "user": user,
        "stream_status": stream_manager.status()
    }


@app.post("/save-settings")
def save_settings(
    email: str = Form(...),
    stream_key: str = Form(""),
    layout: str = Form("shorts"),
    custom_text: str = Form(""),
    feat_vis: str = Form("false"),
    feat_pan: str = Form("false"),
    feat_wea: str = Form("false"),
    feat_gli: str = Form("false"),
    feat_hos: str = Form("false")
):
    email = email.strip().lower()
    user = get_or_create_user(email)
    db = load_users_db()
    db[email].update({
        "stream_key": stream_key.strip(),
        "layout": layout,
        "custom_text": custom_text,
        "feat_vis": feat_vis,
        "feat_pan": feat_pan,
        "feat_wea": feat_wea,
        "feat_gli": feat_gli,
        "feat_hos": feat_hos,
        "last_seen": int(time.time())
    })
    save_users_db(db)
    return {"success": True, "user": db[email]}


@app.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    x_user_email: str = Header(default="achadar839@gmail.com")
):
    extension = Path(file.filename or "").suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Unsupported file format")

    filename = secrets.token_hex(8) + "_" + Path(file.filename or "video").name.replace(" ", "_")
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

    # Save automatically to user's profile if it's a video
    if extension in {".mp4", ".mkv", ".mov", ".webm"}:
        email = x_user_email.strip().lower() or "achadar839@gmail.com"
        get_or_create_user(email)
        db = load_users_db()
        if email in db:
            db[email]["video_path"] = str(destination)
            db[email]["video_name"] = file.filename or filename
            save_users_db(db)

    return {
        "success": True,
        "file_path": str(destination),
        "filename": file.filename or filename
    }


@app.post("/start")
def start_stream(
    video_path: str = Form(""),
    stream_key: str = Form(""),
    custom_text: str = Form(""),
    layout: str = Form("shorts"),
    thumbnail: str = Form(""),
    feat_vis: str = Form("false"),
    feat_pan: str = Form("false"),
    feat_wea: str = Form("false"),
    feat_gli: str = Form("false"),
    feat_hos: str = Form("false"),
    x_user_email: str = Header(default="achadar839@gmail.com")
):
    email = x_user_email.strip().lower() or "achadar839@gmail.com"
    user = get_or_create_user(email)

    final_video_path = video_path.strip() or user.get("video_path", "")
    final_stream_key = stream_key.strip() or user.get("stream_key", "")

    # Fallback to latest uploaded video on disk if path is empty or missing
    if not final_video_path or not Path(final_video_path).is_file():
        videos = sorted(
            [p for p in UPLOAD_DIR.glob("*") if p.suffix.lower() in {".mp4", ".mkv", ".mov", ".webm"}],
            key=lambda x: x.stat().st_mtime,
            reverse=True
        )
        if videos:
            final_video_path = str(videos[0])

    if not final_video_path or not Path(final_video_path).is_file():
        raise HTTPException(status_code=400, detail="Server par koi video nahi mila. Ek baar Browse se video chunein.")

    if not final_stream_key:
        raise HTTPException(status_code=400, detail="YouTube Stream Key daalna zaroori hai")

    # Save settings for this user permanently
    db = load_users_db()
    if email in db:
        db[email].update({
            "video_path": final_video_path,
            "stream_key": final_stream_key,
            "custom_text": custom_text,
            "layout": layout if layout in ("landscape", "shorts") else "shorts",
            "feat_vis": feat_vis,
            "feat_pan": feat_pan,
            "feat_wea": feat_wea,
            "feat_gli": feat_gli,
            "feat_hos": feat_hos,
            "last_seen": int(time.time())
        })
        save_users_db(db)

    config = {
        "user_email": email,
        "video_path": str(Path(final_video_path).resolve()),
        "stream_key": final_stream_key,
        "custom_text": custom_text,
        "layout": layout if layout in ("landscape", "shorts") else "shorts",
        "thumbnail": thumbnail,
        "feat_vis": feat_vis,
        "feat_pan": feat_pan,
        "feat_wea": feat_wea,
        "feat_gli": feat_gli,
        "feat_hos": feat_hos
    }

    if stream_manager.running:
        stream_manager.stop()
        time.sleep(1.5)

    success = stream_manager.start(config)
    if not success:
        raise HTTPException(status_code=500, detail=stream_manager.last_error or "Could not start FFmpeg")

    return {
        "success": True,
        "message": "Infer Live Engine LIVE ho gaya! 🔥",
        "video_path": final_video_path,
        "layout": config["layout"]
    }


@app.post("/stop")
def stop_stream():
    stream_manager.stop()
    return {"success": True, "message": "Stream stopped successfully"}


@app.get("/status")
def stream_status(x_user_email: str = Header(default="achadar839@gmail.com")):
    email = x_user_email.strip().lower()
    db = load_users_db()
    if email and email in db:
        db[email]["last_seen"] = int(time.time())
        save_users_db(db)

    st = stream_manager.status()
    now = int(time.time())
    all_users = list(db.values())
    online_users = [u for u in all_users if now - u.get("last_seen", 0) < 120]

    st["total_users"] = len(all_users)
    st["online_count"] = max(1, len(online_users))
    if email and email in db and db[email].get("is_admin"):
        st["users_list"] = [
            {
                "email": u["email"],
                "name": u.get("name", ""),
                "is_admin": u.get("is_admin", False),
                "online": (now - u.get("last_seen", 0) < 120),
                "has_video": bool(u.get("video_path"))
            }
            for u in all_users
        ]
    return st


@app.post("/update-text")
def update_text(custom_text: str = Form("")):
    stream_manager.update_text(custom_text)
    return {"success": True, "message": "Live Ticker Updated!"}
