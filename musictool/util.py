"""Shared helpers: text normalisation, matching, LRC handling, platform paths, HTTP."""

import difflib
import html
import json
import os
import platform
import re
import shutil
import socket
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = os.environ.get(
    "MUSICTOOL_UA", "musictool/1.0 (personal script; contact: you@example.com)")

AUDIO_EXT = (".m4a", ".mp3", ".flac", ".opus", ".ogg", ".aac", ".wav", ".mka")
TS = re.compile(r"\[\d{1,2}:\d{2}(?:[.:]\d{1,3})?\]")

# Characters yt-dlp/YouTube leave in filenames that break matching.
CHAR_FIX = {
    "\u29f8": "/", "\uff5c": "|", "\uff1a": ":", "\uff1f": "?",
    "\u2019": "'", "\u2018": "'", "\u201c": '"', "\u201d": '"',
    "\u2013": "-", "\u2014": "-", "\u00a0": " ", "\u3000": " ",
}

PAREN_NOISE = re.compile(
    r"[\(\[]\s*(feat\.?|ft\.?|featuring|with|con|official.*|lyrics?.*|audio|"
    r"music video|video|visualizer|live|ao vivo|en vivo|radio version|"
    r"extended version|viral version|spontaneous|spontâneo|remaster\w*|"
    r"mono|stereo|bonus track|version|versión|acoustic|acústico|explicit|"
    r"clean|slowed|reverb|sped up)\b[^\)\]]*[\)\]]", re.IGNORECASE)


# --------------------------------------------------------------------------
# platform
# --------------------------------------------------------------------------
def is_windows() -> bool:
    return os.name == "nt"


def is_termux() -> bool:
    return "com.termux" in os.environ.get("PREFIX", "") or bool(
        os.environ.get("TERMUX_VERSION"))


# --------------------------------------------------------------------------
# network resilience
#
# Home WiFi that drops for a few minutes after a power cut is the norm here, so
# every network touch-point retries instead of failing. These helpers decide
# what counts as a (retryable) network problem and how long to wait between
# tries so a 2-3 minute outage is slept through rather than treated as an error.
# --------------------------------------------------------------------------
NET_HINTS = (
    "urlopen error", "url error", "name or service not known",
    "nodename nor servname", "getaddrinfo", "name resolution",
    "temporary failure in name resolution", "network is unreachable",
    "network unreachable", "no route to host", "connection refused",
    "connection reset", "connection aborted", "connection timed out",
    "timed out", "timeout", "ssl", "eof occurred", "remote disconnected",
    "an existing connection was forcibly closed", "broken pipe",
    "internet connection", "unable to connect", "could not connect",
    "no internet", "offline", "dns", "socket", "http error 5",  # 5xx = server
)


def is_net_error(text):
    """True when `text` (an error message / stderr) looks like a connectivity
    problem worth retrying, rather than a bad URL or bad input."""
    t = (text or "").lower()
    if not t:
        return False
    # Things that are definitively NOT network problems must win, so a message
    # like "video unavailable" is never retried for minutes.
    for hard in ("no such file", "not found: url", "private video",
                 "video unavailable", "sign in", "unsupported url",
                 "is not a valid url", "no matches found"):
        if hard in t:
            return False
    return any(h in t for h in NET_HINTS)


def net_sleeps(max_wait=240, base=2, cap=60):
    """Exponential back-off sleeps that add up to `max_wait` seconds.

    With the defaults this is 2,4,8,16,32,60,60,58 -- long enough to ride out a
    two-to-three-minute WiFi drop on the first failing request, after which the
    rest of the batch proceeds normally.
    """
    sleeps = []
    total = 0.0
    s = float(base)
    while total < max_wait:
        step = min(s, cap, max_wait - total)
        if step <= 0:
            break
        sleeps.append(step)
        total += step
        s = min(s * 2, cap)
    return sleeps


def existing_paths(paths):
    """Split `paths` into (exist, missing) without touching the network."""
    good, missing = [], []
    for p in paths or []:
        q = os.path.abspath(os.path.expanduser(str(p)))
        (good if os.path.exists(q) else missing).append(str(p))
    return good, missing


