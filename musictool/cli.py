"""Command line interface."""

import argparse
import os
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

from . import cancel
from . import config as configmod
from . import (chat, download, formats, live, lyrics, parts, progress,
               providers, rangeproxy, tags)
from .uicmd import is_playlist_url
from .util import (AUDIO_EXT, classify, common_root, detect_browser,
                   existing_paths, ffmpeg_hint, have_ffmpeg, is_termux,
                   is_windows, js_runtime, parse_range, part_label,
                   pot_server_reachable, ytdlp_cmd, USER_AGENT)

PROG = "musictool"


def _add_download_flags(p):
    g = p.add_argument_group("download")
    g.add_argument("-o", "--output-dir", help="where to save files")
    g.add_argument("-t", "--template", help="yt-dlp output template")
    g.add_argument("--format", dest="audio_format", choices=list(
        "m4a mp3 opus flac aac ogg wav".split()), help="audio container")
    g.add_argument("-q", "--audio-quality", help="0 (best) - 10 (worst), or a bitrate")
    g.add_argument("--keep-video", action="store_true",
                   help="keep the video instead of extracting audio")
    g.add_argument("--no-thumbnail", dest="embed_thumbnail", action="store_false",
                   help="do not embed cover art")
    g.add_argument("--no-metadata", dest="embed_metadata", action="store_false")
    g.add_argument("--chapters", dest="embed_chapters", action="store_true")
    g.add_argument("--just-download", dest="just_download", action="store_true",
                   help="add nothing to the file afterwards: no title, cover, "
                        "chapters or subtitles inside it, no lyrics -- it is "
                        "done the moment the download is (--subs are saved as "
                        "files beside it, a stream's chat too)")
    g.add_argument("--ascii", dest="ascii_names", action="store_true",
                   help="restrict filenames to ASCII (helps on some players)")
    g.add_argument("--cookies", help="cookies.txt (age-restricted / member content)")
    g.add_argument("--cookies-from-browser", dest="cookies_from_browser",
                   choices=["auto", "off", "edge", "chrome", "firefox", "brave",
                            "vivaldi", "opera", "chromium", "whale", "safari"],
                   help="read your YouTube login straight out of a browser "
                        "('auto' picks the one you actually use) -- no cookies.txt "
                        "to export; this is what YouTube's 'Sign in to confirm "
                        "you're not a bot' needs")
    g.add_argument("--proxy")
    g.add_argument("--limit-rate", dest="rate_limit", help="e.g. 2M")
    g.add_argument("--concurrent", type=int, help="parallel fragment downloads")
    g.add_argument("--jobs", type=int, default=1,
                   help="videos to download at the same time (1-8, default 1). "
                        "--concurrent splits one video into pieces; this runs "
                        "several videos at once")
    g.add_argument("--retries", type=int, help="download attempts on network errors")
    g.add_argument("--net-max-wait", dest="net_max_wait", type=float,
                   help="seconds to keep retrying across a WiFi drop (default 240)")
    g.add_argument("--archive", dest="download_archive",
                   help="archive file to skip already-downloaded videos")
    g.add_argument("--items", dest="playlist_items",
                   help="playlist range, e.g. 1-50 or 5,10,15-20")
    g.add_argument("--no-playlist", action="store_true",
                   help="if the URL is in a playlist, take only this video")
    g.add_argument("--yes-playlist", action="store_true",
                   help="take the whole playlist (default when given a playlist URL)")
    g.add_argument("--range", dest="part", metavar="START-END",
                   help="only this part of a finished video, e.g. 1:00:00- "
                        "(from 1 hour to the end), 1:00:00-2:30:00, -45:00 or "
                        "1h-2h30m. Saved as 'NAME (from 1h00m00s)'; `chat` "
                        "reads that and matches the chat to it")
    g.add_argument("--no-fast-range", dest="fast_range", action="store_false",
                   default=None,
                   help="fetch a --range part the old way, by ffmpeg alone "
                        "(slow with an ffmpeg older than 8.1), instead of "
                        "through musictool's 10 MB pieces")
    g.add_argument("--live-from-now", dest="live_from_now", action="store_true",
                   help="a stream that is live right now is recorded from the "
                        "start by default; this records from now instead")


def _add_quality_flags(p):
    g = p.add_argument_group("quality")
    g.add_argument("--quality", help="video height you want, e.g. 720 or 1080 "
                                     "(also: best, smallest)")
    g.add_argument("--max-size", dest="max_size",
                   help="size ceiling for the file, e.g. 250M or 1G -- picks the "
                        "best quality that fits under it")
    g.add_argument("--audio-lang", dest="audio_lang", default="en",
                   help="spoken language you want when a video is dubbed, "
                        "e.g. en (default) or es; 'any' takes whatever is best")
    g.add_argument("-f", "--format-selector", dest="format_selector",
                   help="a raw yt-dlp -f selector, e.g. 137+140 (overrides the "
                        "two above)")
    g.add_argument("--exact", action="store_true",
                   help="take exactly the stream(s) given with -f, e.g. -f "
                        "299+140 -- never a substitute -- and only copy them: "
                        "nothing is converted (audio keeps its codec; video "
                        "and audio go into a container that holds both as "
                        "they are)")
    g.add_argument("--merge-format", dest="merge_format",
                   choices=["mkv", "mp4", "webm", "mov"],
                   help="container to merge video+audio into (mkv accepts "
                        "everything)")
    g.add_argument("--subs", dest="embed_subs", action="store_true",
                   help="embed subtitles")
    g.add_argument("--sub-langs", dest="sub_langs",
                   help="subtitle languages, e.g. en,es (default: what yt-dlp picks)")
    g.add_argument("--remote-components", dest="remote_components",
                   help="let yt-dlp fetch a component it needs, e.g. ejs:github")
    g.add_argument("--js-runtimes", dest="js_runtimes",
                   help="JavaScript runtime for YouTube's n-challenge, e.g. "
                        "node (yt-dlp only enables deno by default)")


def _add_lyrics_flags(p):
    g = p.add_argument_group("lyrics")
    g.add_argument("--no-lyrics", action="store_true", help="download only")
    g.add_argument("--lyrics-only", action="store_true",
                   help="skip download, only fetch lyrics")
    g.add_argument("--lyrics-dir", help="folder for .lrc sidecars")
    g.add_argument("--providers", help="comma-separated order, e.g. lrclib,netease")
    g.add_argument("--allow-unsynced", action="store_true",
                   help="accept untimed lyrics when nothing synced exists")
    g.add_argument("--shift", type=float, help="offset every timestamp by N seconds")
    g.add_argument("--delay", type=float, help="seconds between API requests")
    g.add_argument("--force", action="store_true",
                   help="redo files that already have synced lyrics")
    g.add_argument("--recursive", action="store_true", help="descend into subfolders")
    g.add_argument("--limit", type=int, help="stop after N fixed files")
    g.add_argument("--dry-run", action="store_true", help="change nothing")
    g.add_argument("--no-embed", action="store_true", help="write .lrc only")


