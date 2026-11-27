"""Your YouTube login, from the browser on one device to another (your phone).

    laptop:  musictool cookies --send        reads it from Firefox (or the
                                             browser you use), offers it once
    phone:   musictool cookies --receive     finds the laptop by itself, saves
                                             it as cookies.txt

Only the YouTube cookies leave the browser -- never the bank's or the mail's:
yt-dlp's browser reader returns every site's cookies, so they are filtered
here before anything is written to the screen, a file or the network. Nothing
is saved on the sending computer.

How the phone finds the laptop: it broadcasts a question on the Wi-Fi (UDP,
port 8765); the laptop answers with the address to fetch, which includes a
random one-time code. The laptop hands the file out once, then stops; it
stops anyway after --wait seconds (5 minutes), on Stop, or on Ctrl+C. No
internet is used: everything stays on your own network.
"""

import http.server
import os
import secrets
import select
import socket
import tempfile
import time
import urllib.request

from . import cancel

PORT = 8765
ASK = b"MUSICTOOL-COOKIES?"
ANSWER = b"MUSICTOOL-COOKIES "
HEADER = "# Netscape HTTP Cookie File"
# Any of these means the browser is signed in to YouTube, not just visiting.
LOGIN = ("LOGIN_INFO", "SID", "__Secure-1PSID", "__Secure-3PSID", "SAPISID")


class ShareError(Exception):
    """Something the user can fix; the message says how."""


# ---------------------------------------------------------------- the file

def is_youtube(host):
    h = host.strip().lower()
    if h.startswith("#httponly_"):
        h = h[len("#httponly_"):]
    h = h.lstrip(".")
    return h == "youtube.com" or h.endswith(".youtube.com")


def youtube_only(text):
    """A Netscape cookies.txt with only the youtube.com cookies kept.
    -> (text, how many, signed in?)"""
    keep, names = [], set()
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        cookie = not line.startswith("#") or \
            line.lower().startswith("#httponly_")
        if not cookie:
            continue
        f = line.split("\t")
        if len(f) == 7 and is_youtube(f[0]):
            keep.append(line)
            names.add(f[5])
    out = "\n".join([HEADER, "# YouTube only, from musictool cookies", ""]
                    + keep) + "\n"
    return out, len(keep), any(n in names for n in LOGIN)


def read_browser(spec, ytdlp=None, timeout=120):
    """Every cookie of browser `spec` (firefox, chrome, firefox:PROFILE ...)
    through yt-dlp, which knows where each browser keeps them and how to
    decrypt them. -> the YouTube ones, as youtube_only() returns them.

    yt-dlp writes what it read to --cookies when it exits, even when it
    exits on "no URL given" -- so nothing is downloaded. It writes every
    site's cookies, though: to a private temp folder, deleted right here."""
    if ytdlp is None:
        from .util import ytdlp_cmd
        ytdlp = ytdlp_cmd()
    tmp = tempfile.mkdtemp(prefix="mtck-")
    path = os.path.join(tmp, "all.txt")
    try:
        try:
            r = cancel.run(list(ytdlp) + ["--ignore-config",
                                          "--cookies-from-browser", spec,
                                          "--cookies", path],
                           capture_output=True, text=True, errors="replace",
                           timeout=timeout)
        except FileNotFoundError:
            raise ShareError("yt-dlp is not installed (pip install -U "
                             "\"yt-dlp[default]\")") from None
        if not os.path.exists(path):
            tail = [ln for ln in ((r.stderr or "") + (r.stdout or ""))
                    .splitlines() if "ERROR" in ln or "rror" in ln]
            raise ShareError(
                f"could not read the {spec} login"
                + (f": {tail[-1].strip()}" if tail else "")
                + ("\n  Chrome and Edge on Windows lock their cookies while "
                   "they run -- Firefox doesn't: sign in to YouTube in "
                   "Firefox and use --from firefox"
                   if spec.split(":")[0] in ("chrome", "edge", "brave",
                                             "chromium", "opera", "vivaldi")
                   else ""))
        with open(path, encoding="utf-8", errors="replace") as fh:
            return youtube_only(fh.read())
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
        try:
            os.rmdir(tmp)
        except OSError:
            pass


def read_file(path):
    if not os.path.exists(path):
        raise ShareError(f"cookies file not found: {path}")
    with open(path, encoding="utf-8", errors="replace") as fh:
        return youtube_only(fh.read())


def save(text, path):
    """Write it so a half-written file never replaces a good one."""
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    for n in range(20):                 # Windows: a scanner may hold it
        try:
            os.replace(tmp, path)
            return path
        except PermissionError:
            if n == 19:
                raise
            time.sleep(0.25)
    return path


# ---------------------------------------------------------------- network

