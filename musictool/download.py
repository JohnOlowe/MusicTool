"""yt-dlp wrapper: single videos, playlists, and a videoId index."""

import json
import os
import re
import shutil
import subprocess
import sys
import time

from . import cancel, formats, progress
from .util import (ytdlp_cmd, have_ffmpeg, is_windows, is_net_error, net_sleeps,
                   detect_browser, js_runtime, ffmpeg_hint)

INDEX_NAME = ".musictool-index.json"

# Matches the naming your get_music.sh used: "Artist - Title.m4a", with the
# " - Topic" suffix that YouTube's auto-generated music channels add removed.
DEFAULT_TEMPLATE = "%(uploader)s - %(title)s.%(ext)s"
# --replace-in-metadata needs FIELDS, REGEX and REPLACE -- passing only the
# first two makes yt-dlp swallow the *next* option as the replacement, which
# both breaks the artist cleanup and leaves the following template to be
# treated as a URL ("[generic] ... is not a valid URL"). The empty string is
# what actually deletes the suffix.
TOPIC_FIX = ("uploader", " - Topic$", "")


# What YouTube says when it will not serve a video, and what to do about it.
BLOCKERS = (
    # a full disk: Linux/macOS, Windows, and Python's own wording of both
    ("no space left on device", "disk_full"),
    ("not enough space on the disk", "disk_full"),
    ("[errno 28]", "disk_full"),
    ("[winerror 112]", "disk_full"),
    ("sign in to confirm you're not a bot", "bot"),
    ("not a bot", "bot"),
    ("please sign in", "bot"),
    ("confirm your age", "age"),
    ("age-restricted", "age"),
    ("private video", "private"),
    ("members-only", "members"),
    ("join this channel", "members"),
    ("video unavailable", "unavailable"),
    ("no longer available", "unavailable"),
    # --- the ones that are not really about the video at all. These are the
    #     failures a cookies.txt / anti-bot setup produces, and each of them
    #     has a different fix, so they must not all land in "bot".
    ("sign in again to continue", "session"),
    ("sign in again", "session"),
    ("failed to decrypt with dpapi", "dpapi"),
    ("could not copy chrome cookie database", "cookies_locked"),
    ("login with oauth is no longer supported", "oauth_gone"),
    ("no supported javascript runtime", "js_runtime"),
    ("n challenge solving failed", "js_runtime"),
    ("signature solving failed", "js_runtime"),
    # what yt-dlp says when the challenge failed and only the storyboard
    # pictures are left -- it is the JS setup, not the video
    ("only images are available", "js_runtime"),
    ("pot:bgutil", "pot_server"),
    ("127.0.0.1:4416", "pot_server"),
    ("the page needs to be reloaded", "reload"),
    ("requested format is not available", "no_format"),
)

# Chromium-derived browsers share one cookie format and one encryption lock,
# which is why the error message says "Chrome" even for Edge/Brave/Vivaldi.
CHROMIUM = ("chrome", "chromium", "edge", "brave", "vivaldi", "opera", "whale")

# What solving YouTube's JS challenge takes (yt-dlp 2026.10.26 --help).
JS_FIX = ("It needs two things: the solver scripts -- pip install -U "
          "\"yt-dlp[default]\" (plain 'pip install yt-dlp' leaves them out), "
          "or add --remote-components ejs:github -- and a JavaScript runtime: "
          "deno, or node (--js-runtimes node; musictool adds it when node is "
          "all you have). 'musictool doctor' shows which one is missing.")

