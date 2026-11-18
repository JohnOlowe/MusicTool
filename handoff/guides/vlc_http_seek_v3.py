#!/usr/bin/env python3
"""v3: instant load - copy audio, fast probesize, cache first moov"""
import http.server, subprocess, os, re
VIDEO="GTA V - FULL GAME (4K 60FPS) Walkthrough Gameplay No Commentary.webm"
AUDIO="GTA V - FULL GAME (4K 60FPS) Walkthrough Gameplay No Commentary.m4a"
PORT=8080; DURATION=73682.33
TOTAL=os.path.getsize(VIDEO)+os.path.getsize(AUDIO) if os.path.exists(VIDEO) and os.path.exists(AUDIO) else 33855694238
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?")[0] not in ("/stream.mp4","/stream.mkv"):
            self.send_response(302); self.send_header("Location","/stream.mp4"); self.end_headers(); return
        rs=0
        if "Range" in self.headers:
            m=re.search(r"bytes=(\d+)-",self.headers["Range"])
            if m: rs=int(m.group(1))
        # VLC probes first 2MB - don't seek, serve from 0 fast
        start=0 if rs<5*1024*1024 else (rs/TOTAL)*DURATION
        print(f"GET {self.headers.get('Range','full')} -> {start:.1f}s ({start/3600:.2f}h)")
        cmd=["ffmpeg","-hide_banner","-loglevel","error",
             "-probesize","32M","-analyzeduration","0",
             "-ss",str(start),"-i",VIDEO,"-ss",str(start),"-i",AUDIO,
             "-map","0:v:0","-map","1:a:0","-c:v","copy","-c:a","copy",
             "-movflags","frag_keyframe+empty_moov+default_base_moof","-f","mp4","pipe:1"]
        if rs: self.send_response(206); self.send_header("Content-Range",f"bytes {rs}-{TOTAL-1}/{TOTAL}")
        else: self.send_response(200)
        self.send_header("Content-Type","video/mp4"); self.send_header("Accept-Ranges","bytes")
        self.send_header("Content-Length",str(TOTAL-rs)); self.send_header("Cache-Control","no-cache"); self.end_headers()
        p=subprocess.Popen(cmd,stdout=subprocess.PIPE)
        try:
            while True:
                c=p.stdout.read(256*1024)
                if not c: break
                try: self.wfile.write(c)
                except: break
        finally: p.terminate()
    def log_message(self,fmt,*a): pass
print(f"v3 TOTAL {TOTAL} DURATION {DURATION:.1f}s"); print(f"VLC x86: http://localhost:8080/stream.mp4  (copy audio = instant)")
http.server.HTTPServer(("127.0.0.1",PORT),H).serve_forever()
