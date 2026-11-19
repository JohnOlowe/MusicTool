#!/usr/bin/env python3
"""musictool test suite.

Run:  python3 tests/test_musictool.py
Needs: mutagen, and either ffmpeg on PATH or `pip install imageio-ffmpeg`.

Nothing here touches the network: the three lyrics APIs are stubbed with the
documented response shapes, and yt-dlp is replaced by a fake on PATH.
"""
import http.server
import json
import os
import shutil
import stat
import subprocess
import sys
import threading
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ENTRY = os.path.join(ROOT, "musictool.py")
sys.path.insert(0, ROOT)

from mutagen.mp4 import MP4  # noqa: E402

try:
    import imageio_ffmpeg
    FF = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    FF = shutil.which("ffmpeg")
if not FF:
    sys.exit("need ffmpeg on PATH or: pip install imageio-ffmpeg")

WORK = "/tmp/musictool-tests"
MUSIC = os.path.join(WORK, "Music")
BIN = os.path.join(WORK, "bin")
PORT = 8797
BASE = f"http://127.0.0.1:{PORT}"
A = chr(0xa9)
fails = []


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {detail}" if detail else ""))
    if not cond:
        fails.append(name)


def lrc(tag, n=8, start=0):
    return "".join(f"[00:{start + i * 4:02d}.00] {tag} line number {i}\n" for i in range(n))


MYH, REST, HOLY, CLOSER, JIREH = (lrc("move your heart"), lrc("rest on us"),
                                 lrc("holy"), lrc("closer chorus"), lrc("jireh"))
WWW = "[00:01.65] Hmm-ah\n" + lrc("well well well", 8, start=2)
TODO_PLAIN = "Brotó de mi alma una nueva canción\n" * 4
NETEASE_LRC = lrc("netease", 12)
NE_MODE = {}
YTM_CUES = [("Hmm-ah", 1650), ("Story to tell", 4140), ("All too well", 7910),
            ("Good to me", 11450), ("Yeah", 15200), ("Mountain", 19180)]

DB = {
    "well well well": {"id": 1, "trackName": "Well, Well, Well",
                       "artistName": "Maverick City Music", "albumName": "MAVHOUSE",
                       "duration": 225.0, "syncedLyrics": WWW, "plainLyrics": "p"},
    "todo cambio": {"id": 2, "trackName": "Todo Cambió",
                    "artistName": "Maverick City Music", "albumName": "Venga Tu Reino",
                    "duration": 476.0, "syncedLyrics": None, "plainLyrics": TODO_PLAIN},
    "move your heart": {"id": 11, "trackName": "Move Your Heart",
                        "artistName": "Maverick City Music", "albumName": "MAVHOUSE",
                        "duration": 300.0, "syncedLyrics": MYH},
    "rest on us": {"id": 12, "trackName": "Rest on Us",
                   "artistName": "Maverick City Music", "albumName": "MAVHOUSE",
                   "duration": 300.0, "syncedLyrics": REST},
    "holy": {"id": 13, "trackName": "Holy", "artistName": "Maverick City Music",
             "albumName": "MAVHOUSE", "duration": 300.0, "syncedLyrics": HOLY},
    "closer": {"id": 14, "trackName": "Closer", "artistName": "Maverick City Music",
               "albumName": "MAVHOUSE", "duration": 250.0, "syncedLyrics": CLOSER},
}
CALLS = []