ADVICE = {
    "bot": ("YouTube wants a logged-in session. Log in to YouTube in Firefox "
            "and use --cookies-from-browser firefox -- it reads your existing "
            "login, nothing to export. (On Windows, Chrome and Edge lock their "
            "cookies with App-Bound Encryption, which yt-dlp cannot open; on "
            "macOS/Linux they work too.) Or pass an exported cookies.txt with "
            "--cookies."),
    "age": ("Age-restricted: sign in with an account that can view it "
            "(--cookies-from-browser), or download it in the browser."),
    "private": "This video is private; only its owner's account can fetch it.",
    "members": "Members-only: needs the membership account's cookies.",
    "unavailable": "YouTube says this video is unavailable or removed.",
    "network": ("Could not reach YouTube at all -- check the connection, then "
                "try again (this is not a block)."),
    "session": ("Your exported cookies.txt was flagged as a hijacked session "
                "and killed -- refreshing the page will not bring it back. "
                "Re-export from a fresh incognito login, or skip the file "
                "entirely and use --cookies-from-browser firefox."),
    "dpapi": ("Chrome and Edge encrypt their cookies with App-Bound "
              "Encryption, which yt-dlp cannot open. Log in to YouTube in "
              "Firefox and use --cookies-from-browser firefox -- Firefox has "
              "no such lock. (Edge reports this as a 'Chrome' error because "
              "it is the same code underneath.)"),
    "cookies_locked": ("The browser is still holding its cookie database "
                       "open. Close it completely -- on Windows Edge keeps "
                       "background processes alive (Startup Boost), so kill "
                       "them in Task Manager or turn Startup Boost off -- "
                       "then run the command again."),
    "oauth_gone": ("yt-dlp dropped the TV/OAuth login. Use "
                   "--cookies-from-browser firefox, or an exported "
                   "cookies.txt with --cookies."),
    "js_runtime": ("YouTube's challenge was not solved, so yt-dlp got no audio "
                   "or video streams. " + JS_FIX),
    "pot_server": ("The bgutil PO-token plugin is installed but its server is "
                   "not answering on 127.0.0.1:4416. Start it (docker run -p "
                   "4416:4416 brainicism/bgutil-ytdlp-pot-provider) or "
                   "uninstall the plugin -- this is a plugin, not yt-dlp's "
                   "own JS solver."),
    "reload": ("That is the bot wall talking. It almost always means the JS "
               "challenge was not solved: install deno or node and add "
               "--remote-components ejs:github (see 'js runtime' in "
               "musictool doctor)."),
    "disk_full": ("The disk is full. Nothing downloaded so far is lost: free "
                  "some space (or choose another folder with -o) and run the "
                  "same download again -- it carries on from where it stopped. "
                  "A live stream can be watched meanwhile: musictool live "
                  "FOLDER --watch."),
    "no_format": ("That format is not offered to your account or client. "
                  "List what is really there with: musictool formats URL "
                  "--show-audio --all-formats. If that list is empty too, "
                  "YouTube's challenge was not solved -- see 'ejs' and 'js "
                  "runtime' in musictool doctor."),
}


def classify_error(text):
    """The failure kind, so each one gets its own fix.

    'bot', 'age', 'private', 'members', 'unavailable', 'network', 'unknown',
    plus the cookie/anti-bot ones: 'session', 'dpapi', 'cookies_locked',
    'oauth_gone', 'js_runtime', 'pot_server', 'reload', 'no_format', and
    'disk_full'.
    """
    t = (text or "").lower()
    for needle, kind in BLOCKERS:
        if needle in t:
            return kind
    if is_net_error(text or ""):
        return "network"
    return "unknown"


BOT_HINT = (
    "YouTube is refusing this without a login ('Sign in to confirm you're not a "
    "bot'). Fix: log in to YouTube in Firefox and set 'Cookies from browser' to "
    "firefox -- it reads your existing login, nothing to export. On Windows, "
    "Chrome and Edge lock their cookies with App-Bound Encryption, which yt-dlp "
    "cannot open (macOS/Linux: any browser works). Or export a cookies.txt and "
    "pass --cookies.")


def cookie_args(o, log=None):
    """--cookies / --cookies-from-browser for this run.

    A cookies.txt file always wins when given. Otherwise 'auto' looks for an
    installed browser with a cookie database and uses the newest one, which on
    Windows means the login is picked up with no exporting at all. Detection
    failing is not an error: we just say so and carry on.
    """
    say = log or (lambda m: None)
    path = o.get("cookies")
    if path:
        if not os.path.exists(path):
            say(f"WARNING: cookies file not found: {path}")
        return ["--cookies", path]
    spec = (o.get("cookies_from_browser") or "").strip().lower()
    if spec in ("", "off", "none", "no"):
        return []
    if spec == "auto":
        name, store = detect_browser()
        if not name:
            say("note: no browser cookie database found; downloading without "
                "cookies (set 'cookies from browser' if YouTube asks to sign in)")
            return []
        say(f"cookies: reading your {name} login ({store})")
        return ["--cookies-from-browser", name]
    return ["--cookies-from-browser", spec]


def load_index(directory):
    p = os.path.join(directory, INDEX_NAME)
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_index(directory, index):
    os.makedirs(directory, exist_ok=True)
    p = os.path.join(directory, INDEX_NAME)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(index, fh, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, p)


