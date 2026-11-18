#!/usr/bin/env python3
"""VLC zero-copy seek - v2: handles Range + fragmented MP4 for seeking"""
import http.server, subprocess, os, re, urllib.parse

VIDEO = "GTA V - FULL GAME (4K 60FPS) Walkthrough Gameplay No Commentary.webm"
AUDIO = "GTA V - FULL GAME (4K 60FPS) Walkthrough Gameplay No Commentary.m4a"
PORT = 8080
DURATION = 73682.33

TOTAL = os.path.getsize(VIDEO) + os.path.getsize(AUDIO) if os.path.exists(VIDEO) and os.path.exists(AUDIO) else 33855694238

class H(http.server.BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(TOTAL))
        self.end_headers()
    def do_GET(self):
        if self.path.split("?")[0] != "/stream.mp4":
            self.send_response(302); self.send_header("Location","/stream.mp4"); self.end_headers(); return
        rs = 0
        if "Range" in self.headers:
            m=re.search(r"bytes=(\d+)-", self.headers["Range"])
            if m: rs=int(m.group(1))
        start = (rs/TOTAL)*DURATION if TOTAL else 0
        print(f"Seek bytes {rs} -> {start/3600:.2f}h ({start:.1f}s)")
        # fragmented MP4 is seek-friendly over http (moov at start via empty_moov)
        cmd=["ffmpeg","-hide_banner","-loglevel","error",
             "-ss",str(start),"-i",VIDEO,"-ss",str(start),"-i",AUDIO,
             "-map","0:v:0","-map","1:a:0","-c:v","copy","-c:a","aac","-ar","48000","-b:a","128k",
             "-movflags","frag_keyframe+empty_moov+default_base_moof","-f","mp4","pipe:1"]
        if rs:
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {rs}-{TOTAL-1}/{TOTAL}")
        else:
            self.send_response(200)
        self.send_header("Content-Type","video/mp4")
        self.send_header("Accept-Ranges","bytes")
        self.send_header("Content-Length", str(TOTAL-rs))
        self.send_header("Cache-Control","no-cache")
        self.end_headers()
        proc=subprocess.Popen(cmd, stdout=subprocess.PIPE)
        try:
            while True:
                c=proc.stdout.read(256*1024)
                if not c: break
                try: self.wfile.write(c)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    break
        finally:
            proc.terminate(); proc.wait()
    def log_message(self,fmt,*a): print(fmt%a)

print(f"TOTAL {TOTAL} DURATION {DURATION}"); print(f"Open VLC x86: http://localhost:{PORT}/stream.mp4")
http.server.HTTPServer(("127.0.0.1",PORT),H).serve_forever()