def common_root(paths):

    """Deepest folder that contains every path (a folder counts as itself).

    Used when the GUI hands over a subset of individual files: the report and
    the .lrc sidecars must land next to those files, not in some remembered
    output folder.
    """
    dirs = []
    for p in paths or []:
        p = os.path.abspath(os.path.expanduser(str(p)))
        dirs.append(p if os.path.isdir(p) else os.path.dirname(p))
    if not dirs:
        return "."
    if len(set(dirs)) == 1:
        return dirs[0]
    try:
        return os.path.commonpath(dirs)
    except (ValueError, AttributeError):
        return dirs[0]


def config_dir() -> str:
    if is_windows():
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
            os.path.expanduser("~"), ".config")
    d = os.path.join(base, "musictool")
    os.makedirs(d, exist_ok=True)
    return d


# --------------------------------------------------------------------------
# Browser cookies
#
# YouTube now refuses playlist downloads from a plain IP with "Sign in to
# confirm you're not a bot". yt-dlp can read the login straight out of an
# installed browser (--cookies-from-browser), which on Windows means no
# exporting a cookies.txt at all. These are the same profile locations
# yt-dlp itself uses (yt_dlp/cookies.py, 2026.08.19), so detection cannot
# point at a browser yt-dlp is unable to read.
# --------------------------------------------------------------------------
BROWSERS = ("edge", "chrome", "firefox", "brave", "vivaldi", "opera",
            "chromium", "whale", "safari")


def browser_dirs(platform=None, env=None):
    """{browser: user-data directory} for this OS."""
    platform = platform or sys.platform
    env = os.environ if env is None else env
    home = os.path.expanduser("~")
    if platform in ("win32", "cygwin"):
        prof = env.get("USERPROFILE") or home
        local = env.get("LOCALAPPDATA") or os.path.join(prof, "AppData", "Local")
        roam = env.get("APPDATA") or os.path.join(prof, "AppData", "Roaming")
        return {
            "edge": os.path.join(local, "Microsoft", "Edge", "User Data"),
            "chrome": os.path.join(local, "Google", "Chrome", "User Data"),
            "brave": os.path.join(local, "BraveSoftware", "Brave-Browser",
                                  "User Data"),
            "vivaldi": os.path.join(local, "Vivaldi", "User Data"),
            "chromium": os.path.join(local, "Chromium", "User Data"),
            "whale": os.path.join(local, "Naver", "Naver Whale", "User Data"),
            "opera": os.path.join(roam, "Opera Software", "Opera Stable"),
            "firefox": os.path.join(roam, "Mozilla", "Firefox", "Profiles"),
        }
    if platform == "darwin":
        app = os.path.join(home, "Library", "Application Support")
        return {
            "edge": os.path.join(app, "Microsoft Edge"),
            "chrome": os.path.join(app, "Google", "Chrome"),
            "brave": os.path.join(app, "BraveSoftware", "Brave-Browser"),
            "vivaldi": os.path.join(app, "Vivaldi"),
            "chromium": os.path.join(app, "Chromium"),
            "whale": os.path.join(app, "Naver", "Whale"),
            "opera": os.path.join(app, "com.operasoftware.Opera"),
            "safari": os.path.join(home, "Library", "Cookies"),
            "firefox": os.path.join(app, "Firefox", "Profiles"),
        }
    cfg = env.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
    return {
        "edge": os.path.join(cfg, "microsoft-edge"),
        "chrome": os.path.join(cfg, "google-chrome"),
        "brave": os.path.join(cfg, "BraveSoftware", "Brave-Browser"),
        "vivaldi": os.path.join(cfg, "vivaldi"),
        "chromium": os.path.join(cfg, "chromium"),
        "whale": os.path.join(cfg, "naver-whale"),
        "opera": os.path.join(cfg, "opera"),
        "firefox": os.path.join(cfg, "firefox"),
    }


def cookie_stores(browser, user_dir):
    """Candidate cookie files for a browser, most likely first."""
    if browser == "firefox":
        try:
            profs = sorted(os.listdir(user_dir))
        except OSError:
            return []
        return [os.path.join(user_dir, p, "cookies.sqlite") for p in profs]
    if browser == "safari":
        return [os.path.join(user_dir, "Cookies.binarycookies")]
    return [os.path.join(user_dir, "Default", "Network", "Cookies"),
            os.path.join(user_dir, "Default", "Cookies")]


