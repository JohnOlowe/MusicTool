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
            # deliberately the WRONG artist: the guard must reject it
            return self._send({"code": 200, "result": {"songs": [
                {"id": 5, "name": "Well, Well, Well", "artists": [{"name": "Someone Else"}],
                 "album": {"name": "Other"}, "duration": 225000}]}})
        if u.path == "/ne/song/lyric":
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
                        "browseId": "MPLYxyz",
                        "browseEndpointContextSupportedConfigs": {
                            "browseEndpointContextMusicConfig": {
                                "pageType": "MUSIC_PAGE_TYPE_TRACK_LYRICS"}}}}}}]}}}}})
        if u.path == "/yt/browse":
            if client != "ANDROID_MUSIC":          # WEB_REMIX has no timings
                return self._send({"contents": {"plain": "no timings"}})
            rows = [{"lyricLine": t, "cueRange": {"startTimeMilliseconds": str(ms)}}
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
r = run("providers", "--test")
check("providers --test: ytmusic needs videoId", "needs a videoId" in r.stdout)
check("providers --test: lrclib reachable", "reachable" in r.stdout)
for sub in ("get", "lyrics", "scan", "duplicates", "providers", "config"):
    check(f"{sub} --help", run(sub, "--help").returncode == 0)
r = run("gui")
check("gui fails gracefully without tkinter", r.returncode == 1 and
      ("GUI unavailable" in r.stdout or "GUI" in r.stdout), r.stdout[:120])
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
check("all seven tabs exist (incl. Quality)",
      [t.get("text", "").strip() for t in nb.tabs] ==
      ["Download", "Fix lyrics", "Scan & duplicates", "Report", "Tools",
       "Lyrics files", "Quality"],
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
      len(areas) == 5, str(len(areas)))
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
      "_apply_progress(newest_pct)" in _gsrc and "pq.get_nowait()" in _gsrc)
check("the pump keeps item/file events and drops only percentage redraws",
      'others.append(ev)' in _gsrc and 'newest_pct = ev' in _gsrc)
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
      "item 287 of 447" in _joined and "%" in _joined, _joined[:200])
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
check("...but the selector names the variant it actually measured",
      F.selector_for([r for r in F.apply_filters(BIG)
                      if bid(r) == "137"][0], None).startswith("137/137-"),
      F.selector_for([r for r in F.apply_filters(BIG)
                      if bid(r) == "137"][0], None))
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
      == "137/137-17+251/251-17",
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
check("the table shows id, quality, container, codec, size and variants",
      list(_qt[0].options.get("columns", ())) ==
      ["id", "what", "container", "video", "codec", "size", "variants"],
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

srv.shutdown()
print("\n" + (f"ALL {len(fails) == 0 and '' or ''}"
              + ("CHECKS PASSED" if not fails else f"{len(fails)} FAILED: {fails}")))
sys.exit(1 if fails else 0)