def build_parser():
    ap = argparse.ArgumentParser(
        prog=PROG,
        description="Download music and fetch synced lyrics. "
                    "Windows, macOS, Linux and Termux.")
    ap.add_argument("--version", action="version", version="musictool 1.0")
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("get", help="download from a video or playlist URL, then fetch lyrics")
    g.add_argument("urls", nargs="+")
    _add_download_flags(g)
    _add_quality_flags(g)
    _add_lyrics_flags(g)

    ck = sub.add_parser(
        "check", help="is this URL reachable, or does YouTube want a login? "
                      "(checks with NO cookies by default)")
    ck.add_argument("urls", nargs="+")
    ck.add_argument("--with-cookies", action="store_true",
                    help="check using your cookies/browser login instead of "
                         "as a stranger")
    _add_download_flags(ck)

    fm = sub.add_parser(
        "formats", help="list the qualities a video offers, with their sizes")
    fm.add_argument("urls", nargs="+")
    fm.add_argument("--all-formats", action="store_true",
                    help="show every row yt-dlp reports, including the "
                         "duplicate variant splits and same-quality doubles")
    fm.add_argument("--container", choices=["mp4", "webm", "mkv"],
                    help="only this container")
    fm.add_argument("--codec", help="only this codec, e.g. avc1, vp9, av01, opus")
    fm.add_argument("--show-audio", action="store_true",
                    help="include the audio-only tracks in the table, in "
                         "your --audio-lang (any = every language)")
    fm.add_argument("--sort", choices=["size_desc", "size_asc", "resolution",
                                       "container"], default="size_desc")
    fm.add_argument("--auto", choices=formats.AUTO_MODES,
                    help="suggest a quality for you: best, compatible "
                         "(H.264/mp4), fit (best under --max-size), "
                         "smallest, audio")
    _add_quality_flags(fm)
    _add_download_flags(fm)

    d = sub.add_parser("lyrics", help="fetch/fix lyrics for existing files or folders")
    d.add_argument("paths", nargs="+")
    _add_lyrics_flags(d)

    s = sub.add_parser("scan", help="offline report of which files need lyrics")
    s.add_argument("paths", nargs="+")
    s.add_argument("--lyrics-dir")
    s.add_argument("--recursive", action="store_true")
    s.add_argument("--json", action="store_true")

    dup = sub.add_parser(
        "duplicates", aliases=["dupes"],
        help="find files that share the same lyrics (wrong-lyrics damage) and refetch them")
    dup.add_argument("paths", nargs="+")
    dup.add_argument("--min-chars", type=int, default=40,
                     help="ignore lyric text shorter than this (default 40)")
    dup.add_argument("--suspicious-only", action="store_true",
                     help="only groups whose members have different titles")
    dup.add_argument("--fix", action="store_true",
                     help="re-fetch lyrics for every file in the groups found")
    dup.add_argument("--json", action="store_true")
    _add_lyrics_flags(dup)

    ex = sub.add_parser("extract", help="write each file's embedded lyrics to a "
                                         ".lrc you can edit by hand")
    ex.add_argument("paths", nargs="+")
    ex.add_argument("--out", help="folder for the .lrc (default: next to each file)")
    ex.add_argument("--recursive", action="store_true")
    ex.add_argument("--force", action="store_true", help="overwrite an existing .lrc")

    em = sub.add_parser("embed", help="embed a .lrc you made or downloaded into the "
                                       "matching song")
    em.add_argument("paths", nargs="+", help="audio file(s)/folder(s), or one audio "
                                              "plus one .lrc/.txt")
    em.add_argument("--lyrics-dir", help="extra folder to look for .lrc files")
    em.add_argument("--recursive", action="store_true")
    em.add_argument("--no-sidecar", action="store_true",
                    help="embed only; do not also write a .lrc next to the file")
    em.add_argument("--dry-run", action="store_true")

    pr = sub.add_parser("providers", help="list lyrics sources and test them")
    pr.add_argument("--test", action="store_true", help="make a live probe request")

    c = sub.add_parser("config", help="show or set saved defaults")
    c.add_argument("key", nargs="?")
    c.add_argument("value", nargs="?")
    c.add_argument("--reset", action="store_true")

    rl = sub.add_parser(
        "relang",
        help="replace a downloaded file's audio with another language, "
             "without downloading the video again")
    rl.add_argument("paths", nargs="+", help="files or folders to repair")
    rl.add_argument("--audio-lang", dest="audio_lang", default="en",
                    help="the language you want (default en)")
    rl.add_argument("--cookies", help="cookies.txt, if YouTube asks")
    rl.add_argument("--cookies-from-browser", dest="cookies_from_browser",
                    help="auto | off | firefox | chrome | edge ...")
    rl.add_argument("--proxy", help="proxy URL, e.g. socks5://127.0.0.1:1080")
    rl.add_argument("--force", action="store_true",
                    help="replace even when the file does not say what "
                         "language it already has")
    rl.add_argument("--dry-run", dest="dry_run", action="store_true",
                    help="say what would change and download nothing")
    rl.add_argument("--list", action="store_true",
                    help="just list what language each file is in and the "
                         "link it came from (no downloads, no network)")
    lv = sub.add_parser(
        "live", help="live streams: is it live and how big; what is in a "
                     "stopped recording; watch it so far; download the rest")
    lv.add_argument("folder", nargs="?",
                    help="the folder the recording is in (default: your "
                         "download folder)")
    lv.add_argument("--status", metavar="URL",
                    help="is this stream live, since when, how big so far, "
                         "and is there room for it")
    lv.add_argument("--join", action="store_true",
                    help="join the loose pieces, then make 'TITLE (so far)"
                         ".mp4' to watch")
    lv.add_argument("--no-copy", dest="no_copy", action="store_true",
                    help="with --join: join only, make no copy")
    lv.add_argument("--watch", action="store_true",
                    help="only make the copy to watch -- safe while the "
                         "recording is still running")
    lv.add_argument("--out", help="folder for the copy to watch, e.g. another "
                                  "drive when this one is full")
    lv.add_argument("--resume", metavar="URL",
                    help="download the rest of a stopped recording; pieces "
                         "already here are not downloaded again")
    lv.add_argument("--force", action="store_true",
                    help="join even though the files changed in the last 30 s "
                         "(only when no download is running)")
    lv.add_argument("--cookies", help="cookies.txt, if YouTube asks")
    lv.add_argument("--cookies-from-browser", dest="cookies_from_browser",
                    help="auto | off | firefox | chrome | edge ...")
    lv.add_argument("--proxy", help="proxy URL")
    lv.add_argument("--js-runtimes", dest="js_runtimes",
                    help="JavaScript runtime for YouTube's challenge, e.g. node")
    lv.add_argument("--remote-components", dest="remote_components",
                    help="e.g. ejs:github")
    ch = sub.add_parser(
        "chat", help="a finished live stream's chat replay as subtitles: "
                     "downloaded (text only), timed to your video -- or the "
                     "part of it you have -- and optionally added to it as "
                     "a subtitle track")
    ch.add_argument("source", help="the video's link, a .live_chat.json you "
                                   "already have -- or a chat file this made "
                                   "(.ass/.srt/.vtt/.txt, or a video with the "
                                   "chat track), to make it again in another "
                                   "format or with other settings; what you "
                                   "leave out stays as that file had it")
    ch.add_argument("-o", "--output-dir", dest="output_dir",
                    help="the folder with the video (default: your download "
                         "folder); the chat file is saved there too")
    ch.add_argument("--video", help="the video file to match, when the folder "
                                    "has several or its name lacks [ID]")
    ch.add_argument("--format", dest="sub_format", choices=chat.FORMATS,
                    help="ass (default: placement, colours and spacing "
                    "everywhere), srt (plays everywhere), vtt (browsers), txt "
                    "(a readable log, not a subtitle)")
    ch.add_argument("--lines", type=int,
                    help="messages on screen at once (default 5). A message "
                         "stays until this many newer ones are below it. 1 = "
                         "like normal subtitles, 6+ = scrolling chat")
    ch.add_argument("--hold", type=float,
                    help="longest a message stays, in seconds (default 15; "
                         "0 = until pushed off, even in a quiet chat)")
    ch.add_argument("--side", choices=chat.SIDES, help="default right")
    ch.add_argument("--place", choices=chat.PLACES,
                    help="height on screen (default bottom)")
    ch.add_argument("--width", type=int,
                    help="ass/vtt: the chat column's width, %% of the "
                         "picture (default 32)")
    ch.add_argument("--wrap", type=int,
                    help="srt/vtt: break lines after about this many letters "
                         "(default 40)")
    ch.add_argument("--pad", type=int,
                    help="srt: push the text right with this many spaces, for "
                         "players that ignore the side setting (try 40-60)")
    ch.add_argument("--no-names", dest="names", action="store_const",
                    const=False, help="messages only, no user names")
    ch.add_argument("--names", dest="names", action="store_const", const=True,
                    help="names back on (for a file made with --no-names "
                         "from the .live_chat.json)")
    ch.add_argument("--colours", "--colors", dest="colours",
                    choices=chat.COLOUR_MODES,
                    help="name (default): everyone's name and message in a "
                         "colour of their own -- the owner, moderators, "
                         "members and Super Chats in YouTube's; role: only "
                         "those; off: white")
    ch.add_argument("--box", dest="box", action="store_const", const=True,
                    help="ass/vtt: on a see-through dark box (default: no "
                         "box, outlined text with a shadow)")
    ch.add_argument("--no-box", dest="box", action="store_const", const=False,
                    help=argparse.SUPPRESS)
    ch.add_argument("--font-size", dest="font_size", type=int, metavar="PX",
                    help="ass: how big the text is, in pixels on a 1080p "
                         "picture (scaled with the video; default 45). VLC "
                         "cannot resize ASS while it plays -- remake the "
                         "file with this instead (`chat FILE.ass "
                         "--font-size 32`, nothing is downloaded)")
    ch.add_argument("--gap", type=float,
                    help="space between messages, in lines (default 0.4; 0 = "
                         "none). srt/vtt can only space whole lines: 1+")
    ch.add_argument("--range", dest="part", metavar="START-END",
                    help="which part of the stream, e.g. 0:00:00-6:40:00 . "
                         "A video named with its part ('NAME (1h00m00s-"
                         "2h00m00s)', as `get --range` saves it) is always "
                         "timed from its name: this only picks the parts "
                         "inside the range, the others are left alone. A "
                         "video without a part in its name (or given with "
                         "--video) is taken to hold this range")
    ch.add_argument("--offset", type=float,
                    help="seconds to move the chat later (negative: earlier); "
                         "on a chat file made before, this replaces the "
                         "offset it was made with")
    ch.add_argument("--embed", action="store_true",
                    help="also add it to the video as a subtitle track (copied, "
                         "not re-encoded): an .mkv is updated, an .mp4 gets an "
                         ".mkv copy beside it")
    ch.add_argument("--cookies", help="cookies.txt, if YouTube asks")
    ch.add_argument("--cookies-from-browser", dest="cookies_from_browser",
                    help="auto | off | firefox | chrome | edge ...")
    ch.add_argument("--proxy", help="proxy URL")
    ch.add_argument("--js-runtimes", dest="js_runtimes",
                    help="JavaScript runtime for YouTube's challenge, e.g. node")
    ch.add_argument("--remote-components", dest="remote_components",
                    help="e.g. ejs:github")
    sub.add_parser("doctor", help="check that yt-dlp, ffmpeg and tkinter are available")
    sub.add_parser("gui", help="launch the graphical interface")
    return ap


def _opts(args):
    """Turn parsed CLI args into the config dict."""
    over = {}
    for k in ("output_dir", "template", "audio_format", "audio_quality",
              "keep_video", "embed_metadata", "embed_thumbnail", "embed_chapters",
              "ascii_names", "cookies", "cookies_from_browser",
              "quality", "max_size", "format_selector", "merge_format", "exact",
              "audio_lang", "live_from_now", "just_download", "fast_range",
              "embed_subs", "sub_langs", "remote_components", "js_runtimes",
              "proxy", "rate_limit", "concurrent",
              "retries", "net_max_wait", "download_archive", "lyrics_dir",
              "allow_unsynced", "delay",
              "recursive", "limit", "dry_run", "no_embed", "shift", "force"):
        if getattr(args, k, None) is not None:
            over[k] = getattr(args, k)
    if getattr(args, "playlist_items", None):
        over["playlist_items"] = args.playlist_items
    if getattr(args, "no_playlist", False):
        over["no_playlist"] = True
    if getattr(args, "yes_playlist", False):
        over["yes_playlist"] = True
    if getattr(args, "providers", None):
        over["provider_order"] = [p.strip() for p in args.providers.split(",") if p.strip()]
    if getattr(args, "no_lyrics", False) or getattr(args, "just_download",
                                                       False):
        over["no_lyrics"] = True            # lyrics are put inside the file
    if getattr(args, "lyrics_only", False):
        over["lyrics_only"] = True
    if getattr(args, "audio_quality", None):
        over["audio_quality"] = args.audio_quality
    return configmod.merged(over)


def _duration(text):
    try:
        n = int(float(text))
    except (TypeError, ValueError):
        return ""
    return f"{n // 60}:{n % 60:02d}"