def js_runtime():
    """The JavaScript runtime yt-dlp can use to solve YouTube's n-challenge.

    yt-dlp only enables deno by default; node has to be opted in with
    --js-runtimes node. -> (name, path) or (None, None).
    """
    for name in ("deno", "node"):
        path = shutil.which(name)
        if path:
            return name, path
    return None, None


def pot_server_reachable(host="127.0.0.1", port=4416, timeout=0.4):
    """Is the bgutil PO-token provider answering? (It is a separate server.)"""
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def detect_browser(platform=None, env=None):
    """The browser most likely holding the user's YouTube login.

    Picks the one whose cookie database was touched most recently -- that is
    the browser they actually watch YouTube in. -> (name, cookie_path) or
    (None, None) when nothing is installed.
    """
    best = (None, None, -1.0)
    for name, user_dir in browser_dirs(platform, env).items():
        for path in cookie_stores(name, user_dir):
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            if mtime > best[2]:
                best = (name, path, mtime)
    return best[0], best[1]


def ytdlp_cmd() -> list:
    """Prefer the CLI, fall back to `python -m yt_dlp` (common on Termux)."""
    exe = shutil.which("yt-dlp") or shutil.which("yt-dlp.exe")
    if exe:
        return [exe]
    return [sys.executable, "-m", "yt_dlp"]


def have_ffmpeg() -> bool:
    return bool(shutil.which("ffmpeg"))


def ffmpeg_hint() -> str:
    if is_termux():
        return "pkg install ffmpeg"
    if is_windows():
        return "winget install Gyan.FFmpeg  (or choco install ffmpeg)"
    return "install ffmpeg with your package manager"


# --------------------------------------------------------------------------
# text normalisation and matching  (ported from the verified fix_lyrics.py)
# --------------------------------------------------------------------------
def fix_chars(s: str) -> str:
    for k, v in CHAR_FIX.items():
        s = s.replace(k, v)
    return unicodedata.normalize("NFKC", html.unescape(s))


def strip_paren(s: str) -> str:
    prev, out = None, (s or "").strip()
    while prev != out:
        prev = out
        out = PAREN_NOISE.sub("", out).strip(" -\u2013\u2014|,")
    return out or (s or "").strip()