def record(directory, filepath, video_id, url):
    idx = load_index(directory)
    idx[os.path.basename(filepath)] = {"video_id": video_id, "url": url}
    save_index(directory, idx)


def ffprobe_cmd():
    """ffprobe ships with ffmpeg; None when only one of them is installed."""
    return shutil.which("ffprobe")


STREAM_RE = re.compile(
    r"Stream #\d+:\d+(?:\((?P<lang>[a-z]{2,3})\))?\s*:\s*"
    r"(?P<kind>Video|Audio|Subtitle|Attachment)", re.I)


def streams(path):
    """[(kind, lang)] for every stream in a file, using whatever is installed.

    ffprobe when it is there; otherwise `ffmpeg -i`, which prints the same
    information on stderr -- a ffmpeg-only install (Termux, a stripped Windows
    build) must not silently lose the ability to tell one language from
    another.
    """
    exe = ffprobe_cmd()
    if exe:
        try:
            out = subprocess.run(
                [exe, "-v", "error", "-of", "csv=p=0",
                 "-show_entries", "stream=codec_type:stream_tags=language",
                 path], capture_output=True, text=True, timeout=120)
            rows = []
            for line in (out.stdout or "").splitlines():
                cells = [c.strip() for c in line.split(",")]
                if cells and cells[0]:
                    rows.append((cells[0].lower(),
                                 (cells[1].lower() if len(cells) > 1 else "")))
            if rows:
                return rows
        except Exception:                                # noqa: BLE001
            pass
    exe = shutil.which("ffmpeg")
    if not exe:
        return []
    try:
        out = subprocess.run([exe, "-hide_banner", "-i", path],
                             capture_output=True, text=True, timeout=120)
    except Exception:                                    # noqa: BLE001
        return []
    text = (out.stderr or "") + (out.stdout or "")
    return [(m.group("kind").lower(), (m.group("lang") or "").lower())
            for m in STREAM_RE.finditer(text)]


def audio_track_lang(path):
    """The language tag on a file's first audio track; '' when not tagged.

    '' does not mean English -- it means nobody wrote the tag down, which is
    exactly why relang says "unknown" instead of guessing.
    """
    for kind, lang in streams(path):
        if kind == "audio":
            return lang
    return ""


def has_video_stream(path):
    return any(kind == "video" for kind, _lang in streams(path))


def fetch_format(url, o, fmt_id, workdir, log=print):
    """Download ONE format on its own -- the audio track, not the video.

    Returns the file it wrote, or None. This is the cheap half of a repair:
    a 40 MiB audio track instead of the 600 MiB video already on disk.
    """
    args = list(ytdlp_cmd()) + [
        "--no-warnings", "--no-playlist", "-f", str(fmt_id),
        "-o", os.path.join(workdir, "audio-%(id)s.%(ext)s"),
        "--no-simulate", "--no-quiet",
        "--print", "after_move:%(filepath)s", url]
    args += cookie_args(o)
    log("  $ " + " ".join(_q(a) for a in args))
    try:
        proc = subprocess.run(args, capture_output=True, text=True,
                              timeout=3600)
    except Exception as e:                               # noqa: BLE001
        log(f"  !! {type(e).__name__}: {e}")
        return None
    for line in reversed((proc.stdout or "").splitlines()):
        line = line.strip()
        if line and os.path.exists(line):
            return line
    err = ((proc.stderr or proc.stdout or "").strip()[-300:]
           if proc.returncode else "yt-dlp printed no file")
    kind, advice = classify_error(err)
    log(f"  !! could not fetch that track ({kind}): {err[:160]}")
    if advice:
        log(f"     -> {advice}")
    return None


def swap_audio(video_path, audio_path, log=print):
    """Keep this file's video, take the other file's audio. No re-encoding.

    `-map 0` pulls in everything the old file had -- picture, cover art,
    subtitles -- then `-map -0:a` drops only its audio, so nothing else about
    the file changes.
    """
    exe = shutil.which("ffmpeg")
    if not exe:
        log("  !! ffmpeg is needed to re-mux: " + ffmpeg_hint())
        return False
    ext = os.path.splitext(video_path)[1].lower()
    out = video_path + (".new" + (ext or ".mkv"))
    cmd = [exe, "-y", "-v", "error", "-i", video_path, "-i", audio_path,
           "-map", "0", "-map", "-0:a", "-map", "1:a:0",
           "-c", "copy", "-map_metadata", "0", "-map_chapters", "0", out]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    except Exception as e:                               # noqa: BLE001
        log(f"  !! {type(e).__name__}: {e}")
        return False
    if proc.returncode or not os.path.exists(out):
        log(f"  !! ffmpeg could not re-mux: "
            f"{(proc.stderr or '').strip()[-200:]}")
        if os.path.exists(out):
            os.remove(out)
        return False
    os.replace(out, video_path)
    return True


