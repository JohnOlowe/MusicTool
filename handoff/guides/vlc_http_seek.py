#!/usr/bin/env python3
"""
VLC zero-copy seek for YouTube DASH: webm 1080p60 VP9 + m4a AAC
No mux file needed. Serves http://localhost:8080/stream.mkv that VLC can seek.
Run this .py in the folder with both files, then open the URL in VLC (x86 is fine).

How seeking works: VLC sends Range: bytes=... , we estimate time = bytes/total*DURATION
and restart ffmpeg with -ss. Not sample-accurate but lands within ~10sec for 20h file.
"""
import http.server, subprocess, os, shlex, urllib.parse, re

VIDEO = "GTA V - FULL GAME (4K 60FPS) Walkthrough Gameplay No Commentary.webm"
AUDIO = "GTA V - FULL GAME (4K 60FPS) Walkthrough Gameplay No Commentary.m4a"
PORT = 8080
DURATION = 73682.33  # from your ffprobe 20:28:02.33 - update if different file

TOTAL = os.path.getsize(VIDEO) + os.path.getsize(AUDIO) if os.path.exists(VIDEO) and os.path.exists(AUDIO) else 1

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/stream.mkv":
            self.send_response(302)
            self.send_header("Location", "/stream.mkv")
            self.end_headers()
            return

        # Parse Range: bytes=START-
        range_start = 0
        if "Range" in self.headers:
            m = re.search(r"bytes=(\d+)-", self.headers["Range"])
            if m:
                range_start = int(m.group(1))

        # byte -> time approximation
        start_time = (range_start / TOTAL) * DURATION if TOTAL else 0
        # clamp
        start_time = max(0, min(start_time, DURATION-1))

        print(f"VLC seek bytes {range_start}/{TOTAL} -> ~{start_time:.1f}s")

        # Use ffmpeg copy - no re-encode, just mux on the fly
        # For x86 VLC: output matroska is most compatible with VP9+AAC
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-ss", str(start_time),
            "-i", VIDEO,
            "-ss", str(start_time),
            "-i", AUDIO,
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", "copy",
            "-f", "matroska", "pipe:1"
        ]

        self.send_response(206 if range_start else 200)
        self.send_header("Content-Type", "video/x-matroska")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-cache")
        # Fake length so VLC shows timeline
        self.send_header("Content-Length", str(TOTAL))
        self.end_headers()

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
        try:
            while True:
                chunk = proc.stdout.read(256*1024)
                if not chunk:
                    break
                self.wfile.write(chunk)
        except BrokenPipeError:
            pass
        finally:
            proc.terminate()

    def log_message(self, fmt, *args):
        print(fmt % args)

print(f"Serving {VIDEO} + {AUDIO}")
print(f"TOTAL ~{TOTAL} bytes  DURATION {DURATION:.1f}s")
print(f"Open in VLC (x86): http://localhost:{PORT}/stream.mkv")
print("Seek to middle in VLC - server will restart at ~10h mark")
http.server.HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