def norm_key(s: str) -> str:
    s = fix_chars(s or "").lower()
    s = "".join(c for c in unicodedata.normalize("NFKD", s)
                if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", s)).strip()


def similarity(a: str, b: str) -> float:
    na, nb = norm_key(a), norm_key(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    r = difflib.SequenceMatcher(None, na, nb).ratio()
    ta, tb = set(na.split()), set(nb.split())
    if ta and tb and (ta <= tb or tb <= ta):
        r = max(r, 0.9)
    if na in nb or nb in na:
        r = max(r, 0.85)
    return r


def medley_parts(title: str) -> list:
    return [p.strip() for p in re.split(r"\s*(?:/|\|)\s*", fix_chars(title or ""))
            if p.strip()]


def artist_variants(tag_artist: str, fname_artist: str) -> list:
    out = []
    for src in (tag_artist, fname_artist):
        if not src:
            continue
        src = fix_chars(src).strip()
        if src and src not in out:
            out.append(src)
        head = re.split(r",|;", src)[0].strip()
        if head and head not in out:
            out.append(head)
    return [a for a in out if a]


def title_variants(title: str) -> list:
    t = fix_chars(title or "").strip()
    out = []
    for cand in (strip_paren(t), t):        # clean title first: what DBs store
        if cand and cand not in out:
            out.append(cand)
    parts = medley_parts(t)
    if len(parts) > 1:
        for p in parts:
            for cand in (p, strip_paren(p)):
                if cand and cand not in out:
                    out.append(cand)
        joined = " / ".join(parts)
        if joined not in out:
            out.append(joined)
    return out


# --------------------------------------------------------------------------
# LRC
# --------------------------------------------------------------------------
def parse_cues(lrc: str) -> list:
    """'[mm:ss.xx] text' -> [(seconds, text)]"""
    out = []
    for m in re.finditer(r"\[(\d{1,2}):(\d{2})(?:[.:](\d{1,3}))?\]\s?([^\n]*)",
                         lrc or ""):
        mm, ss, frac, text = m.groups()
        if frac is None:
            secs = int(mm) * 60 + int(ss)
        else:
            scale = 10 ** (3 - len(frac))
            secs = int(mm) * 60 + int(ss) + int(frac) * scale / 1000.0
        out.append((round(secs, 3), text.strip()))
    return out


def classify(lrc: str) -> str:
    """synced | partial | plain | empty"""
    if not lrc or len(lrc.strip()) < 20:
        return "empty"
    cues = len(TS.findall(lrc))
    lines = len([l for l in lrc.splitlines() if l.strip()])
    if cues >= 5 and cues >= 0.4 * max(lines, 1):
        return "synced"
    return "partial" if cues >= 1 else "plain"


def shift_lrc(lrc: str, seconds: float) -> str:
    def fix(m):
        total = int(m.group(1)) * 60 + int(m.group(2)) + (
            int(m.group(3)) * (10 ** (3 - len(m.group(3)))) / 1000.0
            if m.group(3) else 0.0)
        total = max(0.0, total + seconds)
        return "[%02d:%05.2f]" % (int(total // 60), total % 60)
    return re.sub(r"\[(\d{1,2}):(\d{2})(?:[.:](\d{1,3}))?\]", fix, lrc or "")


def timed_to_lrc(cues) -> str:
    """[(ms, text)] -> LRC text"""
    out = []
    for ms, text in cues:
        s = max(0.0, ms / 1000.0)
        out.append("[%02d:%05.2f] %s" % (int(s // 60), s % 60, text))
    return "\n".join(out) + "\n"


def to_plain(lrc: str) -> str:
    cues = parse_cues(lrc)
    if not cues:
        return lrc
    return "\n".join(t for _, t in cues if t) + "\n"


# --------------------------------------------------------------------------
# HTTP with cache + rate-limit handling
# --------------------------------------------------------------------------
class Http:
    def __init__(self, cache_path=None, delay=0.35, timeout=30, max_wait=240):
        self.delay = delay
        self.timeout = timeout
        self.max_wait = max_wait
        self.calls = 0
        self.cache = {}
        self.cache_path = cache_path
        if cache_path and os.path.exists(cache_path):
            try:
                self.cache = json.load(open(cache_path, encoding="utf-8"))
            except Exception:
                self.cache = {}

    def save(self):
        if self.cache_path:
            tmp = self.cache_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.cache, fh, ensure_ascii=False)
            os.replace(tmp, self.cache_path)

    def json(self, url, params=None, headers=None, method="GET", body=None,
             use_cache=True):
        key = json.dumps([method, url, sorted((params or {}).items()),
                          body if isinstance(body, str) else None],
                         sort_keys=True, ensure_ascii=False)
        if use_cache and key in self.cache:
            return self.cache[key]
        full = url + ("?" + urllib.parse.urlencode(params) if params else "")
        data = None if body is None else (
            body.encode("utf-8") if isinstance(body, str) else
            json.dumps(body).encode("utf-8"))
        result = None
        net_wait = iter(net_sleeps(self.max_wait))
        for _ in range(1 + 8):                      # 8 network retries max
            h = {"User-Agent": USER_AGENT}
            if data is not None:
                h["Content-Type"] = "application/json"
            h.update(headers or {})
            try:
                req = urllib.request.Request(full, data=data, headers=h, method=method)
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read().decode("utf-8", "replace")
                result = json.loads(raw)
                break
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    time.sleep(int(e.headers.get("Retry-After") or 5))
                    continue
                result = None
                break
            except (urllib.error.URLError, socket.timeout, TimeoutError,
                    ConnectionError, OSError):
                # Connectivity problem (WiFi down): sleep it out and retry.
                w = next(net_wait, None)
                if w is None:
                    result = None
                    break
                time.sleep(w)
                continue
            except Exception:
                result = None
                break
        self.calls += 1
        if use_cache:
            self.cache[key] = result
        time.sleep(self.delay)
        return result
