"""Command line interface."""

import argparse
import os
import subprocess
import sys

from . import cancel
from . import config as configmod
from . import download, formats, lyrics, providers, tags
from .util import (AUDIO_EXT, classify, common_root, detect_browser,
                   existing_paths, ffmpeg_hint, have_ffmpeg, is_termux,
                   is_windows, js_runtime, pot_server_reachable, ytdlp_cmd,
                   USER_AGENT)

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
                    help="include the audio-only tracks in the table")
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
    sub.add_parser("doctor", help="check that yt-dlp, ffmpeg and tkinter are available")
    sub.add_parser("gui", help="launch the graphical interface")
    return ap


def _opts(args):
    """Turn parsed CLI args into the config dict."""
    over = {}
    for k in ("output_dir", "template", "audio_format", "audio_quality",
              "keep_video", "embed_metadata", "embed_thumbnail", "embed_chapters",
              "ascii_names", "cookies", "cookies_from_browser",
              "quality", "max_size", "format_selector", "merge_format",
              "audio_lang",
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
    if getattr(args, "no_lyrics", False):
        over["no_lyrics"] = True
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

    ok = blocked = 0
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
            ok += 1
            print(f"    reachable  \"{info.get('title') or info.get('id')}\""
                  f"  {_duration(info.get('duration'))}")
        print("    -> no login needed for this one")

    print("\n" + "=" * 60)
    print(f"checked {ok + blocked} URL(s): {ok} reachable, {blocked} blocked")
    if blocked:
        print("cookies are needed for the blocked ones -- see the advice above")
    else:
        print("you are not blocked: download with no cookies at all")
    return 0 if not blocked else 1


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
        dur = _duration(info.get("duration"))
        print(formats.table(
            shown, f"    {info.get('title') or info.get('id')}"
                   + (f"  ({dur})" if dur else "")))
        if len(shown) < len(raw):
            print(f"    ({len(raw)} rows from yt-dlp collapsed to {len(shown)}: "
                  f"variant splits and same-quality doubles are merged -- "
                  f"--all-formats shows every row)")
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
        if sel:
            print(f"\n    to take this one:")
            print(f"      musictool get \"{url}\" --keep-video -f {sel}"
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
                print(f"      musictool get \"{url}\" --keep-video -f {asel}"
                      f"{' --merge-format ' + o['merge_format'] if o.get('merge_format') else ''}")
            else:
                print(f"\n    auto ({auto}): nothing to pick -- {why}")
    return rc


def _resolve_quality(url, o, log=print):
    """Turn --quality / --max-size into a concrete -f for this URL.

    Costs one metadata request, and only when asked: an explicit -f always wins.
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
            info = download.probe(url, o)
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
    info = download.probe(url, o)
    if not isinstance(info, dict) or info.get("error"):
        log(f"  !! could not read the qualities: "
            f"{str((info or {}).get('error'))[:160]}")
        log("  !! so --quality/--max-size cannot be honoured here; yt-dlp "
            "will pick its own default")
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


def cmd_get(args):
    o = _opts(args)
    os.makedirs(o["output_dir"], exist_ok=True)
    if getattr(args, "output_dir", None):
        # Remember an explicit -o so the next run (CLI or GUI) starts here.
        configmod.remember(output_dir=o["output_dir"])
    o["lyrics_dir"] = configmod.lyrics_dir_for(o, o["output_dir"])
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
        if o.get("no_lyrics") or cancel.requested():
            return
        infos = lyrics.gather([path], (o["lyrics_dir"],))
        if not infos:
            return
        print(f"\n==> lyrics for {os.path.basename(path)}")
        bucket, entry = lyrics.process_file(infos[0], o, http, provs, log=print)
        res[bucket].append(entry)
        res["total"] += 1

    targets = []
    stopped = False
    for url in args.urls:
        if o.get("lyrics_only"):
            continue
        if cancel.requested():
            stopped = True
            break
        print(f"\n==> {url}")
        # Quality is resolved PER VIDEO. Format ids are not global: 140-4 is
        # the English track on one video and the Spanish original on the next,
        # and 614 may not exist at all. Resolving once for the whole batch and
        # reusing the selector is how a dubbed video slips through in the
        # wrong language while everything reports success.
        per = dict(o)
        _resolve_quality(url, per)
        files, err = download.download(
            url, per, on_file=lambda pth, vid, u=url: on_file(pth, vid, u))
        targets += [p for p, _ in files]
        if err == cancel.CANCELLED:
            stopped = True
            break
        if err:
            print(f"!! {err}")
            continue
        print(f"    {len(files)} file(s)")
    if stopped:
        if targets:
            print(f"\n==> {cancel.CANCELLED}: keeping the {len(targets)} file(s) "
                  f"that finished (they already have their lyrics)")
        else:
            print(f"\n==> {cancel.CANCELLED}")
        print("    a half-finished file keeps its .part, so the next run "
              "resumes it instead of starting over")

    if o.get("no_lyrics"):
        print("\n==> skipping lyrics (--no-lyrics)")
        return 0
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
        if p.needs_video:
            print(f"{p.name:<10} skipped -- needs a videoId (populated by `get`)")
            continue
        try:
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


def cmd_doctor(_args):
    ok = True
    print("platform  :", "Windows" if is_windows() else
          ("Termux/Android" if is_termux() else sys.platform))
    print("python    :", sys.version.split()[0])
    cmd = list(ytdlp_cmd())
    try:
        r = subprocess.run(cmd + ["--version"], capture_output=True, text=True,
                           timeout=30)
        ver = (r.stdout or "").strip()
        if r.returncode == 0 and ver:
            print(f"yt-dlp    : {ver}  via {' '.join(cmd)}")
        else:
            print("yt-dlp    : MISSING  ->  pip install yt-dlp")
            ok = False
    except Exception as e:
        print(f"yt-dlp    : MISSING ({e})  ->  pip install yt-dlp")
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
    run()
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
               "relang": cmd_relang,
               "doctor": cmd_doctor, "gui": cmd_gui}.get(args.cmd)
    if handler is None:
        print(f"unknown command: {args.cmd}")
        return 2
    return handler(args)
