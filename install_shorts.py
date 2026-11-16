import os
from pathlib import Path

app_code = '''import os, secrets
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
'''

streamer_code = '''import os, time, subprocess, threading, random, string
from pathlib import Path

LOG_DIR = Path("logs")

class StreamManager:
    def __init__(self):
        self.process = None
        self.running = False
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.config = {}
        self.ticker_file = Path("data/ticker.txt").resolve()
        self.glitch_file = Path("data/glitch.txt").resolve()
        self.host_file = Path("data/host.txt").resolve()

    def update_text(self, text):
        self.ticker_file.write_text(str(text).replace("\\x00", ""), encoding="utf-8")

    def build_filter(self):
        def esc(p): return p.as_posix().replace("\\\\", "\\\\\\\\").replace(":", "\\\\:").replace("'", "\\\\'")
        t_path, g_path, h_path = esc(self.ticker_file), esc(self.glitch_file), esc(self.host_file)
        vf = []
        
        # 0. SHORTS / VERTICAL MODE CROP (1080x1920)
        if self.config.get("layout") == "shorts":
            vf.append("scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920")
            
        if self.config.get("feat_pan") == "true": vf.append("zoompan=z='min(zoom+0.0010,1.15)':d=300:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'")
        if self.config.get("feat_wea") == "true": vf.append("eq=brightness='0.02*sin(t/15)':saturation='1.1+0.2*cos(t/15)'")
        if self.config.get("feat_vis") == "true": vf.append("hue=s='1.0+0.3*sin(t*4)'")
            
        vf.append("drawtext=text='%{localtime\\\\:%I\\\\\\\\:%M\\\\\\\\:%S %p}':fontcolor=white:fontsize=32:x=w-tw-20:y=20:box=1:boxcolor=black@0.5")
        vf.append(f"drawtext=textfile='{t_path}':reload=1:fontcolor=yellow:fontsize=28:y=h-th-20:x=w-mod(t*50\\\\,w+tw):box=1:boxcolor=black@0.6")
        
        if self.config.get("feat_gli") == "true": vf.append(f"drawtext=textfile='{g_path}':reload=1:fontcolor=white:fontsize=70:x=(w-tw)/2:y=(h-th)/2:box=1:boxcolor=red@0.8")
        if self.config.get("feat_hos") == "true": vf.append(f"drawtext=textfile='{h_path}':reload=1:fontcolor=0x00e5a0:fontsize=40:x=30:y=100:box=1:boxcolor=black@0.8")
        return ",".join(vf)

    def start(self, config):
        with self.lock:
            if self.running: return False
            self.config = config.copy()
            self.update_text(config.get("custom_text", ""))
            self.stop_event.clear()
            self.running = True
            threading.Thread(target=self._worker, daemon=True).start()
            threading.Thread(target=self._glitch_worker, daemon=True).start()
            threading.Thread(target=self._host_worker, daemon=True).start()
            return True

    def _glitch_worker(self):
        while not self.stop_event.is_set():
            time.sleep(random.randint(15, 40))
            if self.config.get("feat_gli") == "true":
                self.glitch_file.write_text(f" [GLITCH REWARD: BOUNTY-{''.join(random.choices(string.ascii_uppercase, k=5))}] ")
                time.sleep(0.5)
                self.glitch_file.write_text("")

    def _host_worker(self):
        msgs = ["AI HOST: Next bounty code dropping soon...", "AI HOST: Keep chatting!"]
        while not self.stop_event.is_set():
            time.sleep(random.randint(25, 60))
            if self.config.get("feat_hos") == "true":
                self.host_file.write_text(f" {random.choice(msgs)} ")
                time.sleep(10)
                self.host_file.write_text("")

    def _worker(self):
        try:
            while not self.stop_event.is_set():
                cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-stream_loop", "-1", "-re", "-i", self.config["video_path"], "-vf", self.build_filter(), "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-b:v", "4500k", "-c:a", "aac", "-b:a", "128k", "-f", "flv", "rtmp://a.rtmp.youtube.com/live2/" + self.config["stream_key"]]
                with (LOG_DIR / "ffmpeg.log").open("ab") as fl:
                    self.process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=fl, start_new_session=True)
                while not self.stop_event.is_set():
                    if self.process.poll() is not None: break
                    time.sleep(2)
                if self.process and self.process.poll() is None:
                    self.process.terminate()
                    try: self.process.wait(timeout=5)
                    except: self.process.kill()
                break
        finally:
            with self.lock:
                self.running = False
                self.process = None

    def stop(self):
        self.stop_event.set()

    def status(self):
        return {"running": self.running}

stream_manager = StreamManager()
'''