def build_args(url, o):
    """Translate options into a yt-dlp argv list."""
    args = list(ytdlp_cmd())
    args += ["--no-warnings", "--restrict-filenames" if o.get("ascii_names") else
             "--no-restrict-filenames"]

    if o.get("no_playlist"):
        args.append("--no-playlist")
    elif o.get("yes_playlist"):
        args.append("--yes-playlist")
    if o.get("playlist_items"):
        args += ["--playlist-items", str(o["playlist_items"])]
    if o.get("playlist_start"):
        args += ["--playlist-start", str(o["playlist_start"])]
    if o.get("playlist_end"):
        args += ["--playlist-end", str(o["playlist_end"])]
    args += cookie_args(o)
    if o.get("download_archive"):
        args += ["--download-archive", o["download_archive"]]
    if o.get("rate_limit"):
        args += ["--limit-rate", o["rate_limit"]]
    if o.get("proxy"):
        args += ["--proxy", o["proxy"]]
    if o.get("concurrent"):
        args += ["--concurrent-fragments", str(o["concurrent"])]
    # live streams (see live.plan): from the start, wait for one that has not
    # begun, or only a stretch of a video (the rest of a stopped recording)
    if o.get("live_from_start"):
        args.append("--live-from-start")
    if o.get("wait_for_video"):
        args += ["--wait-for-video", str(o["wait_for_video"])]
    if o.get("download_sections"):
        args += ["--download-sections", str(o["download_sections"])]
        # A part is fetched by ffmpeg, not yt-dlp's own downloader, and ffmpeg
        # gives up at the first dropped connection -- quietly: measured, a
        # 15 s drop turned a 4-minute part into 6 seconds, "100%", exit code
        # 0. With these it reconnects where it stopped (1, 3, 7, 15 s ...);
        # a longer outage is caught afterwards (cli._download_part).
        rc = ffmpeg_reconnect_args()
        if rc:
            args += ["--downloader-args", "ffmpeg_i:" + rc]
    args += ["--retries", str(o.get("retries", 5))]
    args += ["--fragment-retries", str(o.get("retries", 5))]
    # Without these a WiFi drop silently ruins a download made of pieces
    # (DASH: every live stream, and a stream that has just ended): yt-dlp
    # used up its 5 retries in milliseconds, then SKIPPED the piece and went
    # on -- measured, a 20 s drop skipped 64 of 238 pieces, said "100%", and
    # exited 0 with half the video missing. Now each retry waits (1, 2, 4,
    # 8, 16 s: about half a minute in all), and a piece that still cannot be
    # had stops yt-dlp instead, so the network loop in download() waits for
    # the connection and carries on from that piece.
    args += ["--retry-sleep", "http:exp=1:30",
             "--retry-sleep", "fragment:exp=1:30",
             "--abort-on-unavailable-fragments"]
    # --continue resumes a half-finished .part file after an outage
    # instead of throwing it away and starting the download over.
    args.append("--continue")

    if not o.get("keep_video"):
        args += ["-x", "--audio-format", o.get("audio_format", "m4a")]
        if o.get("audio_format") != "best":
            # "best" (--exact) copies the audio as it is; a quality would
            # only matter to a re-encode
            args += ["--audio-quality", str(o.get("audio_quality", 4))]
    if o.get("embed_metadata", True):
        args.append("--embed-metadata")
    if o.get("embed_thumbnail"):
        args.append("--embed-thumbnail")
        if not o.get("keep_video"):
            args += ["--convert-thumbnails", "png"]
    if o.get("embed_chapters"):
        args.append("--embed-chapters")
    if o.get("embed_subs"):
        args.append("--embed-subs")
        if o.get("sub_langs"):
            args += ["--sub-langs", o["sub_langs"]]
    # a concrete quality, chosen from the size list (`formats`), or a raw -f
    if o.get("format_selector"):
        args += ["-f", o["format_selector"]]
    else:
        # No selector of our own: ask yt-dlp for the language you want, with a
        # fallback to its usual choice when nothing is tagged. Its own default
        # ranks the ORIGINAL language first, which is not the same as the
        # language you asked for on a dubbed video.
        _sel = formats.audio_lang_selector(o.get("keep_video"),
                                           o.get("audio_lang"))
        if _sel:
            args += ["-f", _sel]
    if o.get("merge_format"):
        args += ["--merge-output-format", o["merge_format"]]
    # --exact with two audio (or video) streams: keep them all, as chosen
    if o.get("audio_multistreams"):
        args.append("--audio-multistreams")
    if o.get("video_multistreams"):
        args.append("--video-multistreams")
    args += runtime_args(o)
    # Keep the artist tag clean, exactly like the original shell script did.
    args += list(("--replace-in-metadata",) + TOPIC_FIX)
    args += ["--parse-metadata", "%(artist,uploader)s:%(meta_artist)s"]
    args += ["-o", o.get("template") or DEFAULT_TEMPLATE]
    args += ["-P", o.get("output_dir") or "."]
    args.append(url)
    return args