def cmd_check(args):
    """Is YouTube serving this to you right now -- or does it want a login?

    Checks with NO cookies unless --with-cookies is given, because "am I
    blocked?" only means anything from the outside: a request that succeeds
    while carrying your login proves nothing about the next anonymous one.
    """
    o = _opts(args)
    if not getattr(args, "with_cookies", False):
        o["cookies"] = ""
        o["cookies_from_browser"] = "off"
        print("checking with no cookies, as a stranger -- this is the honest "
              "answer to 'am I blocked?'")
    else:
        print("checking WITH your cookies/login (that is not the same question)")

    ok = blocked = nostream = 0
    for url in args.urls:
        print(f"\n==> {url}")
        info = download.probe(url, dict(o, flat=True))
        err = info.get("error") if isinstance(info, dict) else None
        if err:
            kind = download.classify_error(err)
            first = [x for x in err.splitlines() if x.strip()][:1]
            blocked += 1
            print(f"    BLOCKED ({kind})  {first[0].strip()[:160] if first else err[:160]}")
            advice = download.ADVICE.get(kind)
            if advice:
                print(f"    -> {advice}")
            continue
        entries = info.get("entries") if isinstance(info, dict) else None
        if entries:
            first = entries[0] or {}
            ok += 1
            print(f"    reachable  playlist of {len(entries)} items; first is "
                  f"\"{first.get('title') or first.get('id')}\"")
        else:
            why = download.no_media_reason(info)
            if why:
                # YouTube answered, but the JS challenge was not solved, so
                # there is nothing to download. Not a login problem.
                nostream += 1
                print(f"    NO STREAMS  \"{info.get('title') or info.get('id')}\""
                      f"  {_duration(info.get('duration'))}")
                print(f"    -> {why}")
                continue
            ok += 1
            print(f"    reachable  \"{info.get('title') or info.get('id')}\""
                  f"  {_duration(info.get('duration'))}")
            st = live.status(info)
            if st in live.LIVE or st == "is_upcoming":
                ago = live.started_ago(info)
                print(f"    {live.STATUS_TEXT[st]}"
                      + (f", started {live.hms(ago)} ago"
                         if st == "is_live" and ago else "")
                      + " -- `get` records it from the start; musictool live "
                        "--status URL says how big it is")
        print("    -> no login needed for this one")

    print("\n" + "=" * 60)
    print(f"checked {ok + blocked + nostream} URL(s): {ok} reachable, "
          f"{blocked} blocked" + (f", {nostream} with no streams" if nostream
                                  else ""))
    if blocked:
        print("cookies are needed for the blocked ones -- see the advice above")
    if nostream:
        print("no streams = your yt-dlp setup, not a login: run musictool doctor")
    if not blocked and not nostream:
        print("you are not blocked: download with no cookies at all")
    return 0 if not (blocked or nostream) else 1


def cmd_formats(args):
    """List every quality a URL offers, biggest first, with real sizes."""
    o = _opts(args)
    rc = 0
    for url in args.urls:
        print(f"\n==> {url}")
        info = download.probe(url, o)
        if not isinstance(info, dict) or info.get("error"):
            err = (info or {}).get("error", "no information") if isinstance(info, dict) \
                else "no information"
            kind = download.classify_error(err)
            print(f"    !! {err.splitlines()[0][:200] if err else 'unknown error'}")
            if download.ADVICE.get(kind):
                print(f"    -> {download.ADVICE[kind]}")
            rc = 1
            continue
        raw = formats.rows_from_info(info)
        why = download.no_media_reason(info)
        if why:
            print(f"    {info.get('title') or info.get('id')}")
            print(f"    !! {why}")
            rc = 1
            continue
        shown = formats.apply_filters(
            raw,
            max_height=None,
            container=getattr(args, "container", None),
            codec=getattr(args, "codec", None),
            max_bytes=formats.parse_size(o.get("max_size")),
            include_audio=bool(getattr(args, "show_audio", False))
            or not o.get("keep_video"),
            collapse=not getattr(args, "all_formats", False),
            dedupe=not getattr(args, "all_formats", False),
            sort=getattr(args, "sort", "size_desc"))
        # audio rows in your spoken language only, like the download picks
        merged = len(shown)
        shown, lang_note = formats.filter_audio_lang(
            shown, str(o.get("audio_lang") or ""))
        dur = _duration(info.get("duration"))
        if live.status(info) in live.LIVE:
            print(f"    {live.STATUS_TEXT[live.status(info)]}: these are the "
                  f"formats from the start (what `get` records); sizes grow "
                  f"while it runs")
        print(formats.table(
            shown, f"    {info.get('title') or info.get('id')}"
                   + (f"  ({dur})" if dur else "")))
        if merged < len(raw):
            print(f"    ({len(raw)} rows from yt-dlp collapsed to {merged}: "
                  f"variant splits and same-quality doubles are merged -- "
                  f"--all-formats shows every row)")
        if lang_note:
            print(f"    ({lang_note})")
        # the *table* is the filtered list; the *picker* also gets the audio
        # tracks the table hid, or a 1080p video would be paired with the
        # 360p combined format just because the real audio was filtered out.
        rows = formats.with_audio_pool(shown, raw)
        if not rows:
            print("    no formats reported")
            rc = 1
            continue
        cap = formats.parse_size(o.get("max_size"))
        want = str(o.get("quality") or "").strip().lower().rstrip("p")
        height = int(want) if want.isdigit() else None
        lang = str(o.get("audio_lang") or "")
        spoken = formats.languages_in(rows)
        if len(spoken) > 1:
            said = ", ".join(f"{c} (original)" if orig else c
                             for c, orig, _n in spoken)
            print(f"\n    spoken languages: {said}")
            taking = formats.preferred_language(rows, lang)
            if not lang or lang.lower() in ("any", "auto", "original", "*"):
                print("    will take: the best available "
                      "(--audio-lang picks one)")
            elif taking:
                print(f"    will take: {taking}")
            else:
                print(f"    will take: the best available -- nothing here is "
                      f"tagged {lang}")
        sel, v, a, note = formats.pick(
            rows, height=height, max_bytes=cap,
            audio_only=not o.get("keep_video"),
            smallest=want in ("smallest", "worst"), lang=lang)
        # an audio pick is extracted to --format; only a video keeps its file
        kv = " --keep-video" if (o.get("keep_video") or (
            getattr(args, "auto", None) and args.auto != "audio")) else ""
        if sel:
            print(f"\n    to take this one:")
            print(f"      musictool get \"{url}\"{kv} -f {sel}"
                  f"{' --merge-format ' + o['merge_format'] if o.get('merge_format') else ''}"
                  f"     # {formats.describe_choice(v, a)}")
        if note:
            print(f"    note: {note}")
        auto = getattr(args, "auto", None)
        if auto:
            asel, av, aa, why = formats.auto_pick(rows, mode=auto,
                                                  max_bytes=cap, lang=lang)
            if asel:
                print(f"\n    auto ({auto}): {why}")
                print(f"      musictool get \"{url}\"{kv} -f {asel}"
                      f"{' --merge-format ' + o['merge_format'] if o.get('merge_format') else ''}")
            else:
                print(f"\n    auto ({auto}): nothing to pick -- {why}")
    return rc


def _apply_exact(o, info, log=print):
    """--exact: check the ids against this video and set up a copy-only run.

    -> False when a stream id is not on this video (nothing is downloaded --
    a substitute is exactly what --exact rules out). Otherwise `o` is set so
    that nothing gets converted:
      * a video stream picked -> the video is kept (that is what was chosen)
        and video + audio are merged with a plain copy into a container that
        holds both (mp4, webm, or mkv for a mixed pair), unless
        --merge-format names one;
      * audio alone -> taken out with its own codec (-x --audio-format best:
        Opus stays Opus in .opus, AAC stays AAC in .m4a), never re-encoded
        to --format;
      * no subtitle streams are added (they would be converted for mp4).
    Without a probe (a playlist) only the ids' form was checked.
    """
    sel = o["format_selector"]
    if o.get("embed_subs"):
        # put inside an mp4 they would be converted (and a chat cannot go in
        # at all): saved beside the video instead. They used to be dropped
        # without a word -- --exact --subs got no chat.
        o["embed_subs"] = False
        o["write_subs"] = True
        log("  subtitles (a stream's chat too) are saved beside the video, "
            "not put inside it: --exact copies only the streams you chose")
    if not isinstance(info, dict) or info.get("error"):
        o["audio_format"] = "best"
        log(f"  exactly: -f {sel}, copied as it is (no list to check the ids "
            f"against -- yt-dlp stops if one is missing)")
        return True
    rows = formats.rows_from_info(info)
    plan = formats.exact_plan(sel, rows)
    if plan["error"]:
        log("!! --exact: " + plan["error"])
        return False
    if plan["video"]:
        o["keep_video"] = True
        if len(plan["rows"]) > 1 and not o.get("merge_format"):
            box = plan["container"]
            if box == "webm" and o.get("embed_thumbnail"):
                # yt-dlp cannot put a cover picture in webm and fails the
                # run AFTER downloading; mkv takes the same streams unchanged
                box = "mkv"
                log("  (webm cannot hold the thumbnail -> .mkv, the same "
                    "streams unchanged; --no-thumbnail keeps .webm)")
            o["merge_format"] = box
        elif len(plan["rows"]) == 1 and plan["rows"][0]["ext"] == "webm":
            o["embed_thumbnail"] = False    # same reason, and nothing to merge
    else:
        o["audio_format"] = "best"
    if len(plan["video"]) > 1:
        o["video_multistreams"] = True
    if len([r for r in plan["audio"] if r["kind"] == "audio"]) > 1:
        o["audio_multistreams"] = True
    what = " + ".join(formats.describe_stream(r) for r in plan["rows"])
    if plan["video"] and len(plan["rows"]) > 1:
        into = f"put together as they are in .{o.get('merge_format') or plan['container']}"
    elif plan["video"]:
        into = f"kept as it is (.{plan['rows'][0]['ext']})"
    else:
        a = plan["rows"][0]
        into = ("taken out as it is (." + {"opus": "opus", "mp4a": "m4a",
                                            "aac": "m4a", "vorbis": "ogg"}.get(
                    a["acodec"], a["ext"]) + ")")
    log(f"  exactly: {what}\n    -> {into}; nothing is converted")
    return True