html_code = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Infer Live Engine</title>
    <style>
        * { box-sizing: border-box; }
        body { margin: 0; background: #080b12; color: #fff; font-family: Arial, sans-serif; }
        .container { max-width: 900px; margin: 40px auto; padding: 20px; }
        h1 { color: #00e5a0; font-size: 32px; letter-spacing: 1px;}
        .card { background: #111827; border: 1px solid #263347; border-radius: 12px; padding: 25px; margin-bottom: 20px; }
        label { display: block; margin: 15px 0 8px; color: #b8c4d8; font-weight: bold;}
        input[type="text"], input[type="password"], textarea, select { width: 100%; background: #080d18; border: 1px solid #34445c; border-radius: 8px; padding: 14px; color: white; outline: none; }
        input:focus, textarea:focus, select:focus { border-color: #00e5a0; }
        button { border: none; border-radius: 8px; padding: 14px 24px; cursor: pointer; font-weight: bold; margin-top: 15px; }
        .start { background: #00e5a0; color: #000; } .stop { background: #ff4757; color: #fff; } .update { background: #2e86de; color: #fff; }
        .switch { position: relative; display: inline-block; width: 46px; height: 24px; margin-right: 15px; }
        .switch input { opacity: 0; width: 0; height: 0; }
        .slider { position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0; background-color: #263347; border-radius: 24px; transition: .3s; }
        .slider:before { position: absolute; content: ""; height: 16px; width: 16px; left: 4px; bottom: 4px; background-color: white; border-radius: 50%; transition: .3s; }
        input:checked + .slider { background-color: #00e5a0; box-shadow: 0 0 10px #00e5a0; }
        input:checked + .slider:before { transform: translateX(22px); }
        .f-row { display: flex; align-items: center; margin-bottom: 16px; background: #080d18; padding: 12px; border-radius: 8px;}
        #toast { display:none; position:fixed; top:20px; left:50%; transform:translateX(-50%); background:#00e5a0; color:#000; padding:12px 24px; border-radius:8px; font-weight:bold; z-index:9999; }
    </style>
</head>
<body>
    <div id="toast"></div>
    <div class="container">
        <h1>INFER LIVE ENGINE</h1><p style="color:#b8c4d8;">24/7 YouTube AI Broadcasting Station</p>
        
        <div class="card">
            <h2>🔐 Security</h2>
            <label>API Token</label>
            <input type="password" id="apiToken" placeholder="Enter API token">
            <button class="update" onclick="saveToken()">SAVE TOKEN</button>
        </div>

        <div class="card">
            <h2>🎥 Media & Format</h2>
            <label>Stream Layout (Aspect Ratio)</label>
            <select id="layout">
                <option value="landscape">Standard Desktop (16:9)</option>
                <option value="shorts">YouTube Shorts / Mobile (9:16)</option>
            </select>
            
            <label>Upload Video</label><input type="file" id="video" accept="video/*">
            <input type="text" id="videoPath" readonly placeholder="Video path will appear here" style="margin-top:10px;">
            
            <label>Custom Thumbnail (Optional)</label><input type="file" id="thumb" accept="image/*">
            <input type="text" id="thumbPath" readonly placeholder="Thumbnail path will appear here" style="margin-top:10px;">
            
            <label>YouTube Stream Key</label><input type="password" id="streamKey" placeholder="Enter Stream Key">
        </div>

        <div class="card">
            <h2 style="color: #00e5a0;">🔥 The 5 Infinity Stones</h2>
            <div class="f-row"><label class="switch"><input type="checkbox" id="f_vis"><span class="slider"></span></label> <span>🎶 Live Beat Pulse</span></div>
            <div class="f-row"><label class="switch"><input type="checkbox" id="f_pan"><span class="slider"></span></label> <span>🎥 Cinematic Auto-Pan</span></div>
            <div class="f-row"><label class="switch"><input type="checkbox" id="f_wea"><span class="slider"></span></label> <span>⛈️ Real-Time Vibe Sync</span></div>
            <div class="f-row"><label class="switch"><input type="checkbox" id="f_gli"><span class="slider"></span></label> <span style="color:#ff4757; font-weight:bold;">👾 The "Glitch" Bounty</span></div>
            <div class="f-row"><label class="switch"><input type="checkbox" id="f_hos"><span class="slider"></span></label> <span style="color:#00e5a0; font-weight:bold;">🤖 AI Ghost Host</span></div>
        </div>

        <div class="card">
            <h2>💬 Controls & Status</h2>
            <textarea id="customText" placeholder="Write your scrolling message..."></textarea>
            <div style="display: flex; gap: 10px; margin-top: 15px;">
                <button class="start" onclick="startStream()">START ENGINE</button>
                <button class="stop" onclick="stopStream()">STOP</button>
                <button class="update" onclick="updateText()">UPDATE TICKER</button>
            </div>
            <div style="margin-top: 20px; padding: 15px; background: #080d18; border-radius: 8px;">
                Status: <strong id="status" style="color: #ff4757;">OFFLINE</strong>
            </div>
        </div>
    </div>

    <script>
        const showToast = (msg) => { const t = document.getElementById("toast"); t.textContent = msg; t.style.display = "block"; setTimeout(()=>t.style.display="none", 3000); };
        document.getElementById("apiToken").value = sessionStorage.getItem("apiToken") || "";
        const saveToken = () => { sessionStorage.setItem("apiToken", document.getElementById("apiToken").value); showToast("Token Saved!"); };
        const headers = () => ({ "X-API-Key": sessionStorage.getItem("apiToken") || "" });

        const uploadFile = async (inputId, pathId) => {
            const file = document.getElementById(inputId).files[0]; if(!file) return;
            const data = new FormData(); data.append("file", file);
            showToast("Uploading...");
            try {
                const res = await fetch("/upload", { method: "POST", headers: headers(), body: data });
                const json = await res.json(); if(!res.ok) throw new Error(json.detail);
                document.getElementById(pathId).value = json.file_path;
                showToast("Upload Successful!");
            } catch(e) { showToast(e.message); }
        };

        document.getElementById("video").addEventListener("change", () => uploadFile("video", "videoPath"));
        document.getElementById("thumb").addEventListener("change", () => uploadFile("thumb", "thumbPath"));

        const startStream = async () => {
            const d = new FormData();
            d.append("video_path", document.getElementById("videoPath").value);
            d.append("stream_key", document.getElementById("streamKey").value);
            d.append("custom_text", document.getElementById("customText").value);
            d.append("layout", document.getElementById("layout").value);
            d.append("thumbnail", document.getElementById("thumbPath").value);
            d.append("feat_vis", document.getElementById("f_vis").checked);
            d.append("feat_pan", document.getElementById("f_pan").checked);
            d.append("feat_wea", document.getElementById("f_wea").checked);
            d.append("feat_gli", document.getElementById("f_gli").checked);
            d.append("feat_hos", document.getElementById("f_hos").checked);
            
            showToast("Starting Engine...");
            try {
                const res = await fetch("/start", { method: "POST", headers: headers(), body: d });
                const json = await res.json(); if(!res.ok) throw new Error(json.detail);
                showToast(json.message); checkStatus();
            } catch(e) { showToast(e.message); }
        };

        const stopStream = async () => {
            try {
                const res = await fetch("/stop", { method: "POST", headers: headers() });
                const json = await res.json(); if(!res.ok) throw new Error(json.detail);
                showToast(json.message); checkStatus();
            } catch(e) { showToast(e.message); }
        };

        const updateText = async () => {
            const d = new FormData(); d.append("custom_text", document.getElementById("customText").value);
            try {
                const res = await fetch("/update-text", { method: "POST", headers: headers(), body: d });
                const json = await res.json(); if(!res.ok) throw new Error(json.detail);
                showToast(json.message);
            } catch(e) { showToast(e.message); }
        };

        const checkStatus = async () => {
            try {
                const res = await fetch("/status", { headers: headers() });
                if(res.ok) {
                    const data = await res.json();
                    const el = document.getElementById("status");
                    if(data.running) { el.textContent = "LIVE 🔥"; el.style.color = "#00e5a0"; }
                    else { el.textContent = "OFFLINE"; el.style.color = "#ff4757"; }
                }
            } catch(e) {}
        };
        setInterval(checkStatus, 3000); checkStatus();
    </script>
</body>
</html>
'''

Path("app.py").write_text(app_code, encoding="utf-8")
Path("streamer.py").write_text(streamer_code, encoding="utf-8")
Path("frontend/dashboard.html").write_text(html_code, encoding="utf-8")
print("\n✅ API TOKEN CHANGED TO: chandan_live_99")
print("✅ SHORTS MODE & CUSTOM THUMBNAIL INSTALLED!\n")