_RECONNECT = []


def ffmpeg_reconnect_args():
    """ffmpeg's reconnect options, as one string -- only the ones this ffmpeg
    knows (an unknown option would stop every part download). Checked once."""
    if not _RECONNECT:
        want = ["reconnect", "reconnect_streamed", "reconnect_on_network_error",
                "reconnect_delay_max"]
        have = ""
        try:
            exe = shutil.which("ffmpeg")
            if exe:
                have = subprocess.run([exe, "-hide_banner", "-h", "protocol=http"],
                                      capture_output=True, text=True, timeout=20,
                                      errors="replace").stdout or ""
        except Exception:                                   # noqa: BLE001
            have = ""
        opts = [w for w in want if re.search(rf"^\s*-{w}\b", have, re.M)]
        val = {"reconnect_delay_max": "30"}
        _RECONNECT.append(" ".join(f"-{w} {val.get(w, '1')}" for w in opts))
    return _RECONNECT[0]


def runtime_args(o):
    """--remote-components / --js-runtimes for every yt-dlp call.

    yt-dlp enables only deno by default. When node is the only runtime
    installed, it is enabled here, so YouTube's challenge can still be solved
    without remembering --js-runtimes node. An explicit --js-runtimes always
    wins."""
    args = []
    if o.get("remote_components"):
        args += ["--remote-components", o["remote_components"]]
    js = o.get("js_runtimes")
    if not js and js_runtime()[0] == "node":
        js = "node"
    if js:
        args += ["--js-runtimes", js]
    return args


def probe(url, o):
    """Cheap metadata read, no download. -> the info dict, or {"error": ...}.

    yt-dlp's warnings come back in info["_warnings"]. They used to be thrown
    away (--no-warnings), and with them the one line that says *why* a video
    has no formats: "n challenge solving failed".
    """
    args = list(ytdlp_cmd()) + [
        "--flat-playlist" if o.get("flat") else "--no-playlist",
        # -J still runs format selection, and when the JS challenge fails
        # (only storyboard pictures left) that selection dies with "Requested
        # format is not available" -- which hid the real cause and sent you
        # to `formats`, the very command that had just failed. This keeps the
        # info, so the empty list can be explained.
        "-J", "--ignore-no-formats-error", url]
    # A live stream offers different formats from the start than from now,
    # and from the start is what `get` records -- so list those. It changes
    # nothing for a video that is not live.
    if not o.get("live_from_now"):
        args.insert(-1, "--live-from-start")
    args += cookie_args(o)
    # check/formats/quality picks used to ignore --js-runtimes and
    # --remote-components here, so only the download itself honoured them
    args += runtime_args(o)
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=180)
    except Exception as e:
        return {"error": str(e)}
    lines = [x.strip() for x in (out.stderr or "").splitlines() if x.strip()]
    warns = [x for x in lines if x.startswith("WARNING:")]
    if out.returncode != 0:
        errs = [x for x in lines if x.startswith("ERROR:")]
        # the ERROR first (callers show line one), then the warnings that
        # explain it, so classify_error sees the cause and not just the effect
        why = [w for w in warns if classify_error(w) != "unknown"]
        text = "\n".join(errs + why) if errs else "\n".join(lines[-6:])
        return {"error": (text or (out.stdout or "").strip())[-1200:]}
    try:
        info = json.loads(out.stdout)
    except Exception:
        return {"error": "could not parse yt-dlp output"}
    if isinstance(info, dict):
        info["_warnings"] = warns
    return info