def _resolve_quality(url, o, log=print, info=None):
    """Turn --quality / --max-size into a concrete -f for this URL.

    Costs one metadata request, and only when asked: an explicit -f always wins.
    `info` is a probe already made for this URL (get's live check), reused.
    """
    if o.get("format_selector"):
        return
    cap = formats.parse_size(o.get("max_size")) if o.get("max_size") else None
    raw = str(o.get("quality") or "").strip()
    want = raw.lower().rstrip("p")
    height = int(want) if want.isdigit() else None
    if raw and not height and want not in ("smallest", "worst", "best"):
        # Never swallow this. The old code returned here in silence, so a
        # selector pasted into --quality produced no -f at all and yt-dlp
        # picked its own default: 1.74 GiB of 2160p instead of the 1080p that
        # was asked for.
        if formats.looks_like_selector(raw):
            o["format_selector"] = raw
            log(f"  note: '{raw}' is a raw format selector, not a height "
                f"-- using it as -f")
            info = info if info is not None else download.probe(url, o)
            if isinstance(info, dict) and not info.get("error"):
                for warn in formats.check_selector(
                        raw, formats.rows_from_info(info)):
                    log(f"  !! {warn}")
                if not o.get("keep_video"):
                    log("  note: video formats need --keep-video; without it "
                        "you get audio only")
            return
        log(f"  !! ignoring --quality '{raw}': that is not a height (720, "
            f"1080) or a preset (best, smallest). A raw yt-dlp selector goes "
            f"in --format-selector / -f.")
    if not cap and not height and want not in ("smallest", "worst", "best"):
        return
    if (height or want) and not o.get("keep_video"):
        log("  note: video quality needs --keep-video; without it you get "
            "audio only")
    info = info if info is not None else download.probe(url, o)
    if not isinstance(info, dict) or info.get("error"):
        log(f"  !! could not read the qualities: "
            f"{str((info or {}).get('error'))[:160]}")
        log("  !! so --quality/--max-size cannot be honoured here; yt-dlp "
            "will pick its own default")
        return
    why = download.no_media_reason(info)
    if why:
        log(f"  !! {why}")
        log("  !! so --quality/--max-size cannot be honoured here")
        return
    rows = formats.rows_from_info(info)
    sel, v, a, note = formats.pick(
        rows, height=height, max_bytes=cap,
        audio_only=not o.get("keep_video"),
        smallest=want in ("smallest", "worst"),
        lang=str(o.get("audio_lang") or ""))
    if not sel:
        log(f"  !! no quality matched ({note})")
        log("  !! so --quality/--max-size cannot be honoured here; yt-dlp "
            "will pick its own default")
        return
    o["format_selector"] = sel
    log(f"  quality: -f {sel}   ({formats.describe_choice(v, a)})")
    if note:
        log(f"  note: {note}")


def _plan_part(url, per, info, live_st, part, say):
    """Set up `get --range`: only START-END of a finished video, cut by
    ffmpeg (reading through rangeproxy), saved as 'NAME (from 1h00m00s).ext'.
    -> False when it cannot be done (said why)."""
    a, b = part
    if is_playlist_url(url) or per.get("yes_playlist"):
        say("!! --range is for one video, not a playlist")
        return False
    if live_st in live.LIVE or live_st == "is_upcoming":
        say("!! --range needs a finished video; this one is still live. "
            "Record it with `get` (from the start) and `live --watch` it.")
        return False
    total = (info or {}).get("duration")
    if total and a >= total:
        say(f"!! the video is only {live.hms(total)} long")
        return False
    if total and b is not None and b > total:
        b = None
    end = b if b is not None else total
    per["live_from_start"] = False
    per["no_lyrics"] = True
    per["no_rerun"] = True              # see _download_part
    if per.get("embed_subs"):
        # inside the part they would keep the whole video's times (every
        # line 57 s early in a part from 0:57); beside it they are cut to it
        per["embed_subs"] = False
        per["write_subs"] = True
        say("  subtitles are saved beside the part, timed to it")
    t = per.get("template") or download.DEFAULT_TEMPLATE
    base = None
    if t.endswith(".%(ext)s"):
        base = t[:-len(".%(ext)s")]
        if "%(id)" not in base:
            base += " [%(id)s]"          # so `chat` finds the video by its id
    per["_part"] = {"a": a, "b": b, "end": end, "base": base,
                    "vid": (info or {}).get("id")}
    _set_part(per, a, b, end)
    span = f"{live.hms(a)} to " + (live.hms(b) if b is not None else
                                   f"the end ({live.hms(total)})" if total
                                   else "the end")
    say(f"  only part of it: {span}" + (f", {live.hms(end - a)} of video"
                                         if end else ""))
    if rangeproxy.wanted(per):
        say("  fast: ffmpeg reads the part through musictool, which fetches "
            "it from YouTube in 10 MB pieces, like a whole download")
    elif not download.ffmpeg_chunked():
        slow = f" ({live.hms((end - a) / 2)} or longer here)" if end else ""
        why = ("--proxy / --limit-rate" if per.get("fast_range", True)
               else "--no-fast-range")
        say("  note: with " + why + " this ffmpeg fetches the part in one "
            "long request, which YouTube slows to about 2x real time" + slow
            + ". ffmpeg 8.1 or newer asks in 10 MB pieces by itself (check: "
            "ffmpeg -h protocol=http lists -request_size).")
    return True


def _num(x):
    """12.5 -> '12.5', 14430.15 -> '14430.15' (not :g, which keeps 6 digits
    and gave '14430.1' -- before the keyframe nudge -- or '1e+05')."""
    return f"{float(x):.3f}".rstrip("0").rstrip(".")


def _set_part(per, start, b, end, label_start=None, seek=None):
    """Point the download at START-END: what ffmpeg is asked for (`seek`,
    just after the keyframe) and what the file is called (`label_start`,
    the keyframe itself)."""
    ls = start if label_start is None else label_start
    sk = start if seek is None else seek
    per["download_sections"] = (f"*{_num(sk)}-{_num(b)}" if b is not None
                                else f"*{_num(sk)}-inf")
    per["section_span"] = (ls, end)
    base = per["_part"]["base"]
    if base:
        per["template"] = base + f" ({part_label(ls, b)}).%(ext)s"


def _part_space(per, info, a, end, say, ids=None):
    """Before a part: will what is left of it (A to END) fit on the disk,
    and in one file?"""
    folder = per.get("output_dir") or "."
    est = parts.estimate_bytes(info, ids or per.get("format_selector"),
                               (end - a) if end else None)
    if not est:
        return
    free = parts.free_bytes(folder)
    gb = 1024 ** 3
    if free is not None and est > free:
        upto = a + (end - a) * free / est
        say(f"  !! about {est / gb:.1f} GB is needed and {free / gb:.1f} GB "
            f"is free: it will stop around {live.hms(upto)}. What came down "
            f"by then stays a playable file; free some space and run the same "
            f"command again to carry on from there.")
    typ = parts.fs_type(folder)
    if parts.is_fat(typ) and est > parts.FAT_LIMIT:
        say(f"  note: this card is FAT32 ({typ}), which holds no file of 4 GB "
            f"or more, and the part is about {est / gb:.1f} GB. When a file "
            f"reaches 4 GB it is closed (it plays) and the rest goes into a "
            f"new file, by itself.")


def _size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _download_part(url, per, info, say, tag, on_file):
    """Fetch a part so that whatever comes down plays, and carry on from
    where the files end -- after a drop, the 4 GB limit of a FAT32 card, or
    on a later run -- instead of starting the part over. -> (files, error).

    ffmpeg reads it through musictool's range proxy (rangeproxy.py), which
    fetches in 10 MB pieces: a part comes as fast as a whole video, with any
    ffmpeg. Everything else is as it was."""
    def waiting(w, err):
        say(f"  ! network problem -- waiting {int(w)}s; the part goes on "
            f"where it was")
        progress.report({"type": "wait", "secs": w, "tag": tag} if tag else
                        {"type": "wait", "secs": w})

    fast = (rangeproxy.Proxy(on_wait=waiting) if rangeproxy.wanted(per)
            else None)
    want_subs = bool(per.get("write_subs") or per.get("embed_subs"))
    folder = per.get("output_dir") or "."
    try:
        before = set(os.listdir(folder))
    except OSError:
        before = set()
    try:
        files, err = _part_rounds(url, per, info, say, tag, on_file, fast)
        if want_subs:
            _part_subs(per, files, before, say)
        return files, err
    finally:
        if fast is not None:
            fast.stop()
            if fast.requests:
                say(f"  fast: {fast.summary()}")


def _part_subs(per, files, before, say):
    """After a part: yt-dlp's subtitles of the whole video (named after the
    part's first file) get the video's plain name, NAME [id].en.vtt, and
    every file of the part its own, cut to it (parts.cut_subtitles)."""
    P = per["_part"]
    vid = P.get("vid")
    folder = per.get("output_dir") or "."
    try:
        names = os.listdir(folder)
    except OSError:
        return
    if not vid:
        return
    for n in names:
        m = parts.SUB_PART_RE.search(n)
        if not m or f"[{vid}]" not in n or n in before:
            continue
        src = os.path.join(folder, n)
        dst = os.path.join(folder, n[:m.start()] + f".{m.group(10)}."
                           f"{m.group(11)}")
        try:
            if os.path.exists(dst):
                os.remove(src)
            else:
                os.replace(src, dst)
        except OSError:
            pass
    starts = P.get("starts") or {}
    made = []
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return
    for path, _v in files:
        name = os.path.basename(path)
        m = parts.LABEL_RE.search(name)
        lab = parts.parse_label(name)
        if not m or not lab or m.group(11):          # unfinished: .part
            continue
        base, stem = name[:m.start()], name[:-len(lab[2])]
        start = starts.get(path, lab[0])
        length = lab[1] - start + 1 if lab[1] is not None else None
        for w in names:
            mm = re.fullmatch(re.escape(base) + r"\.([\w-]+)\.("
                              + "|".join(parts.SUB_TEXT) + ")", w, re.I)
            if not mm:
                continue
            out = os.path.join(folder, f"{stem}.{mm.group(1)}.{mm.group(2)}")
            if os.path.exists(out):
                continue
            try:
                with open(os.path.join(folder, w), encoding="utf-8",
                          errors="replace") as fh:
                    text = fh.read()
                with open(out, "w", encoding="utf-8") as fh:
                    fh.write(parts.cut_subtitles(text, mm.group(2), start,
                                                 length))
                made.append(out)
            except OSError as e:
                say(f"  ! could not cut the subtitles to the part ({e})")
    if made:
        say("  subtitles cut to the part: "
            + ", ".join(os.path.basename(p) for p in made))


def _fast_info(info, fast, say):
    """The info for yt-dlp to load, its links pointed at the proxy -> a
    temporary file, or None (nothing to read through it: HLS, another site)."""
    try:
        return rangeproxy.write_info(info, fast)
    except (OSError, ValueError, TypeError) as e:
        say(f"  ! could not prepare the fast way ({e}); ffmpeg fetches it alone")
        return None