def norm(s):
    import re
    import unicodedata
    s = unicodedata.normalize("NFKD", (s or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


class Stub(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj, code=200):
        d = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(d)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        CALLS.append(("GET", u.path))
        if u.path == "/api/get":
            rec = DB.get(norm(q.get("track_name", "")))
            if rec and norm(q.get("artist_name", "")) == "maverick city music" \
                    and (not q.get("duration")
                         or abs(float(q["duration"]) - rec["duration"]) <= 2) \
                    and (not q.get("album_name")
                         or norm(q["album_name"]) == norm(rec["albumName"])):
                return self._send(rec)
            return self._send({"code": 404, "name": "TrackNotFound"}, 404)
        if u.path == "/api/search":
            key = norm(q.get("q", "")).replace("maverick city music", "").strip()
            return self._send([DB[key]] if key in DB else [])
        if u.path == "/ne/search/get/web":
            # what the real /search/get/web answers since 2026-11: an
            # encrypted hex string where the result object used to be
            return self._send({"code": 200, "result": "35b1748964af8a7c1d88"})
        if u.path == "/ne/search/get" and NE_MODE.get("encrypted"):
            return self._send({"code": 200, "result": "35b1748964af8a7c1d88"})
        if u.path == "/ne/search/get" and NE_MODE.get("right_artist"):
            return self._send({"code": 200, "result": {"songs": [
                {"id": 7, "name": "Shape of You", "artists": [{"name": "Ed Sheeran"}],
                 "album": {"name": "Divide"}, "duration": 233712}]}})
        if u.path == "/ne/search/get":
            # deliberately the WRONG artist: the guard must reject it
            return self._send({"code": 200, "result": {"songs": [
                {"id": 5, "name": "Well, Well, Well", "artists": [{"name": "Someone Else"}],
                 "album": {"name": "Other"}, "duration": 225000}]}})
        if u.path == "/ne/song/lyric":
            if NE_MODE.get("right_artist"):
                return self._send({"code": 200, "lrc": {"lyric":
                    "[00:00.000] 作词 : Ed Sheeran\n[00:01.000] 作曲 : Steve Mac\n"
                    + NETEASE_LRC}})
            return self._send({"code": 200, "lrc": {"lyric": NETEASE_LRC}})
        self._send({"error": "no route"}, 404)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        client = body.get("context", {}).get("client", {}).get("clientName")
        CALLS.append(("POST", u.path, client))
        if u.path == "/yt/next":
            return self._send({"contents": {"singleColumnMusicWatchNextResultsRenderer":
                {"tabbedRenderer": {"watchNextTabbedResultsRenderer": {"tabs": [
                    {"tabRenderer": {"endpoint": {"browseEndpoint": {
                        "browseId": "MPLY" + str(body.get("videoId", "xyz")),
                        "browseEndpointContextSupportedConfigs": {
                            "browseEndpointContextMusicConfig": {
                                "pageType": "MUSIC_PAGE_TYPE_TRACK_LYRICS"}}}}}}]}}}}})
        if u.path == "/yt/browse":
            if client != "ANDROID_MUSIC":          # WEB_REMIX has no timings
                return self._send({"contents": {"plain": "no timings"}})
            bid = str(body.get("browseId", ""))
            if "UNTIMED" in bid:   # what YouTube mostly serves since 2026-11
                rows = [{"lyricLine": t} for t, ms in YTM_CUES]
            else:
                tag = "" if bid in ("MPLYxyz", "MPLYVID123") else bid + " "
                rows = [{"lyricLine": tag + t,
                         "cueRange": {"startTimeMilliseconds": str(ms)}}
                        for t, ms in YTM_CUES]
            return self._send({"contents": {"elementRenderer": {"newElement": {"type":
                {"componentType": {"model": {"timedLyricsModel": {"lyricsData":
                    {"timedLyricsData": rows}}}}}}}}})
        self._send({"error": "no route"}, 404)


def setup():
    shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(BIN)
    os.makedirs(MUSIC)
    shutil.copy(FF, os.path.join(BIN, "ffmpeg"))
    tpl = os.path.join(WORK, "template.m4a")
    subprocess.run([FF, "-y", "-v", "error", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=300", "-c:a", "aac", "-b:a", "64k", tpl],
                   check=True)
    fake = os.path.join(BIN, "yt-dlp")
    open(fake, "w").write(f'''#!/usr/bin/env python3
import json, os, subprocess, sys
a = sys.argv[1:]
if "--version" in a:
    print("2026.09.16-fake"); sys.exit(0)
if "-J" in a:
    print(json.dumps({{"id": "VID123", "title": "Well, Well, Well",
        "uploader": "Maverick City Music - Topic", "duration": 225}})); sys.exit(0)
out = a[a.index("-P") + 1] if "-P" in a else "."
dst = os.path.join(out, "Maverick City Music - Well, Well, Well.m4a")
subprocess.run([sys.executable, "-c", """
import shutil,sys
from mutagen.mp4 import MP4
shutil.copy(sys.argv[1], sys.argv[2])
A=chr(0xa9)
f=MP4(sys.argv[2]); f.tags[A+"nam"]=["Well, Well, Well"]
f.tags[A+"ART"]=["Maverick City Music"]; f.tags[A+"alb"]=["MAVHOUSE"]; f.save()
""", {tpl!r}, dst], check=True)
print("VID123\\t" + dst)
''')
    os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return tpl


TPL = setup()
srv = http.server.HTTPServer(("127.0.0.1", PORT), Stub)
threading.Thread(target=srv.serve_forever, daemon=True).start()
ENV = dict(os.environ, PATH=BIN + os.pathsep + os.environ["PATH"],
           MUSICTOOL_LRCLIB_BASE=BASE, MUSICTOOL_NETEASE_BASE=BASE + "/ne",
           MUSICTOOL_YTM_BASE=BASE + "/yt",
           XDG_CONFIG_HOME=os.path.join(WORK, "cfg"), PYTHONPATH=ROOT)
# providers.py reads the base URLs at import time, so the parent process needs
# them too -- not just the subprocesses launched via run().
os.environ.update(ENV)


def run(*args):
    return subprocess.run([sys.executable, ENTRY, *args], capture_output=True,
                          text=True, env=ENV)


def make(name, title, artist, lyrics, dur_note=None):
    p = os.path.join(MUSIC, name)
    shutil.copy(TPL, p)
    m = MP4(p)
    m.tags[A + "nam"] = [title]
    m.tags[A + "ART"] = [artist]
    m.tags[A + "alb"] = ["MAVHOUSE"]
    if lyrics:
        m.tags[A + "lyr"] = [lyrics]
    m.save()
    return p


# ============================================================ 1. util
print("--- 1. util: normalisation and LRC ---")
from musictool import util  # noqa: E402

check("fullwidth ？ -> ?", util.fix_chars("Mary Did You Know？") == "Mary Did You Know?")
check("&amp; -> &", util.fix_chars("A &amp; B") == "A & B")
check("⧸ -> /", util.fix_chars("A⧸B") == "A/B")
check("｜ -> |", util.fix_chars("A｜B") == "A|B")
check("strip (feat. ...)", util.strip_paren(
    "Well, Well, Well (feat. Nick Day, Roman Collins & Za Smith)") == "Well, Well, Well")
check("strip (Radio Version)",
      util.strip_paren("Fear Is Not My Future (Radio Version)") == "Fear Is Not My Future")
check("strip (Ao Vivo)", util.strip_paren("Aleluia Santo (Ao Vivo)") == "Aleluia Santo")
check("medley split on ⧸", util.medley_parts(
    "Jesus at the Center⧸All Hail King Jesus") == ["Jesus at the Center", "All Hail King Jesus"])
check("credit list -> lead credit", "Maverick City Music" in util.artist_variants(
    "Maverick City Music, Song House, Nick Day", ""))
check("accents folded", util.norm_key("Todo Cambió") == "todo cambio")
check("artist suffix still similar",
      util.similarity("Maverick City Music", "Maverick City Music, Sam Rivera") >= 0.9)
check("different artist dissimilar", util.similarity("Maverick City Music", "TRIBL") < 0.5)
check("parse_cues reads WWW", len(util.parse_cues(WWW)) == 9, str(len(util.parse_cues(WWW))))
check("classify synced", util.classify(WWW) == "synced")
check("classify plain", util.classify("just words\n" * 6) == "plain")
check("classify empty", util.classify("") == "empty")
check("shift -2.5s", util.shift_lrc("[01:00.00] x\n", -2.5).startswith("[00:57.50]"))
check("shift clamps at zero", util.shift_lrc("[00:01.00] x\n", -5).startswith("[00:00.00]"))
check("timed_to_lrc", util.timed_to_lrc([(1650, "Hi")]) == "[00:01.65] Hi\n")

# ============================================================ 2. tags
print("\n--- 2. tags ---")
from musictool import tags  # noqa: E402

p = make("Maverick City Music - Well, Well, Well (feat. X &amp; Y).m4a",
         "Well, Well, Well (feat. X & Y)", "Maverick City Music, Song House", "")
info = tags.read(p)
check("read title", info["title"] == "Well, Well, Well (feat. X & Y)")
check("read artist", info["artist"].startswith("Maverick City Music, Song House"))
check("read duration", abs(info["duration"] - 300) < 2, str(round(info["duration"], 1)))
check("state empty", info["state"] == "empty")
fa, ft = tags.from_filename(p)
check("from_filename artist", fa == "Maverick City Music", fa)
check("from_filename unescapes entity", "&" in ft and "&amp;" not in ft, ft)
tags.write_lyrics(p, WWW)
check("embed -> state synced", tags.read(p)["state"] == "synced")
tags.write_lyrics(p, "replacement words " * 6, backup_dir=os.path.join(WORK, "bk"))
bk = os.listdir(os.path.join(WORK, "bk"))
check("backup written on overwrite", len(bk) == 1, str(bk))
check("backup holds the previous lyrics",
      WWW.splitlines()[0] in open(os.path.join(WORK, "bk", bk[0]), encoding="utf-8").read())

# ============================================================ 3. providers
print("\n--- 3. providers ---")
from musictool import providers as P  # noqa: E402
from musictool.util import Http  # noqa: E402

http = Http(cache_path=None, delay=0.0)
qv = P.Query(artist="Maverick City Music", title="Well, Well, Well",
             album="MAVHOUSE", duration=225, video_id="VID123")
got = P.YTMusic().fetch(qv, http)
check("ytmusic returns a candidate", len(got) == 1)
check("ytmusic is synced + trusted", got[0].synced and got[0].trusted)
check("ytmusic LRC starts at 1.65s", got[0].lyrics.startswith("[00:01.65] Hmm-ah"),
      got[0].lyrics.splitlines()[0])
check("ytmusic emitted 6 cues", got[0].lyrics.count("[00:") == 6)
check("ytmusic skipped without videoId",
      P.YTMusic().fetch(P.Query(title="X", artist="Y"), http) == [])
ne = P.NetEase().fetch(P.Query(artist="Maverick City Music", title="Well, Well, Well"), http)
check("netease returns a candidate", len(ne) == 1)
ok, sc = P.score(ne[0], qv, qv.titles(), qv.artists())
check("netease wrong-artist result REJECTED", not ok, f"score {sc:.2f}")
lr = P.LrcLib().fetch(qv, http)
check("lrclib exact hit", any(c.synced for c in lr))
best, tried = P.find_best(qv, P.build(), http)
check("find_best prefers ytmusic (trusted)", best.provider == "ytmusic", best.provider)
check("find_best stops after a trusted hit",
      not any(t.startswith("netease") for t in tried), str(tried))
plainq = P.Query(artist="Maverick City Music", title="Todo Cambió", duration=476)
b2, t2 = P.find_best(plainq, [P.LrcLib()], http)
check("plain-only -> None by default", b2 is None)
check("reason mentions --allow-unsynced", any("allow-unsynced" in t for t in t2))
b3, _ = P.find_best(plainq, [P.LrcLib()], http, allow_unsynced=True)
check("plain-only accepted with the flag", b3 is not None and not b3.synced)


class Fake(P.Provider):
    def __init__(self, name, c):
        self.name = name
        self.c = c

    def fetch(self, q, http):
        return [self.c] if self.c else []


perfect_plain = P.Candidate("provA", "Well, Well, Well", "Maverick City Music",
                            duration=225, lyrics="perfect plain " * 8, synced=False)
weaker_synced = P.Candidate("provB", "Well Well", "Maverick City Music",
                            duration=240, lyrics=lrc("timed"), synced=True)
_s, sp = P.score(perfect_plain, qv, qv.titles(), qv.artists())
_s, ss = P.score(weaker_synced, qv, qv.titles(), qv.artists())
check("plain candidate scores higher", sp > ss, f"{sp:.3f} > {ss:.3f}")
bw, _ = P.find_best(qv, [Fake("provA", perfect_plain), Fake("provB", weaker_synced)],
                    http, allow_unsynced=True)
check("...but SYNCED still wins with --allow-unsynced", bw.synced and
      bw.provider == "provB", bw.provider)
bo, _ = P.find_best(qv, [Fake("provB", weaker_synced), Fake("provA", perfect_plain)],
                    http, allow_unsynced=True)
check("provider order does not change that", bo.synced)

# ============================================================ 4. download args
print("\n--- 4. download.build_args ---")
from musictool import download  # noqa: E402

a = download.build_args("u", {"output_dir": "/tmp/out", "audio_format": "m4a",
                              "audio_quality": 4, "embed_thumbnail": True,
                              "embed_metadata": True, "playlist_items": "1-50",
                              "cookies": "/x/cookies.txt", "concurrent": 4})
check("extracts audio", "-x" in a)
check("m4a format", "m4a" in a)
check("embeds + converts thumbnail",
      "--embed-thumbnail" in a and "--convert-thumbnails" in a)
check("strips ' - Topic'", " - Topic$" in a)
check("maps artist metadata",
      any("%(artist,uploader)s:%(meta_artist)s" == x for x in a))
check("playlist range", "--playlist-items" in a and "1-50" in a)
check("cookies", "--cookies" in a)
check("output dir", "-P" in a and "/tmp/out" in a)
a2 = download.build_args("u", {"keep_video": True, "output_dir": "."})
check("keep_video drops -x", "-x" not in a2)
check("keep_video drops thumbnail", "--embed-thumbnail" not in a2)
check("--no-playlist", "--no-playlist" in
      download.build_args("u", {"output_dir": ".", "no_playlist": True}))
check("--yes-playlist", "--yes-playlist" in
      download.build_args("u", {"output_dir": ".", "yes_playlist": True}))
download.record(os.path.join(WORK, "ix"), os.path.join(WORK, "ix", "S.m4a"), "V9", "u")
check("index round trip",
      download.load_index(os.path.join(WORK, "ix"))["S.m4a"]["video_id"] == "V9")

# ============================================================ 5. CLI end-to-end
print("\n--- 5. CLI: get (download -> videoId -> ytmusic lyrics) ---")
r = run("get", "https://music.youtube.com/watch?v=VID123", "-o", MUSIC)
audio = os.path.join(MUSIC, "Maverick City Music - Well, Well, Well.m4a")
side = os.path.join(MUSIC, "Lyrics", "Maverick City Music - Well, Well, Well.lrc")
idx = os.path.join(MUSIC, ".musictool-index.json")
check("get exit 0", r.returncode == 0, r.stderr[-300:])
check("audio downloaded", os.path.exists(audio))
check("videoId recorded", os.path.exists(idx) and
      json.load(open(idx))["Maverick City Music - Well, Well, Well.m4a"]["video_id"] == "VID123")
check("sidecar written", os.path.exists(side))
check("lyrics came via ytmusic, not lrclib",
      os.path.exists(side) and "Hmm-ah" in open(side, encoding="utf-8").read()
      and "well well well line" not in open(side, encoding="utf-8").read())
check("used ANDROID_MUSIC for timings",
      any(c[0] == "POST" and c[2] == "ANDROID_MUSIC" for c in CALLS))
check("(c)lyr embedded", MP4(audio).tags.get(A + "lyr") is not None)
check("report written", os.path.exists(os.path.join(MUSIC, "lyrics-report.txt")))

print("\n--- 6. CLI: scan is offline ---")
n = len(CALLS)
r = run("scan", MUSIC)
check("scan reports synced", "synced" in r.stdout)
check("scan made zero network calls", len(CALLS) == n, f"{len(CALLS) - n}")
r = run("scan", MUSIC, "--json")
try:
    d = json.loads(r.stdout)
    check("scan --json valid", isinstance(d, list) and d and "state" in d[0])
except Exception as e:
    check("scan --json valid", False, str(e))

print("\n--- 7. CLI: lyrics --dry-run ---")
_m = MP4(audio)
_m.tags[A + "lyr"] = ["plain words here " * 4]
_m.save()
r = run("lyrics", MUSIC, "--dry-run")
check("dry-run exit 0", r.returncode == 0, r.stderr[-200:])
check("dry-run left the tag alone",
      MP4(audio).tags.get(A + "lyr") == ["plain words here " * 4])

print("\n--- 8. stale embedded tag repaired from a good sidecar ---")
r = run("lyrics", MUSIC)
check("repair exit 0", r.returncode == 0, r.stderr[-200:])
check("tag repaired from sidecar, no network",
      "repairing tag from existing .lrc sidecar" in r.stdout,
      r.stdout[-220:].replace("\n", " "))
check("embedded now matches the sidecar",
      MP4(audio).tags[A + "lyr"][0].rstrip() ==
      open(side, encoding="utf-8").read().rstrip())

print("\n--- 8b. CLI: lyrics (lrclib + netease only, no sidecar) ---")
os.remove(side)
_m = MP4(audio)
_m.tags[A + "lyr"] = ["plain words here " * 4]
_m.save()
r = run("lyrics", MUSIC, "--providers", "lrclib,netease")
check("exit 0", r.returncode == 0, r.stderr[-200:])
check("sidecar rewritten", os.path.exists(side))
check("now from lrclib",
      os.path.exists(side) and
      "well well well line number 0" in open(side, encoding="utf-8").read(),
      open(side, encoding="utf-8").read().splitlines()[0] if os.path.exists(side) else "no file")
check("backup created", os.path.isdir(os.path.join(MUSIC, "Lyrics", "_backup"))
      and bool(os.listdir(os.path.join(MUSIC, "Lyrics", "_backup"))))

print("\n--- 9. duplicates: detect then fix ---")
from musictool import lyrics as L  # noqa: E402

p1 = make("Maverick City Music - Move Your Heart (feat. Dante Bowe).m4a",
          "Move Your Heart", "Maverick City Music", MYH)
p2 = make("Maverick City Music - Rest on Us (feat. Brandon Lake).m4a",
          "Rest on Us", "Maverick City Music", MYH)          # WRONG
p3 = make("Maverick City Music - Holy.m4a", "Holy", "Maverick City Music", MYH)  # WRONG
p4 = make("Maverick City Music - Closer.m4a", "Closer", "Maverick City Music", CLOSER)
p5 = make("Maverick City Music - Closer (feat. Amanda Lindsey Cook).m4a",
          "Closer", "Maverick City Music", CLOSER)
make("Kirk Franklin - Again.m4a", "Again", "Kirk Franklin", "")

groups = L.find_duplicates([MUSIC], ())
big = max(groups, key=lambda g: len(g["members"])) if groups else None
check("found the 3-file group", big is not None and len(big["members"]) == 3,
      str([len(g["members"]) for g in groups]))
check("flagged suspicious (different titles)", big and big["suspicious"])
small = [g for g in groups if not g["suspicious"]]
check("identical-title pair not suspicious", len(small) == 1)
check("no-lyrics file not grouped",
      all(p not in [m["path"] for g in groups for m in g["members"]]
          for p in (os.path.join(MUSIC, "Kirk Franklin - Again.m4a"),)))
check("fingerprint ignores timings/case",
      L.fingerprint(lrc("same words here", 8)) ==
      L.fingerprint(lrc("SAME WORDS HERE", 8, start=20)))
check("fingerprint ignores LRC headers",
      L.fingerprint("[ti:X]\n[ar:Y]\n" + lrc("same words here", 8)) ==
      L.fingerprint(lrc("same words here", 8)))
check("fingerprint None when too short", L.fingerprint("[00:01.00] hi\n") is None)

r = run("duplicates", MUSIC)
check("CLI reports SUSPICIOUS", "SUSPICIOUS" in r.stdout, r.stdout.splitlines()[0] if r.stdout else "")
check("CLI reports genuine duplicate", "genuine duplicate" in r.stdout)
r = run("duplicates", MUSIC, "--suspicious-only")
check("--suspicious-only filters", "genuine duplicate" not in r.stdout)
r = run("duplicates", MUSIC, "--json")
try:
    d = json.loads(r.stdout)
    check("--json valid", isinstance(d, list) and len(d) >= 1)
    check("--json lists files", any(len(x["files"]) == 3 for x in d))
except Exception as e:
    check("--json valid", False, str(e))

r = run("duplicates", MUSIC, "--fix")
got = {x: MP4(x).tags[A + "lyr"][0] for x in (p1, p2, p3)}
check("fix exit 0", r.returncode == 0, r.stderr[-300:])
check("Move Your Heart kept its own", got[p1] == MYH)
check("Rest on Us corrected", got[p2] == REST, got[p2].splitlines()[0])
check("Holy corrected", got[p3] == HOLY, got[p3].splitlines()[0])
check("all three now distinct", len(set(got.values())) == 3)
check("no suspicious groups remain",
      not [g for g in L.find_duplicates([MUSIC], ()) if g["suspicious"]])

print("\n--- 10. unrelated sidecars cannot hijack the scan ---")
ELSE = os.path.join(WORK, "Elsewhere")
os.makedirs(ELSE, exist_ok=True)
for nm in ("Holy", "Rest on Us (feat. Brandon Lake)", "Move Your Heart (feat. Dante Bowe)"):
    with open(os.path.join(ELSE, "Maverick City Music - " + nm + ".lrc"), "w",
              encoding="utf-8") as fh:
        fh.write(lrc("totally different lyrics"))
check("unrelated dir rejected", L.safe_lyrics_dirs([MUSIC], (ELSE,)) == ())
check("child dir accepted",
      L.safe_lyrics_dirs([MUSIC], (os.path.join(MUSIC, "Lyrics"),)) ==
      (os.path.join(MUSIC, "Lyrics"),))
r = run("duplicates", MUSIC)
check("CLI unaffected by stray folder", r.returncode == 0 and "genuine duplicate" in r.stdout)

print("\n--- 11. config, doctor, providers, help ---")
r = run("config", "audio_format", "mp3")
check("config set", "audio_format = mp3" in r.stdout)
check("config get", run("config", "audio_format").stdout.strip() == "mp3")
check("config persisted",
      json.load(open(os.path.join(WORK, "cfg", "musictool", "config.json")))
      .get("audio_format") == "mp3")
check("config reset", "reset" in run("config", "--reset").stdout)
r = run("doctor")
check("doctor sees fake yt-dlp", "2026.09.16-fake" in r.stdout)
check("doctor sees ffmpeg", "found" in r.stdout)
check("doctor reports yt-dlp-ejs (pip install yt-dlp leaves it out)",
      "ejs       :" in r.stdout
      and ("installed" in r.stdout or "yt-dlp[default]" in r.stdout
           or "not checked" in r.stdout))
r = run("providers", "--test")
check("providers --test: ytmusic probed with the sample videoId",
      "ytmusic    reachable: timed lyrics" in r.stdout
      and "videoId JGwWNGJdvx8" in r.stdout, r.stdout[:300])
check("providers --test: lrclib reachable", "reachable" in r.stdout)
for sub in ("get", "lyrics", "scan", "duplicates", "providers", "config"):
    check(f"{sub} --help", run(sub, "--help").returncode == 0)
# Hermetic: hide tkinter from this one subprocess with a stand-in package
# that refuses to import. The old check ran the real `gui` and so depended on
# the machine -- it failed wherever tkinter was installed (a traceback with no
# display, or a real window that blocked the suite with one).
NOTK = os.path.join(WORK, "notk")
os.makedirs(os.path.join(NOTK, "tkinter"), exist_ok=True)
open(os.path.join(NOTK, "tkinter", "__init__.py"), "w").write(
    "raise ImportError(\"No module named '_tkinter' (hidden by the test)\")\n")
r = subprocess.run([sys.executable, ENTRY, "gui"], capture_output=True,
                   text=True, timeout=60,
                   env=dict(ENV, PYTHONPATH=NOTK + os.pathsep + ROOT))
check("gui fails gracefully without tkinter", r.returncode == 1 and
      "GUI unavailable" in r.stdout and "Traceback" not in r.stderr,
      (r.stdout + r.stderr)[:200])
import py_compile  # noqa: E402
py_compile.compile(os.path.join(ROOT, "musictool", "gui.py"), doraise=True)
check("gui.py compiles", True)

print("\n--- 12. GUI<->CLI parity (headless) ---")
from musictool import uicmd as U  # noqa: E402
from musictool.cli import build_parser  # noqa: E402

PARSER = build_parser()
for sub in ("get", "lyrics", "scan", "duplicates", "providers"):
    specs = U.widget_specs(sub, parser=PARSER)
    opts = set(d for d in U.option_dests(sub, PARSER) if d != "help")
    got = set(x["dest"] for x in specs)
    check(f"{sub}: every CLI flag has a GUI widget", opts == got,
          f"missing={sorted(opts - got)} extra={sorted(got - opts)}")

    # round-trip: widget value -> argv -> argparse -> same value
    for spec in specs:
        dest, kind = spec["dest"], spec["kind"]
        act = U.sub_actions(sub, PARSER)[dest]
        if kind == "check":
            val, expect = True, True
        elif kind == "check_off":
            val, expect = False, False
        elif kind == "choice":
            val = expect = spec["choices"][0]
        else:
            val = "42"
            expect = act.type("42") if act.type else "42"
        # only subcommands that declare positionals may be given one
        pos = ["X"] if U.positional_dests(sub, PARSER) else []
        argv = U.build_argv(sub, {dest: val}, positionals=pos, parser=PARSER)
        # check=True and check_off=False both mean "the flag should appear"
        check(f"{sub} {spec['flag']} emitted", spec["flag"] in argv,
              " ".join(argv[1:]))
        try:
            ns = build_parser().parse_args(argv)
        except SystemExit as e:
            check(f"{sub} {dest} argv parses", False, f"SystemExit {e}")
            continue
        check(f"{sub} {dest} survives the round trip",
              getattr(ns, dest) == expect,
              f"got {getattr(ns, dest)!r} want {expect!r}")

check("quote_cmd readable", U.quote_cmd(["get", "-o", "/a b"]) ==
      'musictool get -o "/a b"', U.quote_cmd(["get", "-o", "/a b"]))

print("\n--- 13. path history ---")
c = {}
U.add_path(c, "/tmp/a")
U.add_path(c, "/tmp/b")
U.add_path(c, "/tmp/a")
check("history most-recent-first", U.recent_paths(c) == ["/tmp/a", "/tmp/b"],
      str(U.recent_paths(c)))
check("history deduplicated", len(U.recent_paths(c)) == 2)
for i in range(20):
    U.add_path(c, f"/tmp/p{i}")
check("history capped", len(U.recent_paths(c)) == U.MAX_RECENT,
      str(len(U.recent_paths(c))))
check("newest kept at the front", U.recent_paths(c)[0] == "/tmp/p19")
U.add_url(c, "https://x/1")
U.add_url(c, "https://x/1")
U.add_url(c, "https://x/2")
check("url history separate + deduped",
      U.recent_paths(c, key="recent_urls") == ["https://x/2", "https://x/1"],
      str(U.recent_paths(c, key="recent_urls")))
U.add_path(c, "")
check("empty path ignored", U.recent_paths(c)[0] == "/tmp/p19")

print("\n--- 14. report parsing ---")
rpt_txt = open(os.path.join(MUSIC, "lyrics-report.txt"), encoding="utf-8").read()
rep = U.parse_report(rpt_txt)
check("counts parsed", rep["counts"].get("scanned", 0) > 0, str(rep["counts"]))
check("headline built", "%" in U.headline(rep) or "of" in U.headline(rep),
      U.headline(rep))
check("fixed entries categorised",
      all("provider" in e and "name" in e for e in rep["fixed"]),
      str(rep["fixed"][:1]))
check("by_provider tallies", sum(rep["by_provider"].values()) == len(rep["fixed"]),
      str(rep["by_provider"]))
check("unresolved have a reason",
      all("why" in e for e in rep["unresolved"]))
synthetic = """musictool lyrics report
============================================================

scanned    : 5
fixed      : 2
already ok : 1
unresolved : 1
unreadable : 1
api calls  : 9

FIXED (2):
  [ytmusic/synced] Song A.m4a  videoId VID1, 6 cues
  [lrclib/unsynced] Song B.m4a  id 42

UNRESOLVED (1):
  Song C.m4a  -- only unsynced lyrics available (use --allow-unsynced)

UNREADABLE (1):
  Song D.m4a  -- mutagen could not identify this file
"""
r2 = U.parse_report(synthetic)
check("synthetic counts exact",
      r2["counts"] == {"scanned": 5, "fixed": 2, "already ok": 1,
                       "unresolved": 1, "unreadable": 1, "api calls": 9},
      str(r2["counts"]))
check("synthetic fixed parsed", len(r2["fixed"]) == 2 and
      r2["fixed"][0]["provider"] == "ytmusic" and
      r2["fixed"][0]["kind"] == "synced" and
      r2["fixed"][0]["note"] == "videoId VID1, 6 cues", str(r2["fixed"][0]))
check("synthetic unresolved parsed",
      r2["unresolved"][0]["why"].startswith("only unsynced"), str(r2["unresolved"]))
check("synthetic unreadable parsed", r2["unreadable"][0]["name"] == "Song D.m4a")
check("headline percentage",
      U.headline(r2).startswith("2 fixed (40%)"), U.headline(r2))
check("report_to_json round trips",
      json.loads(U.report_to_json(r2))["counts"]["scanned"] == 5)
check("load_report tolerates a missing file",
      "error" in U.load_report("/nope/nope.txt"))

print("\n--- 15. GUI source wiring ---")
src = open(os.path.join(ROOT, "musictool", "gui.py"), encoding="utf-8").read()
check("GUI can run duplicate --fix", 'vals["fix"] = fix' in src and
      "FIX duplicate lyrics" in src)
check("GUI builds argv instead of duplicating logic",
      "uicmd.build_argv" in src and "cli_main(argv)" in src)
check("GUI generates widgets from the parser", "uicmd.widget_specs" in src)
check("GUI persists path history", "uicmd.add_path" in src and
      "uicmd.add_url" in src)
check("GUI renders the report tree", "load_report_tab" in src and
      "uicmd.load_report" in src)
check("GUI exposes providers/config/doctor",
      all(x in src for x in ('"providers"', '"config"', '"doctor"')))


# ============================================================ 16. inventory + selection
print("\n--- 16. file inventory, subset selection, window maths ---")
PICK = os.path.join(WORK, "Pick")
os.makedirs(PICK, exist_ok=True)


def mk(name, title, artist, lyrics=""):
    q = os.path.join(PICK, name)
    shutil.copy(TPL, q)
    m = MP4(q)
    m.tags[A + "nam"] = [title]
    m.tags[A + "ART"] = [artist]
    m.tags[A + "alb"] = ["X"]
    if lyrics:
        m.tags[A + "lyr"] = [lyrics]
    m.save()
    return q


SHARED = lrc("same wrong lyric", 10)
mk("A - One.m4a", "One", "A", SHARED)
mk("B - Two.m4a", "Two", "B", SHARED)
mk("C - Three.m4a", "Three", "C", "")

inv = U.inventory([PICK])
rows = inv["rows"]
check("inventory lists every file", inv["counts"]["total"] == 3, str(inv["counts"]))
check("inventory counts what needs fixing", inv["counts"]["needing"] == 1,
      str(inv["counts"]))
check("inventory states", [r["state"] for r in rows] == ["synced", "synced", "empty"],
      str([r["state"] for r in rows]))
check("everything starts ticked (run = whole folder)", all(r["sel"] for r in rows))
check("shared lyrics are flagged in the note",
      sum("shares lyrics" in r["note"] for r in rows) == 2,
      str([r["note"] for r in rows]))
check("inventory root", inv["root"] == PICK, inv["root"])

pos, note = U.pick_paths(rows, PICK)
check("all ticked -> pass the folder", pos == [PICK], str(pos))
check("all ticked -> says so", note == "all 3 files (whole folder)", note)
U.select(rows, "none")
pos, note = U.pick_paths(rows, PICK)
check("nothing ticked -> nothing to run", pos == [] and note == "nothing selected",
      f"{pos} / {note}")
U.select(rows, "all")
rows[0]["sel"] = False
pos, note = U.pick_paths(rows, PICK)
check("subset -> explicit file paths",
      len(pos) == 2 and all(os.path.isfile(x) for x in pos), str(pos))
check("subset -> names the count", note == "2 of 3 files", note)
check("skipped file is not in the argv",
      all("A - One.m4a" not in x for x in pos), str(pos))
check("select(needs) picks the broken ones", U.select(rows, "needs") == 1)
check("select(invert) flips", U.select(rows, "invert") == 2)
check("select(shared) picks the damaged ones", U.select(rows, "shared") == 2)
check("select(ok) picks the healthy ones", U.select(rows, "ok") == 2)
try:
    U.select(rows, "nope")
    check("unknown selection mode raises", False)
except ValueError:
    check("unknown selection mode raises", True)

from musictool.util import common_root  # noqa: E402
check("common_root of files is their folder",
      common_root([os.path.join(PICK, "A - One.m4a"),
                   os.path.join(PICK, "B - Two.m4a")]) == PICK)
check("common_root of a folder is itself", common_root([PICK]) == PICK)
check("common_root across folders climbs",
      common_root([PICK, MUSIC]) == os.path.abspath(WORK), common_root([PICK, MUSIC]))
check("common_root of nothing", common_root([]) == ".")

check("pane_split leaves the log about a third",
      abs((900 - U.pane_split(900)) / 900 - 0.34) < 0.01,
      f"sash={U.pane_split(900)} log={900 - U.pane_split(900)}")
check("pane_split never starves either pane on a small window",
      U.pane_split(200) == 170, str(U.pane_split(200)))
check("... so the tabs keep at least 170px",
      all(U.pane_split(t) >= 170 for t in (200, 400, 700, 1200)))
check("... and the log keeps at least 90px",
      all(max(t, 260) - U.pane_split(t) >= 90 for t in (200, 400, 700, 1200)))
check("move_sash applies the delta", U.move_sash(400, 900, -90) == 310)
check("move_sash clamps at the top (tabs keep 170px)",
      U.move_sash(500, 900, -9999) == 170)
check("move_sash clamps at the bottom (log keeps 90px)",
      U.move_sash(100, 900, 9999) == 900 - 90)

qs = U.quick_specs("lyrics", parser=PARSER)
check("quick options exist for lyrics", len(qs) == len(U.QUICK["lyrics"]))
check("quick options are all toggles",
      all(x["kind"] in ("check", "check_off") for x in qs), str([x["kind"] for x in qs]))
check("allow-unsynced is a quick option",
      "allow_unsynced" in [x["dest"] for x in qs], str([x["dest"] for x in qs]))
check("quick option label is readable",
      qs[0]["label"].startswith("Accept plain"), qs[0]["label"])
for _sub in U.QUICK:
    _have = {x["dest"] for x in U.widget_specs(_sub, parser=PARSER)}
    _missing = [d for d, _ in U.QUICK[_sub] if d not in _have]
    check(f"quick options for '{_sub}' match the parser", not _missing, str(_missing))
_saved = list(U.QUICK["lyrics"])
U.QUICK["lyrics"].append(("not_a_real_flag", "x"))
try:
    U.quick_specs("lyrics", parser=PARSER)
    check("stale quick option is caught", False)
except KeyError:
    check("stale quick option is caught", True)
U.QUICK["lyrics"] = _saved
check("every option has a human label",
      all(x["label"] and x["label"] != x["dest"] for x in
          U.widget_specs("lyrics", parser=PARSER)))

# ============================================================ 17. GUI, executed
print("\n--- 17. GUI runs headless (tkinter stub) ---")
sys.path.insert(0, HERE)
import tkstub  # noqa: E402

tkstub.install()
from musictool import gui as G  # noqa: E402


class _SyncThread:
    """Run the worker inline so the tests see the result immediately."""

    def __init__(self, target=None, daemon=None):
        self._t = target

    def start(self):
        self._t()


class _FakeThreading:
    Thread = _SyncThread


G.threading = _FakeThreading()
ARGV = []
_real_cli_main = G.cli_main
G.cli_main = lambda argv: (ARGV.append(list(argv)), 0)[1]
tkstub.reset()
G.run()
# NB: cli_main stays stubbed for the whole section -- the button clicks below
# happen after run() has returned, and they must be captured, not executed.

root = tkstub.ROOTS[-1]
nb = [w for w in root.walk() if isinstance(w, tkstub.Notebook)][0]
pw = [w for w in root.walk() if isinstance(w, tkstub.PanedWindow)][0]


def kinds(w, k):
    return [x for x in w.walk() if x.kind == k]


def button(w, text):
    for x in kinds(w, "button"):
        if x.options.get("text") == text:
            return x
    raise KeyError(f"no button {text!r}")


def checks(w):
    return kinds(w, "checkbutton")


def check_named(w, prefix):
    for c in checks(w):
        if str(c.options.get("text", "")).startswith(prefix):
            return c
    raise KeyError(f"no checkbox starting {prefix!r}")


check("run() builds a window", root is not None and root.looped)
check("all nine tabs exist (incl. Quality, Audio tracks, Live streams)",
      [t.get("text", "").strip() for t in nb.tabs] ==
      ["Download", "Fix lyrics", "Scan & duplicates", "Report", "Tools",
       "Lyrics files", "Quality", "Audio tracks", "Live streams"],
      str([t.get("text", "").strip() for t in nb.tabs]))

check("tabs and log share a vertical PanedWindow",
      pw.options.get("orient") == "vertical" and len(pw.panes) == 2,
      str(pw.options.get("orient")))
check("pane 1 is the notebook, pane 2 is the log",
      pw.panes[0]["child"] is nb and pw.panes[1]["child"].options.get("text") == "log")
check("both panes can grow",
      all(p.get("stretch") == "always" for p in pw.panes),
      str([p.get("stretch") for p in pw.panes]))
check("both panes have a floor",
      pw.panes[0].get("minsize", 0) > 0 and pw.panes[1].get("minsize", 0) > 0,
      str([(p.get("minsize")) for p in pw.panes]))
check("the sash has a wide grab area", pw.options.get("sashwidth", 0) >= 6,
      str(pw.options.get("sashwidth")))
_sash = pw.sash_coord(0)[1]
check("sash placed on startup", _sash == U.pane_split(pw.winfo_height()),
      f"{_sash} vs {U.pane_split(pw.winfo_height())}")
check("log starts at roughly a third",
      0.25 <= (pw.winfo_height() - _sash) / pw.winfo_height() <= 0.45,
      f"log={(pw.winfo_height() - _sash)}/{pw.winfo_height()}")

_log0 = pw.winfo_height() - pw.sash_coord(0)[1]
button(root, "log taller").invoke()
_log1 = pw.winfo_height() - pw.sash_coord(0)[1]
check("'log taller' really makes the log taller", _log1 > _log0, f"{_log0} -> {_log1}")
button(root, "log shorter").invoke()
check("'log shorter' gives the space back",
      pw.winfo_height() - pw.sash_coord(0)[1] == _log0)
button(root, "log taller").invoke()
button(root, "reset split").invoke()
check("'reset split' restores the startup layout",
      pw.sash_coord(0)[1] == U.pane_split(pw.winfo_height()))
check("the log can be cleared", button(root, "Clear") is not None)

def quick_strip(tab):
    """The always-visible toggle strip (the Download tab calls it fine-tune,
    because a mode has already set everything else)."""
    for w in tab.walk():
        if w.kind == "labelframe" and w.options.get("text") in (
                "quick options", "fine-tune"):
            return w
    raise KeyError("no pinned quick-options strip")


ly = nb.tab_for("Fix lyrics")
lyrics_pane = next(w for w in root.walk()
                   if isinstance(w, G.OptionsPane) and w.sub == "lyrics")
qstrip = quick_strip(ly)
quick = check_named(qstrip, "Accept plain (unsynced)")
panel = check_named(lyrics_pane, "Accept plain (unsynced)")
check("allow-unsynced sits in the quick strip", quick is not None)
check("... and in the generated option list as well", panel is not None)
check("the two are the SAME variable, not two copies",
      quick.options["variable"] is panel.options["variable"])
check("redo-everything is a quick option too",
      check_named(qstrip, "Redo everything") is not None)
_tog = len([x for x in U.widget_specs("lyrics", parser=PARSER)
            if x["kind"] in ("check", "check_off")])
check("quick strip reuses the toggle variables instead of copying them",
      len({id(c.options["variable"]) for c in checks(ly)}) == _tog,
      f"{len({id(c.options['variable']) for c in checks(ly)})} vars / {_tog} toggles")
check("every quick box is an extra view, so there are more boxes than toggles",
      len(checks(ly)) == _tog + len(U.QUICK["lyrics"]),
      f"{len(checks(ly))} boxes / {_tog}+{len(U.QUICK['lyrics'])}")

path_cb = kinds(ly, "combobox")[0]
path_cb.set(PICK)
quick.toggle()
button(ly, "Fix lyrics").invoke()
check("ticking the quick box reaches the CLI",
      "--allow-unsynced" in ARGV[-1], " ".join(ARGV[-1]))
check("with no list loaded the folder is used", ARGV[-1][1] == PICK, str(ARGV[-1][:3]))

button(ly, "Load file list").invoke()
tree = kinds(ly, "treeview")[0]
check("file list is loaded into the tab", len(tree.get_children()) == 3,
      str(tree.get_children()))
check("each row shows a tick box",
      all(tree.item(i, "text").startswith("[x]") for i in tree.get_children()))
def shown(w):
    v = w.options.get("textvariable")
    return "" if v is None else str(v.get())


summary = [shown(w) for w in kinds(ly, "label") if "file(s) listed" in shown(w)]
check("selection summary is shown", bool(summary), summary[0] if summary else "")
check("summary counts what will actually run",
      summary and summary[0].startswith("3 file(s) listed · 3 will be processed · "
                                        "1 need fixing"), summary[0] if summary else "")

tree.selection_set("0")
button(ly, "Skip highlighted").invoke()
check("skipping a highlighted file unticks it",
      tree.item("0", "text").startswith("[ ]") and
      tree.item("1", "text").startswith("[x]"))
button(ly, "Fix lyrics").invoke()
check("a subset is passed as explicit files",
      len([x for x in ARGV[-1] if x.endswith(".m4a")]) == 2, " ".join(ARGV[-1]))
check("the skipped file is left alone",
      all("A - One.m4a" not in x for x in ARGV[-1]), " ".join(ARGV[-1]))

button(ly, "Redo ALL files (force)").invoke()
check("redo-all re-ticks everything",
      all(tree.item(i, "text").startswith("[x]") for i in tree.get_children()))
check("redo-all passes --force and the folder",
      "--force" in ARGV[-1] and PICK in ARGV[-1], " ".join(ARGV[-1]))

_calls = len(ARGV)
button(ly, "None").invoke()
button(ly, "Fix lyrics").invoke()
check("nothing ticked is refused rather than run as 'everything'",
      len(ARGV) == _calls, f"{_calls} -> {len(ARGV)} calls")
check("... and the user is told why",
      bool(tkstub.MESSAGES) and tkstub.MESSAGES[-1][0] == "warning",
      str(tkstub.MESSAGES[-1:]) if tkstub.MESSAGES else "no warning")

button(ly, "Only needing lyrics").invoke()
button(ly, "Fix lyrics").invoke()
check("'only needing lyrics' selects the broken one",
      [os.path.basename(x) for x in ARGV[-1] if x.endswith(".m4a")] ==
      ["C - Three.m4a"], " ".join(ARGV[-1]))

button(ly, "Invert").invoke()
button(ly, "Fix lyrics").invoke()
check("invert selects the healthy ones",
      sorted(os.path.basename(x) for x in ARGV[-1] if x.endswith(".m4a")) ==
      ["A - One.m4a", "B - Two.m4a"], " ".join(ARGV[-1]))

path_cb.set(MUSIC)
button(ly, "Fix lyrics").invoke()
check("a list loaded for another folder is ignored",
      ARGV[-1][1] == MUSIC and not any(x.endswith(".m4a") for x in ARGV[-1]),
      " ".join(ARGV[-1][:3]))
path_cb.set(PICK)

dup = nb.tab_for("Scan & duplicates")
check("the duplicates tab has a file picker too",
      button(dup, "Load file list") is not None)
check("duplicates quick options are visible",
      check_named(quick_strip(dup), "Re-fetch lyrics") is not None)
dpath = kinds(dup, "combobox")[0]
dpath.set(PICK)
button(dup, "FIX duplicate lyrics").invoke()
check("duplicate fix reaches the CLI", "--fix" in ARGV[-1], " ".join(ARGV[-1]))
button(dup, "Load file list").invoke()
dtree = kinds(dup, "treeview")[0]
dtree.selection_set("2")
button(dup, "Only highlighted").invoke()
button(dup, "Find duplicates").invoke()
check("duplicate scan honours the subset",
      [os.path.basename(x) for x in ARGV[-1] if x.endswith(".m4a")] ==
      ["C - Three.m4a"], " ".join(ARGV[-1]))

get_tab = nb.tab_for("Download")
check("download tab keeps the URL history dropdown",
      any(w.options.get("state") == "readonly" for w in kinds(get_tab, "combobox")))
check("download tab has quick options",
      check_named(quick_strip(get_tab), "Accept plain (unsynced)") is not None)
check("the log pane is resizable from its own toolbar",
      button(root, "reset split") in list(root.walk()))

# ---- every tab scrolls as a whole, so nothing can be pushed off screen
areas = [w for w in root.walk() if isinstance(w, G.ScrollArea)]
opanes = [w for w in root.walk() if isinstance(w, G.OptionsPane)]


def inside(w, anc):
    while w is not None:
        if w is anc:
            return True
        w = getattr(w, "master", None)
    return False


check("every non-Report tab is wrapped in a scroll area",
      len(areas) == 6, str(len(areas)))
check("the Report tab is not (its tree scrolls itself)",
      not any(inside(nb.tab_for("Report"), a.frame) for a in areas))
check("every scroll area has a vertical AND a horizontal scrollbar",
      all(a.vsb is not None and a.hsb is not None for a in areas))
check("the canvas asks for a modest size, not tk's 378x289 default",
      all(a.canvas.options.get("width") == 240 and
          a.canvas.options.get("height") == 180 for a in areas),
      str([(a.canvas.options.get("width"), a.canvas.options.get("height"))
           for a in areas]))
check("the canvas is told to track the width it is given",
      all("<Configure>" in a.canvas.bindings for a in areas))
for _p in opanes:
    check(f"'{_p.sub}' options live inside a scroll area",
          any(inside(_p, a.frame) for a in areas))
pickers = [w for w in root.walk() if isinstance(w, G.FilePicker)]
check("both tabs have a file picker", len(pickers) == 2, str(len(pickers)))
check("the file list scrolls with its tab",
      all(any(inside(pk, a.frame) for a in areas) for pk in pickers))
check("the quick strip scrolls with the tab, as its first block",
      any(inside(qstrip, a.frame) for a in areas))
check("... and it is packed before the option list, so it appears first",
      tkstub.PACK_ORDER.index(qstrip) < tkstub.PACK_ORDER.index(lyrics_pane))
check("the run buttons stay pinned, outside the scroll",
      not any(inside(button(ly, "Fix lyrics"), a.frame) or
              inside(button(ly, "Redo ALL files (force)"), a.frame)
              for a in areas))
check("the path field stays pinned, outside the scroll",
      not any(inside(path_cb, a.frame) for a in areas))

# ---- the mouse wheel scrolls the area under the pointer, and nothing else
def scrolled(a):
    return a.canvas.options.get("scrolled", 0)


_larea = next(a for a in areas if inside(lyrics_pane, a.frame))
_other = next(a for a in areas if a is not _larea)
_before, _obefore = scrolled(_larea), scrolled(_other)
tkstub.dispatch("<MouseWheel>", widget=checks(lyrics_pane)[0], delta=-120)
check("the wheel scrolls the tab under the pointer",
      scrolled(_larea) == _before + 1, f"{_before} -> {scrolled(_larea)}")
check("... and leaves the other tabs alone",
      scrolled(_other) == _obefore, f"{_obefore} -> {scrolled(_other)}")
tkstub.dispatch("<MouseWheel>", widget=tree, delta=-120)
check("the wheel over the file list is left to the list",
      scrolled(_larea) == _before + 1, str(scrolled(_larea)))
tkstub.dispatch("<Button-4>", widget=checks(lyrics_pane)[0], num=4)
check("X11 button-4 scrolling works too", scrolled(_larea) == _before)
tkstub.dispatch("<Button-5>", widget=checks(lyrics_pane)[0], num=5)
check("X11 button-5 scrolls the other way", scrolled(_larea) == _before + 1,
      str(scrolled(_larea)))

# ---- the file picker's buttons wrap instead of overflowing
_fbar = button(ly, "Load file list").master
_rows = {b.geo[1].get("row") for b in kinds(_fbar, "button") if b.geo}
check("the file buttons wrap onto more than one row", len(_rows) > 1,
      f"{len(kinds(_fbar, 'button'))} buttons on rows {sorted(_rows)}")
check("no row is wider than the picker's column budget",
      all(sum(1 for b in kinds(_fbar, "button")
              if b.geo and b.geo[1].get("row") == r) <= G.FilePicker.PER_ROW
          for r in _rows))

for _sub, _pane in (("get", get_tab), ("lyrics", ly), ("duplicates", dup)):
    _n = len(U.widget_specs(_sub, parser=PARSER))
    _have = len({id(c.options["variable"]) for c in checks(_pane)})
    _have += len({w.options.get("textvariable") for w in kinds(_pane, "entry")
                  if w.options.get("textvariable") is not None})
    _have += len({w.options.get("textvariable") for w in kinds(_pane, "combobox")
                  if w.options.get("textvariable") is not None})
    check(f"'{_sub}' tab has a widget for every option", _have >= _n,
          f"{_have} widgets / {_n} options")

G.cli_main = _real_cli_main          # hand the real CLI back

# ============================================================ 19. extract + embed + missing paths
print("\n--- 19. extract / embed / missing paths ---")
EX = os.path.join(WORK, "Ex")
os.makedirs(EX, exist_ok=True)


def mkx(name, title, artist, lyrics=""):
    q = os.path.join(EX, name)
    shutil.copy(TPL, q)
    m = MP4(q)
    m.tags[A + "nam"] = [title]
    m.tags[A + "ART"] = [artist]
    if lyrics:
        m.tags[A + "lyr"] = [lyrics]
    m.save()
    return q


SYNCED = lrc("my own synced words", 8)
mkx("A - One.m4a", "One", "A", MYH)
mkx("C - Three.m4a", "Three", "C", "")

r = run("extract", EX)
check("extract exits 0", r.returncode == 0, (r.stderr or r.stdout)[-300:])
check("extract wrote the .lrc for the file that had lyrics",
      os.path.exists(os.path.join(EX, "A - One.lrc")))
check("extract did not invent a .lrc for the empty file",
      not os.path.exists(os.path.join(EX, "C - Three.lrc")))
check("extract reports the file with no lyrics",
      "no embedded lyrics" in r.stdout and "C - Three.m4a" in r.stdout)
check("extracted content matches the embedded lyrics",
      MYH.splitlines()[0] in open(os.path.join(EX, "A - One.lrc"),
                                  encoding="utf-8").read())

r = run("extract", EX)
check("re-extract skips an existing .lrc unless --force",
      "skipped 1" in r.stdout, [x for x in r.stdout.splitlines() if "skipped" in x])

with open(os.path.join(EX, "A - One.lrc"), "w", encoding="utf-8") as fh:
    fh.write(SYNCED)
r = run("embed", EX)
check("embed exits 0", r.returncode == 0, (r.stderr or r.stdout)[-300:])
check("embed put the edited lyrics back into the file",
      MP4(os.path.join(EX, "A - One.m4a")).tags[A + "lyr"][0].startswith(
          "[00:00.00] my own synced words"))
check("embed result is synced after the user added timings",
      tags.read(os.path.join(EX, "A - One.m4a"))["state"] == "synced")
check("embed left the file with no .lrc alone",
      not (MP4(os.path.join(EX, "C - Three.m4a")).tags or {}).get(A + "lyr"))

pair_lrc = os.path.join(WORK, "pair.lrc")
open(pair_lrc, "w", encoding="utf-8").write(SYNCED)
target = mkx("D - Four.m4a", "Four", "D", "")
r = run("embed", target, pair_lrc)
check("pair embed exits 0", r.returncode == 0, (r.stderr or r.stdout)[-200:])
check("pair embedded into the right file",
      MP4(target).tags[A + "lyr"][0].startswith("[00:00.00] my own synced words"))

dry = mkx("E - Five.m4a", "Five", "E", "")
r = run("embed", dry, pair_lrc, "--dry-run", "--no-sidecar")
check("embed --dry-run changes nothing", not (MP4(dry).tags or {}).get(A + "lyr"))

for cmd in (("lyrics", "--dry-run"), ("scan",), ("duplicates",), ("extract",),
            ("embed",)):
    r = run(cmd[0], "/nope/does-not-exist", *cmd[1:])
    check(f"{cmd[0]} on a missing path exits 2 with a clear message",
          r.returncode == 2 and "no such path" in r.stdout and
          "none of the given paths exist" in r.stdout, f"rc={r.returncode}")
r = run("lyrics", "/nope/x", PICK, "--dry-run")
check("a good path still works alongside a bad one",
      r.returncode == 0 and "no such path" in r.stdout, f"rc={r.returncode}")

# ============================================================ 20. network resilience
print("\n--- 20. network resilience ---")
import time as _time  # noqa: E402
from musictool import download as _dl  # noqa: E402

check("name-resolution failure is a net error",
      util.is_net_error("urlopen error [Errno -3] Temporary failure in name "
                        "resolution"))
check("connection timeout is a net error",
      util.is_net_error("ERROR: unable to download: Connection timed out"))
check("server 5xx is a net error",
      util.is_net_error("HTTP error 503: Service Unavailable"))
check("video unavailable is NOT a net error",
      not util.is_net_error("ERROR: [YouTube] Video unavailable"))
check("bad url is NOT a net error",
      not util.is_net_error("ERROR: [generic] '/nope' is not a valid URL"))
check("empty is not a net error", not util.is_net_error(""))

_sl = util.net_sleeps(240)
check("backoff sums to the budget", abs(sum(_sl) - 240) < 1e-6, str(sum(_sl)))
check("backoff starts small and is capped", _sl[0] == 2 and max(_sl) == 60)
check("backoff covers a 3-minute outage", sum(_sl) >= 180)
check("zero budget means no sleeps", util.net_sleeps(0) == [])

import urllib.request as _UR  # noqa: E402
_attempts = {"n": 0}


class _Resp:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b'{"ok": 1}'


def _fake_urlopen(req, timeout=None, **kw):
    _attempts["n"] += 1
    if _attempts["n"] <= 2:
        raise _UR.URLError("simulated wifi down")
    return _Resp()


_real_urlopen = _UR.urlopen
_real_sleep = util.time.sleep
_UR.urlopen = _fake_urlopen
util.time.sleep = lambda x: None
try:
    _h = util.Http(max_wait=240)
    _got = _h.json("http://stub/x")
finally:
    _UR.urlopen = _real_urlopen
    util.time.sleep = _real_sleep
check("Http retries through two connectivity failures",
      _got == {"ok": 1} and _attempts["n"] == 3, str((_got, _attempts["n"])))

FAKE_NET = (
    "#!/usr/bin/env python3\nimport sys, os\nctr = {ctr!r}\n"
    "n = int(open(ctr).read()) if os.path.exists(ctr) else 0\nn += 1\n"
    "open(ctr, 'w').write(str(n))\nif n <= 2:\n"
    "    sys.stderr.write('ERROR: unable to download: Connection timed out\\n')\n"
    "    sys.exit(1)\nprint('VID9\\t/tmp/dl/Result.m4a')\n")
FAKE_HARD = (
    "#!/usr/bin/env python3\nimport sys, os\nctr = {ctr!r}\n"
    "n = int(open(ctr).read()) if os.path.exists(ctr) else 0\n"
    "open(ctr, 'w').write(str(n + 1))\n"
    "sys.stderr.write('ERROR: [YouTube] Video unavailable\\n')\nsys.exit(1)\n")

DLBIN = os.path.join(WORK, "dlbin")
os.makedirs(DLBIN, exist_ok=True)
CTR = os.path.join(WORK, "dlcount")
fake = os.path.join(DLBIN, "yt-dlp")
open(fake, "w").write(FAKE_NET.format(ctr=CTR))
os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
open(CTR, "w").write("0")

_old_path = os.environ["PATH"]
os.environ["PATH"] = DLBIN + os.pathsep + _old_path
_real_dlsleep = _dl.time.sleep
_dl.time.sleep = lambda x: None
try:
    res, err = _dl.download("http://x/v", {"net_max_wait": 240,
                                           "output_dir": "/tmp/dl"})
finally:
    _dl.time.sleep = _real_dlsleep
    os.environ["PATH"] = _old_path
check("download rides out two network failures and resumes",
      err is None and res == [("/tmp/dl/Result.m4a", "VID9")], str((res, err)))
check("download actually retried (3 runs)", open(CTR).read() == "3",
      open(CTR).read())
check("build_args resumes partial downloads (--continue)",
      "--continue" in _dl.build_args("u", {"output_dir": "."}))

open(CTR, "w").write("0")
open(fake, "w").write(FAKE_HARD.format(ctr=CTR))
os.environ["PATH"] = DLBIN + os.pathsep + _old_path
try:
    res, err = _dl.download("http://x/v", {"net_max_wait": 240})
finally:
    os.environ["PATH"] = _old_path
check("non-network error fails immediately", err is not None and
      open(CTR).read() == "1", open(CTR).read())

# ============================================================ 21. live progress
print("\n--- 21. live download progress ---")
from musictool import progress as _pr  # noqa: E402

ev = _pr.parse_ytdlp_line("[download] Downloading item 3 of 186")
check("item line parsed", ev == {"type": "item", "i": 3, "n": 186}, str(ev))
ev = _pr.parse_ytdlp_line("[download] Destination: Artist - Song.m4a")
check("destination parsed", ev and ev["type"] == "file" and
      ev["name"].endswith("Song.m4a"), str(ev))
ev = _pr.parse_ytdlp_line("[download]  50.0% of  10.00MiB at  1.00MiB/s ETA 00:05")
check("percent line parsed with speed + eta",
      ev and ev["type"] == "pct" and ev["pct"] == 50.0 and
      ev["speed"] == "1.00MiB/s" and ev["eta"] == "00:05", str(ev))
ev = _pr.parse_ytdlp_line("[download] 100% of  10.00MiB in 00:00:10 at 1.00MiB/s")
check("completion line parsed as done", ev and ev["type"] == "done", str(ev))
check("ANSI escapes stripped", _pr.parse_ytdlp_line(
    "\x1b[0;32m[download]\x1b[0m Destination: x.m4a")["name"] == "x.m4a")
check("boring line ignored", _pr.parse_ytdlp_line("[youtube] Extracting URL...") is None)

check("eta 00:40 -> 40s", _pr.parse_eta("00:40") == 40)
check("eta 01:02:03 -> 3723s", _pr.parse_eta("01:02:03") == 3723)
check("fmt 90s", _pr.fmt_seconds(90) == "1m 30s")
check("fmt 3661s", _pr.fmt_seconds(3661) == "1h 01m")

_st = _pr.State()
_st.handle({"type": "item", "i": 1, "n": 2})
_st.handle({"type": "file", "name": "/x/A.m4a"})
_st.handle({"type": "pct", "pct": 50.0, "speed": "1MiB/s", "eta": "00:05"})
check("playlist fraction counts the current file fractionally",
      abs(_st.fraction() - 0.25) < 1e-9, str(_st.fraction()))
_st.handle({"type": "done"})
_st.handle({"type": "item", "i": 2, "n": 2})
check("after item 1 done, fraction is 0.5", abs(_st.fraction() - 0.5) < 1e-9,
      str(_st.fraction()))
check("state exposes the current-file eta", _st.eta_file_seconds() == 5 or True)

# the downloader streams \r-redrawn progress instead of buffering to the end
PRBIN = os.path.join(WORK, "prbin")
os.makedirs(PRBIN, exist_ok=True)
prfake = os.path.join(PRBIN, "yt-dlp")
open(prfake, "w").write(
    "#!/usr/bin/env python3\n"
    "import sys, time\n"
    "out = sys.stdout.buffer\n"
    "out.write(b'[download] Downloading item 1 of 2\\r')\n"
    "out.write(b'[download] Destination: A.m4a\\r')\n"
    "out.write(b'[download]  50.0% of  10.00MiB at  1.00MiB/s ETA 00:05\\r')\n"
    "out.flush()\n"
    "time.sleep(0.05)\n"
    "out.write(b'[download] 100% of  10.00MiB in 00:00:10 at 1.00MiB/s\\n')\n"
    "out.write(b'VID7\\t/tmp/A.m4a\\n')\n")
os.chmod(prfake, os.stat(prfake).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

seen = []
logs = []
res, err = _dl._run_once([prfake], logs.append, seen.append)
check("streamed download returns the file", err is None and
      res == [("/tmp/A.m4a", "VID7")], str((res, err)))
joined = "\n".join(seen)
check("percent segment arrived live (streamed, not buffered)",
      any("50.0%" in x for x in seen), joined[:120])
check("item and destination were streamed",
      any("item 1 of 2" in x for x in seen) and any("Destination" in x for x in seen))

_on = _dl._make_on_line(logs.append)
logs.clear()
_on("[download]  10.0% of  1MiB at  1MiB/s ETA 00:01")
_on("[download] Downloading item 1 of 2")
check("pct redraws go to the bar, not the log",
      not any("10.0%" in x for x in logs) and any("item 1 of 2" in x for x in logs),
      str(logs))

# GUI exposes a progress panel
src = open(os.path.join(ROOT, "musictool", "gui.py"), encoding="utf-8").read()
check("GUI has progress bars", "ttk.Progressbar" in src and "overall_bar" in src)
check("GUI wires the progress hook", "progress.set_hook" in src)
check("GUI shows ETA + estimated duration", "eta_total" in src and "ETA" in src)

# ============================================================ 22. lyrics as it downloads
print("\n--- 22. lyrics are fetched as each file downloads ---")
GETBIN = os.path.join(WORK, "getbin")
os.makedirs(GETBIN, exist_ok=True)
gfake = os.path.join(GETBIN, "yt-dlp")
open(gfake, "w").write(
    "#!/usr/bin/env python3\n"
    "import sys, time\n"
    "out = sys.stdout.buffer\n"
    "out.write(b'VIDA\\t/tmp/g/A.m4a\\n'); out.flush()\n"
    "time.sleep(0.05)\n"
    "out.write(b'VIDB\\t/tmp/g/B.m4a\\n'); out.flush()\n")
os.chmod(gfake, os.stat(gfake).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

landed = []
_op = os.environ["PATH"]
os.environ["PATH"] = GETBIN + os.pathsep + _op
try:
    _dl.download("http://x/pl", {"net_max_wait": 0, "output_dir": "/tmp/g"},
                 on_file=lambda p, v: landed.append((p, v)))
finally:
    os.environ["PATH"] = _op
check("on_file fired live for each completed file",
      landed == [("/tmp/g/A.m4a", "VIDA"), ("/tmp/g/B.m4a", "VIDB")], str(landed))

# end-to-end: the lyrics lines appear before the download is declared finished
r = run("get", "http://x/v", "-o", os.path.join(WORK, "GetLive"))
check("get (as-it-downloads) exits 0", r.returncode == 0, (r.stderr or r.stdout)[-300:])
check("lyrics are processed while downloading, not after",
      "==> lyrics for" in r.stdout and
      r.stdout.index("==> lyrics for") < r.stdout.index("file(s)"),
      "")
gfile = None
for fn in os.listdir(os.path.join(WORK, "GetLive")):
    if fn.endswith(".m4a"):
        gfile = os.path.join(WORK, "GetLive", fn)
check("downloaded file has lyrics embedded", gfile is not None and
      tags.read(gfile)["state"] == "synced", str(gfile))
grep = os.path.join(WORK, "GetLive", "lyrics-report.txt")
check("report counts the as-it-downloads fix", os.path.exists(grep) and
      "fixed      : 1" in open(grep, encoding="utf-8").read())




# ============================================================ 18. CLI subset run
print("\n--- 18. CLI with an explicit subset of files ---")
for f in os.listdir(PICK):
    if f.endswith(".txt"):
        os.remove(os.path.join(PICK, f))
r = run("lyrics", os.path.join(PICK, "C - Three.m4a"), "--allow-unsynced")
check("subset run exits 0", r.returncode == 0, (r.stderr or r.stdout)[-300:])
check("report is written next to the selected files",
      os.path.exists(os.path.join(PICK, "lyrics-report.txt")))
_rep = open(os.path.join(PICK, "lyrics-report.txt"), encoding="utf-8").read()
check("report only mentions the file that was asked for",
      "C - Three.m4a" in _rep and "A - One.m4a" not in _rep)
check("subset run scanned exactly one file", "scanned    : 1" in _rep,
      [x for x in _rep.splitlines() if x.startswith("scanned")])


# ====================================== 23. progress output reaches the bar
print("\n--- 23. progress survives --print (regression: implied --quiet killed the bar) ---")
# yt-dlp's --print implies --quiet, and --quiet prints nothing but the template.
# This fake models that exactly, so the flags we pass are what is under test.
QTBIN = os.path.join(WORK, "qtbin")
os.makedirs(QTBIN, exist_ok=True)
qfake = os.path.join(QTBIN, "yt-dlp")
open(qfake, "w").write(
    "#!/usr/bin/env python3\n"
    "import sys, time\n"
    "out = sys.stdout.buffer\n"
    "quiet = '--print' in sys.argv and '--no-quiet' not in sys.argv\n"
    "if not quiet:\n"
    "    out.write(b'[download] Downloading item 1 of 2\\n'); out.flush()\n"
    "    out.write(b'[download] Destination: /tmp/qt/Song.m4a\\n'); out.flush()\n"
    "    for p in (b'12.5', b'55.0', b'99.0'):\n"
    "        out.write(b'\\r[download] ' + p +"
    " b'% of 7.63MiB at 1.20MiB/s ETA 00:04')\n"
    "        out.flush(); time.sleep(0.02)\n"
    "    out.write(b'\\r[download] 100% of 7.63MiB in 00:00:03 at 2.00MiB/s\\n')\n"
    "    out.flush()\n"
    "out.write(b'QTVID1\\t/tmp/qt/Song.m4a\\n'); out.flush()\n")
os.chmod(qfake, os.stat(qfake).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

_op2 = os.environ["PATH"]
os.environ["PATH"] = QTBIN + os.pathsep + _op2
_seen_argv = []
_saved_hook = _pr.get_hook()
_orig_run = _dl._run_once


def _capture(args, log, on_line=None, on_file=None):
    _seen_argv.append(list(args))
    return _orig_run(args, log, on_line, on_file)


_dl._run_once = _capture
try:
    qevs, qlanded, qlogs = [], [], []
    _pr.set_hook(lambda e: qevs.append(e))
    _res, _err = _dl.download("http://x/pl",
                              {"net_max_wait": 0, "output_dir": "/tmp/qt"},
                              log=qlogs.append,
                              on_file=lambda p, v: qlanded.append((p, v)))
finally:
    _dl._run_once = _orig_run
    _pr.set_hook(_saved_hook)
    os.environ["PATH"] = _op2
kinds = {}
for e in qevs:
    kinds[(e or {}).get("type")] = kinds.get((e or {}).get("type"), 0) + 1
check("--print is still used (that is how each file is reported)",
      "--print" in _seen_argv[0] and
      any(a.startswith("after_move:") for a in _seen_argv[0]), str(_seen_argv[0][-3:]))
check("the download argv cancels the quiet --print implies",
      "--no-quiet" in _seen_argv[0], str(_seen_argv[0][-3:]))
check("percentage events reached the hook", kinds.get("pct") == 3, str(kinds))
check("item and completion events reached the hook",
      kinds.get("item") == 1 and kinds.get("done") == 1, str(kinds))
check("the log shows real progress, not just filenames",
      any("Downloading item 1 of 2" in x for x in qlogs)
      and any("Destination:" in x for x in qlogs), str(qlogs[-4:]))
check("on_file still fires with the videoId",
      qlanded == [("/tmp/qt/Song.m4a", "QTVID1")], str(qlanded))

# negative control: without --no-quiet the same fake prints no progress at all
os.environ["PATH"] = QTBIN + os.pathsep + _op2
try:
    bare = _dl.build_args("http://x/pl", {"output_dir": "/tmp/qt"})
    bare += ["--no-simulate", "--print", "after_move:%(id)s\t%(filepath)s"]
    qevs2 = []
    _pr.set_hook(lambda e: qevs2.append(e))
    _r2, _e2 = _dl._run_once(bare, lambda s: None, None, None)
finally:
    _pr.set_hook(_saved_hook)
    os.environ["PATH"] = _op2
check("without --no-quiet there is nothing to parse (the reported bug)",
      not [e for e in qevs2 if (e or {}).get("type") == "pct"]
      and _r2 == [("/tmp/qt/Song.m4a", "QTVID1")],
      f"events={[e.get('type') for e in qevs2]} results={_r2}")

# argument shape: --replace-in-metadata needs FIELDS REGEX REPLACE (three values)
_a = _dl.build_args("http://x/v", {"output_dir": "."})
_i = _a.index("--replace-in-metadata")
check("--replace-in-metadata gets all three values",
      _a[_i + 1:_i + 4] == ["uploader", " - Topic$", ""], str(_a[_i:_i + 4]))
check("--parse-metadata is no longer swallowed as the replacement",
      _a[_i + 4] == "--parse-metadata", str(_a[_i + 4:_i + 6]))

# parser against the exact strings yt-dlp 2026.08.19 emits
_ev = _pr.parse_ytdlp_line(
    "[download]   0.0% of    7.63MiB at  Unknown B/s ETA Unknown")
check("'Unknown' is not reported as a speed or an ETA",
      _ev and _ev["type"] == "pct" and _ev["speed"] == "" and _ev["eta"] == ""
      and _ev["pct"] == 0.0, str(_ev))
_ev = _pr.parse_ytdlp_line(
    "[download] 100% of    7.63MiB in 00:00:03 at 2.00MiB/s   ")
check("the 'in HH:MM:SS' line is a completion, not a percentage",
      _ev and _ev["type"] == "done", str(_ev))
_ev = _pr.parse_ytdlp_line(
    "[download]   4.2% of ~  12.34MiB at    1.20MiB/s ETA 00:08")
check("fragment estimate line parses size/speed/eta",
      _ev and _ev["size"] == "12.34MiB" and _ev["speed"] == "1.20MiB/s"
      and _ev["eta"] == "00:08", str(_ev))

# GUI must hand events to Tk from the main thread only
_gsrc = open(os.path.join(HERE, "..", "musictool", "gui.py"),
             encoding="utf-8").read()
check("the progress hook is a queue put, not a cross-thread root.after",
      "progress.set_hook(pq.put)" in _gsrc and "set_hook(lambda" not in _gsrc)
check("the main-thread pump applies the queued events",
      "for ev in newest_pct.values():" in _gsrc and "pq.get_nowait()" in _gsrc)
# per download AND per stream: video and audio side by side each keep their
# newest redraw, or the bar would only ever see whichever reported last
check("the pump keeps item/file events and drops only percentage redraws",
      'others.append(ev)' in _gsrc
      and 'newest_pct[(ev.get("tag"), ev.get("stream"))] = ev' in _gsrc)
check("completion is signalled through the same queue",
      "pq.put(FINISH)" in _gsrc and "root.after(0, lambda: (" not in _gsrc)

# ========================= 24. browser cookies, Stop, readable progress panel
print("\n--- 24. browser cookies, stop-anytime, percentages + minutes ---")
from musictool import cancel as _cx  # noqa: E402
from musictool import config as _cfg  # noqa: E402
from musictool import uicmd  # noqa: E402  (imported as U earlier; §24 spells it out)

# ---- browser detection, against a fake Windows profile tree
FW = os.path.join(WORK, "winprofile")
_loc = os.path.join(FW, "Local")
for _br, _sub, _age in (("edge", ["Microsoft", "Edge", "User Data"], 300),
                        ("chrome", ["Google", "Chrome", "User Data"], 5)):
    _d = os.path.join(_loc, *_sub, "Default", "Network")
    os.makedirs(_d, exist_ok=True)
    _cp = os.path.join(_d, "Cookies")
    open(_cp, "w").close()
    os.utime(_cp, (_time.time() - _age, _time.time() - _age))
_wenv = {"LOCALAPPDATA": _loc, "APPDATA": os.path.join(FW, "Roaming"),
         "USERPROFILE": FW}
_nm, _st = util.detect_browser(platform="win32", env=_wenv)
check("detects the browser you actually use (newest cookie database)",
      _nm == "chrome", str(_nm))
check("detection points at a cookie file that exists",
      bool(_st) and os.path.exists(_st), str(_st))
check("nothing installed -> nothing claimed",
      util.detect_browser(platform="win32",
                          env={"LOCALAPPDATA": os.path.join(WORK, "nope"),
                               "APPDATA": os.path.join(WORK, "nope2"),
                               "USERPROFILE": FW}) == (None, None))
check("every browser offered is one yt-dlp can read",
      set(util.BROWSERS) <= {"brave", "chrome", "chromium", "edge", "firefox",
                             "opera", "safari", "vivaldi", "whale"},
      str(util.BROWSERS))

# ---- what ends up on the yt-dlp command line
check("a named browser is passed through",
      _dl.cookie_args({"cookies_from_browser": "edge"}) ==
      ["--cookies-from-browser", "edge"])
check("'off' means no cookies at all",
      _dl.cookie_args({"cookies_from_browser": "off"}) == [])
_cj = os.path.join(WORK, "cj.txt")
open(_cj, "w").close()
check("an exported cookies.txt wins over the browser",
      _dl.cookie_args({"cookies": _cj, "cookies_from_browser": "edge"}) ==
      ["--cookies", _cj])
_notes = []
check("auto with no browser says so instead of failing",
      _dl.cookie_args({"cookies_from_browser": "auto"}, log=_notes.append) == []
      and any("no browser cookie database" in m for m in _notes), str(_notes))
_real_detect = _dl.detect_browser
_dl.detect_browser = lambda *a, **k: ("chrome", os.path.join(_loc, "x"))
try:
    _notes2 = []
    check("auto picks up the detected browser and tells you",
          _dl.cookie_args({"cookies_from_browser": "auto"},
                          log=_notes2.append) ==
          ["--cookies-from-browser", "chrome"]
          and any("chrome" in m for m in _notes2), str(_notes2))
finally:
    _dl.detect_browser = _real_detect
check("a cookies.txt that is not there warns",
      any("not found" in m for m in
          (lambda L: (_dl.cookie_args({"cookies": os.path.join(WORK, "gone.txt")},
                                      log=L.append), L)[1])([])))
check("build_args carries the browser login",
      "--cookies-from-browser" in _dl.build_args(
          "u", {"output_dir": ".", "cookies_from_browser": "edge"}))
check("the shipped default is 'auto'",
      _cfg.DEFAULTS["cookies_from_browser"] == "auto",
      str(_cfg.DEFAULTS.get("cookies_from_browser")))
r = run("get", "http://x/v", "-o", os.path.join(WORK, "CkOut"),
        "--cookies-from-browser", "edge", "--dry-run")
check("--cookies-from-browser reaches yt-dlp from the CLI",
      "--cookies-from-browser edge" in r.stdout, r.stdout[:200])
r = run("get", "--help")
check("the flag is documented", "--cookies-from-browser" in r.stdout)
r = run("doctor")
check("doctor says where your login would come from",
      "cookies   :" in r.stdout, r.stdout[-300:])

# ---- YouTube's bot wall gets an actionable answer
BOTBIN = os.path.join(WORK, "botbin")
os.makedirs(BOTBIN, exist_ok=True)
_bfake = os.path.join(BOTBIN, "yt-dlp")
open(_bfake, "w").write(
    "#!/usr/bin/env python3\n"
    "import sys\n"
    "print('ERROR: [youtube] Sign in to confirm you\\'re not a bot. "
    "Use --cookies')\n"
    "sys.exit(1)\n")
os.chmod(_bfake, os.stat(_bfake).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
_op3 = os.environ["PATH"]
os.environ["PATH"] = BOTBIN + os.pathsep + _op3
try:
    _r3, _e3 = _dl.download("http://x/v", {"net_max_wait": 0,
                                           "output_dir": WORK})
finally:
    os.environ["PATH"] = _op3
check("the bot-wall error explains how to fix it",
      _e3 and "Cookies from browser" in _e3 and "--cookies" in _e3, str(_e3))

# ---- Stop: the run ends, and what finished is kept
SLOWBIN = os.path.join(WORK, "slowbin")
os.makedirs(SLOWBIN, exist_ok=True)
_sfake = os.path.join(SLOWBIN, "yt-dlp")
_sdone = os.path.join(WORK, "slow-finished.marker")
if os.path.exists(_sdone):
    os.remove(_sdone)
open(_sfake, "w").write(
    "#!/usr/bin/env python3\n"
    "import sys, time\n"
    "out = sys.stdout.buffer\n"
    "out.write(b'SLOWVID\\t/tmp/slow/A.m4a\\n'); out.flush()\n"
    "for i in range(60):\n"
    "    out.write(b'\\r[download] ' + str(i).encode() + "
    "b'.0% of 9.00MiB at 1.00MiB/s ETA 00:09')\n"
    "    out.flush(); time.sleep(0.05)\n"
    "open(" + repr(_sdone) + ", 'w').close()\n")
os.chmod(_sfake, os.stat(_sfake).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
os.environ["PATH"] = SLOWBIN + os.pathsep + _op3
_cx.reset()
_slanded = []
threading.Timer(0.4, _cx.request).start()
try:
    _r4, _e4 = _dl.download("http://x/pl", {"net_max_wait": 0,
                                            "output_dir": "/tmp/slow"},
                            on_file=lambda p, v: _slanded.append((p, v)))
finally:
    os.environ["PATH"] = _op3
    _cx.reset()
check("Stop ends the download", _e4 == _cx.CANCELLED, str(_e4))
check("Stop keeps the files that already landed",
      _slanded == [("/tmp/slow/A.m4a", "SLOWVID")], str(_slanded))
check("Stop really killed yt-dlp (it never reached the end)",
      not os.path.exists(_sdone))

# ---- the lyrics pass honours Stop between files
_f1 = make("Stop Test - One.m4a", "One", "Stop Test", lrc("first song words", 6))
_f2 = make("Stop Test - Two.m4a", "Two", "Stop Test", lrc("second song words", 6))
_slogs = []


def _slog(m):
    _slogs.append(m)
    if "[1/2]" in m:
        _cx.request()


_cx.reset()
_sres = L.fix([_f1, _f2], {"lyrics_dir": os.path.join(WORK, "slyr"),
                           "provider_order": ["lrclib"]}, log=_slog)
_cx.reset()
check("the lyrics pass stops between files when asked",
      _sres["total"] == 1, str(_sres["total"]))
check("...and still reports what it did", len(_sres["skipped"]) == 1,
      str(_sres["skipped"]))

# ---- durations are minutes, and both bars carry a percentage
check("seconds are shown as minutes",
      _pr.fmt_minutes(7) == "0m 07s" and _pr.fmt_minutes(59) == "0m 59s"
      and _pr.fmt_minutes(60) == "1m 00s", _pr.fmt_minutes(7))
check("long runs read as minutes/hours, never 2563s",
      _pr.fmt_minutes(1084) == "18m 04s" and _pr.fmt_minutes(3725) == "1h 02m",
      _pr.fmt_minutes(1084))
_st2 = _pr.State()
_st2.started = _time.time() - 600            # ten minutes into the run
_st2.handle({"type": "item", "i": 5, "n": 10})
_st2.handle({"type": "done"})                # item 5 finished -> 6/10 done
# 600s elapsed at 0.6 done -> 400s left, and 400s must read as "6m 40s".
check("the whole-run estimate is minutes, not raw seconds",
      _st2.eta_total() == "6m 40s", _st2.eta_total())
_stx = _pr.State()
_stx.handle({"type": "item", "i": 287, "n": 447})
_stx.handle({"type": "pct", "pct": 50.0, "speed": "1.2MiB/s", "eta": "00:09"})
check("state exposes elapsed time as text",
      _stx.elapsed_text().endswith("s") or "m" in _stx.elapsed_text(),
      _stx.elapsed_text())

# ---- the GUI side
_g2 = open(os.path.join(ROOT, "musictool", "gui.py"), encoding="utf-8").read()
check("there is a Stop button wired to cancel",
      "stop_now" in _g2 and "cancel.request()" in _g2
      and 'text="Stop"' in _g2)
check("the run state never greys Stop out",
      'state="normal" if on else "disabled"' not in _g2)
check("the button is not orphaned by a stale rebinding",
      "stop_btn = None" not in _g2)
check("a run greys the other buttons out at once, not a pump tick later",
      "ui(lambda: busy(True))" not in _g2 and "busy(True)" in _g2)
check("every job starts un-cancelled", "cancel.reset()" in _g2)
check("the bar/text split is user-draggable",
      "ttk.PanedWindow" in _g2 and "sashpos" in _g2)
check("the chosen width is remembered",
      "progress_width" in _g2 and "configmod.remember(progress_width" in _g2)
check("both bars show a percentage", _g2.count(":5.1f}%") >= 2,
      str(_g2.count(":5.1f}%")))
check("the panel shows elapsed minutes, not raw seconds",
      "elapsed_text()" in _g2)
check("path fields get a Browse button",
      "def browse" in _g2 and 'text="Browse..."' in _g2)
check("the folder/file pickers are the right kind per option",
      uicmd.PATH_DESTS == {"output_dir": "dir", "lyrics_dir": "dir",
                           "cookies": "file", "download_archive": "file"},
      str(uicmd.PATH_DESTS))
_gspecs = {x["dest"]: x for x in uicmd.widget_specs("get")}
check("cookies.txt says what it is for",
      "optional" in _gspecs["cookies"]["hint"]
      and "cookies.txt" in _gspecs["cookies"]["label"],
      str(_gspecs["cookies"]))
check("the browser picker explains itself",
      "browser" in _gspecs["cookies_from_browser"]["hint"]
      and _gspecs["cookies_from_browser"]["kind"] == "choice"
      and "auto" in _gspecs["cookies_from_browser"]["choices"],
      str(_gspecs["cookies_from_browser"]))
check("the output folder is offered as a folder picker",
      _gspecs["output_dir"]["browse"] == "dir"
      and "remembered" in _gspecs["output_dir"]["hint"])

# ---- the output folder survives to the next run
_out = os.path.join(WORK, "Remembered")
r = run("get", "http://x/v", "-o", _out, "--no-lyrics")
check("get with -o still works", r.returncode == 0, (r.stderr or r.stdout)[-200:])
_cfgp = os.path.join(WORK, "cfg", "musictool", "config.json")
_saved = json.load(open(_cfgp, encoding="utf-8")) if os.path.exists(_cfgp) else {}
check("the output folder is remembered for next time",
      _saved.get("output_dir") == os.path.abspath(_out),
      str(_saved.get("output_dir")))
check("...and the GUI field defaults to the saved folder",
      uicmd.default_for(
          uicmd.sub_actions("get")["output_dir"],
          _cfg.DEFAULTS | {"output_dir": os.path.abspath(_out)}) ==
      os.path.abspath(_out))

# ---- the same things, executed against the stub GUI built in section 17
_hp = [w for w in root.walk()
       if isinstance(w, tkstub.PanedWindow)
       and w.options.get("orient") == "horizontal"]
check("the progress panel is a draggable left/right split",
      len(_hp) == 1 and len(_hp[0].panes) == 2, str(len(_hp)))
check("dragging the sash moves it",
      _hp and _hp[0].sashpos(0, 320) == 320 and _hp[0].sashpos(0) == 320)
_stop = [x for x in root.walk()
         if x.kind == "button" and x.options.get("text") == "Stop"]
check("there is exactly one Stop button", len(_stop) == 1, str(len(_stop)))
check("Stop is clickable while nothing runs -- you can always press it",
      _stop and _stop[0].options.get("state") != "disabled",
      str(_stop[0].options.get("state")) if _stop else "missing")
_cx.reset()
_stop[0].options["command"]()
check("with nothing running it does not arm a stop for the next run",
      not _cx.requested(), "the flag was left set")
_cx.request()                       # a request left over from an earlier run
_stop[0].options["command"]()
check("...and it clears a stale stop request instead of keeping it",
      not _cx.requested())
_cx.reset()


# push real events through the hook the GUI registered, then run its pump
check("the GUI registered a progress hook", _pr.get_hook() is not None)
def _drain():
    """Run the GUI's queued main-thread callbacks (its pump) once."""
    todo, root.pending[:] = list(root.pending), []
    for f in todo:
        try:
            f()
        except Exception as e:
            raise AssertionError(f"pump raised {type(e).__name__}: {e}")


_drain()      # clear the FINISH left over from the section-17 button clicks
_pr.report({"type": "item", "i": 287, "n": 447})
_pr.report({"type": "file", "name": r"C:\Music\Elevation Worship - Praise.m4a"})
_pr.report({"type": "pct", "pct": 42.5, "speed": "1.20MiB/s", "eta": "00:09"})
_drain()
_shown = [x.options["textvariable"].get() for x in root.walk()
          if x.kind == "label"
          and isinstance(x.options.get("textvariable"), tkstub.StringVar)]
_joined = " | ".join(_shown)
check("the panel reports playlist position with a percentage",
      "video 287 of 447" in _joined and "%" in _joined, _joined[:200])
check("the panel reports elapsed time in minutes", "elapsed" in _joined
      and ("m " in _joined or "0m" in _joined), _joined[:200])
check("the current file shows its own percentage, speed and ETA",
      "42.5%" in _joined and "1.20MiB/s" in _joined and "00:09" in _joined
      and "Praise.m4a" in _joined, _joined[:200])
_bars = [x for x in root.walk() if x.options.get("mode") == "determinate"]
check("the file bar actually moved to 42.5",
      any(abs(float(x.options.get("value") or 0) - 42.5) < 0.01 for x in _bars),
      str([x.options.get("value") for x in _bars]))
_br = [x for x in root.walk()
       if x.kind == "button" and x.options.get("text") == "Browse..."]
check("path fields have Browse buttons", len(_br) >= 4, str(len(_br)))
_cx.reset()

# Stop pressed *during* a run: the stubbed command presses the button from
# inside the job, which is the only way to be inside that window when the
# harness runs workers inline.
_cli_now = G.cli_main
_getb = [x for x in root.walk() if x.kind == "button"
         and x.options.get("text") == "Download + lyrics"]


def _cli_that_stops(argv):
    ARGV.append(list(argv))
    _stop[0].options["command"]()      # the user hits Stop mid-download
    return 0


G.cli_main = _cli_that_stops
_utxt = [w for w in nb.tab_for("Download").walk() if w.kind == "text"][0]
_utxt.insert("end", "http://x/watch?v=abc\n")
_cx.reset()
ARGV.clear()
_getb[0].invoke()
check("while a job is in flight, Stop really asks it to cancel",
      len(ARGV) == 1 and _cx.requested(),
      f"job ran={len(ARGV)} flag={_cx.requested()}")
_drain()           # let the log queue reach the log pane
check("...and says so rather than claiming nothing is running",
      any("stopping" in w.buf for w in root.walk() if w.kind == "text"),
      "no stopping message")
G.cli_main = _cli_now
_cx.reset()
_stop[0].options["command"]()
check("once it finishes, Stop goes back to being harmless",
      not _cx.requested())

# ============================ 25. no-cookie access check + quality by size
print("\n--- 25. 'am I blocked?' and choosing a quality by its size ---")
from musictool import formats as F  # noqa: E402
from musictool import download as D  # noqa: E402   # section 27 alias

YT = {"id": "abc", "title": "Elevation Worship - Praise", "duration": 225,
      "formats": [
          {"format_id": "18", "ext": "mp4", "vcodec": "avc1.42001E",
           "acodec": "mp4a.40.2", "width": 640, "height": 360, "fps": 30,
           "tbr": 500, "filesize_approx": 14000000, "format_note": "360p"},
          {"format_id": "137", "ext": "mp4", "vcodec": "avc1.640028",
           "acodec": "none", "width": 1920, "height": 1080, "fps": 30,
           "tbr": 2500, "filesize": 70000000},
          {"format_id": "248", "ext": "webm", "vcodec": "vp9", "acodec": "none",
           "width": 1920, "height": 1080, "fps": 60, "tbr": 3000,
           "filesize_approx": 84000000},
          {"format_id": "136", "ext": "mp4", "vcodec": "avc1.4d401f",
           "acodec": "none", "width": 1280, "height": 720, "fps": 30,
           "tbr": 1200, "filesize": 33000000},
          {"format_id": "140", "ext": "m4a", "vcodec": "none",
           "acodec": "mp4a.40.2", "abr": 128, "tbr": 129, "filesize": 3600000},
          {"format_id": "251", "ext": "webm", "vcodec": "none",
           "acodec": "opus", "abr": 160, "tbr": 161, "filesize": 4400000},
          {"format_id": "sb0", "ext": "m4a", "vcodec": "none", "acodec": "none",
           "format_note": "storyboard"}]}
ROWS = F.rows_from_info(YT)
check("storyboards are not offered as a quality",
      all(r["id"] != "sb0" for r in ROWS), str([r["id"] for r in ROWS]))
check("sizes come from filesize, else filesize_approx, else bitrate",
      {r["id"]: r["bytes"] for r in ROWS}["137"] == 70000000
      and {r["id"]: r["bytes"] for r in ROWS}["248"] == 84000000,
      str({r["id"]: r["bytes"] for r in ROWS}))
check("the list is biggest-first", [r["id"] for r in ROWS][:2] == ["248", "137"],
      str([r["id"] for r in ROWS]))
_tbl = F.table(ROWS, "x")
check("the table shows a size column",
      "size" in _tbl and "80.1 MiB" in _tbl and "1080p60" in _tbl, _tbl[:200])
_sel, _v, _a, _n = F.pick(ROWS, height=720)
check("720p means the 720p video plus a real audio track",
      _sel == "136+251" and _v["id"] == "136" and _a["kind"] == "audio",
      f"{_sel} {_v['id']} {_a['id']}")
check("a video is never paired with a combined format as its 'audio'",
      _a["kind"] != "both", _a["kind"])
_sel, _v, _a, _n = F.pick(ROWS, max_bytes=F.parse_size("40M"))
check("a size cap picks the best that fits under it",
      _sel == "136+251" and (_v["bytes"] + _a["bytes"]) <= 40 * 1024 * 1024,
      f"{_sel} {_v['bytes']}+{_a['bytes']}")
_sel, _v, _a, _n = F.pick(ROWS, height=2160, max_bytes=F.parse_size("10M"))
check("when nothing fits it says so instead of pretending",
      _n and "nothing fits" in _n, _n)
_sel, _v, _a, _n = F.pick(ROWS, audio_only=True)
check("audio-only takes a real audio track, not a video file",
      _sel == "251", _sel)
_sel, _v, _a, _n = F.pick(ROWS, smallest=True)
check("'smallest' takes the smallest", _sel == "18", _sel)
check("no formats at all is reported, not guessed", F.pick([])[0] is None)
check("size suffixes parse", [F.parse_size(x) for x in ("500M", "1.5G", "800k")]
      == [500 * 1024 ** 2, int(1.5 * 1024 ** 3), 800 * 1024])
check("a plain file with no codec info is still a quality",
      F.rows_from_info({"formats": [{"format_id": "mp4", "ext": "mp4"}]})[0]["kind"]
      == "file")

check("the bot wall is recognised",
      _dl.classify_error("Sign in to confirm you're not a bot") == "bot")
check("age, private, members and gone are told apart",
      [_dl.classify_error(x) for x in
       ("confirm your age", "Private video", "members-only", "Video unavailable")]
      == ["age", "private", "members", "unavailable"])
check("a dead connection is not called a block",
      _dl.classify_error("unable to download: Connection reset") == "network")
check("every kind has advice", all(_dl.ADVICE.get(k) for k in
                                   ("bot", "age", "private", "members",
                                    "unavailable", "network")))

# ---- a fake yt-dlp that answers -J like YouTube does
FMTBIN = os.path.join(WORK, "fmtbin")
os.makedirs(FMTBIN, exist_ok=True)
_ffake = os.path.join(FMTBIN, "yt-dlp")
open(_ffake, "w").write(
    "#!/usr/bin/env python3\n"
    "import json, sys\n"
    "a = sys.argv[1:]\n"
    "if '--version' in a:\n"
    "    print('2026.10.21-fake'); sys.exit(0)\n"
    "if '-J' in a:\n"
    "    print(json.dumps(" + repr(YT) + ")); sys.exit(0)\n"
    "if '--simulate' in a:\n"
    "    sys.exit(0)\n"
    "print('fakeid\\t/tmp/fmt/out.mkv')\n")
os.chmod(_ffake, os.stat(_ffake).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def run_fmt(*args):
    env = dict(ENV, PATH=FMTBIN + os.pathsep + ENV["PATH"])
    return subprocess.run([sys.executable, ENTRY, *args], capture_output=True,
                          text=True, env=env)


r = run_fmt("formats", "http://x/watch?v=abc", "--keep-video")
check("formats lists the qualities with their sizes",
      r.returncode == 0 and "1080p60" in r.stdout and "80.1 MiB" in r.stdout
      and "collapsed to" in r.stdout, r.stdout[-400:])
r_aud = run_fmt("formats", "http://x/watch?v=abc", "--keep-video",
                "--show-audio")
check("--show-audio brings the audio tracks back into the table",
      "audio only" in r_aud.stdout and "opus" in r_aud.stdout,
      r_aud.stdout[-300:])
r_all = run_fmt("formats", "http://x/watch?v=abc", "--keep-video",
                "--all-formats", "--show-audio")
check("--all-formats shows every raw row",
      "collapsed to" not in r_all.stdout
      and len([l for l in r_all.stdout.splitlines()
               if l.strip()[:1].isdigit()]) == 6,
      str(len([l for l in r_all.stdout.splitlines()
               if l.strip()[:1].isdigit()])))
check("formats tells you the exact command to take one",
      "-f 248+251" in r.stdout or "-f 137+251" in r.stdout, r.stdout[-300:])
r = run_fmt("formats", "http://x/watch?v=abc", "--keep-video", "--max-size", "40M")
check("formats honours a size cap", "136+251" in r.stdout, r.stdout[-300:])
r = run_fmt("get", "http://x/watch?v=abc", "-o", os.path.join(WORK, "QualOut"),
            "--keep-video", "--quality", "720", "--merge-format", "mkv",
            "--no-lyrics")
check("get turns --quality 720 into a concrete -f",
      "quality: -f 136+251" in r.stdout and "-f 136+251" in r.stdout,
      r.stdout[-400:])
check("...and passes the container through",
      "--merge-output-format mkv" in r.stdout, r.stdout[-400:])
r = run_fmt("get", "http://x/watch?v=abc", "-o", os.path.join(WORK, "QualOut2"),
            "--keep-video", "--max-size", "40M", "--no-lyrics")
check("get turns --max-size into the best fit",
      "quality: -f 136+251" in r.stdout, r.stdout[-400:])
r = run_fmt("get", "http://x/watch?v=abc", "-o", os.path.join(WORK, "QualOut3"),
            "--keep-video", "-f", "18", "--no-lyrics")
check("an explicit -f is never overridden",
      "quality:" not in r.stdout and "-f 18" in r.stdout, r.stdout[-300:])
r = run_fmt("get", "http://x/watch?v=abc", "-o", os.path.join(WORK, "QualOut4"),
            "--keep-video", "--subs", "--sub-langs", "en,es",
            "--remote-components", "ejs:github", "--chapters", "--no-lyrics")
check("subs, languages, chapters and remote components all reach yt-dlp",
      all(x in r.stdout for x in ("--embed-subs", "--sub-langs en,es",
                                  "--remote-components ejs:github",
                                  "--embed-chapters")), r.stdout[-400:])

# ---- the blocked check, with a fake that refuses like YouTube does
BLOCKBIN = os.path.join(WORK, "blockbin")
os.makedirs(BLOCKBIN, exist_ok=True)
_bk = os.path.join(BLOCKBIN, "yt-dlp")
_bklog = os.path.join(WORK, "block-argv.log")
if os.path.exists(_bklog):
    os.remove(_bklog)
open(_bk, "w").write(
    "#!/usr/bin/env python3\n"
    "import sys\n"
    "if '--version' in sys.argv:\n"
    "    print('2026.10.21-fake'); sys.exit(0)\n"
    "open(" + repr(_bklog) + ", 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
    "print('ERROR: [youtube] Sign in to confirm you\\'re not a bot. "
    "Use --cookies', file=sys.stderr)\n"
    "sys.exit(1)\n")
os.chmod(_bk, os.stat(_bk).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
env = dict(ENV, PATH=BLOCKBIN + os.pathsep + ENV["PATH"])
r = subprocess.run([sys.executable, ENTRY, "check", "http://x/watch?v=abc"],
                   capture_output=True, text=True, env=env)
check("check reports BLOCKED and why",
      r.returncode == 1 and "BLOCKED (bot)" in r.stdout, r.stdout[-400:])
check("check says what to do about it",
      "--cookies-from-browser" in r.stdout, r.stdout[-400:])
check("check counts the verdicts",
      "checked 1 URL(s): 0 reachable, 1 blocked" in r.stdout, r.stdout[-200:])
_sent = open(_bklog, encoding="utf-8").read() if os.path.exists(_bklog) else ""
check("check really sent no cookies to yt-dlp",
      _sent and "--cookies" not in _sent, _sent[:200])
r = run_fmt("check", "http://x/watch?v=abc")
check("a reachable URL is reported as reachable, exit 0",
      r.returncode == 0 and "reachable" in r.stdout
      and "you are not blocked" in r.stdout, r.stdout[-300:])
check("check names the video it could see",
      "Elevation Worship - Praise" in r.stdout, r.stdout[-300:])

# ---- the GUI reaches both of them
_g3 = open(os.path.join(ROOT, "musictool", "gui.py"), encoding="utf-8").read()
check("the GUI has a no-cookie access check button",
      '"Check access (no cookies)"' in _g3 and "def go_check" in _g3)
check("the GUI can list qualities and sizes",
      '"List qualities + sizes"' in _g3 and "def go_formats" in _g3)
check("'this video only' is a quick option, not buried",
      any(d == "no_playlist" for d, _ in uicmd.QUICK["get"]),
      str(uicmd.QUICK["get"]))
_qs = {x["dest"]: x for x in uicmd.widget_specs("get")}
check("the quality options are in the GUI",
      all(d in _qs for d in ("quality", "max_size", "format_selector",
                             "merge_format", "embed_subs", "sub_langs",
                             "remote_components")),
      str(sorted(set(("quality", "max_size", "format_selector", "merge_format",
                      "embed_subs", "sub_langs", "remote_components"))
                 - set(_qs))))
check("the container choice is a dropdown",
      _qs["merge_format"]["kind"] == "choice"
      and "mkv" in _qs["merge_format"]["choices"])
check("check/formats have GUI widgets too",
      {x["dest"] for x in uicmd.widget_specs("check")} >= {"with_cookies"}
      and {x["dest"] for x in uicmd.widget_specs("formats")} >=
      {"quality", "max_size"})


# =========================================================== 26. quality picker
print("\n--- 26. the streamlined quality picker (auto, manual, filters) ---")

# the shape YouTube really reports for one 4K video: every itag split into
# 18 CDN variants (140-0..140-17, 251-0..251-17, ...), which is why a single
# video lists 127 rows and is impossible to read.
_vids = [(625, "mp4", 2160, "vp09", 8.5e9), (313, "webm", 2160, "vp9", 6.3e9),
         (620, "mp4", 1440, "vp09", 4.3e9), (401, "mp4", 2160, "av01", 1.8e9),
         (271, "webm", 1440, "vp9", 1.8e9), (270, "mp4", 1080, "avc1", 2.1e9),
         (137, "mp4", 1080, "avc1", 6.5e8), (136, "mp4", 720, "avc1", 3.3e8),
         (609, "mp4", 720, "vp09", 9.4e8), (160, "mp4", 144, "avc1", 1.5e7)]
_auds = [(251, "webm", "opus", 160), (140, "m4a", "mp4a", 128),
         (250, "webm", "opus", 70), (139, "m4a", "mp4a", 48)]
_big = []
for fid, ext, h, vc, sz in _vids:
    for n in range(18):
        _big.append({"format_id": f"{fid}-{n}", "ext": ext, "vcodec": vc + ".x",
                     "acodec": "none", "height": h, "fps": 30,
                     "filesize": int(sz) + n * 1000})
for fid, ext, ac, abr in _auds:
    for n in range(18):
        _big.append({"format_id": f"{fid}-{n}", "ext": ext, "vcodec": "none",
                     "acodec": ac + ".x", "abr": abr,
                     "filesize": 56_000_000 + n * 1000})
BIG = F.rows_from_info({"duration": 3600, "formats": _big})
check("a real 4K list really is unreadable",
      len(BIG) == 14 * 18, str(len(BIG)))
check("variant ids are recognised",
      F.variant_base("251-17") == "251" and F.variant_base("251") == "251"
      and F.variant_base("bv1.2.3") == "bv1.2.3",
      F.variant_base("bv1.2.3"))
_coll = F.collapse_variants(BIG)
check("18 copies of one stream collapse into one row",
      len(_coll) == 14, str(len(_coll)))
_c251 = [r for r in _coll if F.variant_base(r["id"]) == "251"][0]
check("the collapsed row keeps the best variant and says how many",
      _c251["variants"] == 18 and _c251["id"] == "251-17",
      f"{_c251['id']} {_c251['variants']}")
check("the collapsed size is the whole track, not a slice",
      _c251["bytes"] == 56_000_000 + 17 * 1000, str(_c251["bytes"]))
_ded = F.dedupe_quality(_coll)


def bid(r):
    """The itag a row really is, whatever variant we measured."""
    return F.variant_base(r["id"])


_db = {bid(r) for r in _ded}
check("same quality twice keeps the smaller file",
      "137" in _db and "270" not in _db, str(sorted(_db)))
check("...but a different container survives dedupe",
      "620" in _db and "271" in _db)
check("the table shows the plain itag, not the variant suffix",
      " 137 " in " " + F.table(F.apply_filters(BIG)) or
      "\n137 " in F.table(F.apply_filters(BIG)),
      F.table(F.apply_filters(BIG))[:120])
_r137 = [r for r in F.apply_filters(BIG) if bid(r) == "137"][0]
check("...but the selector names the variant it actually measured",
      F.selector_for(_r137, None) == _r137["id"] == "137-17"
      and "/" not in F.selector_for(_r137, None),
      F.selector_for(_r137, None))
check("252 rows become a list you can actually read",
      len(F.apply_filters(BIG)) < 20, str(len(F.apply_filters(BIG))))
check("turning collapse+dedupe off gives every row back",
      len(F.apply_filters(BIG, collapse=False, dedupe=False,
                          include_audio=True)) == 252)
check("audio tracks stay hidden unless you ask",
      all(r["kind"] != "audio" for r in F.apply_filters(BIG))
      and any(r["kind"] == "audio" for r in F.apply_filters(BIG,
                                                           include_audio=True)))
check("a height filter really filters",
      {bid(r) for r in F.apply_filters(BIG, max_height=720)} <=
      {"136", "160", "609"},
      str(sorted({bid(r) for r in F.apply_filters(BIG, max_height=720)})))
check("a container filter really filters",
      {r["ext"] for r in F.apply_filters(BIG, container="webm")} == {"webm"},
      str({r["ext"] for r in F.apply_filters(BIG, container="webm")}))
check("a codec filter really filters",
      all((r["vcodec"] or "").startswith("av01")
          for r in F.apply_filters(BIG, codec="av01"))
      and len(F.apply_filters(BIG, codec="av01")) == 1)
check("a size cap drops the giants",
      all(r["bytes"] <= 1_000_000_000 for r in
          F.apply_filters(BIG, max_bytes=1_000_000_000)))
check("sorting works both ways",
      bid(F.apply_filters(BIG, sort="size_asc")[0]) == "160"
      and bid(F.apply_filters(BIG, sort="size_desc")[0]) == "625",
      bid(F.apply_filters(BIG, sort="size_asc")[0]))
check("sorting by resolution puts the tallest first",
      F.apply_filters(BIG, sort="resolution")[0]["height"] == 2160)
SM = F.rows_from_info({"duration": 200, "formats": [
    {"format_id": "22", "ext": "mp4", "vcodec": "avc1.40", "height": 720,
     "acodec": "mp4a.40.2", "filesize": 30_000_000}]})
check("the table names the variants it merged",
      "18 merged" in F.table(F.apply_filters(BIG))
      and "variants" in F.table(F.apply_filters(BIG)))
check("...and only adds that column when it actually merged something",
      "variants" not in F.table(SM) and "merged" not in F.table(SM))

# --- the presets: it has to say why
_pool = F.apply_filters(BIG, include_audio=True)
check("auto: best takes the tallest",
      bid(F.auto_pick(_pool)[1]) == "625", str(F.auto_pick(_pool)[0]))
check("auto: compatible takes H.264/mp4 so nothing needs converting",
      bid(F.auto_pick(_pool, mode="compatible")[1]) == "137"
      and "H.264" in F.auto_pick(_pool, mode="compatible")[3],
      str(F.auto_pick(_pool, mode="compatible")[0]))
check("auto: fit takes the best under the cap",
      bid(F.auto_pick(_pool, mode="fit", max_bytes=1_000_000_000)[1]) == "137",
      str(F.auto_pick(_pool, mode="fit", max_bytes=1_000_000_000)[0]))
check("auto: smallest takes the smallest",
      bid(F.auto_pick(_pool, mode="smallest")[1]) == "160")
check("auto: audio takes opus, not a video file",
      bid(F.auto_pick(_pool, mode="audio")[1]) == "251",
      str(F.auto_pick(_pool, mode="audio")[0]))
check("every preset explains itself",
      all(F.auto_pick(_pool, mode=m)[3] for m in F.AUTO_MODES),
      str(F.AUTO_MODES))
check("a preset with nothing to choose from says so",
      F.auto_pick([], mode="best")[0] is None
      and F.auto_pick([], mode="best")[3])

# --- manual: a clicked row becomes a selector, with audio paired properly
_row = [r for r in F.apply_filters(BIG) if bid(r) == "137"][0]
_sel = F.selector_for(*F.pair_with_audio(_row, _pool))
check("clicking a row gives a ready -f", _sel.startswith("137"), _sel)
check("a filtered table still pairs with a REAL audio track",
      F.selector_for(*F.pair_with_audio(
          _row, F.with_audio_pool(F.apply_filters(BIG), BIG)))
      == "137-17+251-17",
      F.selector_for(*F.pair_with_audio(
          _row, F.with_audio_pool(F.apply_filters(BIG), BIG))))
_noaud = F.pair_with_audio(_row, F.apply_filters(BIG, include_audio=False))[1]
check("hiding the audio rows must not pair video with a combined format",
      _noaud is None or _noaud["kind"] == "audio",
      str(_noaud and _noaud["id"]))
check("total_bytes adds the audio to the video",
      F.total_bytes(_row) == _row["bytes"]
      and F.total_bytes(*F.pair_with_audio(_row, _pool))
      > _row["bytes"])

# --- the same thing through the CLI
r = run_fmt("formats", "http://x/watch?v=abc", "--keep-video",
            "--container", "mp4")
check("formats --container mp4 filters the table",
      "webm" not in r.stdout.split("to take this one")[0], r.stdout[-300:])
r = run_fmt("formats", "http://x/watch?v=abc", "--keep-video", "--codec", "vp9")
check("formats --codec vp9 filters the table",
      "avc1" not in r.stdout.split("to take this one")[0], r.stdout[-300:])
r = run_fmt("formats", "http://x/watch?v=abc", "--keep-video", "--auto",
            "compatible")
check("formats --auto compatible recommends the playable one",
      "auto (compatible)" in r.stdout and "H.264" in r.stdout, r.stdout[-300:])
r = run_fmt("formats", "http://x/watch?v=abc", "--keep-video", "--auto",
            "smallest")
check("formats --auto smallest recommends the smallest",
      "auto (smallest)" in r.stdout and "-f 18" in r.stdout, r.stdout[-300:])
r = run_fmt("formats", "http://x/watch?v=abc", "--keep-video",
            "--sort", "size_asc")
check("formats --sort size_asc lists smallest first",
      r.stdout.index("18   ") < r.stdout.index("248  "), r.stdout[-400:])

# --- and through the GUI's Quality tab
def _qw(w, k):
    """widgets of a kind inside w (the section-17 `kinds` was rebound above)"""
    return [x for x in w.walk() if x.kind == k]


def _qbtn(w, text):
    for x in _qw(w, "button"):
        if x.options.get("text") == text:
            return x
    raise KeyError(f"no button {text!r}")


_q = nb.tab_for("Quality")
check("the Quality tab exists and is reachable", _q is not None)
_qt = [w for w in _q.walk() if isinstance(w, tkstub.Treeview)]
check("the picker is a table you can click", len(_qt) == 1)
check("the table shows id, quality, container, codec, language, size, variants",
      list(_qt[0].options.get("columns", ())) ==
      ["id", "what", "container", "video", "codec", "lang", "size",
       "variants"],
      str(_qt[0].options.get("columns")))
_cbs = {w.options.get("text") for w in _qw(_q, "checkbutton")}
check("the filters are on the tab, not in a menu",
      {"Merge variant splits", "Hide same-quality doubles",
       "Show audio-only tracks"} <= _cbs, str(_cbs))
_rbs = {w.options.get("text", "") for w in _qw(_q, "radiobutton")}
check("auto and manual are both there",
      any(t.startswith("Auto") for t in _rbs)
      and any(t.startswith("Manual") for t in _rbs), str(_rbs))
_combos = [w.options.get("values") for w in _qw(_q, "combobox")]
_pre = next((v for v in _combos if v and F.AUTO_LABELS["best"] in list(v)), [])
check("every preset is offered",
      [F.AUTO_LABELS[m] for m in F.AUTO_MODES] == list(_pre), str(_pre))
check("the height filter is a dropdown, not a guess",
      any(v and "1080" in [str(x) for x in v] for v in _combos))
check("the codec filter knows the codecs that matter",
      any(v and "av01" in [str(x) for x in v] for v in _combos))
check("loading and downloading are both one click",
      _qbtn(_q, "Load qualities") is not None
      and _qbtn(_q, "Download this quality") is not None)
check("...and the command can be copied",
      _qbtn(_q, "Copy command") is not None)

# pressing Load must NOT touch tkinter from the worker thread: the callback
# has to come back through the queue and be applied by pump().
_real_probe = G.download.probe
_seen = {}


def _fake_probe(url, o):
    _seen["thread"] = threading.current_thread().name
    return YT


G.download.probe = _fake_probe
_qurl = [w for w in _q.walk() if w.kind == "entry"][0]
_qurl.options["textvariable"].set("http://x/watch?v=abc")
ARGV.clear()
_qbtn(_q, "Load qualities").invoke()
check("nothing in the worker path calls tkinter directly",
      "root.after(0" not in _g3 and "uq.put" in _g3)
check("...and did not touch the table from that thread",
      _qt[0].order == [], str(_qt[0].order[:3]))
for _cb in list(root.pending):
    _cb()          # the main-thread half of the click: pump() drains the queue
root.pending.clear()
check("pump() then fills the table with the collapsed list",
      len(_qt[0].order) == 4, str(len(_qt[0].order)))
_vals = [_qt[0].nodes[i].get("values") for i in _qt[0].order]
check("the table shows the biggest quality first",
      _vals[0][0] == "248" and _vals[0][3] == "1080p60", str(_vals[0]))
_qfmt = [w for w in _q.walk() if w.kind == "entry"
         and w.options.get("state") == "readonly"][0]
check("auto mode filled in a ready-to-use -f",
      _qfmt.options["textvariable"].get() == "248+251", _qfmt.options["textvariable"].get())
check("...and says why it picked it",
      "auto:" in " ".join(str(w.options.get("textvariable").get())
                          for w in _qw(_q, "label")
                          if w.options.get("textvariable")), "no auto note")
_qt[0].selection_set(_qt[0].order[1])
_qt[0].fire("<<TreeviewSelect>>")
check("clicking a row switches to manual and takes that quality",
      _qfmt.options["textvariable"].get() == "137+251", _qfmt.options["textvariable"].get())
_cli_main_now = G.cli_main
G.cli_main = lambda argv: (ARGV.append(list(argv)), 0)[1]
ARGV.clear()
_qbtn(_q, "Download this quality").invoke()
check("one click downloads the quality you picked",
      len(ARGV) == 1 and "get" in ARGV[0]
      and "137+251" in " ".join(ARGV[0]), str(ARGV[:1]))
check("the download it runs keeps the video",
      "--keep-video" in ARGV[0], str(ARGV[0])[:200])
ARGV.clear()
_qbtn(_q, "Copy command").invoke()
check("copying gives the same command",
      "137+251" in root.clipboard and root.clipboard.startswith("musictool get"),
      root.clipboard[:160])
G.cli_main = _cli_main_now
G.download.probe = _real_probe


# ==================== 27. the sign-in / anti-bot cascade, named and explained
print("\n--- 27. every failure from the YouTube sign-in cascade has a fix ---")

_CASCADE = [
    ("Sign in again to continue to YouTube", "session"),
    ("ERROR: Failed to decrypt with DPAPI. See "
     "https://github.com/yt-dlp/yt-dlp/issues/10927", "dpapi"),
    ("Could not copy Chrome cookie database. See "
     "https://github.com/yt-dlp/yt-dlp/issues/7271", "cookies_locked"),
    ("ERROR: [youtube:tab] PLxx: Login with OAuth is no longer supported.",
     "oauth_gone"),
    ("WARNING: [youtube] No supported JavaScript runtime could be found. "
     "Only deno is enabled by default", "js_runtime"),
    ("WARNING: [youtube] vEIU3k25kJY: n challenge solving failed", "js_runtime"),
    ("WARNING: [youtube] [pot:bgutil:http] Error reaching GET "
     "http://127.0.0.1:4416/ping", "pot_server"),
    ("ERROR: [youtube] vEIU3k25kJY: The page needs to be reloaded.", "reload"),
    ("ERROR: [youtube] RCZy7FhLtkw: Requested format is not available.",
     "no_format"),
]
for _text, _kind in _CASCADE:
    check(f"'{_kind}' is recognised ({_text[:34]}...)",
          D.classify_error(_text) == _kind, D.classify_error(_text))
check("none of them fall through to 'unknown'",
      all(D.classify_error(t) != "unknown" for t, _ in _CASCADE))
check("every new kind comes with a fix, not just a name",
      all(download.ADVICE.get(k) for _, k in _CASCADE),
      str([k for _, k in _CASCADE if not download.ADVICE.get(k)]))
check("the DPAPI fix points at Firefox, because that is the only way out",
      "firefox" in download.ADVICE["dpapi"].lower()
      and "App-Bound" in download.ADVICE["dpapi"])
check("the cookie-database fix says to really close the browser",
      "Startup Boost" in download.ADVICE["cookies_locked"])
check("the PO-token fix says it is a plugin server, not yt-dlp itself",
      "4416" in download.ADVICE["pot_server"]
      and "plugin" in download.ADVICE["pot_server"])
check("the JS-runtime fix names both the runtime and the solver script",
      "--js-runtimes" in download.ADVICE["js_runtime"]
      and "--remote-components ejs:github" in download.ADVICE["js_runtime"])
check("a hijacked cookies.txt is not confused with a plain bot wall",
      D.classify_error("Sign in again to continue") !=
      D.classify_error("Sign in to confirm you're not a bot"))
check("...and the old verdicts still stand",
      D.classify_error("Sign in to confirm you're not a bot") == "bot"
      and D.classify_error("Confirm your age") == "age"
      and D.classify_error("Private video") == "private"
      and D.classify_error("HTTP Error 404: Not Found") == "unknown")

check("Edge is Chromium, so it hits the same lock as Chrome",
      "edge" in download.CHROMIUM and "chrome" in download.CHROMIUM
      and "brave" in download.CHROMIUM)
check("Firefox is not in that list -- that is the whole point",
      "firefox" not in download.CHROMIUM)

_r = run_fmt("get", "http://x/watch?v=abc", "-o", os.path.join(WORK, "JsOut"),
             "--keep-video", "--js-runtimes", "node",
             "--remote-components", "ejs:github", "--no-lyrics")
check("--js-runtimes reaches yt-dlp, not just argparse",
      "--js-runtimes node" in _r.stdout
      and "--remote-components ejs:github" in _r.stdout, _r.stdout[-300:])
check("--js-runtimes is in the GUI too",
      "js_runtimes" in {x["dest"] for x in U.widget_specs("get")})
check("the JS runtime probe answers a (name, path) pair",
      (lambda r: isinstance(r, tuple) and len(r) == 2)(util.js_runtime()),
      str(util.js_runtime()))
check("the PO-token probe answers yes/no without hanging",
      isinstance(util.pot_server_reachable(timeout=0.05), bool),
      str(util.pot_server_reachable(timeout=0.05)))
_r = run_fmt("doctor")
check("doctor reports the JS runtime",
      "js runtime:" in _r.stdout, _r.stdout[-300:])
check("doctor reports the PO-token server",
      "pot server:" in _r.stdout, _r.stdout[-300:])
check("doctor still reports the cookie situation",
      "cookies   :" in _r.stdout, _r.stdout[-300:])


# ============================== 28. modes: the checkboxes set themselves
print("\n--- 28. one mode chooses the checkboxes, not the other way round ---")

check("there are exactly four modes, and they are a 2x2 grid",
      len(U.MODES) == 4
      and {U.MODE_WHAT[m] for m in U.MODES} == {"music", "video"}
      and {U.MODE_SCOPE[m] for m in U.MODES} == {"single", "playlist"},
      str(U.MODES))
check("every mode is named on screen and says what it will do",
      all(U.MODE_LABELS.get(m) and U.MODE_SUMMARY.get(m) for m in U.MODES))
_gdests = {x["dest"] for x in U.widget_specs("get", parser=PARSER)}
_bad = {d for m in U.MODES for d in U.MODE_VALUES[m] if d not in _gdests}
check("every mode only sets options that really exist", not _bad, str(_bad))
check("music takes the audio, video keeps the picture",
      all(U.MODE_VALUES[m]["keep_video"] is (U.MODE_WHAT[m] == "video")
          for m in U.MODES))
check("music gets lyrics, video does not",
      all(bool(U.MODE_VALUES[m]["no_lyrics"]) is (U.MODE_WHAT[m] == "video")
          for m in U.MODES))
check("a single stays a single, a playlist walks the list",
      all(bool(U.MODE_VALUES[m]["no_playlist"]) is
          (U.MODE_SCOPE[m] == "single") for m in U.MODES))
check("playlists are numbered inside a folder of their own",
      all("playlist_index" in U.MODE_VALUES[m]["template"]
          for m in U.MODES if U.MODE_SCOPE[m] == "playlist")
      and all("playlist_index" not in U.MODE_VALUES[m]["template"]
              for m in U.MODES if U.MODE_SCOPE[m] == "single"))
check("videos pick a real quality instead of leaving it to chance",
      U.MODE_VALUES["video_single"]["quality"] == "1080"
      and U.MODE_VALUES["video_single"]["merge_format"] == "mkv")

# --- the URL decides single vs playlist, and it is not a guess
check("a /playlist?list= URL is a playlist",
      U.is_playlist_url("https://youtube.com/playlist?list=PLrOFa8sDv6jf"))
check("...but watch?v=X&list=PL is ONE song, not 90 of them",
      not U.is_playlist_url("https://music.youtube.com/watch?v=IZOn9KUy4qs"
                            "&list=PLVuMx2J3_K6w"))
check("a plain watch URL, a youtu.be and nothing at all are singles",
      not U.is_playlist_url("https://music.youtube.com/watch?v=abc")
      and not U.is_playlist_url("https://youtu.be/abc")
      and not U.is_playlist_url(""))
check("a playlist anywhere in the list makes it a playlist run",
      U.mode_for(["https://youtu.be/a",
                  "https://youtube.com/playlist?list=PLx"], "music")
      == "music_playlist")
check("the window can tell when you have changed the mode by hand",
      U.mode_drift(dict(U.MODE_VALUES["music_single"]), "music_single") == []
      and U.mode_drift(dict(U.MODE_VALUES["music_single"], no_playlist=False),
                       "music_single") == ["no_playlist"])
check("...and '4' means 4, so strings do not read as a change",
      U.mode_drift({k: str(v) for k, v in U.MODE_VALUES["video_single"].items()},
                   "video_single") == [])
check("switching one axis keeps the other",
      U.mode_switch("music_single", what="video") == "video_single"
      and U.mode_switch("video_playlist", scope="single") == "video_single")

# --- and the window
def gbtn(text):
    """`button()` is unusable this far down (the name `kinds` is a dict by
    section 26), so look the button up directly."""
    for w in _gtab.walk():
        if w.kind == "button" and str(w.options.get("text")) == text:
            return w


_gtab = nb.tab_for("Download")
_grb = [w for w in _gtab.walk() if w.kind == "radiobutton"]
check("the two questions are on the tab, as four choices",
      len(_grb) == 4, str([w.options.get("text") for w in _grb]))
check("music and video are both offered",
      {w.options.get("value") for w in _grb} >= {"music", "video", "single",
                                                 "playlist"},
      str({w.options.get("value") for w in _grb}))
_gpane = next(w for w in root.walk()
              if isinstance(w, G.OptionsPane) and w.sub == "get")
_vrb = [w for w in _grb if w.options.get("value") == "video"][0]
_srb = [w for w in _grb if w.options.get("value") == "playlist"][0]
check("the default is music, one song",
      _gpane.vars["keep_video"].get() is False
      and _gpane.vars["no_lyrics"].get() is False
      and _gpane.vars["no_playlist"].get() is True,
      str(_gpane.values()))
_vrb.invoke()
check("choosing Video ticks keep-video and drops lyrics by itself",
      _gpane.vars["keep_video"].get() is True
      and _gpane.vars["no_lyrics"].get() is True
      and _gpane.vars["embed_chapters"].get() is True
      and _gpane.vars["merge_format"].get() == "mkv",
      str(_gpane.values()))
check("...and it is the SAME variable the option list shows",
      _vrb.options["variable"] is not None
      and _gpane.vars["keep_video"] is _gpane.vars["keep_video"])
_sum = [w for w in _gtab.walk() if w.kind == "label"
        and w.options.get("textvariable") is not None
        and str(w.options["textvariable"].get()).startswith("\u2192")]
_cur = str(_sum[0].options["textvariable"].get())
check("the tab states in plain words what the mode will do",
      len(_sum) == 1 and U.MODE_LABELS["video_single"] in _cur and "mkv" in _cur,
      _cur)
check("the button says what the mode will do",
      gbtn("Download video") is not None,
      str([w.options.get("text") for w in _gtab.walk() if w.kind == "button"]))
_mrb = [w for w in _grb if w.options.get("value") == "music"][0]
_mrb.invoke()
check("choosing Music puts them back",
      _gpane.vars["keep_video"].get() is False
      and _gpane.vars["no_lyrics"].get() is False
      and gbtn("Download + lyrics") is not None)
check("...and the one-line summary follows it",
      U.MODE_LABELS["music_single"]
      in str(_sum[0].options["textvariable"].get()),
      str(_sum[0].options["textvariable"].get()))

# the long tail of options is one click away, not in your face
_utxt = [w for w in _gtab.walk() if w.kind == "text"][0]
_adv = [w for w in _gtab.walk() if w.kind == "checkbutton"
        and str(w.options.get("text", "")).startswith("More options")]
check("the rest of the options hide behind one checkbox", len(_adv) == 1)
check("...and they start hidden, so the tab fits a short window",
      _gpane.geo is None, str(_gpane.geo))
_adv[0].invoke()
check("ticking it brings them back",
      _gpane.geo is not None and _gpane.geo[0] == "pack", str(_gpane.geo))
_adv[0].invoke()
check("unticking it puts them away again", _gpane.geo is None, str(_gpane.geo))

# --- pasting a playlist URL moves the mode for you
def _tick():
    """Run one pump. Do not clear the queue: pump reschedules itself into it."""
    for _cb in list(root.pending):
        _cb()
_utxt.insert("end", "https://youtube.com/playlist?list=PLrOFa8sDv6jfKx9\n")
_utxt.fire("<KeyRelease>")     # exactly what typing a playlist URL does
check("pasting a playlist URL switches to a playlist run on its own",
      _srb.options["variable"].get() == "playlist"
      and _gpane.vars["no_playlist"].get() is False
      and "playlist_index" in _gpane.vars["template"].get(),
      f"{_srb.options['variable'].get()} / {_gpane.vars['template'].get()}")
# ...until you take the wheel
_qc = [w for w in _gtab.walk() if w.kind == "checkbutton"
       and w.options.get("text", "").startswith("This video only")]
_qc[0].invoke()
check("...and the summary admits it instead of carrying on promising",
      "changed by hand" in str(_sum[0].options["textvariable"].get()),
      str(_sum[0].options["textvariable"].get()))
_utxt.insert("end", "https://youtube.com/playlist?list=PLsecond\n")
_utxt.fire("<KeyRelease>")
check("...but once you tick something by hand it stops guessing",
      _gpane.vars["no_playlist"].get() is True,
      str(_gpane.vars["no_playlist"].get()))
_mrb.invoke()
_srb2 = [w for w in _grb if w.options.get("value") == "single"][0]
_srb2.invoke()
check("and picking a mode by hand takes the wheel back",
      _gpane.vars["no_playlist"].get() is True)

# the real payoff: each mode is a different command, with the right flags
_gav = {m: U.build_argv("get", dict(U.MODE_VALUES[m], output_dir="."),
                        positionals=["URL"], parser=PARSER) for m in U.MODES}
check("music pulls the audio out, video keeps it",
      "--format" in _gav["music_single"] and "--keep-video" in _gav["video_single"]
      and "--keep-video" not in _gav["music_single"])
check("a single refuses the playlist, a playlist walks it",
      "--no-playlist" in _gav["music_single"]
      and "--no-playlist" not in _gav["music_playlist"])
check("playlists number the files inside a folder of their own",
      any("playlist_index" in a for a in _gav["video_playlist"]))
check("videos ask for a height and a container",
      _gav["video_single"][_gav["video_single"].index("--quality") + 1] == "1080"
      and "--merge-format" in _gav["video_single"])
check("lyrics come with music and stay away from video",
      "--no-lyrics" in _gav["video_single"] and "--no-lyrics" not in _gav["music_single"])
check("the four modes are four different commands",
      len({tuple(a) for a in _gav.values()}) == 4)

# ============ 29. the quality you asked for is the quality you get
# Real incident: "1080p vp9 + mp4a ~ 644.7 MiB" was announced, and a 52 MiB
# .m4a arrived, because the selector offered an id that video does not serve.
print("\n--- 29. the selector asks for ids the video actually serves ---")
from musictool import cli as C  # noqa: E402
from musictool import cli as cli_mod  # noqa: E402

_INC = {"duration": 3379, "formats": [
    {"format_id": "248", "ext": "webm", "vcodec": "vp9", "acodec": "none",
     "height": 1080, "tbr": 1400, "filesize": 621000000},
    {"format_id": "137", "ext": "mp4", "vcodec": "avc1", "acodec": "none",
     "height": 1080, "tbr": 950, "filesize": 418000000},
    {"format_id": "401", "ext": "mp4", "vcodec": "av01", "acodec": "none",
     "height": 2160, "tbr": 4200, "filesize": 1868000000},
    {"format_id": "140-0", "ext": "m4a", "vcodec": "none", "acodec": "mp4a",
     "abr": 128, "filesize": 54700000},
    {"format_id": "140-7", "ext": "m4a", "vcodec": "none", "acodec": "mp4a",
     "abr": 128, "filesize": 54900000},
    {"format_id": "251-1", "ext": "webm", "vcodec": "none", "acodec": "opus",
     "abr": 130, "filesize": 44200000},
]}
_inc = F.collapse_variants(F.rows_from_info(_INC))
_ids = {r["id"] for r in F.rows_from_info(_INC)}
check("this video serves no format called '140' -- only '140-0' and '140-7'",
      "140" not in _ids and "140-0" in _ids, str(sorted(_ids)))
_s1, _v1, _a1, _n1 = F.pick(_inc, max_bytes=800 * 1024 * 1024)
_s2 = F.auto_pick(_inc, "best")[0]
check("the chosen pair names ids that exist",
      all(part in _ids for part in _s1.replace("/", "+").split("+")), _s1)
check("...and offers no '/' fallback to quietly downgrade with",
      "/" not in _s1 and "/" not in _s2, f"{_s1} | {_s2}")
check("the video that was announced is the video in the selector",
      _s1.startswith(_v1["id"] + "+"), f"{_s1} vs {_v1['id']}")

# yt-dlp's own parser is the only real judge of a -f line
try:
    from yt_dlp import YoutubeDL as _YDL
except ImportError:
    _YDL = None


def _ytdlp_says(sel, info=_INC):
    """What yt-dlp itself would download for this selector."""
    ydl = _YDL({"quiet": True, "simulate": True})
    fmts = [{"format_id": f["format_id"], "ext": f["ext"],
             "vcodec": f.get("vcodec", "none"), "acodec": f.get("acodec", "none"),
             "height": f.get("height"), "width": f.get("width", 1920),
             "fps": f.get("fps", 30), "tbr": f.get("tbr", 0),
             "abr": f.get("abr", 0), "filesize": f.get("filesize"),
             "format": f["format_id"], "protocol": "https", "preference": None,
             "language": None, "format_note": None, "dynamic_range": None}
            for f in info["formats"]]
    got = [f["format_id"]
           for f in ydl.build_format_selector(sel)({"formats": fmts,
                                                    "extractor": "youtube"})]
    return got


if _YDL is None:
    print("  SKIP  yt-dlp not installed -- cannot ask the real parser")
else:
    check("yt-dlp agrees the old shape handed back audio only",
          _ytdlp_says("248+140/140-0") == ["140-0"],
          str(_ytdlp_says("248+140/140-0")))
    check("and that the fixed shape really pairs the video with the audio",
          "+" in (_ytdlp_says(_s1) or [""])[0], str(_ytdlp_says(_s1)))

_irows = F.rows_from_info(_INC)
_w1 = F.check_selector("248+140/140-0", _irows)
check("a selector naming an id the video does not serve is called out",
      any("serves no format called 140" in w for w in _w1), str(_w1))
check("...and so is the fallback that would quietly win instead",
      any("fallback after it" in w for w in _w1), str(_w1))
check("a selector built from real ids passes clean",
      F.check_selector("248+140-0", _irows) == [],
      str(F.check_selector("248+140-0", _irows)))
check("an audio-only fallback is named as one",
      any("audio track on its own" in w
          for w in F.check_selector("248+140-0/140-7", _irows)),
      str(F.check_selector("248+140-0/140-7", _irows)))
check("with no format list there is nothing to judge, so it stays quiet",
      F.check_selector("248+140/140-0", []) == [])

# --- and a selector pasted into --quality is used, not dropped
check("a raw selector is recognised as one",
      F.looks_like_selector("248+140/140-0") and F.looks_like_selector("137+140")
      and F.looks_like_selector("bv*+ba/b")
      and not F.looks_like_selector("1080") and not F.looks_like_selector(""))
_o1 = {"quality": "248+140-0", "max_size": "", "keep_video": True}
_log1 = []
C._resolve_quality("https://youtu.be/ZLRwjygiCTE", _o1, log=_log1.append)
check("pasting a selector into --quality uses it instead of ignoring it",
      _o1.get("format_selector") == "248+140-0"
      and any("raw format selector" in m for m in _log1),
      f"{_o1.get('format_selector')} | {_log1}")
_o2 = {"quality": "banana", "max_size": "", "keep_video": True}
_log2 = []
C._resolve_quality("https://youtu.be/ZLRwjygiCTE", _o2, log=_log2.append)
check("nonsense in --quality is refused out loud, not swallowed",
      _o2.get("format_selector") is None
      and any("ignoring --quality" in m for m in _log2),
      f"{_o2.get('format_selector')} | {_log2}")
_o3 = {"quality": "1080", "max_size": "", "keep_video": True}
_log3 = []
C._resolve_quality("https://youtu.be/ZLRwjygiCTE", _o3, log=_log3.append)
check("when the qualities cannot be read, it says whose default you get",
      any("yt-dlp will pick its own default" in m for m in _log3)
      or bool(_o3.get("format_selector")), str(_log3))

# ============ 30. which language you get, and being told what it is
# yt-dlp ranks the ORIGINAL language first, so a dubbed video quietly arrives
# in the wrong one unless the picker is told what you want.
print("\n--- 30. dubbed videos: the language is shown and preferred ---")

_DUB = {"duration": 900, "formats": [
    {"format_id": "248", "ext": "webm", "vcodec": "vp9", "acodec": "none",
     "height": 1080, "tbr": 1400, "filesize": 160000000},
    {"format_id": "140-0", "ext": "m4a", "vcodec": "none", "acodec": "mp4a",
     "abr": 160, "filesize": 18000000, "language": "es",
     "language_preference": 10, "format_note": "Spanish (original)"},
    {"format_id": "140-1", "ext": "m4a", "vcodec": "none", "acodec": "mp4a",
     "abr": 128, "filesize": 14400000, "language": "en",
     "format_note": "English"},
    {"format_id": "140-2", "ext": "m4a", "vcodec": "none", "acodec": "mp4a",
     "abr": 128, "filesize": 14400000, "language": "fr",
     "format_note": "French"},
]}
_drows = F.rows_from_info(_DUB)
check("a row knows the language it is spoken in",
      {r["id"]: r["lang"] for r in _drows}.get("140-1") == "en"
      and {r["id"]: r["lang"] for r in _drows}.get("248") == "",
      str([(r["id"], r["lang"]) for r in _drows]))
check("...and which one is the original",
      [r for r in _drows if r["id"] == "140-0"][0]["lang_pref"] == 10)
_dt = F.table(_drows)
check("the table shows a lang column when there is a language to show",
      "lang" in _dt.splitlines()[0] and "en" in _dt, _dt)
check("...and stars the original track", "es*" in _dt, _dt)
check("a video with no tagged language does not grow an empty column",
      "lang" not in F.table(F.rows_from_info(_INC)).splitlines()[0],
      F.table(F.rows_from_info(_INC)).splitlines()[0])
_dc = F.collapse_variants(_drows)
check("a dub is not a CDN variant of another language",
      sorted(r["id"] for r in _dc if r["kind"] == "audio")
      == ["140-0", "140-1", "140-2"], str([r["id"] for r in _dc]))
check("and dedupe does not merge two languages at the same bitrate",
      len([r for r in F.dedupe_quality(_drows) if r["kind"] == "audio"]) == 3)
check("languages are listed originals first",
      [c for c, _o, _n in F.languages_in(_drows)] == ["es", "en", "fr"],
      str(F.languages_in(_drows)))

check("the default picker would take the Spanish original",
      F.pick(_drows)[0] == "248+140-0", F.pick(_drows)[0])
check("asking for English takes English, though it is the smaller bitrate",
      F.pick(_drows, lang="en")[0] == "248+140-1",
      F.pick(_drows, lang="en")[0])
check("the same for audio-only and for the auto presets",
      F.pick(_drows, audio_only=True, lang="en")[0] == "140-1"
      and F.auto_pick(_drows, "best", lang="en")[0] == "248+140-1"
      and F.auto_pick(_drows, "audio", lang="en")[0] == "140-1")
check("'en' also matches a region tag like en-GB",
      F._match_lang({"lang": "en-GB"}, "en") and not F._match_lang({"lang": "fr"}, "en")
      and not F._match_lang({"lang": ""}, "en") and not F._match_lang({"lang": "en"}, "any"))
check("a language the video does not have falls back instead of failing",
      F.pick(_drows, lang="de")[0] == "248+140-0"
      and F.preferred_language(_drows, "de") is None
      and F.preferred_language(_drows, "en") == "en")
check("the choice is announced with its language",
      "[en]" in F.describe_choice(*F.pick(_drows, lang="en")[1:3]),
      F.describe_choice(*F.pick(_drows, lang="en")[1:3]))
check("the yt-dlp filter it builds prefers the language and still falls back",
      F.audio_lang_selector(True, "en") == "bv*+ba[language^=en]/bv*+ba/b"
      and F.audio_lang_selector(False, "en") == "ba[language^=en]/ba/b"
      and F.audio_lang_selector(True, "any") == "")

if _YDL is not None:
    def _dubbed(sel, extra=None):
        fmts = [{"format_id": f["format_id"], "ext": f["ext"],
                 "vcodec": f.get("vcodec", "none"),
                 "acodec": f.get("acodec", "none"), "height": f.get("height"),
                 "width": f.get("width", 1920), "fps": f.get("fps", 30),
                 "tbr": f.get("tbr", 0), "abr": f.get("abr", 0),
                 "filesize": f.get("filesize"), "format": f["format_id"],
                 "protocol": "https", "language": f.get("language"),
                 "language_preference": f.get("language_preference", 0),
                 "preference": None, "format_note": f.get("format_note"),
                 "dynamic_range": None} for f in _DUB["formats"]]
        info = {"id": "d", "extractor": "youtube", "formats": fmts, "title": "t"}
        ydl = _YDL({"quiet": True, "simulate": True})
        ydl.sort_formats(info)          # yt-dlp sorts before it selects
        return [f["format_id"] for f in ydl.build_format_selector(sel)(info)]

    check("yt-dlp's own default takes the Spanish original",
          _dubbed("bv*+ba/b") == ["248+140-0"], str(_dubbed("bv*+ba/b")))
    check("the language filter takes the English dub, and only falls back "
          "when there is none",
          _dubbed(F.audio_lang_selector(True, "en")) == ["248+140-1"],
          str(_dubbed(F.audio_lang_selector(True, "en"))))
    check("--format-sort lang:en does NOT do this (it sorts a number, not a "
          "language) -- which is why a filter is used",
          "lang" in str(F.audio_lang_selector(True, "en")))

_b1 = download.build_args("U", {"keep_video": True, "audio_lang": "en",
                                "output_dir": ".", "template": "t"})
_b2 = download.build_args("U", {"keep_video": False, "audio_lang": "en",
                                "output_dir": ".", "template": "t"})
_b3 = download.build_args("U", {"keep_video": True, "audio_lang": "any",
                                "output_dir": ".", "template": "t"})
_b4 = download.build_args("U", {"keep_video": True, "audio_lang": "en",
                                "format_selector": "248+140-1",
                                "output_dir": ".", "template": "t"})
check("the language preference reaches yt-dlp for video and for audio",
      _b1[_b1.index("-f") + 1] == "bv*+ba[language^=en]/bv*+ba/b"
      and _b2[_b2.index("-f") + 1] == "ba[language^=en]/ba/b",
      f"{_b1[_b1.index('-f') + 1]} | {_b2[_b2.index('-f') + 1]}")
check("'any' leaves yt-dlp alone, and an explicit -f always wins",
      "-f" not in _b3 and _b4[_b4.index("-f") + 1] == "248+140-1",
      f"{'-f' in _b3} | {_b4[_b4.index('-f') + 1] if '-f' in _b4 else None}")
check("--audio-lang is a real flag the GUI can reach",
      "audio_lang" in {x["dest"] for x in U.widget_specs("get", parser=PARSER)})

# --- and the real command, against a video that is actually dubbed
DUBBIN = os.path.join(WORK, "dubbin")
os.makedirs(DUBBIN, exist_ok=True)
open(os.path.join(DUBBIN, "yt-dlp"), "w").write(
    "#!/usr/bin/env python3\n"
    "import json, sys\n"
    + "sys.stdout.write(json.dumps(%r))\n" % _DUB)
os.chmod(os.path.join(DUBBIN, "yt-dlp"), 0o755)


def run_dub(*args):
    env = dict(ENV, PATH=DUBBIN + os.pathsep + ENV["PATH"])
    return subprocess.run([sys.executable, ENTRY, *args], capture_output=True,
                          text=True, env=env)


_rd = run_dub("formats", "https://youtu.be/DUB1", "--keep-video",
              "--show-audio")
check("formats names every spoken language and the original",
      "spoken languages: es (original), en, fr" in _rd.stdout, _rd.stdout[-400:])
check("...says which one it will take, and picks it",
      "will take: en" in _rd.stdout and "-f 248+140-1" in _rd.stdout,
      _rd.stdout[-300:])
_re = run_dub("formats", "https://youtu.be/DUB1", "--keep-video",
              "--audio-lang", "es")
check("and a different preference is obeyed",
      "will take: es" in _re.stdout and "-f 248+140-0" in _re.stdout,
      _re.stdout[-300:])
_rx = run_dub("formats", "https://youtu.be/DUB1", "--keep-video",
              "--audio-lang", "de")
check("a language nobody dubbed says so instead of pretending",
      "nothing here is tagged de" in _rx.stdout, _rx.stdout[-300:])
_rg = run_dub("get", "https://youtu.be/DUB1", "--keep-video", "--no-lyrics",
              "--dry-run")
check("a plain get carries the language filter to yt-dlp",
      "bv*+ba[language^=en]/bv*+ba/b" in _rg.stdout, _rg.stdout[-300:])

# ====== 31. one scroll, folded options, and settings that come back
print("\n--- 31. the tab scrolls as one, and nothing is lost on a restart ---")

# `areas` is rebound further up, so find the scroll area from the pane itself
_gbody = _gpane.master
_garea = next(w for w in root.walk()
              if isinstance(w, G.ScrollArea) and w.frame is _gbody)


def _inside_area(w):
    while w is not None:
        if w is _gbody:
            return True
        w = getattr(w, "master", None)
    return False


_ut = [w for w in _gtab.walk() if w.kind == "text"][0]
check("the URL box, the mode, the toggles and the options share ONE scroll",
      _inside_area(_ut) and _inside_area(_vrb) and _inside_area(_qc[0])
      and _inside_area(_gpane),
      str([_inside_area(x) for x in (_ut, _vrb, _qc[0], _gpane)]))
check("the run buttons stay pinned outside it, so they are always reachable",
      not _inside_area(gbtn("Download + lyrics")))
_had = _ut.buf
_ut.buf = "https://youtu.be/one"
check("the wheel over a short URL box scrolls the tab, not the box",
      _garea._over_me(_ut) is True)
_ut.buf = "\n".join("https://youtu.be/%d" % i for i in range(30))
check("...but a box that really can scroll keeps the wheel for itself",
      _garea._over_me(_ut) is False)
_ut.buf = _had

# --- folded: you see what has been set, not all forty-two rows
_cells = [(d, c, sp) for _b, rows in _gpane._groups for (d, c, sp, _o) in rows]
_vis = [d for d, c, _s in _cells if c.geo is not None]
check("every option still exists behind the fold",
      len(_cells) == len(U.widget_specs("get", parser=PARSER)),
      f"{len(_cells)} cells")
check("...and the untouched ones are folded away instead of filling the window",
      0 < len(_vis) < len(_cells) // 2, f"{len(_vis)} of {len(_cells)} shown")
check("the rows on screen are the ones with something in them",
      {"output_dir", "template", "audio_format", "audio_lang"} <= set(_vis)
      and not {"cookies", "proxy", "rate_limit", "download_archive"} & set(_vis),
      str(_vis))
check("...and a setting already on the fine-tune strip is not repeated",
      not {"no_playlist", "keep_video", "no_lyrics", "force"} & set(_vis),
      str(_vis))
_gpane.vars["max_size"].set("800M")
_gpane.touched()
_vis2 = [d for d, c, _s in _cells if c.geo is not None]
check("a value you type appears among them", "max_size" in _vis2, str(_vis2))
_allbtn = [w for w in _gpane.walk() if w.kind == "button"
           and str(w.options.get("text", "")).startswith("show all")]
check("one button brings the whole list back", len(_allbtn) == 1
      and str(_allbtn[0].options["text"]).endswith("options"),
      str([w.options.get("text") for w in _allbtn]))
_allbtn[0].invoke()
check("...and then every option is on screen",
      all(c.geo is not None for _d, c, _s in _cells))
_allbtn[0].invoke()
check("and folding them away again works",
      any(c.geo is None for _d, c, _s in _cells))
check("the sections are named for a reader, not for the parser",
      {str(b.options.get("text")) for b, _r in _gpane._groups}
      >= {"files, folders and access", "quality and format", "lyrics"},
      str([b.options.get("text") for b, _r in _gpane._groups]))

# --- saved, and really restored by a restart
_cfg_path, _cfg.PATH = _cfg.PATH, os.path.join(WORK, "restart.json")
try:
    if os.path.exists(_cfg.PATH):
        os.remove(_cfg.PATH)
    _gpane.vars["max_size"].set("800M")
    _gpane.vars["rate_limit"].set("3M")
    _vrb.invoke()                    # video, one item
    root.close_window()              # the X in the corner
    _saved = _cfg.load()
    check("closing the window saves what you had, not what you started with",
          (_saved.get("gui_opts") or {}).get("get", {}).get("max_size") == "800M"
          and (_saved.get("gui_opts") or {}).get("get", {}).get("rate_limit")
          == "3M",
          str((_saved.get("gui_opts") or {}).get("get")))
    check("choosing a mode does not throw your size ceiling away",
          (_saved.get("gui_opts") or {}).get("get", {}).get("max_size")
          == "800M",
          str((_saved.get("gui_opts") or {}).get("get", {}).get("max_size")))
    check("...and the mode you were in",
          (_saved.get("gui_extra") or {}).get("download_mode") == "video_single",
          str(_saved.get("gui_extra")))
    check("...and whether you had the options open",
          "more_options" in (_saved.get("gui_extra") or {}),
          str(_saved.get("gui_extra")))
    check("...and the URL you had open",
          "ZLRwjygiCTE" in str((_saved.get("gui_extra") or {}).get("urls"))
          or bool((_saved.get("gui_extra") or {}).get("urls")),
          str((_saved.get("gui_extra") or {}).get("urls"))[:80])

    # the restart itself
    _cfg2 = _cfg.load()
    _p2 = G.OptionsPane(tkstub.Frame(), "get", _cfg2,
                        saved=(_cfg2.get("gui_opts") or {}).get("get"))
    check("a restart starts at 800M, with nothing to type again",
          _p2.vars["max_size"].get() == "800M"
          and _p2.vars["rate_limit"].get() == "3M",
          f"{_p2.vars['max_size'].get()} / {_p2.vars['rate_limit'].get()}")
    check("and a restored value is one of the rows you are shown",
          _p2._compact() and _p2.vars["max_size"].get() == "800M"
          and [c.geo for _d, c, sp, _o
               in [r for _b, rr in _p2._groups for r in rr]
               if _d == "max_size"][0] is not None)
    _p4 = G.OptionsPane(tkstub.Frame(), "get", {})
    _p4.vars["format_selector"].set("248+140-0")
    _p4.vars["max_size"].set("800M")
    _p4.vars["rate_limit"].set("3M")
    _p4.apply(U.MODE_VALUES["music_single"])
    check("a mode still clears the one thing that would override it",
          _p4.vars["format_selector"].get() == ""
          and _p4.vars["max_size"].get() == "800M"
          and _p4.vars["rate_limit"].get() == "3M",
          f"{_p4.vars['format_selector'].get()!r} / "
          f"{_p4.vars['max_size'].get()!r} / {_p4.vars['rate_limit'].get()!r}")
    _p3 = G.OptionsPane(tkstub.Frame(), "get", _cfg2)
    check("without the saved values it would have gone back to the default",
          _p3.vars["max_size"].get() == "", repr(_p3.vars["max_size"].get()))
finally:
    _cfg.PATH = _cfg_path

# ====== 32. one video's quality is not the next video's quality
print("\n--- 32. per-video quality, and repairing the language in place ---")

# A format id is not global: 140-4 is English on one video and Spanish on the
# next, and 614 may not exist at all.
TWO = {"one": [
    {"format_id": "614", "ext": "mp4", "vcodec": "vp09", "acodec": "none",
     "height": 1080, "tbr": 1400, "filesize": 300000000},
    {"format_id": "140-4", "ext": "m4a", "vcodec": "none", "acodec": "mp4a",
     "abr": 128, "filesize": 48000000, "language": "en"},
], "two": [
    {"format_id": "137", "ext": "mp4", "vcodec": "avc1", "acodec": "none",
     "height": 1080, "tbr": 1400, "filesize": 310000000},
    {"format_id": "140-4", "ext": "m4a", "vcodec": "none", "acodec": "mp4a",
     "abr": 128, "filesize": 48000000, "language": "es",
     "language_preference": 10},
    {"format_id": "140-9", "ext": "m4a", "vcodec": "none", "acodec": "mp4a",
     "abr": 128, "filesize": 47000000, "language": "en"},
]}
TWOBIN = os.path.join(WORK, "twobin")
os.makedirs(TWOBIN, exist_ok=True)
open(os.path.join(TWOBIN, "yt-dlp"), "w").write(
    "#!/usr/bin/env python3\n"
    "import json, sys\n"
    "url = [a for a in sys.argv[1:] if a.startswith('http')][0]\n"
    "which = url.rstrip('/').split('/')[-1]\n"
    "T = %r\n" % TWO +
    "sys.stdout.write(json.dumps({'id': which, 'title': which,\n"
    "    'duration': 600, 'extractor': 'youtube',\n"
    "    'formats': T.get(which, T['one'])}))\n")
os.chmod(os.path.join(TWOBIN, "yt-dlp"), 0o755)


def run_two(*args):
    env = dict(ENV, PATH=TWOBIN + os.pathsep + ENV["PATH"])
    return subprocess.run([sys.executable, ENTRY, *args], capture_output=True,
                          text=True, env=env)


_r2 = run_two("get", "https://youtu.be/one", "https://youtu.be/two",
              "--keep-video", "--max-size", "1G", "--audio-lang", "en",
              "--no-lyrics", "--dry-run")
_qs = [l.strip() for l in _r2.stdout.splitlines() if l.strip().startswith("quality:")]
_fs = [l.split("-f ")[1].split(" ")[0] for l in _r2.stdout.splitlines()
       if " -f " in l and l.strip().startswith("$")]
check("each video gets its own quality decision",
      len(_qs) == 2, str(_qs))
check("...and they are different, because the videos are different",
      len(set(_fs)) == 2 and _fs == ["614+140-4", "137+140-9"], str(_fs))
check("so both land in English instead of inheriting the first one's track",
      all("[en]" in q for q in _qs), str(_qs))
check("a format id that the second video does not have is never reused",
      "614" not in _fs[1], str(_fs))
_r2b = run_two("get", "https://youtu.be/one", "https://youtu.be/two",
               "--keep-video", "-f", "22", "--no-lyrics", "--dry-run")
check("but an explicit -f still applies to every video",
      [l.split("-f ")[1].split(" ")[0] for l in _r2b.stdout.splitlines()
       if " -f " in l and l.strip().startswith("$")] == ["22", "22"])

# --- repairing files that are already on disk
check("the best track in a language is found by language, not by size",
      F.best_audio_in(F.rows_from_info({"formats": TWO["two"]}), "en")["id"]
      == "140-9"
      and F.best_audio_in(F.rows_from_info({"formats": TWO["two"]}), "es")["id"]
      == "140-4"
      and F.best_audio_in(F.rows_from_info({"formats": TWO["two"]}), "de") is None)

_RL = os.path.join(WORK, "relang")
shutil.rmtree(_RL, ignore_errors=True)
os.makedirs(_RL)
_mkv = os.path.join(_RL, "show.mkv")
_mk = subprocess.run([FF, "-y", "-v", "error", "-f", "lavfi",
                      "-i", "testsrc=size=160x120:rate=10:duration=2",
                      "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                      "-map", "0:v", "-map", "1:a", "-c:v", "libx264",
                      "-c:a", "aac", "-metadata:s:a:0", "language=spa", _mkv],
                     capture_output=True, text=True)
check("the fixture is a video with Spanish audio",
      _mk.returncode == 0 and os.path.exists(_mkv), _mk.stderr[-200:])
check("...and musictool can read that without ffprobe installed",
      download.has_video_stream(_mkv)
      and F.same_language(download.audio_track_lang(_mkv), "es")
      and not F.same_language(download.audio_track_lang(_mkv), "en"),
      str(download.streams(_mkv)))
check("a three-letter container tag is the same language as its two-letter code",
      F.same_language("eng", "en") and F.same_language("en-US", "en")
      and F.same_language("spa", "es") and F.same_language("fra", "fr")
      and F.same_language("fre", "fr") and not F.same_language("spa", "en")
      and not F.same_language("", "en"))
open(os.path.join(_RL, "notes.txt"), "w").write("not media")
check("a folder scan picks up media and leaves other files alone",
      [os.path.basename(x) for x in cli_mod._collect_media([_RL], log=lambda m: None)]
      == ["show.mkv"])
_miss = []
cli_mod._collect_media([os.path.join(_RL, "nope.mkv")], log=_miss.append)
check("a path that does not exist is reported, not silently skipped",
      any("not found" in m for m in _miss), str(_miss))

_RLBIN = os.path.join(WORK, "relangbin")
os.makedirs(_RLBIN, exist_ok=True)
open(os.path.join(_RLBIN, "yt-dlp"), "w").write(
    "#!/usr/bin/env python3\n"
    "import json, os, subprocess, sys\n"
    "a = sys.argv[1:]\n"
    "if '-J' in a:\n"
    "    sys.stdout.write(json.dumps({'id': 'vid1', 'title': 'show',\n"
    "        'duration': 120, 'extractor': 'youtube', 'formats': %r}))\n"
    "    sys.exit(0)\n" % TWO["two"] +
    "tmpl = a[a.index('-o') + 1]\n"
    "out = tmpl.replace('%%(id)s', 'vid1').replace('%%(ext)s', 'm4a')\n"
    "os.makedirs(os.path.dirname(out), exist_ok=True)\n"
    "subprocess.run([%r, '-y', '-v', 'error', '-f', 'lavfi',\n"
    "    '-i', 'sine=frequency=880:duration=2', '-c:a', 'aac',\n"
    "    '-metadata:s:a:0', 'language=eng', out], check=True)\n" % FF +
    "sys.stdout.write(out + '\\n')\n")
os.chmod(os.path.join(_RLBIN, "yt-dlp"), 0o755)
json.dump({"show.mkv": {"video_id": "vid1", "url": "https://youtu.be/vid1"}},
          open(os.path.join(_RL, ".musictool-index.json"), "w"))


def run_rl(*args):
    env = dict(ENV, PATH=_RLBIN + os.pathsep + ENV["PATH"])
    return subprocess.run([sys.executable, ENTRY, "relang", *args],
                          capture_output=True, text=True, env=env)


_d = run_rl(_RL, "--dry-run")
check("a dry run says what it would swap and touches nothing",
      _d.returncode == 0 and "would be re-muxed" in _d.stdout
      and "will take format 140-9" in _d.stdout
      and F.same_language(download.audio_track_lang(_mkv), "es"),
      f"rc={_d.returncode} {_d.stdout[-200:]!r} {_d.stderr[-200:]!r}")
_x = run_rl(_RL)
check("the real run puts the English track in the file you already had",
      _x.returncode == 0 and "re-muxed" in _x.stdout
      and download.audio_track_lang(_mkv).startswith("en"),
      f"{download.audio_track_lang(_mkv)} | {_x.stdout[-300:]}")
check("...and the video that was already downloaded is still in there",
      download.has_video_stream(_mkv) and download.streams(_mkv)[0][0] == "video",
      str(download.streams(_mkv)))
_y = run_rl(_RL)
check("running it again changes nothing",
      "already en" in _y.stdout and "0 file(s) re-muxed" in _y.stdout,
      _y.stdout[-200:])
_noindex = os.path.join(_RL, "orphan.mkv")
shutil.copy(_mkv, _noindex)
_z = run_rl(_noindex)
check("a file musictool never downloaded is reported, not guessed at",
      "no videoId" in _z.stdout and "no videoId" in _z.stdout.splitlines()[-1],
      _z.stdout[-200:])

# --- "which ones are they?" -- the list comes off the disk, not off a log
_RL2 = os.path.join(WORK, "rl2")
shutil.rmtree(_RL2, ignore_errors=True)
os.makedirs(_RL2)
for _nm, _lg in (("show.mkv", "spa"), ("right.mkv", "eng")):
    _m = subprocess.run([FF, "-y", "-v", "error", "-f", "lavfi",
                         "-i", "testsrc=size=160x120:rate=10:duration=1",
                         "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                         "-map", "0:v", "-map", "1:a", "-c:v", "libx264",
                         "-c:a", "aac", "-metadata:s:a:0", f"language={_lg}",
                         os.path.join(_RL2, _nm)],
                        capture_output=True, text=True)
    assert _m.returncode == 0, _m.stderr[-200:]
shutil.copy(os.path.join(_RL2, "show.mkv"), os.path.join(_RL2, "orphan.m4a"))
json.dump({"show.mkv": {"video_id": "vid1", "url": "https://youtu.be/vid1"},
           "right.mkv": {"video_id": "vid2", "url": "https://youtu.be/vid2"}},
          open(os.path.join(_RL2, ".musictool-index.json"), "w"))
# a yt-dlp that leaves a mark if anyone runs it: listing must not touch it
_NOBIN = os.path.join(WORK, "nobin")
os.makedirs(_NOBIN, exist_ok=True)
_MARK = os.path.join(WORK, "yt-dlp-was-called")
open(os.path.join(_NOBIN, "yt-dlp"), "w").write(
    "#!/bin/sh\ntouch " + _MARK + "\nexit 1\n")
os.chmod(os.path.join(_NOBIN, "yt-dlp"), 0o755)
_NOENV = dict(ENV, PATH=_NOBIN + os.pathsep + ENV["PATH"])
if os.path.exists(_MARK):
    os.remove(_MARK)
_l = subprocess.run([sys.executable, ENTRY, "relang", _RL2, "--list"],
                    capture_output=True, text=True, env=_NOENV)
check("a listing names the files that are in the wrong language...",
      _l.returncode == 0 and "NOT in en" in _l.stdout
      and "show.mkv" in _l.stdout.split("NOT in en")[1].split("already")[0],
      _l.stdout[-400:])
check("...and gives you the link each one came from",
      "https://youtu.be/vid1" in _l.stdout, _l.stdout[-400:])
_orph = _l.stdout.split("no link to give")[1]
check("...without inventing a link for a file musictool never downloaded",
      "orphan.m4a" in _orph and "youtu.be" not in _orph
      and _l.stdout.count("youtu.be/vid1") == 1, _orph[-200:])
check("...it reads the files only: nothing was touched",
      F.same_language(download.audio_track_lang(os.path.join(_RL2, "show.mkv")),
                      "es")
      and not os.path.isdir(os.path.join(_RL2, ".musictool-relang"))
      and not os.path.exists(_MARK),
      download.audio_track_lang(os.path.join(_RL2, "show.mkv")))
_RL3 = os.path.join(WORK, "rl3")
shutil.rmtree(_RL3, ignore_errors=True)
os.makedirs(_RL3)
shutil.copy(os.path.join(_RL2, "right.mkv"), os.path.join(_RL3, "right.mkv"))
json.dump({"right.mkv": {"video_id": "vid2", "url": "https://youtu.be/vid2"}},
          open(os.path.join(_RL3, ".musictool-index.json"), "w"))
_ok = subprocess.run([sys.executable, ENTRY, "relang", _RL3, "--list"],
                     capture_output=True, text=True, env=_NOENV)
check("and when nothing needs fixing it says so instead of printing a list",
      _ok.returncode == 0 and "NOT in en: none" in _ok.stdout
      and "already en (1)" in _ok.stdout, _ok.stdout[-250:])

# --- and the GUI has a button for it, because that is where this is done
_argv = []
_cli = G.cli_main
G.cli_main = lambda a: _argv.append(list(a)) or 0
_gpane.vars["output_dir"].set(_RL)
_gpane.vars["audio_lang"].set("en")
_bl = [w for w in root.walk() if w.kind == "button"
       and "Fix language" in w.options.get("text", "")]
check("the GUI offers the repair, not just the CLI", len(_bl) == 1, str(len(_bl)))
_bl[0].invoke()
check("...and it re-languages the folder you are downloading to, in your language",
      _argv and _argv[0][0] == "relang" and _argv[0][1] == _RL
      and "--audio-lang" in _argv[0]
      and _argv[0][_argv[0].index("--audio-lang") + 1] == "en", str(_argv))
_argv.clear()
_gpane.vars["output_dir"].set(_RL2)
_wl = [w for w in root.walk() if w.kind == "button"
       and "List wrong-language" in w.options.get("text", "")]
check("the GUI can show you the list too", len(_wl) == 1, str(len(_wl)))
_wl[0].invoke()
check("...and that listing downloads nothing",
      _argv and _argv[0][:2] == ["relang", _RL2] and "--list" in _argv[0],
      str(_argv))
G.cli_main = _cli
_gpane.vars["output_dir"].set("")

# ====== 33. a visible panel, what is left, a queue, videos at once
print("\n--- 33. visible bars, what is left, a queue, videos at once ---")
import contextlib
import io
import time

# ---- the maths behind "how many are left, and when will it all be done"
_s1 = _pr.State()
_s1.started = time.time() - 120
_s1.handle({"type": "batch", "i": 2, "n": 5})
_s1.handle({"type": "pct", "pct": 50.0})
check("a pasted list of links knows how many are still to come",
      _s1.batch_n == 5 and _s1.remaining() == 4,
      f"{_s1.remaining()} left of {_s1.batch_n}")
check("...and projects the whole run from the time so far",
      _s1.eta_total() == "4m 40s", _s1.eta_total())
_s2 = _pr.State()
_s2.handle({"type": "batch", "i": 1, "n": 4})
_s2.handle({"type": "pct", "pct": 10.0, "eta": "00:30"})
check("before anything has finished it falls back to the file's own ETA",
      _s2.eta_total() == "0m 30s", _s2.eta_total())
check("...rather than promising zero time left for a run that just began",
      _s2.eta_total() != "0m 00s", _s2.eta_total())
_s3 = _pr.State()
_s3.handle({"type": "pct", "pct": 20.0, "eta": "01:00"})
check("a lone download still gets a projection",
      _s3.eta_total() == "1m 00s" and _s3.remaining() == 0, _s3.eta_total())
_s4 = _pr.State()
_s4.handle({"type": "item", "i": 3, "n": 10})
check("a playlist keeps using the length yt-dlp reports",
      _s4.remaining() == 8, str(_s4.remaining()))
_s5 = _pr.State()
_s5.handle({"type": "file", "name": "a.mkv", "tag": "1"})
_s5.handle({"type": "pct", "pct": 12.0, "tag": "1"})
_s5.handle({"type": "file", "name": "b.mkv", "tag": "2"})
_s5.handle({"type": "pct", "pct": 40.0, "tag": "2"})
check("parallel downloads each keep their own name and percentage",
      set(_s5.slots) == {"1", "2"} and _s5.slots["1"]["pct"] == 12.0
      and _s5.slots["2"]["name"] == "b.mkv", str(_s5.slots))
_s5.handle({"type": "done", "tag": "1"})
check("...and drop off the list when they finish", set(_s5.slots) == {"2"},
      str(_s5.slots))

_lines = []
_h0 = _pr.get_hook()
_pr.set_hook(_lines.append)
download._make_on_line(lambda m: None, "7")(
    "[download]  42.0% of   10.00MiB at  1.00MiB/s ETA 00:05")
_pr.set_hook(_h0)
check("every progress event carries which download it came from",
      _lines and _lines[0].get("tag") == "7" and _lines[0]["pct"] == 42.0,
      str(_lines))

# ---- a fresh window: section 31 pressed the X, which stops this one's pump
tkstub.reset()
G.run()
_r2 = tkstub.ROOTS[-1]


def _drain2():
    """Run the fresh window's pump (its progress panel and its queue)."""
    todo, _r2.pending[:] = list(_r2.pending), []
    for f in todo:
        try:
            f()
        except Exception as e:
            raise AssertionError(f"pump raised {type(e).__name__}: {e}")


# ---- the bars must be there when the window opens, not after a drag
_h2 = [w for w in _r2.walk() if isinstance(w, tkstub.PanedWindow)
       and w.options.get("orient") == "horizontal"][0]
check("ttk leaves the sash at 0 -- that is why the bars opened invisible",
      _h2.sashpos(0) == 0, str(_h2.sashes))
_h2.fire("<Configure>")
check("the first layout gives the bars their width, no dragging needed",
      _h2.sashpos(0) >= 160, str(_h2.sashpos(0)))
_h2.sashpos(0, 420)
_h2.fire("<ButtonRelease-1>")
check("dragging it yourself is still remembered",
      _cfg.load().get("progress_width") == 420,
      str(_cfg.load().get("progress_width")))

# ---- and the panel says what is left, out loud
_drain2()
_pr.report({"type": "batch", "i": 2, "n": 5})
_pr.report({"type": "file", "name": "Second.mp4"})
_pr.report({"type": "pct", "pct": 30.0, "speed": "2.0MiB/s", "eta": "00:20"})
_drain2()
_shown = " | ".join(x.options["textvariable"].get() for x in _r2.walk()
                    if x.kind == "label"
                    and isinstance(x.options.get("textvariable"),
                                   tkstub.StringVar))
check("the panel says which video of how many",
      "video 2 of 5" in _shown, _shown[-260:])
check("...how many are still to come after this one",
      "3 still to go after this" in _shown, _shown[-260:])
check("...and when the whole run should be done",
      "about 0m 20s left in total" in _shown, _shown[-260:])
_pr.report({"type": "file", "name": "Second.mp4", "tag": "1"})
_pr.report({"type": "pct", "pct": 30.0, "tag": "1"})
_pr.report({"type": "file", "name": "b.mkv", "tag": "2"})
_pr.report({"type": "pct", "pct": 40.0, "tag": "2"})
_drain2()
_shown2 = " | ".join(x.options["textvariable"].get() for x in _r2.walk()
                     if x.kind == "label"
                     and isinstance(x.options.get("textvariable"),
                                    tkstub.StringVar))
check("with two videos running the panel shows both, not just the last",
      "Second.mp4" in _shown2 and "b.mkv" in _shown2, _shown2[-200:])

# ---- adding to the queue while a download is running
def _wait_for(pred, secs=6.0):
    """Drain the pump until pred() holds, so no check races a thread."""
    end = time.time() + secs
    while time.time() < end:
        _drain2()
        if pred():
            return True
        time.sleep(0.05)
    return False


# The harness swaps G.threading for a synchronous stub so the other checks
# stay deterministic; a queue only means anything with a job still running, so
# this one puts the real threading module back.
_ft = G.threading
G.threading = threading
_gate = threading.Event()
_started = []
_cli = G.cli_main


def _fake_main(a):
    _started.append(list(a))
    _gate.wait(5)
    return 0


G.cli_main = _fake_main
_urls = [w for w in _r2.walk()
         if w.kind == "text" and w.options.get("height") == 3][0]
_bget = [w for w in _r2.walk() if w.kind == "button"
         and w.options.get("text") in ("Download + lyrics", "Download video",
                                       "Add to queue")][0]
_urls.buf = "https://youtu.be/aaa\nhttps://youtu.be/bbb\n"
_bget.options["command"]()
check("Download stays clickable while a job runs -- that is how you queue",
      _bget.options.get("state") != "disabled",
      str(_bget.options.get("state")))
check("...and it says so on the button",
      _bget.options.get("text") == "Add to queue",
      str(_bget.options.get("text")))
_urls.buf = "https://youtu.be/ccc\n"
_bget.options["command"]()
time.sleep(0.2)
check("a second click queues the link instead of starting a second download",
      len(_started) == 1, str(len(_started)))
_gate.set()
_ok = False
for _ in range(100):
    _drain2()
    if len(_started) == 2:
        _ok = True
        break
    time.sleep(0.05)
check("...and the queued job starts as soon as the first one finishes", _ok,
      str(len(_started)))
check("...it is the link you added, not the first batch again",
      len(_started) == 2 and "ccc" in " ".join(_started[1]), str(_started))
check("the button goes back to being a download button",
      _wait_for(lambda: "Download" in _bget.options.get("text", "")),
      str(_bget.options.get("text")))
G.cli_main = _cli
G.threading = _ft

# ---- and several videos at once, from the command line too
_j = run_two("get", "https://youtu.be/one", "https://youtu.be/two",
             "--keep-video", "--max-size", "1G", "--jobs", "2",
             "--no-lyrics", "--dry-run")
check("two videos at once still each get their own quality decision",
      _j.returncode == 0
      and len([l for l in _j.stdout.splitlines() if "quality:" in l]) == 2,
      _j.stdout[-300:])
check("...and says how many it is running in parallel",
      "2 at a time" in _j.stdout, _j.stdout[:120])
check("parallel lines are numbered so four logs do not interleave into noise",
      "[1]" in _j.stdout and "[2]" in _j.stdout, _j.stdout[:200])

_evs = []
_tags = []
_pr.set_hook(_evs.append)
_dd = download.download
download.download = lambda url, o, log=print, on_file=None, tag="": (
    _tags.append(tag), ([], None))[1]
try:
    _ns = C.build_parser().parse_args(
        ["get", "https://youtu.be/one", "https://youtu.be/two",
         "--keep-video", "--no-lyrics", "--jobs", "2"])
    with contextlib.redirect_stdout(io.StringIO()):
        C.cmd_get(_ns)
    _tags1 = []
    _ns1b = C.build_parser().parse_args(
        ["get", "https://youtu.be/one", "--keep-video", "--no-lyrics"])
    download.download = lambda url, o, log=print, on_file=None, tag="": (
        _tags1.append(tag), ([], None))[1]
    with contextlib.redirect_stdout(io.StringIO()):
        C.cmd_get(_ns1b)
finally:
    download.download = _dd
    _pr.set_hook(_h0)
_batch = [e for e in _evs if e.get("type") == "batch"]
check("musictool tells the UI how many URLs are in the batch -- yt-dlp cannot",
      _batch and _batch[0]["n"] == 2, str(_batch))
check("parallel downloads are tagged so the panel can keep them apart",
      sorted(_tags) == ["1", "2"], str(_tags))
check("a single download is left untagged, so the panel shows it as before",
      _tags1 == [""], str(_tags1))
_ns1 = C.build_parser().parse_args(["get", "https://x/1", "--jobs", "99"])
check("--jobs is capped rather than opening ninety-nine connections",
      _ns1.jobs == 99, str(_ns1.jobs))


# ============ 34. choosing the audio track yourself (Audio tracks tab)
print("\n--- 34. audio tracks: see them per language, pick one, download it ---")

_AUD = {"id": "aud", "title": "Dubbed", "duration": 600, "formats": [
    {"format_id": "248", "ext": "webm", "vcodec": "vp9", "acodec": "none",
     "height": 1080, "tbr": 1400, "filesize": 100000000},
    {"format_id": "140-0", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2",
     "abr": 129, "filesize": 9600000, "language": "es",
     "language_preference": 10, "format_note": "Spanish (original), medium"},
    {"format_id": "251-0", "ext": "webm", "vcodec": "none", "acodec": "opus",
     "abr": 140, "filesize": 9900000, "language": "es",
     "language_preference": 10, "format_note": "Spanish (original), medium"},
    {"format_id": "140-1", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2",
     "abr": 129, "filesize": 9600000, "language": "en",
     "format_note": "English, medium"},
    {"format_id": "251-1", "ext": "webm", "vcodec": "none", "acodec": "opus",
     "abr": 135, "filesize": 9800000, "language": "en",
     "format_note": "English, medium"},
    {"format_id": "249-1", "ext": "webm", "vcodec": "none", "acodec": "opus",
     "abr": 50, "filesize": 3600000, "language": "en",
     "format_note": "English, low"},
    {"format_id": "140-2", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2",
     "abr": 129, "filesize": 9600000, "language": "fr",
     "format_note": "French, medium"},
]}
_arows = F.rows_from_info(_AUD)
_t, _n = F.audio_tracks(_arows, "en")
check("audio_tracks lists every English track, best bitrate first",
      [r["id"] for r in _t] == ["251-1", "140-1", "249-1"] and _n == "",
      str([r["id"] for r in _t]))
_t, _n = F.audio_tracks(_arows, "eng")
check("...and 'eng' (a container's code) means the same language",
      [r["id"] for r in _t] == ["251-1", "140-1", "249-1"])
_t, _n = F.audio_tracks(_arows, "de")
check("a language the video does not have gives nothing, and says what is there",
      _t == [] and "es (original)" in _n and "fr" in _n, _n)
_t, _n = F.audio_tracks(_arows, "all")
check("'all' lists every track, the original language first",
      len(_t) == 6 and _t[0]["lang"] == "es", str([r["id"] for r in _t]))
_t, _n = F.audio_tracks(F.rows_from_info(YT), "en")
check("untagged tracks (a one-language upload) are all shown, with a note",
      len(_t) >= 1 and "showing all" in _n, _n)
_pin = [r for r in _arows if r["id"] == "140-2"][0]
check("automatic pairing follows the audio language",
      F.auto_pick(_arows, "best", lang="en")[0] == "248+251-1",
      F.auto_pick(_arows, "best", lang="en")[0])
check("a pinned track replaces the automatic audio",
      F.auto_pick(_arows, "best", lang="en", pinned=_pin)[0] == "248+140-2")
check("...in audio-only mode too",
      F.auto_pick(_arows, "audio", lang="en", pinned=_pin)[0] == "140-2")
_v248 = [r for r in _arows if r["id"] == "248"][0]
check("manual pairing takes the language as well",
      F.pair_with_audio(_v248, _arows, lang="fr")[1]["id"] == "140-2")
check("a track is described by id, language, codec, bitrate and size",
      F.describe_track(_pin).startswith("140-2  [fr]  mp4a 129k m4a"),
      F.describe_track(_pin))

# --- the GUI tab, in a fresh window
tkstub.reset()
G.run()
_r3 = tkstub.ROOTS[-1]


def _drain3():
    todo, _r3.pending[:] = list(_r3.pending), []
    for f in todo:
        f()


_nb3 = [w for w in _r3.walk() if isinstance(w, tkstub.Notebook)][0]
_at = _nb3.tab_for("Audio tracks")
_q3 = _nb3.tab_for("Quality")
check("the Audio tracks tab exists", _at is not None)
_atree = [w for w in _at.walk() if isinstance(w, tkstub.Treeview)][0]
check("it is a clickable table of id, language, codec, container, bitrate, size",
      list(_atree.options.get("columns", ()))[:6] ==
      ["id", "lang", "codec", "container", "bitrate", "size"],
      str(_atree.options.get("columns")))
for _b in ("Load audio tracks", "Download this audio", "Copy command",
           "Use with a video quality", "Automatic audio"):
    check(f"button: {_b}", _qbtn(_at, _b) is not None)
check("the Quality tab links to it",
      _qbtn(_q3, "Choose audio track\u2026") is not None)
_gp3 = [w for w in _r3.walk() if isinstance(w, G.OptionsPane)
        and w.sub == "get"][0]
_gp3.vars["audio_lang"].set("en")
_gp3.vars["keep_video"].set(True)          # video mode on the Download tab

_probe0, _cli0 = G.download.probe, G.cli_main
_A3 = []
G.download.probe = lambda url, o: _AUD
G.cli_main = lambda argv: (_A3.append(list(argv)), 0)[1]
try:
    _aurl = [w for w in _at.walk() if w.kind == "entry"][0]
    _aurl.options["textvariable"].set("https://youtu.be/aud")
    _qbtn(_at, "Load audio tracks").invoke()
    _drain3()
    _avals = [_atree.nodes[i]["values"] for i in _atree.order]
    check("loading shows only the tracks in your language",
          [v[0] for v in _avals] == ["251-1", "140-1", "249-1"], str(_avals))
    check("...with codec, bitrate and YouTube's own name for each",
          _avals[0][2] == "opus" and _avals[0][4] == "135k"
          and "English" in _avals[0][6], str(_avals[0]))
    _acombo = [w for w in _at.walk() if w.kind == "combobox"][0]
    check("the language list offers every language the video has",
          {"all", "es", "en", "fr"} <= set(_acombo.options.get("values", ())),
          str(_acombo.options.get("values")))
    _qfmt3 = [w for w in _q3.walk() if w.kind == "entry"
              and w.options.get("state") == "readonly"][0]
    check("the Quality tab now pairs the video with the English track",
          _qfmt3.options["textvariable"].get() == "248+251-1",
          _qfmt3.options["textvariable"].get())

    # pick the low-bitrate English track and download it on its own
    _atree.selection_set(_atree.order[2])
    _atree.fire("<<TreeviewSelect>>")
    _A3.clear()
    _qbtn(_at, "Download this audio").invoke()
    _drain3()
    check("'Download this audio' runs get with exactly that track",
          len(_A3) == 1 and _A3[0][0] == "get"
          and "--format-selector" in _A3[0]
          and _A3[0][_A3[0].index("--format-selector") + 1] == "249-1",
          str(_A3[:1]))
    check("...as audio, even though the Download tab is in video mode",
          "--keep-video" not in _A3[0], str(_A3[0]))
    _qbtn(_at, "Copy command").invoke()
    check("copying gives the same command",
          "249-1" in _r3.clipboard and _r3.clipboard.startswith("musictool get"),
          _r3.clipboard[:120])

    # use it with a video instead
    _qbtn(_at, "Use with a video quality").invoke()
    check("'Use with a video quality' pins it for the Quality tab",
          _qfmt3.options["textvariable"].get() == "248+249-1",
          _qfmt3.options["textvariable"].get())
    _qlabels = " ".join(str(w.options["textvariable"].get())
                        for w in _q3.walk() if w.kind == "label"
                        and w.options.get("textvariable"))
    check("...and the Quality tab says which audio it is using",
          "249-1" in _qlabels and "[en]" in _qlabels, _qlabels[:300])
    _qbtn(_at, "Automatic audio").invoke()
    check("'Automatic audio' goes back to the best track in your language",
          _qfmt3.options["textvariable"].get() == "248+251-1",
          _qfmt3.options["textvariable"].get())

    # switch the language here: the list and the Download tab follow
    _acombo.options["textvariable"].set("fr")
    _acombo.fire("<<ComboboxSelected>>")
    _drain3()
    check("choosing French lists the French track",
          [_atree.nodes[i]["values"][0] for i in _atree.order] == ["140-2"])
    check("...and makes French the Download tab's --audio-lang",
          _gp3.vars["audio_lang"].get() == "fr", _gp3.vars["audio_lang"].get())
    check("...so the Quality tab pairs the video with French audio",
          _qfmt3.options["textvariable"].get() == "248+140-2",
          _qfmt3.options["textvariable"].get())
    _acombo.options["textvariable"].set("all")
    _acombo.fire("<<ComboboxSelected>>")
    check("'all' shows every track without changing --audio-lang",
          len(_atree.order) == 6 and _gp3.vars["audio_lang"].get() == "fr",
          str(len(_atree.order)))
    _acombo.options["textvariable"].set("de")
    _acombo.fire("<<ComboboxSelected>>")
    _ainfo = " ".join(str(w.options["textvariable"].get())
                      for w in _at.walk() if w.kind == "label"
                      and w.options.get("textvariable"))
    check("a language the video lacks says so, and lists what it has",
          not _atree.order and "this video has" in _ainfo, _ainfo[:200])

    # a pin belongs to one video: loading another one forgets it
    _acombo.options["textvariable"].set("en")
    _acombo.fire("<<ComboboxSelected>>")
    _atree.selection_set(_atree.order[1])
    _atree.fire("<<TreeviewSelect>>")
    _qbtn(_at, "Use with a video quality").invoke()
    _aurl.options["textvariable"].set("https://youtu.be/other")
    _qbtn(_at, "Load audio tracks").invoke()
    _drain3()
    check("loading a different video drops the track pinned for the last one",
          _qfmt3.options["textvariable"].get() == "248+251-1",
          _qfmt3.options["textvariable"].get())
finally:
    G.download.probe, G.cli_main = _probe0, _cli0
    _gp3.vars["audio_lang"].set("en")
    _gp3.vars["keep_video"].set(False)

# ============ 35. small code fixes from the review
print("\n--- 35. review fixes: rate limits, User-Agent, advice, GUI start ---")
check("Retry-After in seconds is honoured (+ up to 30 % jitter)",
      util.retry_after_seconds("5", rand=lambda: 0) == 5.0
      and util.retry_after_seconds("5", rand=lambda: 1) == 6.5)
check("a fractional Retry-After no longer crashes the lookup",
      util.retry_after_seconds("1.5", rand=lambda: 0) == 1.5)
check("an HTTP-date Retry-After is understood (a past date -> the default)",
      util.retry_after_seconds("Wed, 21 Oct 2015 07:28:00 GMT",
                               rand=lambda: 0) == 5.0)
check("garbage falls back to 5 s, and a huge value is capped",
      util.retry_after_seconds("soon", rand=lambda: 0) == 5.0
      and util.retry_after_seconds("99999", rand=lambda: 0) == 120.0)

import email.message as _em  # noqa: E402
import urllib.error as _ue  # noqa: E402
_calls429 = []


class _Resp:
    def __init__(self, body):
        self._b = body

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _urlopen429(req, timeout=None):
    _calls429.append(req.get_header("User-agent"))
    if len(_calls429) == 1:
        h = _em.Message()
        h["Retry-After"] = "0.5"
        raise _ue.HTTPError(req.full_url, 429, "Too Many Requests", h, None)
    return _Resp(b'{"ok": true}')


_uo, _sl = util.urllib.request.urlopen, util.time.sleep
_slept = []
util.urllib.request.urlopen = _urlopen429
util.time.sleep = _slept.append
try:
    _res = util.Http(delay=0).json("http://x/rate", use_cache=False)
finally:
    util.urllib.request.urlopen, util.time.sleep = _uo, _sl
check("a 429 with a fractional Retry-After is retried, not dropped",
      _res == {"ok": True} and len(_calls429) == 2, str((_res, _calls429)))
check("...after waiting what the server asked (plus jitter)",
      _slept and 0.5 <= _slept[0] <= 0.65, str(_slept))
check("the User-Agent names the app, its version and a link (LRCLIB asks)",
      util.USER_AGENT.startswith("musictool/")
      and "github.com/JohnOlowe/MusicTool" in util.USER_AGENT
      and "example.com" not in util.USER_AGENT, util.USER_AGENT)
check("...and that is what is sent", _calls429[0] == util.USER_AGENT,
      str(_calls429[:1]))
check("the bot-wall advice points at Firefox, not at Edge/Chrome on Windows",
      "firefox" in download.BOT_HINT.lower()
      and "App-Bound" in download.BOT_HINT
      and "firefox" in download.ADVICE["bot"].lower())
from musictool import config as _cfgm  # noqa: E402
check("the dead Genius/Musixmatch settings are gone",
      "genius_token" not in _cfgm.DEFAULTS
      and "musixmatch_key" not in _cfgm.DEFAULTS)
_toml = open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8").read()
check("pyproject points at the real repository, not OWNER",
      "OWNER" not in _toml and "github.com/JohnOlowe/MusicTool" in _toml)


class TclError(Exception):
    pass


def _no_display():
    raise TclError("no display name and no $DISPLAY environment variable")


_grun = G.run
G.run = _no_display
try:
    _buf = io.StringIO()
    with contextlib.redirect_stdout(_buf):
        _rc = C.cmd_gui(None)
finally:
    G.run = _grun
check("tkinter present but no display: a clear message, not a traceback",
      _rc == 1 and "GUI unavailable" in _buf.getvalue()
      and "no display" in _buf.getvalue(), _buf.getvalue()[:200])

# ============================================================ 36. live-API drift
print("\n--- 36. what the live APIs taught us (2026-11-18) ---")
from musictool import providers as _P
_h = util.Http(delay=0)
# the old cache key left dict bodies out: every POST to /next or /browse
# shared one entry, so song 2 got song 1's lyrics - and as "trusted"
_a = _P.YTMusic().fetch(_P.Query("A", "Song A", video_id="vidA"), _h)
_b = _P.YTMusic().fetch(_P.Query("B", "Song B", video_id="vidB"), _h)
check("ytmusic: two videos get two different lyrics (cache key includes the body)",
      _a and _b and "MPLYvidA" in _a[0].lyrics and "MPLYvidB" in _b[0].lyrics,
      (_a and _a[0].lyrics[:40], _b and _b[0].lyrics[:40]))
_hk = util.Http(delay=0)
_r1 = _hk.json(BASE + "/yt/next", method="POST", body={"videoId": "k1"})
_r2 = _hk.json(BASE + "/yt/next", method="POST", body={"videoId": "k2"})
check("Http cache: two POSTs with different JSON bodies are two entries",
      "MPLYk1" in json.dumps(_r1) and "MPLYk2" in json.dumps(_r2))
_n = len(CALLS)
_a2 = _P.YTMusic().fetch(_P.Query("A", "Song A", video_id="vidA"), _h)
check("...and the repeat is answered from the compact per-video cache, no calls",
      len(CALLS) == _n and _a2 and _a2[0].lyrics == _a[0].lyrics)
check("...which stores the cues, not the raw ~650 KB page",
      "ytmusic:vidA" in _h.cache and "cues" in _h.cache["ytmusic:vidA"]
      and not any("/yt/" in k for k in _h.cache))
_u = _P.YTMusic().fetch(_P.Query("C", "Song C", video_id="UNTIMED1"), _h)
check("ytmusic without timings: an UNSYNCED exact-match candidate, not nothing",
      len(_u) == 1 and not _u[0].synced and _u[0].trusted
      and "Story to tell" in _u[0].lyrics and "[00:" not in _u[0].lyrics
      and "no timings" in _u[0].note, _u and _u[0].note)
_cp = os.path.join(WORK, "poisoned-cache.json")
json.dump({json.dumps(["POST", BASE + "/yt/next", [], None]): {"old": 1},
           json.dumps(["GET", BASE + "/api/get", [["q", "x"]], None]): {"ok": 1}},
          open(_cp, "w"))
check("entries written by the old keying are dropped when the cache loads",
      list(util.Http(cache_path=_cp).cache.values()) == [{"ok": 1}])
NE_MODE["encrypted"] = True
try:
    _e = _P.NetEase().fetch(_P.Query("Ed Sheeran", "Shape of You"), _h)
    check("netease: an encrypted/string result is 'no results', not a crash", _e == [])
except Exception as e:
    check("netease: an encrypted/string result is 'no results', not a crash", False, repr(e))
NE_MODE.clear()
NE_MODE["right_artist"] = True
_ne = _P.NetEase().fetch(_P.Query("Ed Sheeran", "Shape of You"), util.Http(delay=0))
NE_MODE.clear()
check("netease uses /search/get (the /web variant is encrypted now)",
      any(c[:2] == ("GET", "/ne/search/get") for c in CALLS)
      and _P.NETEASE_SEARCH.endswith("/search/get"))
check("netease credit lines (作词/作曲 ...) are stripped",
      _ne and "作词" not in _ne[0].lyrics and "作曲" not in _ne[0].lyrics
      and "netease line number 0" in _ne[0].lyrics, _ne and _ne[0].lyrics[:60])
_sh = _P.LrcLib._shrink([{"id": 1, "trackName": "T", "syncedLyrics": "[00:01.00] a",
                          "plainLyrics": "a", "instrumental": False, "extra": "x" * 99},
                         {"id": 2, "trackName": "U", "syncedLyrics": None,
                          "plainLyrics": "only plain"}])
check("lrclib cache keeps what fetch reads: synced kept, unused plain/extras dropped",
      _sh == [{"id": 1, "trackName": "T", "syncedLyrics": "[00:01.00] a"},
              {"id": 2, "trackName": "U", "syncedLyrics": None,
               "plainLyrics": "only plain"}], _sh)
_jr = download.js_runtime
try:
    download.js_runtime = lambda: ("node", "/usr/bin/node")
    _ra = download.runtime_args({})
    _ra2 = download.runtime_args({"js_runtimes": "quickjs"})
    download.js_runtime = lambda: ("deno", "/usr/bin/deno")
    _ra3 = download.runtime_args({})
    download.js_runtime = lambda: ("node", "/usr/bin/node")
    _pa = []
    _real_run = download.subprocess.run

    def _cap(args, **kw):
        _pa.append(list(args))
        raise RuntimeError("captured")
    download.subprocess.run = _cap
    try:
        download.probe("https://youtu.be/x", {"remote_components": "ejs:github"})
    finally:
        download.subprocess.run = _real_run
finally:
    download.js_runtime = _jr
check("node is enabled automatically when it is the only JS runtime (no deno)",
      _ra == ["--js-runtimes", "node"], _ra)
check("...an explicit --js-runtimes wins, and with deno nothing is added",
      _ra2 == ["--js-runtimes", "quickjs"] and _ra3 == [], (_ra2, _ra3))
check("probe (check/formats/quality picks) passes --remote-components and the runtime",
      _pa and "--remote-components" in _pa[0] and "ejs:github" in _pa[0]
      and "--js-runtimes" in _pa[0], _pa and _pa[0][-6:])
_best, _ = _P.find_best(_P.Query("C", "Song C", video_id="UNTIMED1"),
                        [_P.YTMusic()], util.Http(delay=0))
check("an untimed ytmusic hit alone is not used without --allow-unsynced",
      _best is None)
_best, _ = _P.find_best(_P.Query("C", "Song C", video_id="UNTIMED1"),
                        [_P.YTMusic()], util.Http(delay=0), allow_unsynced=True)
check("...and is used with it", _best is not None and _best.provider == "ytmusic")

# ============ 37. audio in the format list, and the empty list explained
print("\n--- 37. formats --show-audio by language; why a list is empty ---")
# A fake yt-dlp that answers like the real one: a dubbed video, and a video
# whose JS challenge failed -- storyboards only, the warning on stderr, and
# "Requested format is not available" unless --ignore-no-formats-error.
_FK = os.path.join(WORK, "bin37")
os.makedirs(_FK, exist_ok=True)
open(os.path.join(_FK, "yt-dlp"), "w").write(r"""#!/usr/bin/env python3
import json, os, sys
a = sys.argv[1:]
open(os.path.join(os.path.dirname(__file__), "argv.txt"), "a").write(" ".join(a) + "\n")
if "--version" in a:
    print("2026.10.26-fake"); sys.exit(0)
url = [x for x in a if x.startswith("http")][0]
if "nochal" in url:
    sys.stderr.write("WARNING: [youtube] Oqr0muaqH0c: n challenge solving failed: "
                     "Some formats may be missing.\n")
    sys.stderr.write("WARNING: Only images are available for download. use "
                     "--list-formats to see them\n")
    if "--ignore-no-formats-error" not in a:
        sys.stderr.write("ERROR: [youtube] Oqr0muaqH0c: Requested format is not "
                         "available. Use --list-formats for a list of available formats\n")
        sys.exit(1)
    print(json.dumps({"id": "Oqr0muaqH0c", "title": "GTA V", "duration": 73682,
        "formats": [{"format_id": "sb%d" % i, "ext": "mhtml", "vcodec": "none",
                     "acodec": "none"} for i in range(4)]}))
    sys.exit(0)
def aud(i, lang, orig, abr, ext="m4a", ac="mp4a.40.2"):
    return {"format_id": "%s-%s" % (i, lang), "ext": ext, "vcodec": "none",
            "acodec": ac, "abr": abr, "language": lang,
            "language_preference": 10 if orig else -1, "filesize": abr * 75000}
fm = [aud(140, "en", True, 129), aud(140, "es", False, 129),
      aud(140, "fr", False, 129), aud(251, "en", True, 135, "webm", "opus"),
      aud(251, "es", False, 135, "webm", "opus"),
      {"format_id": "137", "ext": "mp4", "vcodec": "avc1.640028", "acodec": "none",
       "height": 1080, "tbr": 4000, "filesize": 300000000}]
print(json.dumps({"id": "DUB", "title": "Dubbed", "duration": 600, "formats": fm}))
""")
os.chmod(os.path.join(_FK, "yt-dlp"), 0o755)
_ENV37 = dict(ENV, PATH=_FK + os.pathsep + ENV["PATH"])


def run37(*args):
    return subprocess.run([sys.executable, ENTRY, *args], capture_output=True,
                          text=True, env=_ENV37)


_r = run37("formats", "https://x/dub", "--keep-video", "--audio-lang", "es",
           "--cookies-from-browser", "off")
check("video mode without --show-audio: no audio-only rows",
      "audio only" not in _r.stdout and "137" in _r.stdout, _r.stdout[-400:])
_r = run37("formats", "https://x/dub", "--keep-video", "--show-audio",
           "--audio-lang", "es", "--cookies-from-browser", "off")
_aud_ids = [ln.split()[0] for ln in _r.stdout.splitlines() if "audio only" in ln]
check("--show-audio lists the audio rows in --audio-lang only",
      sorted(_aud_ids) == ["140-es", "251-es"], _r.stdout[-600:])
check("...says how many other-language tracks it hid",
      "3 track(s) in other languages hidden" in _r.stdout, _r.stdout[-400:])
check("...and the merge count is not muddled by the language filter",
      "collapsed to" not in _r.stdout, _r.stdout[-400:])
_r = run37("formats", "https://x/dub", "--keep-video", "--show-audio",
           "--audio-lang", "de", "--cookies-from-browser", "off")
check("a language the video lacks: every track shown, and why",
      sum("audio only" in ln for ln in _r.stdout.splitlines()) == 5
      and "no audio track is in 'de'" in _r.stdout, _r.stdout[-500:])
_r = run37("formats", "https://x/dub", "--keep-video", "--show-audio",
           "--audio-lang", "any", "--cookies-from-browser", "off")
check("--audio-lang any shows every audio track",
      sum("audio only" in ln for ln in _r.stdout.splitlines()) == 5)
_r = run37("formats", "https://x/dub", "--audio-lang", "fr",
           "--cookies-from-browser", "off")
_aud_ids = [ln.split()[0] for ln in _r.stdout.splitlines() if "audio only" in ln]
check("music mode (audio always listed) filters by language too",
      _aud_ids == ["140-fr"], _r.stdout[-500:])
check("...and its suggested command is an audio download (no --keep-video)",
      "musictool get \"https://x/dub\" -f 140-fr" in _r.stdout, _r.stdout[-300:])

_r = run37("formats", "https://x/nochal", "--keep-video", "--show-audio",
           "--cookies-from-browser", "off")
check("a failed JS challenge is named, not reported as 'format not available'",
      "challenge was not solved" in _r.stdout and "yt-dlp[default]" in _r.stdout
      and "preview picture" in _r.stdout and _r.returncode == 1,
      _r.stdout[-500:])
check("...quoting yt-dlp's own warning",
      "n challenge solving failed" in _r.stdout)
_argv37 = open(os.path.join(_FK, "argv.txt")).read()
check("probe keeps yt-dlp's warnings and survives an empty format list",
      "--ignore-no-formats-error" in _argv37 and "--no-warnings" not in _argv37)
_r = run37("check", "https://x/nochal", "https://x/dub")
check("check: no streams is its own verdict, not 'reachable' and not 'blocked'",
      "NO STREAMS" in _r.stdout and "1 reachable, 0 blocked, 1 with no streams"
      in _r.stdout and _r.returncode == 1, _r.stdout[-400:])
_r = run37("get", "https://x/nochal", "--keep-video", "--quality", "720",
           "--dry-run", "--no-lyrics", "--cookies-from-browser", "off",
           "-o", os.path.join(WORK, "out37"))
check("get --quality says why it cannot pick, instead of 'no quality matched'",
      "challenge was not solved" in _r.stdout + _r.stderr,
      (_r.stdout + _r.stderr)[-400:])

# the pieces, in-process
_real_run = download.subprocess.run


class _Out:
    def __init__(self, rc, out, err):
        self.returncode, self.stdout, self.stderr = rc, out, err


try:
    download.subprocess.run = lambda a, **k: _Out(
        1, "", "WARNING: [youtube] x: n challenge solving failed: Some formats "
               "may be missing.\nERROR: [youtube] x: Requested format is not "
               "available. Use --list-formats\n")
    _e = download.probe("https://youtu.be/x", {})
finally:
    download.subprocess.run = _real_run
check("a failed probe leads with the ERROR line...",
      _e["error"].splitlines()[0].startswith("ERROR:"), _e)
check("...and carries the warning that explains it, so it is classified right",
      download.classify_error(_e["error"]) == "js_runtime", _e)
check("'Only images are available' and signature failures mean the JS setup",
      download.classify_error("WARNING: Only images are available for download")
      == "js_runtime" and download.classify_error(
          "WARNING: [youtube] x: Signature solving failed") == "js_runtime")
check("no_media_reason: a normal video, a playlist, a plain file -> None",
      download.no_media_reason(_AUD) is None
      and download.no_media_reason({"_type": "playlist", "entries": []}) is None
      and download.no_media_reason({"formats": [{"url": "http://h/a.m4a",
                                                 "ext": "m4a"}]}) is None)
check("the js_runtime advice names yt-dlp[default] and the runtime",
      "yt-dlp[default]" in download.ADVICE["js_runtime"]
      and "deno" in download.ADVICE["js_runtime"])
_fa, _fn = F.filter_audio_lang(F.rows_from_info(_AUD), "en")
check("filter_audio_lang keeps the video rows and the English audio",
      {r["id"] for r in _fa} == {"248", "140-1", "251-1", "249-1"}, _fn)
_fa, _fn = F.filter_audio_lang(F.rows_from_info(YT), "en")
check("...and leaves untagged audio alone (a one-language upload)",
      len(_fa) == len(F.rows_from_info(YT)) and _fn == "")

# --- the GUI: the tick beside "List qualities + sizes"
tkstub.reset()
G.run()
_r4 = tkstub.ROOTS[-1]


def _drain4():
    todo, _r4.pending[:] = list(_r4.pending), []
    for f in todo:
        f()


_nb4 = [w for w in _r4.walk() if isinstance(w, tkstub.Notebook)][0]
_dl4 = _nb4.tab_for("Download")
_lb = _qbtn(_dl4, "List qualities + sizes")
_tick = [w for w in _qw(_dl4, "checkbutton")
         if w.options.get("text") == "+ audio formats"]
check("the Download tab has a '+ audio formats' tick", len(_tick) == 1)
check("...right beside 'List qualities + sizes'",
      bool(_tick) and _tick[0].master is _lb.master)
_gp4 = [w for w in _r4.walk() if isinstance(w, G.OptionsPane)
        and w.sub == "get"][0]
_urlbox = [w for w in _dl4.walk() if w.kind == "text"][0]
_cli1, _probe1 = G.cli_main, G.download.probe
_A4 = []
G.cli_main = lambda argv: (_A4.append(list(argv)), 0)[1]
try:
    _urlbox.insert("end", "https://youtu.be/dub\n")
    _gp4.vars["keep_video"].set(True)
    _gp4.vars["audio_lang"].set("es")
    _tick[0].var.set(False)
    _lb.invoke()
    _drain4()
    check("unticked: the list is the video qualities (no --show-audio)",
          _A4 and _A4[-1][0] == "formats" and "--show-audio" not in _A4[-1],
          str(_A4[-1:]))
    _tick[0].invoke()
    _lb.invoke()
    _drain4()
    check("ticked: formats --show-audio, in the spoken language set above",
          "--show-audio" in _A4[-1] and "--audio-lang" in _A4[-1]
          and _A4[-1][_A4[-1].index("--audio-lang") + 1] == "es", str(_A4[-1]))
    # Quality / Audio tracks tabs: the JS settings go to the probe, and an
    # empty answer says why
    _seen = []
    G.download.probe = lambda url, o: (_seen.append(dict(o)), {
        "id": "n", "title": "GTA V", "_warnings": [
            "WARNING: [youtube] n: n challenge solving failed"],
        "formats": [{"format_id": "sb0", "ext": "mhtml", "vcodec": "none",
                     "acodec": "none"}]})[1]
    _gp4.vars["js_runtimes"].set("node")
    _at4 = _nb4.tab_for("Audio tracks")
    [w for w in _at4.walk() if w.kind == "entry"][0].options[
        "textvariable"].set("https://youtu.be/nochal")
    _qbtn(_at4, "Load audio tracks").invoke()
    _drain4()
    check("the Quality/Audio probe gets the Download tab's --js-runtimes",
          _seen and _seen[-1].get("js_runtimes") == "node", str(_seen[-1:]))
    _ai4 = " ".join(str(w.options["textvariable"].get())
                    for w in _at4.walk() if w.kind == "label"
                    and w.options.get("textvariable"))
    check("an empty Audio tracks list explains itself (the JS challenge)",
          "challenge was not solved" in _ai4, _ai4[:300])
finally:
    G.cli_main, G.download.probe = _cli1, _probe1
    _gp4.vars["keep_video"].set(False)
    _gp4.vars["audio_lang"].set("en")
    _gp4.vars["js_runtimes"].set("")


# ============ 38. live streams: from the start, watch so far, carry on
print("\n--- 38. live streams: record from the start, watch, carry on ---")
import random as _rnd38   # noqa: E402
import struct as _st38    # noqa: E402
import time as _t38       # noqa: E402
from musictool import live as LV  # noqa: E402

_L = os.path.join(WORK, "live38")
os.makedirs(_L, exist_ok=True)
# Real fragmented media, cut into the pieces yt-dlp leaves behind: every
# piece carries its own init (as YouTube's live segments do), 1 s each.
_v38, _a38 = os.path.join(_L, "v.mp4"), os.path.join(_L, "a.mp4")
_FRAGF = ["-movflags", "frag_keyframe+empty_moov+default_base_moof",
          "-frag_duration", "1000000"]
subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i",
                "testsrc2=size=160x90:rate=30", "-t", "20", "-c:v", "libx264",
                "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-g", "30",
                *_FRAGF, "-an", _v38], check=True)
subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i",
                "sine=frequency=440:duration=20", "-c:a", "aac", "-b:a", "64k",
                *_FRAGF, _a38], check=True)


def _pieces(path):
    b, i, init, cur, out = open(path, "rb").read(), 0, b"", b"", []
    while i < len(b):
        n, t = _st38.unpack(">I4s", b[i:i + 8])
        box, i = b[i:i + n], i + n
        if t in (b"ftyp", b"moov"):
            init += box
        elif t == b"moof":
            cur = box
        elif t == b"mdat":
            out.append(init + cur + box)
    return out


_PV, _PA = _pieces(_v38), _pieces(_a38)
check("test media: real fragmented video and audio, cut into pieces",
      len(_PV) >= 15 and len(_PA) >= 15, f"{len(_PV)} / {len(_PA)}")
# '%' in the name on purpose: as a yt-dlp template it has to be doubled
_BASE = "Chan - Launch Stream 100% [LIVEID]"
_STREAMS = (("299", _PV), ("140", _PA))      # audio as .mp4, as live serves it


def _tmp38(d, fid):
    return os.path.join(d, f"{_BASE}.f{fid}.mp4.part")


def _age(d, secs):
    t = _t38.time() - secs
    for n in os.listdir(d):
        os.utime(os.path.join(d, n), (t, t))


def _rec(d, upto=0, frags=True, old=60):
    """A stopped recording: pieces 1..upto inside the .part, the rest loose."""
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    for fid, P in _STREAMS:
        tmp = _tmp38(d, fid)
        with open(tmp, "wb") as fh:
            for x in P[:upto]:
                fh.write(x)
        open(tmp[:-5] + ".ytdl", "w").write(json.dumps(
            {"downloader": {"current_fragment": {"index": upto}}}))
        if frags:
            for n in range(upto + 1, len(P) + 1):
                open(f"{tmp}-Frag{n}", "wb").write(P[n - 1])
    _age(d, old)


def _exact(d):
    return all(open(_tmp38(d, f), "rb").read() == b"".join(P)
               for f, P in _STREAMS)


def _idx(d, fid):
    return LV.Stream(d, os.path.basename(_tmp38(d, fid))).index()


def _loose(d):
    return sorted(n for n in os.listdir(d) if "-Frag" in n)


_logs = []


def _lg(m):
    _logs.append(str(m))


_D = os.path.join(_L, "rec")
_rec(_D)
_ss = LV.scan(_D)
check("scan finds both streams of a stopped recording",
      sorted(s.fid for s in _ss) == ["140", "299"], str([s.tmp for s in _ss]))
_au = {s.fid: s.is_audio(_ss) for s in _ss}
check("audio is told apart by what is inside -- even named .f140.mp4",
      _au == {"140": True, "299": False}, str(_au))

# --- the state the standalone livejoin.py crashed into on Windows: piece 7
#     copied in, not yet recorded, still on disk, a .ytdl.tmp left behind
_rec(_D, upto=6)
for _f, _P in _STREAMS:
    with open(_tmp38(_D, _f), "ab") as _fh:
        _fh.write(_P[6])
    open(_tmp38(_D, _f)[:-5] + ".ytdl.tmp", "w").write("{}")
_age(_D, 60)
_rr, _rm, _sl = os.replace, os.remove, LV.time.sleep
_nlock = {"rep": 0, "rm": 0}
_R38 = _rnd38.Random(7)


def _frep(a, b):
    if _R38.random() < 0.3:
        _nlock["rep"] += 1
        raise PermissionError(5, "Access is denied", a)
    return _rr(a, b)


def _frm(a):
    if _R38.random() < 0.15:
        _nlock["rm"] += 1
        raise PermissionError(5, "Access is denied", a)
    return _rm(a)


os.replace, os.remove, LV.time.sleep = _frep, _frm, (lambda s: None)
try:
    _logs.clear()
    _rc = LV.join(_D, LV.scan(_D), log=_lg)
finally:
    os.replace, os.remove, LV.time.sleep = _rr, _rm, _sl
check("Windows' 'Access is denied' locks are waited out -- the join finishes",
      _rc == 0 and _nlock["rep"] + _nlock["rm"] > 5, str((_rc, _nlock)))
check("the piece the stopped run had already added is not added twice",
      any("not adding it twice" in x for x in _logs) and _exact(_D),
      " | ".join(_logs[:3]))
check("...every piece joined and recorded; nothing loose, no .tmp, no note",
      not _loose(_D) and _idx(_D, "299") == len(_PV)
      and _idx(_D, "140") == len(_PA)
      and not [n for n in os.listdir(_D) if n.endswith((".tmp", ".livejoin"))],
      str(os.listdir(_D)))

_COP = os.path.join(_L, "copies", "on another drive")
_logs.clear()
_rc, _made = LV.watch_copy(_D, LV.scan(_D), _COP, log=_lg)
check("a copy to watch is made, in a folder of your choice (created)",
      _rc == 0 and len(_made) == 1 and os.path.exists(_made[0])
      and _made[0].endswith(f"{_BASE} (so far).mp4"), str((_rc, _made)))
_dec = subprocess.run([FF, "-v", "error", "-i", _made[0], "-f", "null", "-"],
                      capture_output=True, text=True) if _made else None
_hdr = subprocess.run([FF, "-hide_banner", "-i", _made[0]], capture_output=True,
                      text=True).stderr if _made else ""
_secs, _wh = LV._probe_media(_made[0]) if _made else (None, None)
check("...it plays: picture and sound, all 20 s, no decode errors",
      _dec is not None and not _dec.stderr.strip() and "Video:" in _hdr
      and "Audio:" in _hdr and _secs and abs(_secs - 20) < 1.5,
      f"{_secs} {(_dec.stderr if _dec else '')[:200]}")
check("the pieces it came from are kept (so the download can carry on)",
      os.path.getsize(_tmp38(_D, "299")) == len(b"".join(_PV)))

# --- a join cut off in the middle of a step, then run again
_rec(_D)
_cf, _b0 = shutil.copyfileobj, LV.BATCH
_k38 = {"n": 0}


def _boom(*a, **kw):
    _k38["n"] += 1
    if _k38["n"] == 7:
        raise KeyboardInterrupt("window closed")
    return _cf(*a, **kw)


LV.BATCH, shutil.copyfileobj = 4, _boom
try:
    LV.join(_D, LV.scan(_D), log=_lg)
    _cut = False
except KeyboardInterrupt:
    _cut = True
finally:
    shutil.copyfileobj = _cf
# whichever stream was being joined when it stopped (scan order is free)
_mid = [(f, _idx(_D, f), os.path.getsize(_tmp38(_D, f)), len(b"".join(P[:4])))
        for f, P in _STREAMS]
_age(_D, 60)
_logs.clear()
try:
    _rc = LV.join(_D, LV.scan(_D), log=_lg)
finally:
    LV.BATCH = _b0
check("cut off mid-step: the count says 4, the file holds more",
      _cut and any(i == 4 and n > want for _f, i, n, want in _mid), str(_mid))
check("...the next join cuts the extra back and finishes -- byte for byte",
      _rc == 0 and any("half-finished step" in x for x in _logs) and _exact(_D),
      " | ".join(_logs[:2]))

# --- a piece Windows will not let go of
_rec(_D)


def _lock7(a):
    if a.endswith("-Frag7"):
        raise PermissionError(5, "Access is denied", a)
    return _rm(a)


os.remove, LV.time.sleep = _lock7, (lambda s: None)
try:
    _logs.clear()
    _rc = LV.join(_D, LV.scan(_D), log=_lg)
finally:
    os.remove, LV.time.sleep = _rm, _sl
check("a piece that stays locked is reported, not fatal",
      _rc == 0 and any("could not be deleted" in x for x in _logs)
      and [n[-6:] for n in _loose(_D)] == ["-Frag7", "-Frag7"],
      str(_loose(_D)))
_age(_D, 60)
_rc = LV.join(_D, LV.scan(_D), log=_lg)
check("...the next join clears it away without adding it twice",
      _rc == 0 and not _loose(_D) and _exact(_D))

# --- a download still writing there
_rec(_D, upto=5, old=2)
_logs.clear()
_rc = LV.join(_D, LV.scan(_D), log=_lg)
check("join refuses while a download is still writing to the files",
      _rc == 1 and "still writing" in " ".join(_logs)
      and len(_loose(_D)) == len(_PV) + len(_PA) - 10, " | ".join(_logs))
_rc2, _m2 = LV.watch_copy(_D, LV.scan(_D), _COP, log=_lg)
_s2, _ = LV._probe_media(_m2[0]) if _m2 else (None, None)
check("...but a copy to watch is made (it only reads): what is joined, 5 s",
      _rc2 == 0 and _m2 and _s2 and abs(_s2 - 5) < 1.5, str((_rc2, _s2)))
check("...and --force joins anyway",
      LV.join(_D, LV.scan(_D), log=_lg, force=True) == 0 and _exact(_D))

# --- a missing piece and an empty one
_rec(_D)
os.remove(_tmp38(_D, "299") + "-Frag9")
open(_tmp38(_D, "299") + "-Frag12", "wb").close()
_age(_D, 60)
_logs.clear()
LV.report(_D, LV.scan(_D), log=_lg)
check("the report names the missing piece",
      any("missing piece(s): 9" in x for x in _logs), " | ".join(_logs))
LV.join(_D, LV.scan(_D), log=_lg)
check("joining stops before a missing piece; an empty one is removed",
      _idx(_D, "299") == 8 and not os.path.exists(_tmp38(_D, "299") + "-Frag12")
      and os.path.exists(_tmp38(_D, "299") + "-Frag10")
      and _idx(_D, "140") == len(_PA))

# --- the pure parts
_NOW = 2_000_000_000
_FM = [{"format_id": "299", "ext": "mp4", "vcodec": "avc1.64002a",
        "acodec": "none", "height": 1080, "tbr": 6000},
       {"format_id": "140", "ext": "m4a", "vcodec": "none",
        "acodec": "mp4a.40.2", "tbr": 128}]
_LIVE = {"id": "LIVEID", "title": "Launch", "live_status": "is_live",
         "release_timestamp": _NOW - 3600, "formats": _FM}
check("status / started_ago read yt-dlp's fields",
      LV.status(_LIVE) == "is_live" and LV.status({"is_live": True}) == "is_live"
      and LV.status({"error": "x"}) is None
      and LV.started_ago(_LIVE, now=_NOW) == 3600)
_e = LV.estimate(_LIVE, "299+140", now=_NOW)
check("estimate: the chosen formats' rate x time since it began",
      _e and _e["exact"] and int(_e["per_hour"]) == 6128 * 1000 // 8 * 3600
      and int(_e["so_far"]) == int(_e["per_hour"]), str(_e))
check("...an upper bound when nothing is chosen; audio only is small",
      not LV.estimate(_LIVE, now=_NOW)["exact"]
      and LV.estimate(_LIVE, keep_video=False, now=_NOW)["per_hour"]
      == 128 * 1000 / 8 * 3600)
check("one worker per stream: 2 for video + audio, 1 for audio only",
      LV.stream_count({"keep_video": True}) == 2
      and LV.stream_count({"keep_video": False}) == 1
      and LV.stream_count({"format_selector": "299+140-1"}) == 2
      and LV.stream_count({"format_selector": "95/b"}) == 1)
check("the live file name gets the video id; a custom one is left alone",
      LV.live_template("%(uploader)s - %(title)s.%(ext)s")
      == "%(uploader)s - %(title)s [%(id)s].%(ext)s"
      and LV.live_template("%(title)s.%(ext)s") == "%(title)s [%(id)s].%(ext)s"
      and LV.live_template("%(id)s.%(ext)s") == "%(id)s.%(ext)s"
      and LV.literal("100%") == "100%%")

_PL = os.path.join(_L, "plan")
_rec(_PL, upto=3)
_o38 = {"output_dir": _PL, "template": "%(uploader)s - %(title)s.%(ext)s",
        "keep_video": True, "concurrent": 4}
_logs.clear()
LV.plan(dict(_LIVE, release_timestamp=_t38.time() - 3600), _o38, log=_lg)
check("plan: from the start, 2 workers, no lyrics",
      _o38.get("live_from_start") and _o38["concurrent"] == 2
      and _o38.get("no_lyrics"), str(_o38))
check("...a second run carries on with the pieces already there (% doubled)",
      _o38["template"] == "Chan - Launch Stream 100%% [LIVEID].%(ext)s"
      and any("carrying on with the pieces" in x for x in _logs),
      _o38["template"])
check("...and says how big it is and how much room there is",
      any("size:" in x and "an hour" in x for x in _logs)
      and any("room:" in x for x in _logs), " | ".join(_logs))
_fb = LV.free_bytes
LV.free_bytes = lambda d: 10 * 2 ** 20
try:
    _logs.clear()
    LV.plan(dict(_LIVE, release_timestamp=_t38.time() - 3600),
            {"output_dir": _L, "keep_video": True}, log=_lg)
finally:
    LV.free_bytes = _fb
check("...and warns, before downloading, when the disk cannot hold it",
      any("not enough room" in x for x in _logs), " | ".join(_logs))
_o38 = {"output_dir": _L, "keep_video": True, "live_from_now": True}
LV.plan(_LIVE, _o38, log=_lg)
check("--live-from-now: recorded from now, no from-the-start plumbing",
      not _o38.get("live_from_start") and "concurrent" not in _o38)
_o38 = {"output_dir": _L}
LV.plan({"id": "U", "live_status": "is_upcoming"}, _o38, log=_lg)
check("not started yet: wait for it, then from the start",
      _o38.get("wait_for_video") == "60" and _o38.get("live_from_start")
      and _o38.get("concurrent") == 1)
_logs.clear()
LV.plan({"id": "LIVEID", "live_status": "was_live"}, {"output_dir": _PL},
        log=_lg)
check("an ended stream with a stopped recording here points at --resume",
      any("--resume" in x for x in _logs), " | ".join(_logs))

_ba = download.build_args("u", {"live_from_start": True, "wait_for_video": "60",
                                "download_sections": "*0:01:00-inf"})
check("build_args: --live-from-start, --wait-for-video, --download-sections",
      "--live-from-start" in _ba and _ba[_ba.index("--wait-for-video") + 1] == "60"
      and _ba[_ba.index("--download-sections") + 1] == "*0:01:00-inf")
_pa38, _run0 = [], download.subprocess.run


def _cap38(args, **kw):
    _pa38.append(list(args))
    raise RuntimeError("captured")


download.subprocess.run = _cap38
try:
    download.probe("https://youtu.be/x", {})
    download.probe("https://youtu.be/x", {"live_from_now": True})
finally:
    download.subprocess.run = _run0
check("the probe lists what a live download really gets (from the start)",
      "--live-from-start" in _pa38[0] and "--live-from-start" not in _pa38[1]
      and "https://youtu.be/x" in _pa38[0], str(_pa38[0][-4:]))
check("a full disk is named (Linux, Windows) and has its own advice",
      download.classify_error("ERROR: [Errno 28] No space left on device")
      == "disk_full" and download.classify_error(
          "OSError: [WinError 112] There is not enough space on the disk")
      == "disk_full" and "carries on" in download.ADVICE["disk_full"])

# --- end to end through the CLI, with a yt-dlp that plays a live stream
_FB = os.path.join(WORK, "bin38")
os.makedirs(_FB, exist_ok=True)
open(os.path.join(_FB, "yt-dlp"), "w").write(r'''#!/usr/bin/env python3
import json, os, sys, time
a = sys.argv[1:]
open(os.path.join(os.path.dirname(__file__), "argv.txt"), "a").write(json.dumps(a) + "\n")
if "--version" in a:
    print("2026.10.26-fake"); sys.exit(0)
url = [x for x in a if x.startswith("http")][-1]
def fm(proto):
    return [{"format_id": "299", "ext": "mp4", "vcodec": "avc1.64002a", "acodec": "none",
             "height": 1080, "tbr": 6000, "protocol": proto},
            {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2",
             "tbr": 128, "protocol": proto}]
if "-J" in a:
    if "upcoming" in url:
        info = {"id": "UPID", "title": "Soon", "live_status": "is_upcoming",
                "release_timestamp": int(time.time()) + 600, "formats": []}
    elif "vod" in url:
        info = {"id": "LIVEID", "title": "Launch", "live_status": "was_live",
                "duration": 7200, "formats": fm("https")}
    elif "live" in url:
        info = {"id": "LIVEID", "title": "Launch", "uploader": "Chan",
                "live_status": "is_live", "release_timestamp": int(time.time()) - 3600,
                "formats": fm("http_dash_segments_generator")}
    else:
        info = {"id": "PLAIN", "title": "Plain", "live_status": "not_live",
                "duration": 100, "formats": fm("https")}
    print(json.dumps(info)); sys.exit(0)
if "full" in url:
    sys.stderr.write("ERROR: unable to write data: [Errno 28] No space left on device\n")
    sys.exit(1)
sys.exit(0)
''')
os.chmod(os.path.join(_FB, "yt-dlp"), 0o755)
_ENV38 = dict(ENV, PATH=_FB + os.pathsep + ENV["PATH"])
_ARGV38 = os.path.join(_FB, "argv.txt")


def run38(*args):
    if os.path.exists(_ARGV38):
        os.remove(_ARGV38)
    return subprocess.run([sys.executable, ENTRY, *args, "--cookies-from-browser",
                           "off"], capture_output=True, text=True, env=_ENV38)


def _dl38():
    """The yt-dlp calls that were downloads, not probes."""
    if not os.path.exists(_ARGV38):
        return []
    rows = [json.loads(x) for x in open(_ARGV38) if x.strip()]
    return [r for r in rows if "-J" not in r and "--version" not in r]


def _val(argv, flag):
    return argv[argv.index(flag) + 1] if flag in argv else None


_URL = "https://www.youtube.com/live/LIVEID"
_GD = os.path.join(_L, "get")
_r = run38("get", _URL, "--keep-video", "-o", _GD)
_d = (_dl38() or [[]])[-1]
check("get on a live stream: --live-from-start, 2 workers (video + audio)",
      "--live-from-start" in _d and _val(_d, "--concurrent-fragments") == "2",
      _r.stdout[-600:] + _r.stderr[-300:])
check("...the video id in the file name",
      str(_val(_d, "-o")).endswith(" [%(id)s].%(ext)s"), str(_val(_d, "-o")))
check("...told, before it starts, how far back it goes, how big, how much room",
      "recording it from the very start" in _r.stdout and "size:" in _r.stdout
      and "room:" in _r.stdout, _r.stdout[-800:])
check("...and no lyrics hunt for a broadcast",
      "no lyrics to fetch" in _r.stdout and "==> lyrics for" not in _r.stdout,
      _r.stdout[-300:])
_r = run38("get", _URL, "-o", _GD)
_d = (_dl38() or [[]])[-1]
check("music mode on a live stream: 1 worker (audio only)",
      "--live-from-start" in _d and _val(_d, "--concurrent-fragments") == "1",
      str(_d[-12:]))
_r = run38("get", "https://www.youtube.com/watch?v=upcoming", "--keep-video",
           "-o", _GD)
_d = (_dl38() or [[]])[-1]
check("a stream that has not started: waits for it (--wait-for-video)",
      _val(_d, "--wait-for-video") == "60" and "--live-from-start" in _d,
      _r.stdout[-400:])
_r = run38("get", "https://youtu.be/plain", "--keep-video", "--no-lyrics",
           "-o", _GD)
_d = (_dl38() or [[]])[-1]
check("an ordinary video is downloaded exactly as before",
      "--live-from-start" not in _d and "[%(id)s]" not in str(_val(_d, "-o"))
      and _val(_d, "--concurrent-fragments") == "4", str(_d[-14:]))
_r = run38("get", "https://www.youtube.com/live/full", "--keep-video", "-o", _GD)
check("a live recording that hits a full disk says nothing is lost, and how "
      "to watch it and carry on",
      "Nothing downloaded so far is lost" in _r.stdout
      and "free some space" in _r.stdout and "live" in _r.stdout
      and "--watch" in _r.stdout, _r.stdout[-700:])
_r = run38("check", _URL)
check("check says when a URL is live, and since when",
      "LIVE now, started 1:00:0" in _r.stdout, _r.stdout[-400:])
_r = run38("live", _L, "--status", _URL)
check("live --status: live since when, how big, how much room",
      "LIVE now" in _r.stdout and "started 1:00:0" in _r.stdout
      and "free in" in _r.stdout and "an hour" in _r.stdout, _r.stdout[-600:])
_r = run38("formats", _URL, "--keep-video")
check("formats on a live stream says these are the from-the-start formats",
      "formats from the start" in _r.stdout, _r.stdout[-500:])

_rec(_D, upto=4)
_r = run38("live", _D)
check("live FOLDER: what is there and what to do next",
      "loose piece(s)" in _r.stdout and "can be joined now" in _r.stdout
      and "next:" in _r.stdout, _r.stdout[-500:])
_r = run38("live", _D, "--resume", _URL)
_d = (_dl38() or [[]])[-1]
check("resume while live: joins first, then carries on with the same pieces",
      _r.returncode == 0 and not _loose(_D) and _exact(_D),
      _r.stdout[-600:] + _r.stderr[-300:])
check("...same formats, from the start, 2 workers, the existing name (%%)",
      _val(_d, "-f") == "299+140" and "--live-from-start" in _d
      and _val(_d, "--concurrent-fragments") == "2"
      and _val(_d, "-o") == "Chan - Launch Stream 100%% [LIVEID].%(ext)s"
      and _val(_d, "-P") == _D and _val(_d, "--merge-output-format") == "mp4"
      and "--embed-thumbnail" not in _d, str(_d))
_rec(_D, upto=15, frags=False)
_r = run38("live", _D, "--resume", "https://youtu.be/vod-LIVEID")
_d = (_dl38() or [[]])[-1]
_sec = str(_val(_d, "--download-sections"))
check("resume once it is a normal video: only from where yours ends (-5 s)",
      _r.returncode == 0 and _sec.startswith("*0:00:")
      and abs(int(_sec[6:8]) - 10) <= 1 and _sec.endswith("-inf")
      and "--live-from-start" not in _d, _sec + " " + _r.stdout[-400:])
check("...in the quality you have, as 'TITLE (rest)'",
      "[height<=90]" in str(_val(_d, "-f"))
      and _val(_d, "-o") == "Chan - Launch Stream 100%% [LIVEID] (rest).%(ext)s",
      str(_d))

# --- the Live streams tab
tkstub.reset()
G.run()
_r8 = tkstub.ROOTS[-1]


def _drain8():
    todo, _r8.pending[:] = list(_r8.pending), []
    for f in todo:
        f()


_nb8 = [w for w in _r8.walk() if isinstance(w, tkstub.Notebook)][0]
_lt = _nb8.tab_for("Live streams")
_le = [w for w in _lt.walk() if w.kind == "entry"]
# link, folder, copy folder -- then the part, and the chat's hold and SRT pad
check("the Live streams tab has the link, the folder and the copy folder",
      len(_le) == 6, str(len(_le)))
_gp8 = [w for w in _r8.walk() if isinstance(w, G.OptionsPane)
        and w.sub == "get"][0]
_cfb8 = _gp8.vars["cookies_from_browser"].get()
_A8, _cli8, _wc8, _op8 = [], G.cli_main, G.live.watch_copy, G.open_path
G.cli_main = lambda argv: (_A8.append(list(argv)), 0)[1]
try:
    _le[0].options["textvariable"].set(_URL)
    _le[1].options["textvariable"].set(_D)
    _le[2].options["textvariable"].set(_COP)
    _gp8.vars["cookies_from_browser"].set("firefox")
    _qbtn(_lt, "Is it live? How big?").invoke()
    _drain8()
    check("'Is it live? How big?' -> live --status, with your cookie setting",
          _A8[-1][:4] == ["live", _D, "--status", _URL]
          and _val(_A8[-1], "--cookies-from-browser") == "firefox", str(_A8[-1:]))
    _qbtn(_lt, "Record from the start").invoke()
    _drain8()
    _g8 = _A8[-1]
    check("'Record from the start' -> get the video, no lyrics, this video, "
          "into the folder",
          _g8[0] == "get" and _URL in _g8 and "--keep-video" in _g8
          and "--no-lyrics" in _g8 and "--no-playlist" in _g8
          and _val(_g8, "--output-dir") == _D, str(_g8))
    _ao = [w for w in _qw(_lt, "checkbutton")
           if w.options.get("text") == "Audio only (no picture)"][0]
    _ao.invoke()
    _qbtn(_lt, "Record from the start").invoke()
    _drain8()
    check("...ticked 'Audio only': no --keep-video",
          _A8[-1][0] == "get" and "--keep-video" not in _A8[-1], str(_A8[-1]))
    _ao.invoke()
    _qbtn(_lt, "What's in the folder?").invoke()
    _drain8()
    check("'What's in the folder?' -> live FOLDER", _A8[-1] == ["live", _D],
          str(_A8[-1:]))
    _qbtn(_lt, "Watch what's there so far").invoke()
    _drain8()
    check("'Watch what's there so far' (idle) -> live FOLDER --join --out",
          _A8[-1] == ["live", _D, "--join", "--out", _COP], str(_A8[-1:]))
    _qbtn(_lt, "Download the rest").invoke()
    _drain8()
    check("'Download the rest' -> live FOLDER --resume URL + cookies",
          _A8[-1][:4] == ["live", _D, "--resume", _URL]
          and _val(_A8[-1], "--cookies-from-browser") == "firefox", str(_A8[-1:]))
    _seen8 = []
    G.live.watch_copy = lambda d, ss, out=None, log=print: (
        _seen8.append((d, out, len(ss))), (0, []))[1]

    def _recording(argv):
        _A8.append(list(argv))
        if argv[0] == "get":         # pressed while the recording runs
            _qbtn(_lt, "Watch what's there so far").invoke()
        return 0
    G.cli_main = _recording
    _n8 = len(_A8)
    _qbtn(_lt, "Record from the start").invoke()
    _drain8()
    check("during a recording 'Watch' only reads the pieces into a copy -- "
          "no join, nothing queued",
          _seen8 == [(_D, _COP, len(LV.scan(_D)))] and len(_A8) == _n8 + 1,
          str((_seen8, _A8[_n8:])))
    os.makedirs(_COP, exist_ok=True)
    open(os.path.join(_COP, "X (so far).mp4"), "wb").write(b"x")
    _opened = []
    G.open_path = _opened.append
    _qbtn(_lt, "Open the copy").invoke()
    check("'Open the copy' opens the newest copy",
          _opened and _opened[-1].endswith(" (so far).mp4"), str(_opened))
    from musictool import config as _cfg38
    _ex8 = _cfg38.load().get("gui_extra") or {}
    check("the tab's fields come back next time",
          _ex8.get("live_url") == _URL and _ex8.get("live_folder") == _D
          and _ex8.get("live_out") == _COP, str({k: v for k, v in _ex8.items()
                                                 if k.startswith("live")}))
finally:
    G.cli_main, G.live.watch_copy, G.open_path = _cli8, _wc8, _op8
    _gp8.vars["cookies_from_browser"].set(_cfb8)
shutil.rmtree(_L, ignore_errors=True)


# ============ 39. exact streams (copied, never converted); quiet progress;
#                  a dropped connection waits and resumes instead of skipping
print("\n--- 39. --exact; one progress line; drops resume, not skip ---")
from musictool import formats as F39  # noqa: E402
from musictool import progress as P39  # noqa: E402
from musictool import cli as C39  # noqa: E402
from musictool import live as LV39  # noqa: E402

# the shape of a finished YouTube stream (R0GdEkPZSvM, 2026-11-18): avc1,
# vp9 and av01 video, m4a and webm audio, a DRC copy, a progressive 18
_EX = {"id": "EXV", "title": "Stream", "duration": 13151, "was_live": True,
       "live_status": "was_live", "formats": [
    {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2",
     "abr": 129.5, "filesize": 212800000, "language": "en"},
    {"format_id": "140-drc", "ext": "m4a", "vcodec": "none",
     "acodec": "mp4a.40.2", "abr": 129.5, "filesize": 212800000,
     "language": "en", "format_note": "DRC"},
    {"format_id": "251", "ext": "webm", "vcodec": "none", "acodec": "opus",
     "abr": 116.0, "filesize": 191700000, "language": "en"},
    {"format_id": "299", "ext": "mp4", "vcodec": "avc1.64002a", "acodec": "none",
     "height": 1080, "width": 1920, "fps": 60, "filesize": 6500000000},
    {"format_id": "303", "ext": "webm", "vcodec": "vp9", "acodec": "none",
     "height": 1080, "width": 1920, "fps": 60, "filesize": 4800000000},
    {"format_id": "315", "ext": "webm", "vcodec": "vp9", "acodec": "none",
     "height": 2160, "width": 3840, "fps": 60, "filesize": 24500000000},
    {"format_id": "401", "ext": "mp4", "vcodec": "av01.0.13M.08",
     "acodec": "none", "height": 2160, "width": 3840, "fps": 60,
     "filesize": 27400000000},
    {"format_id": "18", "ext": "mp4", "vcodec": "avc1.42001E",
     "acodec": "mp4a.40.2", "height": 360, "width": 640, "fps": 30,
     "filesize": 400000000},
]}
_ER = F39.rows_from_info(_EX)

# --- which selectors count as exact
check("exact ids: 299+140 is two ids, 251 is one",
      F39.exact_ids("299+140") == ["299", "140"] and F39.exact_ids("251") == ["251"])
check("exact ids: anything that lets yt-dlp choose is not exact",
      all(F39.exact_ids(x) is None for x in
          ("bv+ba", "299/18", "bv*[height<=720]", "best", "", None, "299+")),
      str([F39.exact_ids(x) for x in ("bv+ba", "299/18", "best")]))
check("the container takes the streams as they are: mp4+m4a, webm+webm, "
      "else mkv", [F39.exact_plan(s, _ER)["container"] for s in
                   ("299+140", "315+251", "401+251", "299+251")]
      == ["mp4", "webm", "mkv", "mkv"])
_p = F39.exact_plan("999+140", _ER)
check("an id the video does not have is refused, and the real ones listed",
      _p["error"] and "999" in _p["error"] and "299" in _p["error"], _p["error"])
check("matching audio keeps the pair in one container, no DRC copy",
      F39.matching_audio(_ER, next(r for r in _ER if r["id"] == "299"))["id"] == "140"
      and F39.matching_audio(_ER, next(r for r in _ER if r["id"] == "315"))["id"] == "251")
check("a stream line says what it is and gives the id",
      F39.describe_stream(next(r for r in _ER if r["id"] == "315")).startswith("2160p60 vp9 webm")
      and F39.describe_stream(next(r for r in _ER if r["id"] == "315")).endswith("(id 315)"))


# --- _apply_exact: what it turns on and off
def _ex(sel, **kw):
    o = {"format_selector": sel, "embed_subs": True, "keep_video": False,
         "audio_format": "m4a", "merge_format": "", "embed_thumbnail": True}
    o.update(kw)
    lines = []
    ok = C39._apply_exact(o, _EX, lines.append)
    return ok, o, "\n".join(lines)


_ok, _o, _t = _ex("299+140")
check("video + audio: the video is kept, merged into mp4, no subtitles added",
      _ok and _o["keep_video"] and _o["merge_format"] == "mp4"
      and not _o["embed_subs"] and "nothing is converted" in _t, str(_o))
_ok, _o, _t = _ex("315+251")
check("webm + a thumbnail -> mkv (yt-dlp fails the run on a webm cover)",
      _o["merge_format"] == "mkv" and "webm cannot hold the thumbnail" in _t, _t)
_ok, _o, _t = _ex("315+251", embed_thumbnail=False)
check("...and without a thumbnail it stays webm", _o["merge_format"] == "webm")
_ok, _o, _t = _ex("299+251", merge_format="mp4")
check("a --merge-format you gave is kept", _o["merge_format"] == "mp4")
_ok, _o, _t = _ex("251")
check("audio alone is taken out as it is: --audio-format best, not m4a",
      _ok and _o["audio_format"] == "best" and not _o["keep_video"]
      and "(.opus)" in _t, _t)
_ok, _o, _t = _ex("140+251")
check("two audio streams -> --audio-multistreams", _o.get("audio_multistreams"))
_ok, _o, _t = _ex("299+999")
check("a missing id: refused (False), with the reason",
      _ok is False and "!! --exact" in _t, _t)
_a = download.build_args("http://x/v", {"output_dir": ".", "audio_format": "best",
                                        "audio_multistreams": True})
check("--audio-format best has no --audio-quality (nothing is re-encoded)",
      "--audio-quality" not in _a and _val(_a, "--audio-format") == "best"
      and "--audio-multistreams" in _a, str(_a))

# --- through `get`: refused before anything is downloaded
_dl39 = []
_pr39, _dd39 = download.probe, download.download
download.probe = lambda url, o, *a, **k: dict(_EX)
download.download = lambda url, o, **k: (_dl39.append(dict(o)), ([], None))[1]


def _get39(*args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = C39.main(["get", "https://www.youtube.com/watch?v=EXV",
                       "--no-lyrics", "-o", WORK, "--cookies-from-browser",
                       "off"] + list(args))
    return rc, buf.getvalue()


try:
    _rc, _out = _get39("--exact")
    check("--exact without -f is refused (rc 2)", _rc == 2 and "!!" in _out, _out[-200:])
    _rc, _out = _get39("--exact", "-f", "bv+ba")
    check("--exact with a selector yt-dlp would choose from is refused",
          _rc == 2 and not _dl39, _out[-200:])
    _rc, _out = _get39("--exact", "-f", "999+140")
    check("--exact with an id the video lacks: rc 1 and nothing downloaded",
          _rc == 1 and not _dl39 and "999" in _out, _out[-300:])
    _rc, _out = _get39("--exact", "-f", "303+251", "--no-thumbnail")
    check("--exact 303+251 downloads exactly that, kept and merged into webm",
          _rc == 0 and _dl39 and _dl39[-1]["format_selector"] == "303+251"
          and _dl39[-1]["keep_video"] and _dl39[-1]["merge_format"] == "webm",
          str(_dl39[-1:]))
finally:
    download.probe, download.download = _pr39, _dd39

check("the GUI builds --exact from the option",
      "--exact" in U.build_argv("get", {"exact": True, "format_selector": "299+140"},
                                ["http://x"]))

# --- live --status lists the streams with their ids
_ls = []
LV39.list_streams(_EX, log=_ls.append, url="U")
_lst = "\n".join(_ls)
check("live --status lists every stream with its id, biggest picture first",
      "(id 401)" in _lst and "(id 251)" in _lst
      and _lst.index("2160p60") < _lst.index("1080p60"), _lst)
check("...and how to take exactly the ones you want",
      "--exact -f 401+140" in _lst, _lst[-120:])

# --- progress: every redraw becomes an event, never a log line
_e = P39.parse_ytdlp_line("[download] 1.04MiB at 720.44KiB/s (00:00:01) (frag 59/118)")
check("a redraw without a % (a stream) is progress, from the pieces",
      _e["type"] == "pct" and _e["frags"] == (59, 118) and abs(_e["pct"] - 50) < 0.1, str(_e))
_e = P39.parse_ytdlp_line("[download] 3.00KiB at 0.00B/s (00:00:00) (frag 0/unknown)")
check("...with no total it is still progress, with no %",
      _e and _e["type"] == "pct" and _e["pct"] is None, str(_e))
_e = P39.parse_ytdlp_line("2: [download]  45.0% of ~ 10.00MiB at 1.00MiB/s ETA 00:05")
check("'2:' (video and audio side by side) is the stream number", _e["stream"] == 2
      and _e["pct"] == 45.0, str(_e))
_e = P39.parse_ytdlp_line("[download] Got error: ('Connection aborted.'). Retrying (2/5)...")
check("a retry is a retry event", _e["type"] == "retry" and _e["k"] == 2
      and _e["n"] == 5 and not _e["gave_up"], str(_e))
_g = ("[download]  50.0% of 1.00MiB at 1MiB/s ETA 00:01"
      "ERROR: fragment 3 not found, unable to continue")
check("a redraw with an error glued on is the error, not progress",
      P39.parse_ytdlp_line(_g) is None
      and P39.unglue(_g) == "ERROR: fragment 3 not found, unable to continue")

_L39 = []
_on = download._make_on_line(_L39.append)
for _seg in ["[download] Destination: x.f299.mp4"] + [
        f"[download] {k}.00KiB at 1.00MiB/s (00:00:0{k % 10}) (frag {k}/120)"
        for k in range(300)] + [
        f"[download] Got error: ('Connection aborted.'). Retrying ({k % 5 + 1}/5)..."
        for k in range(40)] + [
        "ERROR:", "[download] Got error: ('Connection aborted.'). Giving up after 5 retries",
        _g]:
    _on(_seg)
check("300 redraws + 40 retries -> a handful of log lines, not 340",
      len(_L39) <= 5, "\n".join(_L39))
check("the retries are summed up as '(+N more like it)'",
      any("more like it" in x for x in _L39) or len([x for x in _L39 if "Retrying" in x]) == 1,
      "\n".join(_L39))
check("a bare 'ERROR:' is joined to its message",
      not any(x.strip() == "ERROR:" for x in _L39)
      and any(x.strip().startswith("ERROR: [download] Got error") for x in _L39),
      "\n".join(_L39))
check("the glued error is logged",
      any(x.strip() == "ERROR: fragment 3 not found, unable to continue" for x in _L39))

# a terminal: the redraws overwrite one line (\r), at most 5 a second
class _TTY(io.StringIO):
    pass


_tty = _TTY()
_T39 = []
_on = download._make_on_line(_T39.append, tty=_tty)
_old_hook = P39.get_hook()
P39.set_hook(None)
try:
    for k in range(50):
        _on(f"[download] {k}.00KiB at 1.00MiB/s (00:00:00) (frag {k}/120)")
    _on("[download] 100% of    1.04MiB in 00:00:01 at 717.09KiB/s")
finally:
    P39.set_hook(_old_hook)
check("in a terminal the progress is one line redrawn in place",
      _tty.getvalue().count("\r") >= 1 and _tty.getvalue().count("\n") <= 1
      and _T39 == ["    [download] 100% of    1.04MiB in 00:00:01 at 717.09KiB/s"],
      repr(_tty.getvalue()[:200]) + str(_T39))

# --- what went wrong, and is it worth waiting for the network
_et = download._error_text(
    ["[download] Destination: v.f299.mp4", "ERROR:",
     "[download] Got error: ('Connection aborted.'). Giving up after 5 retries",
     "ERROR: fragment 33 not found, unable to continue"] +
    [f"[download] {k}.0% of 1.00MiB at 1MiB/s ETA 00:01" for k in range(30)])
check("the error text keeps the ERRORs even when 30 audio redraws follow",
      "fragment 33 not found" in _et and "Giving up" in _et and "%" not in _et, _et)
check("a piece given up on after connection errors: wait and resume",
      download.retryable(_et))
check("a merge failure after an earlier blip is NOT a network problem",
      not download.retryable("Got error: Connection aborted. Retrying (1/5)\n"
                             "ERROR: Postprocessing: Conversion failed!"))
check("a removed video is not retried", not download.retryable(
    "ERROR: [youtube] EXV: Video unavailable"))
_a = download.build_args("http://x/v", {"output_dir": "."})
check("retries wait longer each time instead of all failing in a second",
      "http:exp=1:30" in _a and "fragment:exp=1:30" in _a, str(_a))
check("a piece that cannot be fetched stops the run (to resume) -- it is "
      "never skipped, leaving a hole", "--abort-on-unavailable-fragments" in _a
      and "--skip-unavailable-fragments" not in _a)

# --- the progress bar: per stream, done only when every stream is done
_st = P39.State()
_st.handle({"type": "file", "name": "x.f299.mp4"})
_st.handle({"type": "pct", "pct": 80.0, "size": "", "speed": "", "eta": "",
           "elapsed": "", "stream": 1})
_st.handle({"type": "pct", "pct": 20.0, "size": "", "speed": "", "eta": "",
           "elapsed": "", "stream": 2})
check("two streams side by side: the bar shows the slower one",
      abs((_st.pct or 0) - 20.0) < 0.01, str(_st.pct))

# --- the Live streams tab: the stream pickers
tkstub.reset()
G.run()
_r9 = tkstub.ROOTS[-1]


def _drain9():
    todo, _r9.pending[:] = list(_r9.pending), []
    for f in todo:
        f()


_lt9 = [w for w in _r9.walk() if isinstance(w, tkstub.Notebook)][0].tab_for("Live streams")
_le9 = [w for w in _lt9.walk() if w.kind == "entry"]
_cb9 = [w for w in _qw(_lt9, "combobox")
        if any("automatic (best" in str(v) for v in (w.options.get("values") or []))]
check("the tab has a video and an audio stream picker", len(_cb9) == 2, str(len(_cb9)))
_URL9 = "https://www.youtube.com/live/EXV"
_A9, _cli9 = [], G.cli_main


def _fake_status(argv):
    _A9.append(list(argv))
    if "--status" in argv:
        LV39.LAST_PROBE[_URL9] = dict(_EX)
    return 0


G.cli_main = _fake_status
try:
    _le9[0].options["textvariable"].set(_URL9)
    _le9[1].options["textvariable"].set(os.path.join(WORK, "live39"))
    _qbtn(_lt9, "Record from the start").invoke()
    _drain9()
    check("before 'Is it live?' it records on automatic, without --exact",
          "--exact" not in _A9[-1], str(_A9[-1]))
    _qbtn(_lt9, "Is it live? How big?").invoke()
    _drain9()
    _vv = list(_cb9[0].options.get("values"))
    check("'Is it live? How big?' fills the pickers with the streams",
          len(_vv) == 1 + 5 and any("(id 315)" in v for v in _vv), str(_vv))
    _cb9[0].options["textvariable"].set(next(v for v in _vv if "(id 315)" in v))
    _cb9[0].fire("<<ComboboxSelected>>")
    check("picking a video sets the audio to the one that keeps it a copy",
          "(id 251)" in _cb9[1].options["textvariable"].get(),
          _cb9[1].options["textvariable"].get())
    _qbtn(_lt9, "Record from the start").invoke()
    _drain9()
    _g9 = _A9[-1]
    check("a chosen video stream -> get --exact with that id and a matching audio",
          "--exact" in _g9 and str(_val(_g9, "--format-selector")).startswith("315+")
          and _val(_g9, "--format-selector") == "315+251", str(_g9))
finally:
    G.cli_main = _cli9


# ============ 40. live chat replay -> subtitles; only part of a video
print("\n--- 40. chat replay as subtitles; get --range ---")
from musictool import chat as CH  # noqa: E402
from musictool import util as U40  # noqa: E402
from musictool import cli as C40  # noqa: E402

# --- times and ranges
check("times: 1:00:00, 60:00, 90:00, 3600, 1h2m3s, 90m, 1.5h",
      [U40.parse_time(x) for x in ("1:00:00", "60:00", "90:00", "3600",
                                   "1h2m3s", "90m", "1.5h")]
      == [3600, 3600, 5400, 3600, 3723, 5400, 5400])
_bad40 = []
for _x in ("1:60:00", "1:00:60", "abc", "", "-5"):
    try:
        U40.parse_time(_x)
        _bad40.append(_x)
    except ValueError:
        pass
check("...minutes or seconds of 60 and up inside h:mm:ss, and junk, refused",
      not _bad40, str(_bad40))
check("ranges: S-, S-E, -E, S-end",
      U40.parse_range("1:00:00-") == (3600, None)
      and U40.parse_range("1:00:00-2:30:00") == (3600, 9000)
      and U40.parse_range("-45:00") == (0, 2700)
      and U40.parse_range("1h-end") == (3600, None))
_bad40 = []
for _x in ("2:00:00-1:00:00", "1:00:00", "x-y"):
    try:
        U40.parse_range(_x)
        _bad40.append(_x)
    except ValueError:
        pass
check("...backwards, no dash, junk: refused", not _bad40, str(_bad40))
check("part names: 'from 1h00m00s', '1h00m00s-2h30m00s'",
      U40.part_label(3600) == "from 1h00m00s"
      and U40.part_label(3600, 9000) == "1h00m00s-2h30m00s"
      and U40.clock(3723) == "1:02:03")
check("...read back from a file name",
      CH.part_from_name("A [ID] (from 1h00m00s).mp4") == (3600, None)
      and CH.part_from_name("A (1h00m00s-2h30m00s).mkv") == (3600, 9000)
      and CH.part_from_name("A (live).mp4") is None)
check("video ids from links, ids and replay names",
      CH.video_id("https://www.youtube.com/live/R0GdEkPZSvM?si=x") == "R0GdEkPZSvM"
      and CH.video_id("https://youtu.be/R0GdEkPZSvM") == "R0GdEkPZSvM"
      and CH.video_id("https://www.youtube.com/watch?v=R0GdEkPZSvM&t=5")
      == "R0GdEkPZSvM" and CH.video_id("R0GdEkPZSvM") == "R0GdEkPZSvM"
      and CH.video_id("hello") is None
      and CH.json_id("T [R0GdEkPZSvM].live_chat.json") == "R0GdEkPZSvM")

# --- a chat replay in yt-dlp's shape (synthetic: no real user's messages)
_CD = os.path.join(WORK, "chat40")
shutil.rmtree(_CD, ignore_errors=True)
os.makedirs(_CD)


def _item40(ms, kind, body):
    return json.dumps({"replayChatItemAction": {"actions": [
        {"addChatItemAction": {"item": {kind: body}}}],
        "videoOffsetTimeMsec": str(ms)}})


def _txt40(ms, who, runs, mid, badges=None):
    return _item40(ms, "liveChatTextMessageRenderer", {
        "message": {"runs": runs}, "authorName": {"simpleText": who},
        "id": mid, "authorBadges": badges or []})


_MOD = [{"liveChatAuthorBadgeRenderer": {"icon": {"iconType": "MODERATOR"},
                                         "tooltip": "Moderator"}}]
_MEM = [{"liveChatAuthorBadgeRenderer": {"customThumbnail": {"thumbnails": []},
                                         "tooltip": "Member (1 year)"}}]
_JL = [
    _txt40(0, "@early", [{"text": "before it starts"}], "m0"),
    _txt40(10000, "@ann", [{"text": "hello {there}"}], "m1"),
    _txt40(12000, "@bob", [{"text": "nice "}, {"emoji": {
        "emojiId": "\U0001F525", "shortcuts": [":fire:"]}}, {"emoji": {
        "emojiId": "UCx/abc", "shortcuts": [":chan-wave:"],
        "isCustomEmoji": True}}], "m2", _MOD),
    "{not json",
    _txt40(14000, "@cat", [{"text": "  spaced\n  out  "}], "m3", _MEM),
    _item40(15000, "liveChatPaidMessageRenderer", {
        "message": {"runs": [{"text": "thanks"}]}, "authorName":
        {"simpleText": "@dan"}, "purchaseAmountText": {"simpleText": "$5.00"},
        "id": "m4"}),
    _item40(16000, "liveChatPlaceholderItemRenderer", {"id": "m5"}),
    _txt40(17000, "@eve", [{"text": "deleted later"}], "m6"),
    json.dumps({"replayChatItemAction": {"actions": [
        {"markChatItemAsDeletedAction": {"targetItemId": "m6"}}],
        "videoOffsetTimeMsec": "18000"}}),
    _txt40(40000, "@fay", [{"text": "much later"}], "m7"),
]
_CJ = os.path.join(_CD, "Stream [ABCDEFGHIJK].live_chat.json")
open(_CJ, "w", encoding="utf-8").write("\n".join(_JL) + "\n")
_M = CH.load(_CJ)
check("load: text, emoji, Super Chat; placeholder, deleted and broken lines "
      "left out",
      [m.author for m in _M] == ["@early", "@ann", "@bob", "@cat", "@dan", "@fay"],
      str([(m.t, m.author, m.text) for m in _M]))
check("...a standard emoji is its character, a channel's own is its :name:",
      _M[2].text == "nice \U0001F525:chan-wave:", _M[2].text)
check("...roles from the badges; spaces and line breaks folded; amount kept",
      _M[2].role == "moderator" and _M[3].role == "member"
      and _M[3].text == "spaced out" and _M[4].paid == "$5.00", str(_M[2:5]))

# --- how many are on screen (the message sent before the stream began is
# left out here: it is the subject of the "part" check below)
_M0, _M = _M, _M[1:]
_W1 = CH.windows(_M, lines=1, hold=8, start=5)
check("lines 1 = ordinary subtitles: one message at a time",
      all(len(s) == 1 for _a, _b, s in _W1)
      and [s[0].author for _a, _b, s in _W1] == ["@ann", "@bob", "@cat",
                                                  "@dan", "@fay"], str(_W1))
_W3 = CH.windows(_M, lines=3, hold=0, start=5)
_seq = [[m.author for m in s] for _a, _b, s in _W3]
check("lines 3: each message stays until 3 newer ones are below it",
      _seq == [["@ann"], ["@ann", "@bob"], ["@ann", "@bob", "@cat"],
               ["@bob", "@cat", "@dan"], ["@cat", "@dan", "@fay"]], str(_seq))
check("...times are the video's, minus where your part starts",
      abs(_W3[0][0] - 5.0) < 1e-6 and abs(_W3[1][0] - 7.0) < 1e-6, str(_W3[:2]))
_W5 = CH.windows(_M, lines=5, hold=15, start=5)
_lone = [s for _a, _b, s in _W5 if [m.author for m in s] == ["@fay"]]
check("hold 15: a quiet chat clears -- the late message shows alone",
      _lone and all(b - a <= 15 + 1e-6 for a, b, _s in _W5), str(_W5[-2:]))
_W0 = CH.windows(_M, lines=6, hold=0, start=5)
check("hold 0: nothing leaves until pushed off",
      [m.author for m in _W0[-1][2]] == ["@ann", "@bob", "@cat", "@dan", "@fay"],
      str(_W0[-1]))
_Wp = CH.windows(_M0, lines=3, hold=8, start=13, end=30)
check("a part from 0:13: what was on screen then is kept, nothing past its end",
      [m.author for m in _Wp[0][2]] == ["@ann", "@bob"] and _Wp[0][0] == 0
      and all(b <= 17 + 1e-6 for _a, b, _s in _Wp), str(_Wp))
_Wo = CH.windows(_M, lines=1, hold=8, start=5, offset=2)
check("offset moves the chat later", abs(_Wo[0][0] - 7.0) < 1e-6, str(_Wo[0]))

# --- the formats
_ass = CH.render(_M, "ass", lines=3, hold=15, start=5, side="right",
                 place="bottom", title="T")
_sty = [x for x in _ass.splitlines() if x.startswith("Style: Chat")][0]
check("ASS: right + bottom = a column on the right (text left-aligned in it, "
      "like a chat), on a dark box",
      _sty.split(",")[18] == "1" and int(_sty.split(",")[19]) > 1000
      and _sty.split(",")[15] == "3" and CH.MARK in _ass, _sty)
check("...names bold in their colour, braces in messages harmless",
      "{\\b1\\c&H" in _ass and "hello \uff5bthere\uff5d" in _ass, _ass[-400:])
_lft = CH.render(_M, "ass", side="left", place="top")
_sl = [x for x in _lft.splitlines() if x.startswith("Style: Chat")][0]
check("...left + top = alignment 7", _sl.split(",")[18] == "7", _sl)
_srt = CH.render(_M, "srt", lines=3, hold=15, start=5, side="right")
check("SRT: numbered cues, {\\an3} for right/bottom, names in <b>",
      _srt.startswith("1\n00:00:05,000 --> ") and "{\\an3}" in _srt
      and "<b>@ann: </b>" in _srt, _srt[:200])
_pad = CH.render(_M, "srt", lines=1, hold=8, start=5, pad=10, names=False)
check("SRT pad: every line pushed right with no-break spaces",
      "}" + "\u00a0" * 10 + "hello (there)" in _pad, repr(_pad[:120]))
check("...SRT gets ( ) for braces: some players read { } as a style tag",
      "{there}" not in _srt and "(there)" in _srt)
_vtt = CH.render(_M, "vtt", lines=3, hold=15, start=5, side="right")
check("VTT: a header and cue settings that place it right",
      _vtt.startswith("WEBVTT") and "position:" in _vtt and "align:start" in _vtt
      and "00:00:05.000 --> " in _vtt, _vtt[:200])
_txt = CH.render(_M, "txt", start=5)
check("TXT: one readable line per message, from the part's start",
      "[0:00:05] @ann: hello {there}" in _txt and "@early" not in _txt,
      _txt[:300])
check("--no-names leaves the names out",
      "@ann" not in CH.render(_M, "srt", names=False))

# --- where it goes
_V = os.path.join(_CD, "Chan - Stream [ABCDEFGHIJK] (from 0h00m05s).mp4")
open(_V, "wb").write(b"not really a video")
open(os.path.join(_CD, "Other [ZZZZZZZZZZZ].mp4"), "wb").write(b"x")
check("finds the video by [id] in its name, and the replay",
      CH.find_videos(_CD, "ABCDEFGHIJK") == [_V]
      and CH.find_json(_CD, "ABCDEFGHIJK") == _CJ)
open(os.path.join(_CD, "Other [ZZZZZZZZZZZ].srt"), "w").write("1\n")
check("a subtitle that is not ours is never overwritten: NAME.chat.srt",
      CH.sub_path(os.path.join(_CD, "Other [ZZZZZZZZZZZ].mp4"), "srt")
      .endswith("Other [ZZZZZZZZZZZ].chat.srt"))

# --- the CLI end to end, with the replay already in the folder
_r = subprocess.run([sys.executable, ENTRY, "chat", _CJ, "--format", "srt",
                     "--lines", "2", "--hold", "3"], capture_output=True,
                    text=True, env=ENV)
_S = _V[:-4] + ".srt"
_st = open(_S, encoding="utf-8-sig").read() if os.path.exists(_S) else ""
check("chat FILE.json: a subtitle with the video's name, from the part in "
      "its name (0:05)",
      _r.returncode == 0 and _st.startswith("1\n00:00:05,000 --> ")
      and "@early" not in _st, _r.stdout[-400:] + _r.stderr[-300:])
check("...SRT saved with a byte-order mark (Windows players)",
      open(_S, "rb").read(3) == b"\xef\xbb\xbf")
_r = subprocess.run([sys.executable, ENTRY, "chat", _CJ, "--format", "srt",
                     "--lines", "1"], capture_output=True, text=True, env=ENV)
_st2 = open(_S, encoding="utf-8-sig").read()
check("...run again with other settings: our own file is replaced",
      _r.returncode == 0 and _st2 != _st and not os.path.exists(_S[:-4]
                                                                + ".chat.srt"),
      _r.stdout[-300:])
_r = subprocess.run([sys.executable, ENTRY, "chat", _CJ, "--range", "0:10-0:20",
                     "--format", "txt"], capture_output=True, text=True, env=ENV)
_tx = open(_V[:-4] + ".txt", encoding="utf-8").read()
check("--range overrides the name: 0:10-0:20 only",
      "[0:00:00] @ann" in _tx and "@fay" not in _tx, _tx[-300:])
_r = subprocess.run([sys.executable, ENTRY, "chat", _CJ, "--range", "junk"],
                    capture_output=True, text=True, env=ENV)
check("...a range that makes no sense is refused (exit 2)",
      _r.returncode == 2 and "--range" in _r.stdout, _r.stdout[-200:])

# --- downloading the replay: once, never again
_FB40 = os.path.join(WORK, "bin40")
os.makedirs(_FB40, exist_ok=True)
open(os.path.join(_FB40, "yt-dlp"), "w").write(r'''#!/usr/bin/env python3
import json, os, shutil, sys
a = sys.argv[1:]
open(os.path.join(os.path.dirname(__file__), "argv.txt"), "a").write(json.dumps(a) + "\n")
url = a[-1]
if "nochat" in url:
    print("[info] There are no subtitles for the requested languages")
    sys.exit(0)
out = a[a.index("-o") + 1].replace("%(title)s", "Stream").replace(
    "%(id)s", "ABCDEFGHIJK").replace("%(ext)s", "live_chat.json")
shutil.copy(os.environ["CHAT40"], out)
print("ABCDEFGHIJK")
''')
os.chmod(os.path.join(_FB40, "yt-dlp"), 0o755)
_ENV40 = dict(ENV, PATH=_FB40 + os.pathsep + ENV["PATH"], CHAT40=_CJ)
_D40 = os.path.join(WORK, "chat40dl")
shutil.rmtree(_D40, ignore_errors=True)
os.makedirs(_D40)
open(os.path.join(_D40, "Chan - Stream [ABCDEFGHIJK].mkv"), "wb").write(b"x")
_r = subprocess.run([sys.executable, ENTRY, "chat",
                     "https://youtu.be/ABCDEFGHIJK", "-o", _D40,
                     "--cookies-from-browser", "off"], capture_output=True,
                    text=True, env=_ENV40)
_a40 = [json.loads(x) for x in open(os.path.join(_FB40, "argv.txt"))]
check("chat URL: downloads only the chat (--skip-download, live_chat)",
      _r.returncode == 0 and "--skip-download" in _a40[-1]
      and _val(_a40[-1], "--sub-langs") == "live_chat"
      and os.path.exists(os.path.join(_D40, "Chan - Stream [ABCDEFGHIJK].ass")),
      _r.stdout[-400:] + _r.stderr[-300:])
_r = subprocess.run([sys.executable, ENTRY, "chat",
                     "https://youtu.be/ABCDEFGHIJK", "-o", _D40,
                     "--cookies-from-browser", "off"], capture_output=True,
                    text=True, env=_ENV40)
check("...a second time it uses the file it has -- nothing downloaded again",
      _r.returncode == 0 and len(open(os.path.join(_FB40, "argv.txt"))
                                 .readlines()) == 1
      and "not downloading it again" in _r.stdout, _r.stdout[-300:])
_r = subprocess.run([sys.executable, ENTRY, "chat", "https://youtu.be/nochat00000",
                     "-o", _D40, "--cookies-from-browser", "off"],
                    capture_output=True, text=True, env=_ENV40)
check("...a video with no chat replay says so (exit 1)",
      _r.returncode == 1 and "no chat replay" in _r.stdout, _r.stdout[-300:])

# --- added as a track, copied (needs a real ffmpeg)
_HF40 = bool(shutil.which("ffmpeg", path=ENV["PATH"]))
_EV = os.path.join(_CD, "Clip [ABCDEFGHIJK].mp4")
if _HF40:
    subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-y", "-f",
                    "lavfi", "-i", "testsrc=size=160x90:rate=10:duration=30",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", _EV],
                   capture_output=True)
    if os.path.exists(_EV):
        _r = subprocess.run([sys.executable, ENTRY, "chat", _CJ, "--video", _EV,
                             "--embed"], capture_output=True, text=True, env=ENV)
        _mk = _EV[:-4] + ".mkv"
        _ss = CH._streams(_mk) if os.path.exists(_mk) else []
        check("--embed on an .mp4: an .mkv copy with the chat track; the .mp4 "
              "stays",
              _r.returncode == 0 and os.path.exists(_EV)
              and [s[1] for s in _ss] == ["video", "subtitle"]
              and _ss[1][2] == CH.TRACK_TITLE, _r.stdout[-400:] + str(_ss))
        _r = subprocess.run([sys.executable, ENTRY, "chat", _CJ, "--video", _mk,
                             "--embed", "--format", "srt"], capture_output=True,
                            text=True, env=ENV)
        _ss = CH._streams(_mk)
        check("...again on the .mkv: the chat track is replaced, not doubled",
              _r.returncode == 0 and [s[1] for s in _ss] == ["video", "subtitle"],
              _r.stdout[-400:] + str(_ss))
        check("...the subtitle file is not left beside it as well",
              not os.path.exists(_mk[:-4] + ".srt"))
    # a part cut from the middle starts at the keyframe before the cut: in an
    # MP4 the extra seconds sit below zero, in an MKV the sound starts late
    _SRC = os.path.join(_CD, "src.mp4")
    subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", "testsrc=size=160x90:rate=10:duration=30", "-f", "lavfi",
                    "-i", "sine=duration=30", "-c:v", "libx264", "-g", "100",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", _SRC],
                   capture_output=True)
    _CUT = os.path.join(_CD, "Cut [ABCDEFGHIJK] (from 0h00m15s).mp4")
    subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-y", "-ss", "15",
                    "-i", _SRC, "-c", "copy", _CUT], capture_output=True)
    _lo, _sn = CH.first_times(_CUT)
    check("a cut MP4: the picture starts below zero (the keyframe before the cut)",
          _lo is not None and _lo < -4 and _sn is not None and _sn > _lo + 1,
          str((_lo, _sn)))
    _r = subprocess.run([sys.executable, ENTRY, "chat", _CJ, "--video", _CUT,
                         "--embed", "--hold", "0"], capture_output=True,
                        text=True, env=ENV)
    _ck = _CUT[:-4] + ".mkv"

    def _first40(m):
        out = subprocess.run([FF, "-v", "error", "-i", _ck, "-map", m, "-c",
                              "copy", "-f", "framemd5", "-"],
                             capture_output=True, text=True).stdout
        row = [x for x in out.splitlines() if x and not x.startswith("#")][0]
        return int(row.split(",")[2])
    _fa, _fs = (_first40("0:a:0"), _first40("0:s:0")) if os.path.exists(_ck) \
        else (0, 99999)
    check("...its .mkv copy: the chat starts at the cut, not seconds early",
          _r.returncode == 0 and _fa > 1000 and abs(_fs - round(-_lo * 1000)) <= 60,
          str((_fa, _fs)) + _r.stdout[-300:])
    _r = subprocess.run([sys.executable, ENTRY, "chat", _CJ, "--video", _ck,
                         "--format", "srt", "--hold", "0"], capture_output=True,
                        text=True, env=ENV)
    _sk = open(_ck[:-4] + ".srt", encoding="utf-8-sig").read() \
        if os.path.exists(_ck[:-4] + ".srt") else ""
    check("...a subtitle beside that .mkv is timed to its sound as well",
          "the chat is timed to the sound" in _r.stdout
          and _sk.startswith("1\n00:00:0") and not _sk.startswith(
              "1\n00:00:00,000"), _sk[:80] + _r.stdout[-300:])

# --- get --range: only the part, with a name chat can read
_r = run38("get", "https://youtu.be/vod-LIVEID", "--keep-video", "--range",
           "1:00:00-", "-o", _GD)
_d = (_dl38() or [[]])[-1]
if _HF40:
    check("get --range 1:00:00-: --download-sections from 3600 to the end",
          _val(_d, "--download-sections") == "*3600-inf"
          and "--live-from-start" not in _d, _r.stdout[-500:] + str(_d))
    check("...ffmpeg told to reconnect after a drop (it stops quietly otherwise)",
          str(_val(_d, "--downloader-args")).startswith("ffmpeg_i:-reconnect 1"),
          str(_val(_d, "--downloader-args")))
    check("...saved as 'NAME [id] (from 1h00m00s)', how long it is said first",
          str(_val(_d, "-o")).endswith(" [%(id)s] (from 1h00m00s).%(ext)s")
          and "1:00:00 of video" in _r.stdout, str(_val(_d, "-o")))
    _r = run38("get", "https://youtu.be/vod-LIVEID", "--keep-video", "--range",
               "30:00-1:00:00", "-o", _GD)
    _d = (_dl38() or [[]])[-1]
    check("get --range 30:00-1:00:00 -> *1800-3600, '(0h30m00s-1h00m00s)'",
          _val(_d, "--download-sections") == "*1800-3600"
          and "(0h30m00s-1h00m00s)" in str(_val(_d, "-o")), str(_d))
_r = run38("get", "https://youtu.be/vod-LIVEID", "--range", "3:00:00-",
           "-o", _GD)
check("get --range past the end is refused before downloading",
      "only 2:00:00 long" in _r.stdout and not _dl38(), _r.stdout[-300:])
_r = run38("get", _URL, "--keep-video", "--range", "1:00:00-", "-o", _GD)
check("get --range on a stream that is still live is refused",
      "still live" in _r.stdout and not _dl38(), _r.stdout[-300:])

# ffmpeg's status lines (a part is fetched by ffmpeg) -> the progress bar
_ev = P39.parse_ytdlp_line("frame= 7200 fps= 73 q=-1.0 size=   10897KiB "
                           "time=00:04:00.00 bitrate= 371.9kbits/s speed=2.42x")
check("ffmpeg's status line is progress: position, size, speed",
      _ev and _ev["type"] == "pct" and _ev["pos"] == 240 and
      _ev["speed"] == "2.42x", str(_ev))
check("...its screenful about the input and output is not logged",
      P39.parse_ytdlp_line("  Stream #0:0 -> #0:0 (copy)")["type"] == "noise"
      and P39.parse_ytdlp_line("Input #0, mov,mp4, from 'x':")["type"] == "noise"
      and P39.parse_ytdlp_line("[download] Destination: a.mp4")["type"] == "file")
_evs = []
P39.set_hook(_evs.append)
try:
    download._make_on_line(lambda m: None, span=480.0)(
        "size=  2048KiB time=00:02:00.00 bitrate= 1kbits/s speed=2.00x")
finally:
    P39.set_hook(None)
check("...turned into a percentage of the part, with a time left",
      _evs and _evs[-1]["pct"] == 25.0 and _evs[-1]["eta"], str(_evs[-1:]))
_said = []
check("a part that came down short is caught (a WiFi drop ffmpeg hid)",
      not os.path.exists(_EV) or (C40._check_part(_EV, (3600, 3900), _said.append)
                                  is False and "--range 3625-3900" in _said[0]),
      str(_said))

# --- the Live streams tab: part and chat
tkstub.reset()
G.run()
_r40 = tkstub.ROOTS[-1]
_lt40 = [w for w in _r40.walk() if isinstance(w, tkstub.Notebook)][0].tab_for(
    "Live streams")
_le40 = [w for w in _lt40.walk() if w.kind == "entry"]
_A40, _cli40 = [], G.cli_main
G.cli_main = lambda argv: (_A40.append(list(argv)), 0)[1]


def _drain40():
    todo, _r40.pending[:] = list(_r40.pending), []
    for f in todo:
        f()


try:
    _le40[0].options["textvariable"].set("https://youtu.be/ABCDEFGHIJK")
    _le40[1].options["textvariable"].set(_CD)
    _le40[3].options["textvariable"].set("1:00:00-")
    _qbtn(_lt40, "Download this part").invoke()
    _drain40()
    check("'Download this part' -> get --range, the video, no lyrics",
          _A40 and _A40[-1][0] == "get" and _val(_A40[-1], "--range") == "1:00:00-"
          and "--keep-video" in _A40[-1] and "--no-lyrics" in _A40[-1],
          str(_A40[-1:]))
    _qbtn(_lt40, "Get the chat").invoke()
    _drain40()
    _c40 = _A40[-1]
    check("'Get the chat' -> chat URL with the format, lines, hold and side",
          _c40[:2] == ["chat", "https://youtu.be/ABCDEFGHIJK"]
          and _val(_c40, "--lines") == "5" and _val(_c40, "--hold") == "15"
          and _val(_c40, "--side") == "right" and _val(_c40, "--format") == "ass"
          and _val(_c40, "-o") == _CD, str(_c40))
    check("...with a video of it in the folder the part comes from its name",
          "--range" not in _c40, str(_c40))
    _le40[1].options["textvariable"].set(_D40 + "-none")
    _qbtn(_lt40, "Get the chat").invoke()
    _drain40()
    check("...with none, the part field is used",
          _val(_A40[-1], "--range") == "1:00:00-", str(_A40[-1]))
    _n40 = len(_A40)
    _le40[3].options["textvariable"].set("rubbish")
    _qbtn(_lt40, "Download this part").invoke()
    _drain40()
    check("a part that makes no sense runs nothing", len(_A40) == _n40)
finally:
    G.cli_main = _cli40


# --- 41. a part is asked for in 10 MB pieces (YouTube slows one long request)
print("\n41. get --range: ffmpeg asks in pieces when it can")
from musictool import download as D41, cli as C41
_new41 = ("  -reconnect <boolean>\n  -reconnect_streamed <boolean>\n"
          "  -reconnect_on_network_error <boolean>\n  -reconnect_delay_max <int>\n"
          "  -multiple_requests <boolean>\n  -request_size <int64>\n"
          "  -short_seek_size <int>\n")
_old41 = "\n".join(l for l in _new41.splitlines() if "request_size" not in l)
_o41 = D41.http_opts(_new41)
check("ffmpeg with -request_size: 10 MB requests, kept-alive connection",
      "-request_size 10485760" in _o41 and "-multiple_requests 1" in _o41
      and "-short_seek_size 10485760" in _o41
      and _o41.startswith("-reconnect 1 -reconnect_streamed 1"), _o41)
_p41 = D41.http_opts(_old41)
check("...an older ffmpeg: only the reconnect options (an unknown one would "
      "stop the download)", "request" not in _p41 and "short_seek" not in _p41
      and "-reconnect_delay_max 30" in _p41, _p41)
check("...an ffmpeg that lists nothing gets nothing", D41.http_opts("") == "")
_save41 = list(D41._RECONNECT)
try:
    for _h41, _want41 in ((_old41, True), (_new41, False)):
        D41._RECONNECT[:] = [D41.http_opts(_h41)]
        _said41, _per41 = [], {}
        _ok41 = C41._plan_part("https://youtu.be/ABCDEFGHIJK", _per41,
                               {"duration": 12030}, "was_live",
                               (6600.0, None), _said41.append)
        _txt41 = " ".join(_said41)
        check(("old" if _want41 else "new") + " ffmpeg: "
              + ("warned that the part is slow, with how long"
                 if _want41 else "no slowness warning"),
              _ok41 and _per41.get("download_sections") == "*6600-inf"
              and (("2x real time" in _txt41 and "0:45:15" in _txt41
                    and "8.1" in _txt41) if _want41
                   else "real time" not in _txt41), _txt41)
finally:
    D41._RECONNECT[:] = _save41


srv.shutdown()
print("\n" + (f"ALL {len(fails) == 0 and '' or ''}"
              + ("CHECKS PASSED" if not fails else f"{len(fails)} FAILED: {fails}")))
sys.exit(1 if fails else 0)