def no_media_reason(info):
    """Why a probe that worked lists no audio or video, or None if it does.

    The usual cause is the JS challenge: yt-dlp then sees only the storyboard
    pictures, which are not formats you can download, so the table is empty.
    """
    if not isinstance(info, dict) or info.get("error"):
        return None
    fmts = info.get("formats")
    if not isinstance(fmts, list):
        return None     # a playlist, or a site that reports no format list
    media = [f for f in (fmts or [])
             if (f.get("vcodec") or "none") != "none"
             or (f.get("acodec") or "none") != "none"
             or (f.get("vcodec") is None and f.get("acodec") is None
                 and f.get("url") and f.get("ext") != "mhtml")]
    if media:
        return None
    warns = "\n".join(info.get("_warnings") or [])
    pics = len(fmts or [])
    said = [w for w in (info.get("_warnings") or [])
            if classify_error(w) == "js_runtime"]
    if said or pics:
        return ("no audio or video streams"
                + (f", only {pics} preview picture(s)" if pics else "")
                + (" -- YouTube's challenge was not solved. " if said else
                   " -- most likely YouTube's challenge was not solved. ")
                + JS_FIX
                + ("\n   yt-dlp said: " + said[0][:200] if said else ""))
    if classify_error(warns) != "unknown":
        return ("no audio or video streams. "
                + ADVICE[classify_error(warns)])
    return "yt-dlp found no audio or video streams for this video."


def print_target_name(url, o):
    """Ask yt-dlp what it would name this file, without downloading."""
    args = list(ytdlp_cmd()) + [
        "--no-playlist", "--simulate", "--no-warnings",
        "--replace-in-metadata", *TOPIC_FIX,
        "--parse-metadata", "%(artist,uploader)s:%(meta_artist)s",
        "--print", "%(uploader)s - %(title)s", url]
    args += cookie_args(o)
    args += runtime_args(o)
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=120)
    except Exception:
        return None
    line = (r.stdout or "").strip().splitlines()
    return line[0].strip() if line and r.returncode == 0 else None


def _run_once(args, log, on_line=None, on_file=None):
    """Run yt-dlp once, streaming its output so progress shows immediately.

    stdout and stderr are merged and read in chunks split on \r as well as
    \n, because yt-dlp redraws its percentage with carriage returns -- a plain
    read would buffer everything until the process exits.
    """
    try:
        proc = subprocess.Popen(args, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT)
    except FileNotFoundError:
        return [], 'yt-dlp not found. Install it: pip install -U "yt-dlp[default]"'
    except Exception as e:
        return [], str(e)
    segments = []
    results = []
    with proc.stdout:
        for seg in progress.iter_segments(proc.stdout.fileno()):
            # Stop is cooperative: kill yt-dlp between output segments, so the
            # files that already landed keep their place in the results.
            if cancel.requested():
                cancel.stop_process(proc)
                return results, cancel.CANCELLED
            seg = progress.strip_ansi(seg).rstrip()
            if not seg:
                continue
            segments.append(seg)
            if on_line:
                on_line(seg)
            if "\t" in seg and not seg.startswith("["):
                vid, path = seg.split("\t", 1)
                results.append((path.strip(), vid.strip()))
                if on_file:
                    on_file(path.strip(), vid.strip())
    proc.wait()
    if cancel.requested():
        cancel.stop_process(proc)
        return results, cancel.CANCELLED
    if proc.returncode != 0:
        err = _error_text(segments) or f"yt-dlp exited {proc.returncode}"
        if "not a bot" in err or "Sign in to confirm" in err:
            err += "\n" + BOT_HINT
        return results, err
    return results, None


RETRY_LOG_EVERY = 10.0      # seconds between logged retry lines