def _part_rounds(url, per, info, say, tag, on_file, fast):
    P = per["_part"]
    a, b, end, vid = P["a"], P["b"], P["end"], P["vid"]
    folder = per.get("output_dir") or "."
    files = []
    if vid:
        # a run that was killed (Termux closed, power, Force stop) left a
        # .part: keep it, or the new run's ffmpeg -y would overwrite it
        parts.drop_temps(folder, vid)
        for p in parts.leftovers(folder, vid):
            kept = parts.salvage(p, say)
            if kept and on_file:
                on_file(kept[0], vid)        # into the folder's index
    sleeps = iter(download.net_sleeps(float(per.get("net_max_wait", 240))))
    expired = big = stuck = rounds = 0
    last = None
    slow = fast is None                  # the proxy failed: ffmpeg alone
    subs = per.get("embed_subs") or per.get("write_subs")
    put_in = per.get("embed_subs") and not per.get("write_subs")
    if subs and vid and not put_in:
        have = parts.sidecars(folder, vid)
        if have:
            say("  subtitles / chat already here, not fetched again: "
                + ", ".join(os.path.basename(p) for p in have))
            subs = False
    while True:
        rounds += 1
        # the chat is for the whole video: fetched with the first piece only
        # (each yt-dlp run would fetch it all again)
        first = bool(subs and rounds == 1)
        per["embed_subs"] = first and bool(put_in)
        per["write_subs"] = first and not put_in
        if rounds > 1:
            # the links in `info` run out after some hours, and the keyframe
            # look-up uses them: fresh ones for every new piece
            fresh = download.probe(url, per)
            if isinstance(fresh, dict) and not fresh.get("error"):
                info = fresh
        pos, used = (parts.continue_point(parts.coverage(folder, vid), a, end)
                     if vid else (a, []))
        # which formats these are (bv*+ba/b does not say): their start,
        # HLS or not, and the sound filter depend on it
        ids = download.resolve_ids(info, per) or per.get("format_selector")
        hls = any(parts.is_hls(f) for f in parts.chosen_formats(info, ids))
        per["aac_fix"] = download.needs_aac_fix(info, ids)
        if end and pos >= end - 1:
            say("  already here, nothing to download: "
                + ", ".join(os.path.basename(p) for p in used))
            return files, None
        if last is not None and pos <= last + 1:
            stuck += 1
            if stuck >= 3:
                return files, "the part is not getting any further"
        last = pos
        if rounds == 1:
            _part_space(per, info, pos, end, say, ids)
        start = a
        if pos > a + 1:
            start = max(a, pos - 2)      # a little overlap, never a gap
            if hls:
                start = max(a, start - parts.HLS_SLACK)
            say(f"  already here: {live.hms(a)} to {live.hms(pos)} ("
                + ", ".join(os.path.basename(p) for p in used)
                + f") -- fetching {live.hms(start)} on, into a new file")
        key = parts.keyframe_before(info, ids, start,
                                    live.ffmpeg_exe())
        if key is not None and start - key > 0.05:
            got = parts.chosen_formats(info, ids)
            if not got or any((f.get("vcodec") or "none") != "none"
                              for f in got):
                say(f"  starts at the keyframe at {live.hms(key)} "
                    f"({start - key:.1f} s early), so the picture and sound "
                    f"line up from the first frame")
            else:
                say(f"  starts at {live.hms(key)} ({start - key:.1f} s early): "
                    f"the sound can only be cut where its pieces begin")
        if key is not None:
            _set_part(per, start, b, end, label_start=key,
                      seek=round(key + parts.KEY_NUDGE, 3))
        else:
            _set_part(per, start, b, end)
        via = None if slow else _fast_info(info, fast, say)
        if via:
            per["info_json"] = via
            fast.broken = None
            fast.until = b               # no reading ahead past the part
        try:
            got, err = download.download(url, per, log=say, tag=tag,
                                         on_file=on_file)
        finally:
            per.pop("info_json", None)
            if via:
                try:
                    os.remove(via)
                except OSError:
                    pass
        if via and err and err != cancel.CANCELLED and fast.net_error:
            # ffmpeg only saw the proxy give up; this is why (and it makes
            # the wait-for-the-network below happen, as it did without it)
            err += ("\nERROR: musictool's range proxy was unable to connect "
                    "to the server: " + fast.net_error)
        files.extend(got)
        ls = per["section_span"][0]
        if err and "Postprocessing" in err and vid:
            # the part came down whole; only adding the title and cover
            # failed -- that writes a second copy, and a nearly full card
            # has no room for it. The file plays as it is.
            parts.drop_temps(folder, vid)
            done = parts.finished(folder, vid, ls)
            if done:
                parts.drop_cover(done)
                say(f"  kept {os.path.basename(done)} as it is: adding the "
                    f"title and cover failed (with too little free space -- "
                    f"it needs room for a second copy). It plays.")
                if not any(p == done for p, _v in got):
                    got = got + [(done, vid)]
                    files.append((done, vid))
                    if on_file:
                        on_file(done, vid)
                err = None
        if err is None:
            done = got[-1][0] if got else None
            secs = parts.seconds(done) if done and os.path.exists(done) else None
            cut = bool(via and secs and fast.broken)
            if cut:
                # ffmpeg took a cut stream for its end and said "done"; the
                # sound (fetched early) may run on, so the header's length
                # says nothing about how far the picture got. 0 is an answer:
                # the picture's link refused at once, the sound alone came
                # (found on real YouTube -- `or secs` took that for whole)
                played = parts.played_seconds(done)
                if played is not None:
                    secs = played
            if not end or secs is None or (not secs and not cut) \
                    or secs + max(5.0, (end - ls) * 0.01) >= end - ls:
                if done:
                    P.setdefault("starts", {})[done] = ls
                return files, None
            if cut and ls + secs <= pos + 1:
                # cut off again before it got past what is already here
                # (only what was still in memory came): nothing new in it
                try:
                    os.remove(done)
                except OSError:
                    pass
                files[:] = [(p, v) for p, v in files if p != done]
                say(f"  ! cut off again before anything new came "
                    f"({fast.broken}) -- trying again")
                if "403" in str(fast.broken):
                    expired += 1
                    if expired > 3:
                        return files, str(fast.broken)
                continue
            new = parts.relabel(done, ls, ls + secs)
            try:
                os.replace(done, new)
            except OSError:
                new = done
            files[:] = [(new if p == done else p, v) for p, v in files]
            P.setdefault("starts", {})[new] = ls
            if on_file and new != done:
                on_file(new, vid)
            why = (f"the fast way was cut off: {fast.broken}" if cut
                   else "without saying why (a long drop)")
            say(f"!! it stopped at {live.hms(ls + secs)} {why}: kept as "
                f"{os.path.basename(new)}, carrying on")
            if cut and "403" in str(fast.broken):
                expired += 1
                if expired > 3:
                    return files, str(fast.broken)
            continue                     # ffmpeg already waited out the drop
        kept_now = []
        if err is not None:
            for p in parts.leftovers(folder, vid) if vid else []:
                kept = parts.salvage(p, say)
                if kept:
                    kept_now.append(kept[0])
                    files.append((kept[0], vid))
                    if on_file:
                        on_file(kept[0], vid)
        if err == cancel.CANCELLED:
            return files, err
        # FAT32 holds no file of 4 GB; Windows says "not enough space" then
        at_4g = any(_size(p) > parts.FAT_LIMIT - 256 * 1024 ** 2
                    for p in kept_now) and parts.is_fat(parts.fs_type(folder))
        if ("File too large" in err or at_4g) and big < 50:
            big += 1
            say("  the card takes no file of 4 GB or more (FAT32): that file "
                "is closed and plays; the rest goes into a new file")
            continue
        if ("No space left" in err or "Disk quota" in err
                or "not enough space" in err.lower()):
            return files, ("the disk is full. What came down plays; free "
                           "some space and run the same command again -- it "
                           "carries on from where the files end.")
        if "HTTP Error 403" in err or "403 Forbidden" in err:
            if expired < 3:
                expired += 1
                say("  ! YouTube refused to go on (403) -- usually the links "
                    "ran out; asking for fresh ones and carrying on")
                continue
            return files, err
        if download.retryable(err):
            w = next(sleeps, None)
            if w is None:
                return files, err
            say(f"  ! network problem -- waiting {int(w)}s, then carrying on "
                f"from where the file ends")
            if not cancel.sleep(w):
                return files, cancel.CANCELLED
            continue
        if via and not slow:
            # something about reading through the proxy that was not
            # foreseen: the rest the old way rather than not at all
            slow = True
            why = (err.strip().splitlines() or ["?"])[-1][:200]
            say(f"  ! the fast way failed ({why}) -- fetching the rest by "
                f"ffmpeg alone")
            continue
        return files, err