def lan_ip():
    """This device's address on the Wi-Fi. Connecting a UDP socket sends
    nothing; it only makes the system pick the outgoing interface."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
    except OSError:
        ip = ""
    finally:
        s.close()
    if not ip or ip.startswith("127."):
        try:
            ip = socket.gethostbyname(socket.gethostname())
        except OSError:
            ip = "127.0.0.1"
    return ip


def send(text, port=PORT, wait=300, phone_path=None, log=print,
         host="0.0.0.0"):
    """Offer `text` once on the local network. -> (rc, who fetched it)

    rc 0: a device fetched it; 1: nobody did within `wait` seconds (or Stop).
    """
    token = secrets.token_urlsafe(6)
    got = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):                                    # noqa: N802
            if self.path.strip("/") != token or got:
                self.send_error(404)
                return
            body = text.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Disposition",
                             'attachment; filename="cookies.txt"')
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            got["who"] = self.client_address[0]

        def log_message(self, *a):
            pass

    httpd = None
    try:
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        udp.bind((host, port))
    except OSError as e:
        raise ShareError(f"port {port} is in use ({e}) -- is another "
                         f"'cookies --send' running? Or pick --port") from None
    try:
        try:
            httpd = http.server.HTTPServer((host, port), Handler)
        except OSError:
            httpd = http.server.HTTPServer((host, 0), Handler)
        httpd.timeout = 0.2
        tport = httpd.server_address[1]
        ip = lan_ip()
        url = f"http://{ip}:{tport}/{token}"
        where = phone_path or "/storage/emulated/0/Download/cookies.txt"
        log(f"offering your YouTube login on this Wi-Fi, once, for "
            f"{wait // 60 if wait >= 60 else wait} "
            f"{'min' if wait >= 60 else 's'} (Stop ends it)")
        log("  on the phone, in Termux:  musictool cookies --receive"
            + ("" if port == PORT else f" --port {port}"))
        log(f"  or, without musictool:   curl -o {where} {url}")
        log(f"  or open {url} in the phone's browser")
        end = time.monotonic() + wait
        while not got:
            if cancel.requested():
                log("stopped -- nothing was sent")
                return 1, None
            left = end - time.monotonic()
            if left <= 0:
                log(f"nobody fetched it in {wait} s -- stopped offering it.\n"
                    "  Same Wi-Fi on both? On Windows, the first time, "
                    "allow Python through the firewall (Private networks); "
                    "if you clicked Cancel then: Windows Security > Firewall "
                    "> Allow an app.")
                return 1, None
            r, _, _ = select.select([httpd.socket, udp], [], [],
                                    min(0.5, left))
            if udp in r:
                try:
                    msg, addr = udp.recvfrom(256)
                except OSError:
                    msg = b""
                if msg.strip() == ASK:
                    log(f"  found by {addr[0]}")
                    udp.sendto(ANSWER + f"{tport} {token}".encode(), addr)
            if httpd.socket in r:
                httpd.handle_request()
        log(f"sent to {got['who']} -- stopped offering it")
        return 0, got["who"]
    finally:
        udp.close()
        if httpd is not None:
            httpd.server_close()


def find(port=PORT, wait=60, target=None, log=print):
    """Ask the network (or `target`) where the cookies are offered.
    -> the http URL to fetch."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        # 255.255.255.255 does not always leave on the Wi-Fi (Android with
        # mobile data on); the Wi-Fi's own broadcast address (as /24, what
        # home routers hand out) does.
        ip = lan_ip()
        dests = ([target] if target else
                 ["255.255.255.255"] + ([ip.rsplit(".", 1)[0] + ".255"]
                                        if not ip.startswith("127.") else []))
        end = time.monotonic() + wait
        said = False
        while True:
            if cancel.requested():
                raise ShareError("stopped")
            for d in dests:
                try:
                    s.sendto(ASK, (d, port))
                except OSError:
                    pass
            if not said:
                log(f"looking for 'musictool cookies --send' on "
                    f"{target or 'this Wi-Fi'}...")
                said = True
            t = time.monotonic() + 1.0
            while time.monotonic() < t:
                r, _, _ = select.select([s], [], [], 0.25)
                if not r:
                    if cancel.requested():
                        raise ShareError("stopped")
                    continue
                msg, addr = s.recvfrom(256)
                if msg.startswith(ANSWER):
                    try:
                        tport, token = msg[len(ANSWER):].decode().split()
                        int(tport)
                    except ValueError:
                        continue
                    return f"http://{addr[0]}:{tport}/{token}"
            if time.monotonic() >= end:
                raise ShareError(
                    f"no computer answered in {wait} s. Run 'musictool "
                    f"cookies --send' (or the app's 'Send my YouTube login "
                    f"to my phone') on it first, on the same Wi-Fi. Some "
                    f"routers block devices finding each other: then give "
                    f"its address, shown when it starts: "
                    f"musictool cookies --receive http://...")
    finally:
        s.close()


def receive(out, target=None, port=PORT, wait=60, log=print):
    """Fetch the cookies a computer is offering and save them to `out`.
    -> (path, how many, signed in?)

    target: None (look on the Wi-Fi), an address ("192.168.1.5" or
    "192.168.1.5:8765"), or the full http URL the computer printed."""
    t = (target or "").strip()
    if t.startswith("http://") or t.startswith("https://"):
        url = t
    else:
        if ":" in t:
            t, p = t.rsplit(":", 1)
            port = int(p)
        url = find(port, wait, t or None, log)
    log(f"fetching {url.rsplit('/', 1)[0]}/...")
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            body = r.read(5_000_000).decode("utf-8", "replace")
    except OSError as e:
        raise ShareError(f"could not fetch it: {e}") from None
    text, n, signed = youtube_only(body)
    if not n:
        raise ShareError("what came back has no YouTube cookies in it -- "
                         "nothing was saved")
    save(text, out)
    return out, n, signed
