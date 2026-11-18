"""yt-dlp wrapper: single videos, playlists, and a videoId index."""

import json
import os
import re
import shutil
import subprocess
import time

from . import cancel, formats, progress
from .util import (ytdlp_cmd, have_ffmpeg, is_windows, is_net_error, net_sleeps,
                   detect_browser)

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
    ("pot:bgutil", "pot_server"),
    ("127.0.0.1:4416", "pot_server"),
    ("the page needs to be reloaded", "reload"),
    ("requested format is not available", "no_format"),
)

# Chromium-derived browsers share one cookie format and one encryption lock,
# which is why the error message says "Chrome" even for Edge/Brave/Vivaldi.
CHROMIUM = ("chrome", "chromium", "edge", "brave", "vivaldi", "opera", "whale")

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
    "js_runtime": ("YouTube's n-challenge needs a JavaScript runtime. Install "
                   "deno (the default) or node, pass --js-runtimes node if "
                   "you use node, and add --remote-components ejs:github so "
                   "yt-dlp is allowed to fetch the solver script."),
    "pot_server": ("The bgutil PO-token plugin is installed but its server is "
                   "not answering on 127.0.0.1:4416. Start it (docker run -p "
                   "4416:4416 brainicism/bgutil-ytdlp-pot-provider) or "
                   "uninstall the plugin -- this is a plugin, not yt-dlp's "
                   "own JS solver."),
    "reload": ("That is the bot wall talking. It almost always means the JS "
               "challenge was not solved: install deno or node and add "
               "--remote-components ejs:github (see 'js runtime' in "
               "musictool doctor)."),
    "no_format": ("That itag is not offered to your account or client. List "
                  "what is really there with: musictool formats URL "
                  "--show-audio --all-formats"),
}


def classify_error(text):
    """The failure kind, so each one gets its own fix.

    'bot', 'age', 'private', 'members', 'unavailable', 'network', 'unknown',
    plus the cookie/anti-bot ones: 'session', 'dpapi', 'cookies_locked',
    'oauth_gone', 'js_runtime', 'pot_server', 'reload', 'no_format'.
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
    args += ["--retries", str(o.get("retries", 5))]
    args += ["--fragment-retries", str(o.get("retries", 5))]
    # --continue resumes a half-finished .part file after an outage
    # instead of throwing it away and starting the download over.
    args.append("--continue")

    if not o.get("keep_video"):
        args += ["-x", "--audio-format", o.get("audio_format", "m4a")]
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
    if o.get("remote_components"):
        args += ["--remote-components", o["remote_components"]]
    if o.get("js_runtimes"):
        args += ["--js-runtimes", o["js_runtimes"]]
    # Keep the artist tag clean, exactly like the original shell script did.
    args += list(("--replace-in-metadata",) + TOPIC_FIX)
    args += ["--parse-metadata", "%(artist,uploader)s:%(meta_artist)s"]
    args += ["-o", o.get("template") or DEFAULT_TEMPLATE]
    args += ["-P", o.get("output_dir") or "."]
    args.append(url)
    return args


def probe(url, o):
    """Cheap metadata read, no download. -> list of dicts."""
    args = list(ytdlp_cmd()) + [
        "--flat-playlist" if o.get("flat") else "--no-playlist",
        "-J", "--no-warnings", url]
    args += cookie_args(o)
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=180)
    except Exception as e:
        return {"error": str(e)}
    if out.returncode != 0:
        return {"error": (out.stderr or out.stdout or "").strip()[-400:]}
    try:
        return json.loads(out.stdout)
    except Exception:
        return {"error": "could not parse yt-dlp output"}


def print_target_name(url, o):
    """Ask yt-dlp what it would name this file, without downloading."""
    args = list(ytdlp_cmd()) + [
        "--no-playlist", "--simulate", "--no-warnings",
        "--replace-in-metadata", *TOPIC_FIX,
        "--parse-metadata", "%(artist,uploader)s:%(meta_artist)s",
        "--print", "%(uploader)s - %(title)s", url]
    args += cookie_args(o)
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
        return [], "yt-dlp not found. Install it: pip install yt-dlp"
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
        err = "\n".join(segments[-8:])
        err = err[-600:] if err else f"yt-dlp exited {proc.returncode}"
        if "not a bot" in err or "Sign in to confirm" in err:
            err += "\n" + BOT_HINT
        return results, err
    return results, None


def _make_on_line(log, tag=""):
    """Forward readable lines to the log; send every event to the progress hook.

    The per-percent \r redraws would flood the log, so those go only to the
    hook (the GUI bar). Item/file/done and unrecognised (error) lines are logged.

    `tag` marks every event with the download it came from, so several running
    at once can each have their own line in the UI instead of fighting over one.
    """
    def on_line(seg):
        ev = progress.parse_ytdlp_line(seg)
        if ev is not None and tag:
            ev["tag"] = tag
        progress.report(ev)
        if ev is None or ev["type"] in ("item", "file", "done"):
            log("    " + seg)
    return on_line


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
    on_line = _make_on_line(log, tag)
    while True:
        results, err = _run_once(args, log, on_line, on_file)
        if err is None:
            return results, None
        if err == cancel.CANCELLED:
            # Keep what finished: those files already have their lyrics.
            return results, cancel.CANCELLED
        if o.get("dry_run") or not is_net_error(err):
            return [], err
        if cancel.requested():
            return results, cancel.CANCELLED
        w = next(sleeps, None)
        if w is None:
            return [], err
        log(f"  ! network problem -- waiting {int(w)}s, then resuming "
            f"(not restarting)")
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
        problems.append("yt-dlp not found. Install it: pip install yt-dlp")
    return problems