def cmd_get(args):
    o = _opts(args)
    os.makedirs(o["output_dir"], exist_ok=True)
    if getattr(args, "output_dir", None):
        # Remember an explicit -o so the next run (CLI or GUI) starts here.
        configmod.remember(output_dir=o["output_dir"])
    o["lyrics_dir"] = configmod.lyrics_dir_for(o, o["output_dir"])
    if o.get("exact") and not o.get("lyrics_only"):
        # Checked before anything is downloaded: --exact means "these
        # streams and nothing else", so a selector that lets yt-dlp choose,
        # or none at all, is refused rather than quietly widened.
        if not o.get("format_selector"):
            print("!! --exact needs the stream ids: -f 299+140 (video+audio) "
                  "or -f 140 (one stream). `musictool formats URL` lists "
                  "them; the Live streams tab and the Quality tab show them "
                  "too.")
            return 2
        if formats.exact_ids(o["format_selector"]) is None:
            print("!! " + formats.exact_plan(o["format_selector"], [])["error"])
            return 2
    if o.get("just_download") and not o.get("lyrics_only"):
        print("just downloading: nothing is added to the file afterwards (no "
              "title, cover, chapters, subtitles or lyrics inside it)"
              + ("; subtitles are saved beside it" if o.get("embed_subs")
                 else ""))
    part = None
    if getattr(args, "part", None):
        try:
            part = parse_range(args.part)
        except ValueError as e:
            print(f"!! --range: {e}")
            return 2
        if not have_ffmpeg():
            print("!! --range needs ffmpeg (it fetches the part). "
                  + ffmpeg_hint())
            return 2
    for problem in download.preflight(o):
        print("WARNING: " + problem)

    # One shared lyrics session so every file in the batch reuses the cache.
    http, provs = lyrics.open_session(o)
    res = {"fixed": [], "skipped": [], "unresolved": [], "errors": [],
           "total": 0, "api_calls": 0}
    done = set()

    def on_file(path, vid, url):
        """Runs the instant a download lands: record the videoId, then fetch and
        embed that file's lyrics right away -- lyrics travel with the download."""
        if path in done:
            return
        done.add(path)
        if vid:
            download.record(o["output_dir"], path, vid, url)
        if o.get("no_lyrics") or url in live_urls or cancel.requested():
            return
        infos = lyrics.gather([path], (o["lyrics_dir"],))
        if not infos:
            return
        print(f"\n==> lyrics for {os.path.basename(path)}")
        bucket, entry = lyrics.process_file(infos[0], o, http, provs, log=print)
        res[bucket].append(entry)
        res["total"] += 1

    targets = []
    refused = []             # --exact: the stream ids are not on this video
    live_urls = set()        # live recordings: a broadcast has no lyrics
    stopped = False
    urls = [u for u in args.urls if not o.get("lyrics_only")]
    n_jobs = max(1, min(8, int(getattr(args, "jobs", 1) or 1)))
    started = [0]
    lock = threading.Lock()

    def one(url):
        """One URL from start to finish -- safe to run beside the others."""
        nonlocal stopped
        with lock:
            started[0] += 1
            idx = started[0]
        # Parallel runs prefix every line, or four logs interleave into noise.
        say = print if n_jobs == 1 else (lambda m: print(f"[{idx}] {m}"))
        if cancel.requested():
            return
        say(f"\n==> {url}")
        # Quality is resolved PER VIDEO. Format ids are not global: 140-4 is
        # the English track on one video and the Spanish original on the next,
        # and 614 may not exist at all. Resolving once for the whole batch and
        # reusing the selector is how a dubbed video slips through in the
        # wrong language while everything reports success.
        per = dict(o)
        # One metadata read per single video: is it a live stream? (It is
        # reused for the quality pick, so that costs nothing extra.) A
        # playlist is not read item by item -- that would double its time.
        info = None
        if not is_playlist_url(url) and not per.get("yes_playlist"):
            info = download.probe(url, per)
            if not isinstance(info, dict) or info.get("error"):
                info = None          # the download itself will say why
        live_st = live.status(info) if info else None
        _resolve_quality(url, per, log=say, info=info)
        if per.get("exact") and not _apply_exact(per, info, say):
            with lock:
                refused.append(url)
            return
        if part and not _plan_part(url, per, info, live_st, part, say):
            with lock:
                refused.append(url)
            return
        if part:
            live_urls.add(url)        # a slice of a song has no use for lyrics
        elif live_st in live.LIVE or live_st in ("is_upcoming", "was_live"):
            live.plan(info, per, log=say)
            if per.get("no_lyrics"):
                live_urls.add(url)
        # yt-dlp only ever sees one URL, so it cannot tell the UI how many are
        # queued behind it. musictool can.
        progress.report({"type": "batch", "i": idx, "n": len(urls)})
        _tag = "" if n_jobs == 1 else str(idx)
        _onf = (lambda pth, vid, u=url: on_file(pth, vid, u))
        if part:
            files, err = _download_part(url, per, info, say, _tag, _onf)
        else:
            files, err = download.download(url, per, log=say, tag=_tag,
                                           on_file=_onf)
        with lock:
            # extend(), not +=: inside a nested function that would rebind the
            # enclosing local and raise, and it is atomic enough under the lock.
            targets.extend(p for p, _ in files)
        if err == cancel.CANCELLED:
            stopped = True
            return
        if err:
            say(f"!! {err}")
            if per.get("live_from_start"):
                live.after_failure(per, err, log=say)
            return
        say(f"    {len(files)} file(s)")

    if n_jobs == 1:
        for url in urls:
            if cancel.requested():
                stopped = True
                break
            one(url)
    else:
        print(f"\n==> {len(urls)} video(s), {n_jobs} at a time")
        with ThreadPoolExecutor(max_workers=n_jobs) as pool:
            list(pool.map(one, urls))
        if cancel.requested():
            stopped = True
    if stopped:
        if targets:
            print(f"\n==> {cancel.CANCELLED}: keeping the {len(targets)} file(s) "
                  f"that finished (they already have their lyrics)")
        else:
            print(f"\n==> {cancel.CANCELLED}")
        if part:
            print("    what came down of the part plays as it is; running the "
                  "same command again carries on from where it ends, in a "
                  "new file")
        else:
            print("    a half-finished file keeps its .part, so the next run "
                  "resumes it instead of starting over")

    if refused and not targets:
        return 1
    if o.get("no_lyrics"):
        print("\n==> skipping lyrics ("
              + ("--just-download" if o.get("just_download") else "--no-lyrics")
              + ")")
        return 0
    if urls and live_urls >= set(urls):
        # only live recordings: no lyrics to look for -- and no sweep of the
        # whole folder when one stopped early
        print("\n==> live recording(s) / part(s) of a video: no lyrics to fetch")
        return 0 if targets else (130 if stopped else 1)
    if stopped and not targets:
        http.save()
        return 130
    if not targets:
        # --lyrics-only, or nothing downloaded: scan the folder as before.
        return _run_lyrics([o["output_dir"]], o)

    res["api_calls"] = http.calls
    http.save()
    print("\n" + "=" * 60)
    print(f"fixed      : {len(res['fixed'])}")
    print(f"already ok : {len(res['skipped'])}")
    print(f"unresolved : {len(res['unresolved'])}")
    if res["errors"]:
        print(f"unreadable : {len(res['errors'])}")
    print(f"api calls  : {res['api_calls']}")
    if not o.get("dry_run"):
        print("report     : " + lyrics.write_report(o["output_dir"], res))
    return 0


def _run_lyrics(paths, o, note=0):
    res = lyrics.fix(paths, o, log=print)
    if res.get("error"):
        print("!! " + res["error"])
        return 1
    print("\n" + "=" * 60)
    print(f"fixed      : {len(res['fixed'])}")
    print(f"already ok : {len(res['skipped'])}")
    print(f"unresolved : {len(res['unresolved'])}")
    if res["errors"]:
        print(f"unreadable : {len(res['errors'])}")
    print(f"api calls  : {res['api_calls']}")
    if not o.get("dry_run"):
        print("report     : " + lyrics.write_report(o["output_dir"], res))
    return 0


def cmd_lyrics(args):
    good, _ = _report_missing(args.paths)
    if not good:
        return 2
    o = _opts(args)
    # The GUI can hand over a subset of individual files; the report and the
    # .lrc sidecars belong next to those files, not in a remembered output dir.
    o["output_dir"] = os.path.abspath(common_root(good))
    o["lyrics_dir"] = configmod.lyrics_dir_for(o, o["output_dir"])
    return _run_lyrics(good, o)


def cmd_scan(args):
    import json
    good, _ = _report_missing(args.paths)
    if not good:
        return 2
    o = _opts(args)
    root = os.path.abspath(common_root(good))
    dirs = [configmod.lyrics_dir_for(o, root)]
    files = lyrics.gather(good, tuple(dirs),
                          recursive=getattr(args, "recursive", False))
    counts = lyrics.summarise(files)
    if args.json:
        print(json.dumps([{"file": os.path.basename(f["path"]),
                           "state": f["state"], "artist": f["artist"],
                           "title": f["title"],
                           "duration": round(f["duration"], 1),
                           "error": f["error"]} for f in files],
                         ensure_ascii=False, indent=1))
        return 0
    for f in files:
        state = f["error"] and "error" or f["state"]
        print(f"{state:<8} {os.path.basename(f['path'])}")
    print()
    for k in ("synced", "partial", "plain", "empty", "error"):
        if counts.get(k):
            print(f"  {k:<8} {counts[k]}")
    print(f"  {'total':<8} {len(files)}")
    print("\nsynced = fine. partial/plain/empty = needs fixing.")
    return 0


def cmd_duplicates(args):
    import json
    good, _ = _report_missing(args.paths)
    if not good:
        return 2
    o = _opts(args)
    root = os.path.abspath(common_root(good))
    dirs = [configmod.lyrics_dir_for(o, root)]
    groups = lyrics.find_duplicates(good, dirs,
                                    recursive=getattr(args, "recursive", False),
                                    min_chars=args.min_chars)
    if args.suspicious_only:
        groups = [g for g in groups if g["suspicious"]]

    if args.json:
        print(json.dumps([{
            "fingerprint": g["fingerprint"][:16],
            "count": len(g["members"]),
            "distinct_titles": g["distinct_titles"],
            "suspicious": g["suspicious"],
            "preview": g["preview"],
            "files": [os.path.basename(m["path"]) for m in g["members"]],
        } for g in groups], ensure_ascii=False, indent=1))
        return 0

    if not groups:
        print("no duplicate lyrics found")
        return 0

    total = sum(len(g["members"]) for g in groups)
    susp = sum(1 for g in groups if g["suspicious"])
    print(f"{len(groups)} group(s) share lyrics, covering {total} file(s); "
          f"{susp} group(s) look like the wrong-lyrics bug\n")
    for i, g in enumerate(groups, 1):
        mark = "SUSPICIOUS (different titles)" if g["suspicious"] else \
               "same title -- probably a genuine duplicate"
        print(f"[{i}] {len(g['members'])} files, {g['distinct_titles']} distinct "
              f"title(s) -- {mark}")
        print(f"    starts: {g['preview']}")
        for m in g["members"]:
            fa, ft = tags.from_filename(m["path"])
            print(f"      - {os.path.basename(m['path'])}"
                  f"   [{m['state']}]"
                  + (f"  tag title: {m['title']}" if m["title"] and
                     m["title"] != ft else ""))
        print()

    if not args.fix:
        print("re-fetch correct lyrics with: musictool duplicates ... --fix")
        return 0

    targets = [m["path"] for g in groups for m in g["members"]]
    print(f"re-fetching lyrics for {len(targets)} file(s) using each file's OWN "
          f"tags (not folder order)...\n")
    o["force"] = True
    o["output_dir"] = root
    o["lyrics_dir"] = configmod.lyrics_dir_for(o, root)
    res = lyrics.fix(targets, o, log=print)
    print("\n" + "=" * 60)
    print(f"fixed      : {len(res['fixed'])}")
    print(f"unresolved : {len(res['unresolved'])}")
    print(f"api calls  : {res['api_calls']}")
    print("report     : " + lyrics.write_report(o["output_dir"], res))
    still = lyrics.find_duplicates([o["output_dir"]], [o["lyrics_dir"]],
                                   min_chars=args.min_chars)
    still = [g for g in still if g["suspicious"]]
    if still:
        print(f"\nstill duplicated after the fix: {len(still)} group(s) -- "
              f"likely genuinely the same song, or no distinct lyrics exist")
    else:
        print("\nno suspicious duplicates remain")
    return 0


