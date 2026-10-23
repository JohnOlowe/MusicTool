"""yt-dlp wrapper: single videos, playlists, and a videoId index."""

import json
import os
import subprocess
import time

from . import cancel, progress
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
)

ADVICE = {
    "bot": ("YouTube wants a logged-in session. Use --cookies-from-browser "
            "edge (or chrome) -- it reads your existing login, nothing to "
            "export -- or pass an exported cookies.txt with --cookies."),
    "age": ("Age-restricted: sign in with an account that can view it "
            "(--cookies-from-browser), or download it in the browser."),
    "private": "This video is private; only its owner's account can fetch it.",
    "members": "Members-only: needs the membership account's cookies.",
    "unavailable": "YouTube says this video is unavailable or removed.",
    "network": ("Could not reach YouTube at all -- check the connection, then "
                "try again (this is not a block)."),
}


def classify_error(text):
    """'bot', 'age', 'private', 'members', 'unavailable', 'network', 'unknown'."""
    t = (text or "").lower()
    for needle, kind in BLOCKERS:
        if needle in t:
            return kind
    if is_net_error(text or ""):
        return "network"
    return "unknown"


BOT_HINT = (
    "YouTube is refusing this without a login ('Sign in to confirm you're not a "
    "bot'). Fix: set 'Cookies from browser' to your browser (Windows: edge or "
    "chrome) -- it reads your existing YouTube login, nothing to export. Or "
    "export a cookies.txt and pass --cookies.")


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
    if o.get("merge_format"):
        args += ["--merge-output-format", o["merge_format"]]
    if o.get("remote_components"):
        args += ["--remote-components", o["remote_components"]]
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


def _make_on_line(log):
    """Forward readable lines to the log; send every event to the progress hook.

    The per-percent \r redraws would flood the log, so those go only to the
    hook (the GUI bar). Item/file/done and unrecognised (error) lines are logged.
    """
    def on_line(seg):
        ev = progress.parse_ytdlp_line(seg)
        progress.report(ev)
        if ev is None or ev["type"] in ("item", "file", "done"):
            log("    " + seg)
    return on_line


def download(url, o, log=print, on_file=None):
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
    on_line = _make_on_line(log)
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