def _error_text(segments):
    """What went wrong in a failed run, for the log and for retryable().

    Every ERROR line of the run (with the line after a bare "ERROR:"), then
    the last few lines that are not progress redraws. It used to be just the
    last 8 lines: when the video failed on a dropped connection, yt-dlp went
    on to fetch the audio, so those 8 lines were audio percentages -- the
    error was never seen, the network wait never happened, and "!!" printed
    a column of percentages.
    """
    errors = []
    segments = [progress.unglue(x) for x in segments]
    for i, seg in enumerate(segments):
        if seg.lstrip().startswith("ERROR:"):
            line = seg.strip()
            if line == "ERROR:" and i + 1 < len(segments):
                line += " " + segments[i + 1].strip()
            if line not in errors:
                errors.append(line)
    quiet = [x for x in segments
             if (progress.parse_ytdlp_line(x) or {}).get("type") not in
             ("pct", "retry") and not x.lstrip().startswith("ERROR:")]
    text = "\n".join(quiet[-4:] + errors[-4:])
    return text[-900:]


def _make_on_line(log, tag="", tty=None, stats=None, span=None):
    """Forward readable lines to the log; send every event to the progress hook.

    The progress redraws (several a second, per stream) would flood the log,
    so those go only to the hook (the GUI bar) -- or, in a terminal with no
    GUI, are redrawn on one line. Item/file/done, skipped pieces and
    unrecognised (error) lines are logged. A dropped connection makes yt-dlp
    print one "Got error ... Retrying" per attempt, per piece, per worker;
    those are logged at most once every RETRY_LOG_EVERY seconds, with a count
    of the rest.

    `tag` marks every event with the download it came from, so several running
    at once can each have their own line in the UI instead of fighting over one.
    `stats` (a dict) collects what happened: "progress" once anything arrived.
    `span`: the seconds of video being fetched when only a part is taken --
    ffmpeg reports a position, not a percentage, and this turns it into one.
    """
    st = {"last_retry": 0.0, "hidden": 0, "drawn": 0, "last_draw": 0.0}
    stats = stats if stats is not None else {}

    def out(text):
        if st["drawn"]:
            # leave the redrawn progress line in place, start a fresh one
            tty.write("\n")
            st["drawn"] = 0
        log(text)

    def on_line(seg):
        seg = progress.unglue(seg)
        if seg.strip() in ("ERROR:", "WARNING:"):
            # yt-dlp writes "ERROR: " and then the message after a carriage
            # return, so it arrives as two pieces: keep them on one line
            st["prefix"] = seg.strip() + " "
            return
        prefix, st["prefix"] = st.get("prefix", ""), ""
        if prefix and seg.lstrip().startswith(prefix.strip()):
            prefix = ""
        ev = progress.parse_ytdlp_line(seg)
        if ev is not None and tag:
            ev["tag"] = tag
        if ev is not None and ev.get("pos") is not None and span:
            ev["pct"] = min(100.0, 100.0 * ev["pos"] / span)
            m = re.match(r"([\d.]+)x", ev.get("speed") or "")
            if m and float(m.group(1)) > 0:
                left = max(0.0, span - ev["pos"]) / float(m.group(1))
                ev["eta"] = progress.fmt_seconds(left)
        if ev is not None and ev["type"] == "noise":
            return                    # ffmpeg describing its input/output
        progress.report(ev)
        kind = ev["type"] if ev else None
        if kind == "pct":
            stats["progress"] = True
            if tty is not None and progress.get_hook() is None:
                now = time.time()
                if now - st["last_draw"] < 0.2:     # 5 redraws a second
                    return
                st["last_draw"] = now
                if ev.get("pos") is not None and span:
                    # ffmpeg's own line is frame=/q=/bitrate= noise to a reader
                    text = (f"    {ev['pct']:5.1f}% of the part  "
                            f"{progress.fmt_seconds(ev['pos'])} of "
                            f"{progress.fmt_seconds(span)}"
                            + (f"  {ev['got']}" if ev.get("got") else "")
                            + (f"  {ev['speed']}" if ev.get("speed") else "")
                            + (f"  ETA {ev['eta']}" if ev.get("eta") else ""))
                else:
                    text = "    " + re.sub(r"\s+", " ", seg.strip())[:110]
                tty.write("\r" + text.ljust(st["drawn"]))
                tty.flush()
                st["drawn"] = max(len(text), 1)
            return
        if kind == "retry" and not ev.get("gave_up"):
            now = time.time()
            if now - st["last_retry"] < RETRY_LOG_EVERY:
                st["hidden"] += 1
                return
            st["last_retry"] = now
            more = (f"   (+{st['hidden']} more like it)" if st["hidden"] else "")
            st["hidden"] = 0
            out("    " + prefix + ev.get("msg", seg) + more)
            return
        out("    " + prefix + seg)
    return on_line