def _report_missing(paths):
    """Warn about paths that don't exist; the caller skips them gracefully."""
    good, bad = existing_paths(paths)
    for b in bad:
        print(f"!! no such path, skipping: {b}")
    if bad and not good:
        print("!! nothing to do -- none of the given paths exist")
    return good, bad


def _is_audio(p):
    return p.lower().endswith(AUDIO_EXT)


def _embed_one(audio, lrc, args, quiet=False):
    try:
        with open(lrc, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except Exception as e:
        print(f"  ! cannot read {lrc}: {e}")
        return 1
    state = classify(text)
    if not text.strip():
        print(f"  ! {os.path.basename(lrc)} is empty")
        return 1
    if not quiet:
        print(f"embedding {os.path.basename(lrc)} -> "
              f"{os.path.basename(audio)}  [{state}]")
    if getattr(args, "dry_run", False):
        print("  (dry run -- nothing written)")
        return 0
    try:
        tags.write_lyrics(audio, text)
    except Exception as e:
        print(f"  ! embed failed for {os.path.basename(audio)}: {e}")
        return 1
    if not getattr(args, "no_sidecar", False):
        tags.write_sidecar(os.path.dirname(audio) or ".",
                           os.path.splitext(os.path.basename(audio))[0], text)
    return 0


def cmd_extract(args):
    good, _ = _report_missing(args.paths)
    if not good:
        return 2
    files = []
    for pth in good:
        files += tags.list_audio(pth, recursive=args.recursive)
    if not files:
        print("no audio files found in the given paths")
        return 2
    outdir = os.path.abspath(os.path.expanduser(args.out)) if args.out else None
    written = skipped = nolr = 0
    for f in files:
        info = tags.read(f)
        name = os.path.basename(f)
        if not (info["lyrics"] or "").strip():
            print(f"  (no embedded lyrics) {name}")
            nolr += 1
            continue
        d = outdir or os.path.dirname(f)
        dst = os.path.join(d, os.path.splitext(name)[0] + ".lrc")
        if os.path.exists(dst) and not args.force:
            print(f"  (exists -- use --force to overwrite) "
                  f"{os.path.basename(dst)}")
            skipped += 1
            continue
        os.makedirs(d, exist_ok=True)
        txt = info["lyrics"] if info["lyrics"].endswith("\n") else info["lyrics"] + "\n"
        with open(dst, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(txt)
        print(f"  wrote {dst}  [{info['state']}]")
        written += 1
    print(f"\nextracted {written} .lrc, skipped {skipped}, "
          f"no embedded lyrics {nolr} -- of {len(files)} file(s)")
    print("edit the .lrc (add [mm:ss.xx] timings), then put it back with: "
          "musictool embed <file-or-folder>")
    return 0


def cmd_embed(args):
    good, _ = _report_missing(args.paths)
    if not good:
        return 2
    # Explicit pair: one audio file plus one .lrc / .txt.
    if (len(good) == 2 and _is_audio(good[0])
            and good[1].lower().endswith((".lrc", ".txt"))):
        rc = _embed_one(os.path.abspath(good[0]), os.path.abspath(good[1]), args)
        print("embedded 1 file" if rc == 0 else "embed failed")
        return rc
    extra = [args.lyrics_dir] if args.lyrics_dir else []
    files = []
    for pth in good:
        files += tags.list_audio(pth, recursive=args.recursive)
    if not files:
        print("no audio files found in the given paths")
        return 2
    fixed = nolrc = failed = 0
    for f in files:
        src = next((c for c in tags.sidecar_candidates(f, extra)
                    if os.path.exists(c)), None)
        if not src:
            print(f"  (no .lrc found) {os.path.basename(f)}")
            nolrc += 1
            continue
        rc = _embed_one(f, src, args, quiet=True)
        if rc == 0:
            print(f"  embedded {os.path.basename(f)}  <- {os.path.basename(src)}")
            fixed += 1
        else:
            failed += 1
    print(f"\nembedded {fixed}, no .lrc {nolrc}, failed {failed} -- of "
          f"{len(files)} file(s)")
    return 0 if not failed else 1


PROBE_VIDEO_ID = "JGwWNGJdvx8"   # Ed Sheeran - Shape of You; has a lyrics page


def cmd_providers(args):
    for p in providers.build():
        _ok, msg = p.available()
        print(f"{p.name}")
        print(f"    status : {msg}")
        print(f"    about  : {p.blurb}")
    if not args.test:
        print("\nuse --test to make a live probe request to each")
        return 0
    from .util import Http
    http = Http(delay=0.5)
    probe = providers.Query(artist="Maverick City Music", title="Well, Well, Well",
                            duration=225)
    print()
    for p in providers.build():
        try:
            if p.needs_video:
                # a public video with a lyrics page: shows whether YouTube is
                # sending line times today (late 2026 it mostly is not)
                got = p.fetch(providers.Query(artist="Ed Sheeran",
                                              title="Shape of You",
                                              video_id=PROBE_VIDEO_ID), http)
                if not got:
                    print(f"{p.name:<10} nothing for the sample video "
                          f"{PROBE_VIDEO_ID} (needs a videoId from `get`)")
                elif got[0].synced:
                    print(f"{p.name:<10} reachable: timed lyrics "
                          f"({got[0].note})")
                else:
                    print(f"{p.name:<10} reachable, but YouTube sent the lines "
                          "WITHOUT times today -- used only as unsynced "
                          "(needs a videoId from `get`)")
                continue
            got = p.fetch(probe, http)
            synced = sum(1 for c in got if c.synced)
            print(f"{p.name:<10} reachable: {len(got)} result(s), {synced} synced")
        except Exception as e:
            print(f"{p.name:<10} FAILED: {e}")
    print(f"\n({http.calls} requests, User-Agent: {USER_AGENT})")
    return 0


def cmd_config(args):
    if args.reset:
        configmod.save(dict(configmod.DEFAULTS))
        print("reset -> " + configmod.PATH)
        return 0
    cfg = configmod.load()
    if not args.key:
        print(configmod.PATH + "\n")
        for k in sorted(cfg):
            print(f"  {k:<18} {cfg[k]}")
        print("\nset with: musictool config <key> <value>")
        return 0
    if args.value is None:
        print(cfg.get(args.key, f"(unknown key: {args.key})"))
        return 0
    v = args.value
    if v.lower() in ("true", "false"):
        v = v.lower() == "true"
    elif v.replace(".", "", 1).isdigit():
        v = float(v) if "." in v else int(v)
    elif "," in v and args.key == "provider_order":
        v = [x.strip() for x in v.split(",")]
    cfg[args.key] = v
    configmod.save(cfg)
    print(f"{args.key} = {v}")
    return 0


MEDIA_EXTS = (".mkv", ".mp4", ".webm", ".mov", ".m4a", ".mp3", ".opus",
              ".flac", ".ogg", ".wav", ".aac")


def _collect_media(paths, log=print):
    """Every media file in those files/folders, in a stable order."""
    out = []
    for p in paths:
        if os.path.isdir(p):
            for root, _dirs, files in os.walk(p):
                for f in sorted(files):
                    if os.path.splitext(f)[1].lower() in MEDIA_EXTS:
                        out.append(os.path.join(root, f))
        elif os.path.exists(p):
            out.append(p)
        else:
            log(f"  !! not found: {p}")
    return out


def _relang_report(files, lang):
    """Which language is each file actually in, and where did it come from?

    Reads only the files and the index musictool wrote when it downloaded
    them -- no network, no downloads, so it works even while YouTube is
    refusing to talk to you.
    """
    wrong, right, unknown, noid = [], [], [], []
    for path in files:
        name = os.path.basename(path)
        entry = download.load_index(os.path.dirname(path) or ".").get(name)
        url = (entry or {}).get("url") or ""
        have = download.audio_track_lang(path)
        if not url:
            noid.append((name, have))
        elif not have:
            unknown.append((name, url))
        elif formats.same_language(have, lang):
            right.append((name, have, url))
        else:
            wrong.append((name, have, url))

    def block(title, rows, show_url=True):
        print(f"\n{title} ({len(rows)}):")
        for name, have, url in rows:
            print(f"  {have or '?':<5} {name}")
            if show_url and url:
                print(f"        {url}")

    print(f"\n{len(files)} file(s) checked, looking for {lang} audio")
    if wrong:
        block(f"NOT in {lang} -- these are the ones to fix", wrong)
    else:
        print(f"\nNOT in {lang}: none -- every file with a language tag is "
              f"already {lang}")
    if right:
        block(f"already {lang}", right, show_url=False)
    if unknown:
        print(f"\nno language tag, cannot tell ({len(unknown)}):")
        for name, url in unknown:
            print(f"  ?     {name}\n        {url}")
        print("  (these may well be fine -- pass --force to relang to replace "
              "the audio anyway)")
    if noid:
        print(f"\nmusictool never downloaded these, so there is no link to "
              f"give ({len(noid)}):")
        for name, have in noid:
            print(f"  {have or '?':<5} {name}")
        print("  re-download them by hand, or add them to "
              ".musictool-index.json")
    if wrong:
        print(f"\nPut the {lang} audio into those {len(wrong)} file(s) without "
              "re-downloading the video:")
        print(f"  musictool relang \"{os.path.dirname(files[0]) or '.'}\"")
    return 0


def cmd_relang(args):
    """Put the right language in files you have already downloaded.

    A dubbed video can arrive in the wrong language, and re-running `get`
    would pay for the video a second time. This downloads only the audio
    track -- tens of MiB instead of hundreds -- and re-muxes it into the file
    you already have, keeping the picture, the cover and the chapters.
    """
    lang = str(args.audio_lang or "en").strip().lower()
    files = _collect_media(args.paths)
    if not files:
        print("nothing to do: no media files in those paths")
        return 1
    if args.list:
        return _relang_report(files, lang)
    o = {"cookies": args.cookies or "",
         "cookies_from_browser": args.cookies_from_browser or "off",
         "proxy": args.proxy or "", "no_playlist": True}
    n = {"fixed": 0, "would": 0, "already": 0, "unknown": 0, "no-track": 0,
         "no-id": 0, "audio-only": 0, "errors": 0}
    tmp = None
    for path in files:
        if cancel.requested():
            print("\n==> stopped")
            break
        name = os.path.basename(path)
        print(f"\n==> {name}")
        entry = download.load_index(os.path.dirname(path) or ".").get(name)
        url = (entry or {}).get("url")
        if not url:
            print("  !! musictool has no videoId for this file, so it cannot "
                  "find the other language tracks. Re-download it, or add it "
                  "to .musictool-index.json.")
            n["no-id"] += 1
            continue
        info = download.probe(url, o)
        if not isinstance(info, dict) or info.get("error"):
            print(f"  !! could not read the tracks: "
                  f"{str((info or {}).get('error'))[:160]}")
            n["errors"] += 1
            continue
        why = download.no_media_reason(info)
        if why:
            print(f"  !! {why}")
            n["errors"] += 1
            continue
        rows = formats.rows_from_info(info)
        spoken = formats.languages_in(rows)
        if len(spoken) > 1:
            print("    offered: " + ", ".join(
                f"{c}{' (original)' if orig else ''}" for c, orig, _x in spoken))
        want = formats.best_audio_in(rows, lang)
        if want is None:
            print(f"    no track tagged {lang} on this video -- left alone")
            n["no-track"] += 1
            continue
        have = download.audio_track_lang(path)
        if have and formats.same_language(have, lang):
            print(f"    already {have} -- left alone")
            n["already"] += 1
            continue
        if not have and not args.force:
            print("    this file does not say what language it is; pass "
                  "--force to replace it anyway")
            n["unknown"] += 1
            continue
        if not download.has_video_stream(path):
            print("    audio-only file -- there is nothing to keep, so just "
                  f"re-download it: musictool get \"{url}\"")
            n["audio-only"] += 1
            continue
        print(f"    will take format {want['id']} "
              f"({want.get('lang') or lang}, "
              f"{formats.human_bytes(want['bytes'])}) and keep the video")
        if args.dry_run:
            n["would"] += 1
            continue
        tmp = tmp or os.path.join(os.path.dirname(path) or ".",
                                  ".musictool-relang")
        os.makedirs(tmp, exist_ok=True)
        got = download.fetch_format(url, o, want["id"], tmp)
        if not got:
            n["errors"] += 1
            continue
        if download.swap_audio(path, got):
            print(f"    done -- {name} now has the {want.get('lang') or lang} "
                  f"audio")
            n["fixed"] += 1
        else:
            n["errors"] += 1
        try:
            os.remove(got)
        except OSError:
            pass
    if tmp and os.path.isdir(tmp) and not os.listdir(tmp):
        try:
            os.rmdir(tmp)
        except OSError:
            pass
    said = (f"\n{n['fixed']} file(s) re-muxed" if not args.dry_run
            else f"\n{n['would']} file(s) would be re-muxed")
    for label, key in (("already in that language", "already"),
                       ("no such language on the video", "no-track"),
                       ("language unknown (use --force)", "unknown"),
                       ("no videoId", "no-id"),
                       ("audio-only", "audio-only"),
                       ("failed", "errors")):
        if n[key]:
            said += f", {n[key]} {label}"
    print(said)
    return 1 if n["errors"] else 0


def cmd_chat(args):
    """Live chat replay -> subtitles next to (or inside) the video."""
    o = _opts(args)
    rng = None
    if args.part:
        try:
            rng = parse_range(args.part)
        except ValueError as e:
            print(f"!! --range: {e}")
            return 2
    if args.lines is not None and args.lines < 1:
        print("!! --lines must be 1 or more")
        return 2
    if args.font_size is not None and not 8 <= args.font_size <= 300:
        print("!! --font-size is in pixels on a 1080p picture: 8 to 300 "
              "(45 is the default)")
        return 2
    folder = os.path.abspath(os.path.expanduser(o.get("output_dir") or "."))
    video = os.path.abspath(args.video) if args.video else None
    if video and not os.path.isfile(video):
        print(f"!! no such video: {video}")
        return 2
    if video and not getattr(args, "output_dir", None):
        folder = os.path.dirname(video)
    elif chat.source_kind(args.source) != "link" \
            and not getattr(args, "output_dir", None):
        folder = ""                    # the file's own folder
    style = {"fmt": args.sub_format, "lines": args.lines, "hold": args.hold,
             "side": args.side, "place": args.place, "width": args.width,
             "wrap": args.wrap, "pad": args.pad, "names": args.names,
             "box": args.box, "gap": args.gap, "colours": args.colours,
             "font_size": args.font_size,
             "offset": args.offset, "range": rng,
             "video": video, "embed": args.embed}
    return chat.run(args.source, folder, o, style)


def cmd_live(args):
    """Live streams: status, look at a folder, join + watch, resume."""
    o = _opts(args)
    if args.status:
        folder = args.folder or o.get("output_dir") or "."
        info = download.probe(args.status, o)
        if not isinstance(info, dict) or info.get("error"):
            err = str((info or {}).get("error") or "no answer")
            print("!! " + err.splitlines()[0][:300])
            hint = download.ADVICE.get(download.classify_error(err))
            if hint:
                print("-> " + hint)
            return 1
        live.LAST_PROBE[args.status] = info
        live.describe(info, folder if os.path.isdir(folder) else ".")
        live.list_streams(info, url=f'"{args.status}"')
        return 0
    folder = os.path.abspath(os.path.expanduser(
        args.folder or o.get("output_dir") or "."))
    if not os.path.isdir(folder):
        print(f"!! not a folder: {folder}")
        return 2
    if args.resume:
        return live.resume(folder, args.resume, o, force=args.force)
    streams = live.scan(folder)
    if args.watch:
        rc, _made = live.watch_copy(folder, streams, args.out)
        return rc
    if args.join:
        rc = live.join(folder, streams, force=args.force)
        if rc == 0 and not args.no_copy:
            rc, _made = live.watch_copy(folder, live.scan(folder), args.out)
        live.report(folder, live.scan(folder))
        return rc
    live.report(folder, streams)
    if streams:
        print(f'\nnext:  musictool live "{folder}" --join     (or --watch '
              f'while it is still recording)')
    return 0


def ejs_status():
    """yt-dlp-ejs holds the scripts yt-dlp runs (with deno/node) to solve
    YouTube's challenges. `pip install yt-dlp` does NOT bring it; the
    `yt-dlp[default]` extra and the official executables do."""
    import importlib.util
    if importlib.util.find_spec("yt_dlp_ejs"):
        return "yt-dlp-ejs installed (YouTube challenge scripts)"
    if importlib.util.find_spec("yt_dlp"):
        return ('MISSING  ->  pip install -U "yt-dlp[default]"   '
                "(or add --remote-components ejs:github)")
    return ("not checked -- yt-dlp is not installed in this Python "
            "(the official yt-dlp executable bundles it)")


def cmd_doctor(_args):
    ok = True
    print("platform  :", "Windows" if is_windows() else
          ("Termux/Android" if is_termux() else sys.platform))
    print("python    :", sys.version.split()[0])
    cmd = list(ytdlp_cmd())
    try:
        r = subprocess.run(cmd + ["--version"], **cancel.quiet({}),
                           capture_output=True, text=True,
                           timeout=30)
        ver = (r.stdout or "").strip()
        if r.returncode == 0 and ver:
            print(f"yt-dlp    : {ver}  via {' '.join(cmd)}")
        else:
            print("yt-dlp    : MISSING  ->  pip install -U \"yt-dlp[default]\"")
            ok = False
    except Exception as e:
        print(f"yt-dlp    : MISSING ({e})  ->  pip install -U \"yt-dlp[default]\"")
        ok = False
    print("ffmpeg    :", "found" if have_ffmpeg() else
          f"MISSING  ->  {ffmpeg_hint()}")
    name, store = detect_browser()
    print("cookies   :", (f"can read your {name} login  ({store})" if name else
                          "no browser cookie database found -- use --cookies "
                          "with an exported cookies.txt if YouTube asks to sign in"))
    if name in download.CHROMIUM:
        print("            note: " + name + " encrypts its cookies with "
              "App-Bound Encryption -- if yt-dlp says 'Failed to decrypt "
              "with DPAPI', log in to YouTube in Firefox and use "
              "--cookies-from-browser firefox")
    js, jspath = js_runtime()
    print("js runtime:", (f"{js}  ({jspath})" if js else
                          "MISSING  ->  install deno (yt-dlp's default) or "
                          "node; without one the n-challenge fails and "
                          "YouTube answers 'The page needs to be reloaded'"))
    print("ejs       :", ejs_status())
    print("pot server:", ("reachable on 127.0.0.1:4416"
                          if pot_server_reachable() else
                          "not running (only needed if the bgutil PO-token "
                          "plugin is installed)"))
    if not have_ffmpeg():
        ok = False
    try:
        import tkinter  # noqa: F401
        print("tkinter   : available (GUI ready)")
    except Exception:
        hint = "pkg install python-tkinter" if is_termux() else (
            "reinstall Python with the tcl/tk option" if is_windows()
            else "install python3-tk")
        print(f"tkinter   : MISSING  ->  {hint}   (CLI still works)")
    try:
        import mutagen
        print("mutagen   :", getattr(mutagen, "version_string", "?"))
    except Exception:
        print("mutagen   : MISSING  ->  pip install mutagen")
        ok = False
    print("config    :", configmod.PATH)
    print("\n" + ("all good" if ok else "fix the items above"))
    return 0 if ok else 1


def cmd_gui(_args):
    try:
        from .gui import run
    except Exception as e:
        hint = "pkg install python-tkinter" if is_termux() else (
            "reinstall Python with tcl/tk" if is_windows() else "install python3-tk")
        print(f"GUI unavailable: {e}\n  {hint}\n  The CLI works the same: musictool get <url>")
        return 1
    try:
        run()
    except Exception as e:                                  # noqa: BLE001
        # tkinter is installed but Tk cannot open a window: no display (SSH,
        # CI, a headless server, Termux without an X server). That is the same
        # situation as "no tkinter" from the user's side, so it gets the same
        # one-line answer instead of a traceback.
        if type(e).__name__ != "TclError":
            raise
        print(f"GUI unavailable: {e}\n  no display to open a window on "
              f"(set DISPLAY, or use an X server on Termux)\n"
              f"  The CLI works the same: musictool get <url>")
        return 1
    return 0


def main(argv=None):
    cancel.reset()
    cancel.install_sigint()
    args = build_parser().parse_args(argv)
    handler = {"get": cmd_get, "check": cmd_check, "formats": cmd_formats,
               "lyrics": cmd_lyrics, "scan": cmd_scan,
               "duplicates": cmd_duplicates, "dupes": cmd_duplicates,
               "extract": cmd_extract, "embed": cmd_embed,
               "providers": cmd_providers, "config": cmd_config,
               "relang": cmd_relang, "live": cmd_live, "chat": cmd_chat,
               "doctor": cmd_doctor, "gui": cmd_gui}.get(args.cmd)
    if handler is None:
        print(f"unknown command: {args.cmd}")
        return 2
    return handler(args)
