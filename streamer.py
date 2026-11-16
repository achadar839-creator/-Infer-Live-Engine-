import os, time, subprocess, threading, random, string
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
        self.ticker_file.write_text(str(text).replace("\x00", ""), encoding="utf-8")

    def build_filter(self):
        def esc(p): return p.as_posix().replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
        t_path, g_path, h_path = esc(self.ticker_file), esc(self.glitch_file), esc(self.host_file)
        vf = []
        
        # 0. SHORTS / VERTICAL MODE CROP (1080x1920)
        if self.config.get("layout") == "shorts":
            vf.append("scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920")
            
        if self.config.get("feat_pan") == "true": vf.append("zoompan=z='min(zoom+0.0010,1.15)':d=300:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'")
        if self.config.get("feat_wea") == "true": vf.append("eq=brightness='0.02*sin(t/15)':saturation='1.1+0.2*cos(t/15)'")
        if self.config.get("feat_vis") == "true": vf.append("hue=s='1.0+0.3*sin(t*4)'")
            
        vf.append("drawtext=text='%{localtime\\:%I\\\\:%M\\\\:%S %p}':fontcolor=white:fontsize=32:x=w-tw-20:y=20:box=1:boxcolor=black@0.5")
        vf.append(f"drawtext=textfile='{t_path}':reload=1:fontcolor=yellow:fontsize=28:y=h-th-20:x=w-mod(t*50\\,w+tw):box=1:boxcolor=black@0.6")
        
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