def _tty_stream():
    """stdout when it is a terminal (the progress line is redrawn in place)."""
    try:
        return sys.stdout if sys.stdout.isatty() else None
    except Exception:                                   # noqa: BLE001
        return None


def retryable(err):
    """Is this failed yt-dlp run worth waiting for the network and resuming?

    Decided by yt-dlp's final ERROR, not by any line before it: a run that
    had a connection blip and *then* failed to merge (a post-processing
    error) used to be retried for minutes because "connection aborted"
    appeared higher up. A piece given up on ("unable to continue") after
    connection errors is a network failure."""
    text = err or ""
    i = text.rfind("ERROR:")
    final = text[i:] if i >= 0 else text
    low = final.lower()
    if "postprocessing" in low or "conversion failed" in low:
        return False
    if is_net_error(final):
        return True
    return ("unable to continue" in low or low.strip() == "error:"
            or "giving up after" in text.lower()) and is_net_error(text)


def download(url, o, log=print, on_file=None, tag=""):
    """Download and return [(filepath, video_id)].

    Survives WiFi drops: yt-dlp is given --continue so a partial file resumes,
    and if it dies with a connectivity error this loops, sleeping with back-off
    (net_sleeps) until the network is back, then resumes -- it never restarts a
    download from zero because of an outage. Non-network errors fail immediately.
    """
    args = build_args(url, o)
    # --print implies --quiet, and --quiet makes yt-dlp print *nothing* but the
    # requested template -- no "[download] Downloading item 3 of 186", no
    # percentage, no speed, no ETA. That is why the progress bar looked dead:
    # there was never anything to parse. --no-quiet cancels the implied quiet so
    # the live progress lines are emitted again; the after_move template still
    # prints, which is how each finished file (and its videoId) is reported.
    args += ["--no-simulate", "--no-quiet",
             "--print", "after_move:%(id)s\t%(filepath)s"]
    if o.get("dry_run"):
        args = [a for a in args if a != "--no-simulate"]
        args.append("--simulate")
    log("  $ " + " ".join(_q(a) for a in args))
    sleeps = iter(net_sleeps(float(o.get("net_max_wait", 240))))
    stats = {}
    span = o.get("section_span")
    span = (span[1] - span[0]) if span and span[1] is not None else None
    on_line = _make_on_line(log, tag, tty=_tty_stream() if log is print else None,
                            stats=stats, span=span)
    expired = 0
    while True:
        stats.clear()
        results, err = _run_once(args, log, on_line, on_file)
        if err is None:
            return results, None
        if err == cancel.CANCELLED:
            # Keep what finished: those files already have their lyrics.
            return results, cancel.CANCELLED
        if (not o.get("dry_run") and stats.get("progress") and expired < 3
                and re.search(r"HTTP Error 403", err)):
            # YouTube's links last about six hours; a long download on a slow
            # connection outlives them. Asking again gives fresh links, and
            # --continue keeps everything already downloaded.
            expired += 1
            log("  ! YouTube refused to go on (HTTP 403) after downloading "
                "for a while -- usually the links ran out; asking for fresh "
                "ones and carrying on")
            continue
        if o.get("dry_run") or not retryable(err):
            return [], err
        if cancel.requested():
            return results, cancel.CANCELLED
        w = next(sleeps, None)
        if w is None:
            return [], err
        log(f"  ! network problem -- waiting {int(w)}s, then resuming "
            f"(not restarting)")
        progress.report({"type": "wait", "secs": w, "tag": tag} if tag else
                        {"type": "wait", "secs": w})
        time.sleep(w)
        if cancel.requested():
            return results, cancel.CANCELLED


def _q(a):
    return f'"{a}"' if (" " in a or (is_windows() and "&" in a)) else a


def preflight(o, log=print):
    """Warn about things that will bite later."""
    problems = []
    if not o.get("keep_video") and not have_ffmpeg():
        problems.append("ffmpeg not found -- audio extraction/thumbnail embedding "
                        "will fail.")
    try:
        r = subprocess.run(list(ytdlp_cmd()) + ["--version"],
                           capture_output=True, text=True, timeout=30)
        ver = (r.stdout or "").strip()
        if ver:
            log(f"yt-dlp {ver}")
    except Exception:
        problems.append('yt-dlp not found. Install it: pip install -U "yt-dlp[default]"')
    return problems
