"""Command construction and report parsing shared by the GUI and its tests.

Deliberately free of any tkinter import so it can be unit-tested headless.

The GUI does not re-implement option handling: it builds the exact argv the CLI
would receive and calls cli.main(). Because the widgets are generated from the
parser itself, a new CLI flag automatically appears in the GUI.
"""

import argparse
import json
import os
import re

from .tags import from_filename
from .util import common_root

MAX_RECENT = 12

# Options whose value is a path, so the GUI puts a Browse button beside them.
# "dir" opens a folder chooser, "file" a file chooser.
PATH_DESTS = {
    "output_dir": "dir",
    "lyrics_dir": "dir",
    "cookies": "file",
    "download_archive": "file",
}

# One-line help shown under the field, for options that are easy to misuse.
HINTS = {
    "cookies": "optional: an exported cookies.txt -- the browser picker above is easier",
    "cookies_from_browser": "reads your YouTube login from the browser; 'auto' picks the one you use",
    "output_dir": "remembered between runs",
    "playlist_items": "e.g. 287-447 to resume a playlist where you stopped",
    "keep_video": "Keep video (no audio extract)",
    "all_formats": "Show every raw row (do not merge variants)",
    "container": "Only this container",
    "codec": "Only this codec (avc1, vp9, av01, opus)",
    "show_audio": "Include the audio-only tracks",
    "sort": "Sort by",
    "auto": "Suggest a quality for me",
    "max_size": "takes the best quality that fits under this, e.g. 250M or 1G",
    "audio_lang": "when a video is dubbed, take this language -- 'any' takes the original",
    "quality": "a height (720, 1080) or best/smallest -- a raw -f belongs in the selector box",
    "format_selector": "one concrete id per stream, e.g. 248+140-0 (from 'List qualities + sizes')",
    "merge_format": "mkv accepts every codec; mp4 is more compatible",
    "no_playlist": "download just this video, not the playlist it sits in",
    "js_runtimes": "needed for YouTube's n-challenge when deno is not installed",
    "all_formats": "yt-dlp splits one stream into ~18 CDN variants; those are merged by default",
    "auto": "best, compatible (H.264/mp4), fit (best under the size limit), smallest, audio",
    "sort": "size_desc = biggest file first",
    "show_audio": "the audio-only tracks, which normally get merged into the video choice",
    "live_from_now": "a live stream is recorded from its start unless this is ticked",
    "exact": "needs one id per stream in the selector box (e.g. 299+140); "
             "refuses rather than take another stream",
    "part": "a finished video only: 1:00:00- = from 1 hour to the end, "
            "1:00:00-2:30:00, or -45:00; empty = all of it",
}

# ---------------------------------------------------------------------------
# Modes. One per kind of thing people download, because the kind decides
# almost every checkbox: a song wants the audio pulled out and synced lyrics,
# an album wants the same but numbered in a folder of its own, a Short wants
# the picture at its full vertical size, a two-hour talk is happiest as audio
# with its chapters, and a live stream wants recording from its first minute.
# Asking the user to reach those conclusions by ticking nine checkboxes is how
# the wrong combination gets downloaded 447 times.
#
# On top of them sits Smart: it reads the link (classify_links), takes the
# mode that kind of link wants -- or the one you have chosen for that kind
# most often -- and sets it up the way you have set that mode up most often
# (learn / smart_plan). Nothing is learned from a run you did not start.
# ---------------------------------------------------------------------------

SMART = "smart"

MODES = ("song", "album", "music_video", "video", "short", "podcast",
         "video_playlist", "channel", "live")

# Settings saved by older versions name the four modes of the old 2x2 grid.
MODE_ALIASES = {"music_single": "song", "music_playlist": "album",
                "video_single": "video", "video_playlist": "video_playlist"}

# BMP symbols only: Tk 8.6 on Windows cannot draw emoji outside it.
MODE_ICONS = {
    SMART: "\u2726", "song": "\u266a", "album": "\u266b",
    "music_video": "\u25b6", "video": "\u25b6", "short": "\u25ae",
    "podcast": "\u25c9", "video_playlist": "\u2630", "channel": "\u25eb",
    "live": "\u25cf",
}

MODE_LABELS = {
    SMART: "Smart (auto)",
    "song": "Song",
    "album": "Album / music playlist",
    "music_video": "Music video",
    "video": "Video",
    "short": "Short",
    "podcast": "Podcast / talk (audio)",
    "video_playlist": "Video playlist",
    "channel": "Whole channel",
    "live": "Live stream",
}

MODE_SUMMARY = {
    "song": ("audio extracted to m4a, tags + cover embedded, synced lyrics "
             "embedded as the file lands, this song only"),
    "album": ("audio extracted to m4a with lyrics, numbered 001, 002 ... "
              "inside a folder named after the album or playlist"),
    "music_video": ("the video kept in mp4 (plays everywhere) at up to "
                    "1080p, named Artist - Title, no lyrics"),
    "video": ("video kept in mkv at up to 1080p with chapters and "
              "subtitles, no lyrics, this video only"),
    "short": ("the vertical video at its best quality in mp4, no chapters "
              "or subtitles"),
    "podcast": ("audio only -- a fraction of the video's size -- with the "
                "chapters kept, no lyrics"),
    "video_playlist": ("videos kept in mkv at up to 1080p, numbered inside a "
                       "folder named after the playlist, no lyrics"),
    "channel": ("every video of the channel in mkv at up to 1080p, in a "
                "folder per channel, oldest first by date in the name"),
    "live": ("recorded from the stream's first minute, watchable while it "
             "records; a stop loses nothing (Live stream & chat below)"),
}

# What the big button says, so it always names what it is about to do.
MODE_BUTTON = {
    "song": "Download song + lyrics",
    "album": "Download album + lyrics",
    "music_video": "Download music video",
    "video": "Download video",
    "short": "Download Short",
    "podcast": "Download audio",
    "video_playlist": "Download playlist",
    "channel": "Download channel",
    "live": "Record live stream",
}

_MUSIC = {
    "keep_video": False, "audio_format": "m4a", "audio_quality": "4",
    "embed_metadata": True, "embed_thumbnail": True, "embed_chapters": False,
    "embed_subs": False, "just_download": False,
    "no_lyrics": False, "lyrics_only": False,
    "quality": "", "max_size": "", "format_selector": "", "merge_format": "",
    "exact": False,
}
_VIDEO = {
    "keep_video": True, "merge_format": "mkv", "embed_metadata": True,
    "embed_thumbnail": True, "embed_chapters": True, "embed_subs": True,
    "just_download": False, "no_lyrics": True, "lyrics_only": False,
    "quality": "1080", "max_size": "", "format_selector": "", "exact": False,
}
_NUMBERED = "%(playlist_title)s/%(playlist_index)03d - %(title)s.%(ext)s"

# Only the dests that a mode actually has an opinion about. Everything else is
# left alone, so a saved config (proxy, cookies, speed limit) survives a switch.
MODE_VALUES = {
    "song": dict(_MUSIC, no_playlist=True, playlist_items="",
                 template="%(uploader)s - %(title)s.%(ext)s"),
    "album": dict(_MUSIC, no_playlist=False, template=_NUMBERED),
    "music_video": dict(_VIDEO, merge_format="mp4", embed_chapters=False,
                        embed_subs=False, no_playlist=True, playlist_items="",
                        template="%(uploader)s - %(title)s.%(ext)s"),
    "video": dict(_VIDEO, no_playlist=True, playlist_items="",
                  template="%(title)s.%(ext)s"),
    # height<=1080 would turn a 1080x1920 Short into 608x1080: take the best
    "short": dict(_VIDEO, merge_format="mp4", quality="best",
                  embed_chapters=False, embed_subs=False, no_playlist=True,
                  playlist_items="", template="%(uploader)s - %(title)s.%(ext)s"),
    "podcast": dict(_MUSIC, no_lyrics=True, embed_chapters=True,
                    no_playlist=True, playlist_items="",
                    template="%(uploader)s - %(title)s.%(ext)s"),
    "video_playlist": dict(_VIDEO, no_playlist=False, template=_NUMBERED),
    "channel": dict(_VIDEO, no_playlist=False,
                    template="%(uploader)s/%(upload_date)s - %(title)s.%(ext)s"),
    # [id] in the name is how `live` and `chat` find the recording again
    "live": dict(_VIDEO, quality="", embed_subs=False, no_playlist=True,
                 playlist_items="", live_from_now=False,
                 template="%(title)s [%(id)s].%(ext)s"),
}

# Audio or picture, and one item or many: the two facts every mode is made of.
MODE_WHAT = {m: ("video" if MODE_VALUES[m]["keep_video"] else "audio")
             for m in MODES}
MODE_SCOPE = {m: ("single" if MODE_VALUES[m]["no_playlist"] else "playlist")
              for m in MODES}

# The neighbouring mode when a link says "many" (or "one") instead.
MODE_SIBLING = {
    ("song", "playlist"): "album", ("album", "single"): "song",
    ("video", "playlist"): "video_playlist",
    ("video_playlist", "single"): "video",
    ("music_video", "playlist"): "video_playlist",
    ("short", "playlist"): "video_playlist",
    ("channel", "single"): "video", ("live", "playlist"): "video_playlist",
}


def mode_title(mode):
    """'♪ Song' -- the icon and the name, as the mode buttons show them."""
    mode = canonical_mode(mode)
    return f"{MODE_ICONS.get(mode, '')} {MODE_LABELS.get(mode, mode)}".strip()


def canonical_mode(mode, default=SMART):
    """A mode name from any version of the settings, or `default`."""
    mode = MODE_ALIASES.get(mode, mode)
    return mode if mode in MODES or mode == SMART else default


def is_playlist_url(url):
    """Is this URL a playlist, as opposed to one item that sits inside one?

    `watch?v=XXX&list=PL...` is a single song with a playlist in the query
    string -- treating it as a playlist is how 90 files get downloaded when
    one was asked for. Only a bare /playlist?list= is really a playlist.
    """
    u = (url or "").split("#")[0]
    if "list=" not in u:
        return False
    if "v=" in u or "/watch" in u or "/shorts/" in u or "/live/" in u:
        return False
    return "/playlist" in u or u.split("?", 1)[0].rstrip("/").endswith("list")


def scope_of(url):
    """'playlist' or 'single', from the URL rather than from a guess."""
    return "playlist" if is_playlist_url(url) else "single"


def make_mode(what, scope):
    """The basic mode for an (audio|music|video, single|playlist) pair."""
    audio = what in ("audio", "music")
    if scope == "playlist":
        return "album" if audio else "video_playlist"
    return "song" if audio else "video"


def mode_for(urls, what="music"):
    """The basic mode these URLs want. One playlist in the list means a
    playlist run."""
    for u in urls or []:
        if is_playlist_url(u):
            return make_mode(what, "playlist")
    return make_mode(what, "single")


def mode_switch(mode, scope=None, what=None):
    """The neighbouring mode when one axis changes; the same mode when it
    has no neighbour that way (a Short has no 'Short playlist')."""
    mode = canonical_mode(mode, "song")
    if what and what != MODE_WHAT[mode] and not (
            what == "music" and MODE_WHAT[mode] == "audio"):
        return make_mode(what, scope or MODE_SCOPE[mode])
    if scope and scope != MODE_SCOPE[mode]:
        return MODE_SIBLING.get((mode, scope), mode)
    return mode


def mode_drift(values, mode, want=None):
    """Which of the mode's settings the user has since changed by hand.

    The window prints a one-line summary of what the mode will do; that line
    must stop describing the run the moment it is no longer true. `want` is
    what the mode was applied as (its values plus anything learned).
    """
    out = []
    want = want if want is not None else MODE_VALUES[canonical_mode(mode, "song")]
    for dest, val in want.items():
        have = values.get(dest)
        if have is None:
            continue
        if _same(have, val):
            continue
        if val in ("", None) and dest not in MODE_RESET:
            continue            # "no opinion" cannot be contradicted
        out.append(dest)
    return out


def _same(a, b):
    return str(a).strip().lower() == str(b).strip().lower()


# A blank in MODE_VALUES means "no opinion" -- keep whatever the user typed,
# so choosing Video does not throw away the 800M ceiling they set. These few
# are the exception: they silently override everything else, so a mode always
# clears them rather than inheriting a stale one.
MODE_RESET = ("format_selector", "playlist_items", "quality")


# ---------------------------------------------------------------------------
# What a link is. Read from the URL alone -- instant, offline, no guessing
# about the network -- and refined from yt-dlp's answer when one is fetched
# anyway (Identify, Load qualities, Is it live?).
# ---------------------------------------------------------------------------

# kind -> (what it is called, icon, the mode it wants, plural noun)
LINK_KINDS = {
    "ytm_song": ("YouTube Music song", "\u266a", "song", "songs"),
    "ytm_album": ("YouTube Music album", "\u266b", "album", "albums"),
    "ytm_playlist": ("YouTube Music playlist", "\u266b", "album", "playlists"),
    "ytm_artist": ("YouTube Music artist", "\u266b", "album", "artists"),
    "album": ("album (YouTube playlist of an album)", "\u266b", "album",
              "albums"),
    "video": ("YouTube video", "\u25b6", "video", "videos"),
    "in_playlist": ("YouTube video in a playlist -- just this one", "\u25b6",
                    "video", "videos"),
    "short": ("YouTube Short", "\u25ae", "short", "Shorts"),
    "live": ("YouTube live stream", "\u25cf", "live", "live streams"),
    "playlist": ("YouTube playlist", "\u2630", "video_playlist", "playlists"),
    "channel": ("YouTube channel", "\u25eb", "channel", "channels"),
    "music_video": ("music video", "\u266b", "music_video", "music videos"),
    "long": ("long video", "\u25c9", "video", "long videos"),
    "upcoming": ("scheduled live stream (not started yet)", "\u25cf", "live",
                 "scheduled streams"),
    "replay": ("finished live stream", "\u25cf", "live", "finished streams"),
    "sc_track": ("SoundCloud track", "\u266a", "song", "tracks"),
    "sc_set": ("SoundCloud playlist", "\u266b", "album", "playlists"),
    "bc_track": ("Bandcamp track", "\u266a", "song", "tracks"),
    "bc_album": ("Bandcamp album", "\u266b", "album", "albums"),
    "vertical": ("short vertical video", "\u25ae", "short", "short videos"),
    "twitch_live": ("Twitch channel (live)", "\u25cf", "live", "streams"),
    "web": ("video on another site", "\u25b6", "video", "videos"),
    "spotify": ("Spotify link", "!", "song", "Spotify links"),
    "text": ("not a link", "?", None, "lines that are not links"),
}

# what to tell the user about a kind that cannot be downloaded as it is
KIND_WARNING = {
    "spotify": ("Spotify streams are encrypted and cannot be downloaded. "
                "Search the song on music.youtube.com and paste that link -- "
                "the lyrics are found the same way."),
    "text": "that line is not a link -- it will be skipped",
    "upcoming": ("it has not started: Record live stream waits for it and "
                 "records from the first minute"),
}

_ID = r"[A-Za-z0-9_-]{6,}"
SITE_NAMES = {
    "vimeo.com": "Vimeo", "dailymotion.com": "Dailymotion",
    "facebook.com": "Facebook", "fb.watch": "Facebook", "x.com": "X",
    "twitter.com": "X", "instagram.com": "Instagram", "tiktok.com": "TikTok",
    "twitch.tv": "Twitch", "soundcloud.com": "SoundCloud",
    "bandcamp.com": "Bandcamp", "bilibili.com": "Bilibili",
    "reddit.com": "Reddit", "rumble.com": "Rumble", "odysee.com": "Odysee",
}


def _split_url(url):
    """(host without www./m., path, query dict) -- a link without https://
    (pasted from a phone, typed by hand) is read the same way."""
    from urllib.parse import parse_qs, urlsplit
    u = (url or "").strip().strip("<>\"'")
    if not re.match(r"^[a-z][a-z0-9+.-]*://", u, re.I):
        u = "https://" + u
    try:
        parts = urlsplit(u)
    except ValueError:
        return "", "", {}
    host = (parts.hostname or "").lower()
    for pre in ("www.", "m.", "mobile."):
        if host.startswith(pre):
            host = host[len(pre):]
    q = {k: v[0] for k, v in parse_qs(parts.query).items() if v}
    return host, parts.path or "/", q


def _site(host):
    for dom, name in SITE_NAMES.items():
        if host == dom or host.endswith("." + dom):
            return name
    return host


def _looks_like_url(text):
    t = (text or "").strip()
    if not t or " " in t:
        return False
    if re.match(r"^[a-z][a-z0-9+.-]*://", t, re.I):
        return True
    return bool(re.match(r"^[\w-]+(\.[\w-]+)+(/|$)", t))


def classify_link(url):
    """What this link is, from the URL alone.

    -> {"kind", "label", "icon", "mode", "url", "id", "site", "note"}
    """
    out = {"url": (url or "").strip(), "id": "", "site": "", "note": ""}
    kind = "text"
    if _looks_like_url(url):
        host, path, q = _split_url(url)
        low = path.lower()
        lst = q.get("list", "")
        out["site"] = _site(host)
        yt = host in ("youtube.com", "youtu.be", "youtube-nocookie.com") \
            or host.endswith(".youtube.com")
        if host == "music.youtube.com":
            out["site"] = "YouTube Music"
            if q.get("v"):
                kind, out["id"] = "ytm_song", q["v"]
                if lst:
                    out["note"] = "from a playlist -- just this song"
            elif low.startswith("/playlist") and lst:
                kind = "ytm_album" if lst.startswith("OLAK5uy_") else "ytm_playlist"
                out["id"] = lst
            elif low.startswith("/browse/mpreb"):
                kind = "ytm_album"
            elif low.startswith(("/channel/", "/@", "/browse/uc")):
                kind = "ytm_artist"
            else:
                kind = "ytm_song"
        elif yt:
            out["site"] = "YouTube"
            m = re.match(r"^/(shorts|live|embed|v|e)/(" + _ID + ")", path)
            if host == "youtu.be":
                out["id"] = path.strip("/").split("/")[0]
                kind = "in_playlist" if lst else "video"
            elif m:
                out["id"] = m.group(2)
                kind = {"shorts": "short", "live": "live"}.get(m.group(1),
                                                             "video")
            elif q.get("v"):
                out["id"] = q["v"]
                kind = "in_playlist" if lst else "video"
            elif low.startswith("/playlist") and lst:
                out["id"] = lst
                kind = "album" if lst.startswith("OLAK5uy_") else "playlist"
            elif re.match(r"^/(@[^/]+|channel/[^/]+|c/[^/]+|user/[^/]+)", path):
                kind = "channel"
                tail = low.rstrip("/").rsplit("/", 1)[-1]
                if tail == "live":
                    kind = "live"
                elif tail == "shorts":
                    out["note"] = "the channel's Shorts"
                elif tail == "streams":
                    out["note"] = "the channel's past streams"
            else:
                kind = "video"
        elif out["site"] == "SoundCloud":
            kind = "sc_set" if "/sets/" in low else "sc_track"
        elif out["site"] == "Bandcamp":
            kind = "bc_album" if low.startswith("/album/") else "bc_track"
        elif host.endswith("spotify.com"):
            kind, out["site"] = "spotify", "Spotify"
        elif out["site"] == "TikTok" or (
                out["site"] in ("Instagram", "Facebook")
                and re.match(r"^/(reels?|share/r)/", low)):
            kind = "vertical"
        elif out["site"] == "Twitch":
            seg = [s for s in low.split("/") if s]
            kind = "web" if (seg and seg[0] in ("videos", "clip")) or \
                "clip" in host or len(seg) != 1 else "twitch_live"
        else:
            kind = "web"
    label, icon, mode, _pl = LINK_KINDS[kind]
    if kind in ("web", "vertical") and out["site"]:
        label = f"{'short video' if kind == 'vertical' else 'video'} on " \
                f"{out['site']}"
    out.update(kind=kind, label=label, icon=icon, mode=mode)
    return out


_URL_RE = re.compile(r"(?:https?://|www\.|(?:music\.|m\.)?youtube\.com/|"
                     r"youtu\.be/)[^\s<>\"']+", re.I)


def extract_links(text):
    """The links in whatever was pasted, in order, each once.

    A line copied from a chat ("check this out https://youtu.be/x !!") gives
    its link; a line that is nothing but a bare youtube.com/... gives itself;
    a line with no link gives nothing (stray_lines counts those).
    """
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        found = [f.rstrip(").,;:!?]'\"") for f in _URL_RE.findall(line)]
        if not found and _looks_like_url(line):
            found = [line]
        for f in found:
            if f and f not in out:
                out.append(f)
    return out


def stray_lines(text):
    """How many non-empty lines hold no link at all."""
    return sum(1 for line in (text or "").splitlines()
               if line.strip() and not extract_links(line))


def classify_links(urls, refined=None):
    """What a whole paste is: every line read, and the one that leads.

    A playlist anywhere in the list makes it a playlist run (as before); with
    only single items the most common kind leads. `refined` maps a URL to
    what an online look-up found ({url: (kind, note)}), which beats the URL's
    own guess. -> {"kind", "label", "icon", "mode", "items", "links",
    "summary", "warnings"}
    """
    from collections import Counter
    items = [classify_link(u) for u in (urls or []) if str(u).strip()]
    for i in items:
        got = (refined or {}).get(i["url"])
        if not got:
            continue
        k, note = (got, "") if isinstance(got, str) else (got[0], got[1])
        if k in LINK_KINDS:
            label, icon, mode, _pl = LINK_KINDS[k]
            i.update(kind=k, label=label, icon=icon, mode=mode,
                     note=note or i["note"])
    links = [i for i in items if i["mode"]]
    warns = []
    for i in items:
        w = KIND_WARNING.get(i["kind"])
        if w and w not in warns:
            warns.append(w)
    if not links:
        return {"kind": "empty" if not items else "text",
                "label": "no link yet" if not items else "not a link",
                "icon": "" if not items else "?", "mode": None,
                "items": items, "links": 0, "warnings": warns,
                "summary": ("Paste a link from YouTube, YouTube Music, "
                            "SoundCloud, Bandcamp or another video site -- "
                            "its type is recognised as you paste"
                            if not items else "that is not a link")}
    lists = [i for i in links if MODE_SCOPE.get(i["mode"]) == "playlist"]
    if lists:
        lead = lists[0]
    else:
        count = Counter(i["kind"] for i in links)
        top = max(count.values())
        lead = next(i for i in links if count[i["kind"]] == top)
    if len(links) == 1:
        summary = lead["label"] + (f" ({lead['note']})" if lead["note"] else "")
    else:
        count = Counter(i["kind"] for i in links)
        bits = []
        for k, n in sorted(count.items(), key=lambda kv: -kv[1]):
            name = LINK_KINDS[k][0] if n == 1 else LINK_KINDS[k][3]
            bits.append(f"{n} {name}" if n > 1 else f"1 {name}")
        summary = f"{len(links)} links: " + ", ".join(bits)
    return {"kind": lead["kind"], "label": lead["label"], "icon": lead["icon"],
            "mode": lead["mode"], "items": items, "links": len(links),
            "summary": summary, "warnings": warns}


def refine_kind(kind, info):
    """A better answer once yt-dlp has described the video.

    Live now / scheduled / a finished stream, a music video (YouTube files it
    under Music), a Short (vertical, a minute or so) and a long talk are all
    invisible in a plain watch URL.
    """
    if not isinstance(info, dict) or info.get("error"):
        return kind
    if info.get("_type") == "playlist" or info.get("entries") is not None:
        return kind
    status = info.get("live_status") or ""
    if status in ("is_live", "post_live"):
        return "live"
    if status == "is_upcoming":
        return "upcoming"
    if status == "was_live":
        return "replay"
    if kind.startswith(("ytm_", "sc_", "bc_")):
        return kind
    dur = info.get("duration") or 0
    w, h = info.get("width") or 0, info.get("height") or 0
    if kind == "short" or (dur and dur <= 180 and h > w > 0):
        return "short" if kind in ("short", "video", "in_playlist") else "vertical"
    cats = [str(c).lower() for c in (info.get("categories") or [])]
    if "music" in cats and dur and dur < 15 * 60:
        return "music_video"
    if dur and dur >= 20 * 60:
        return "long"
    return kind


# Only single items are worth a look-up: a playlist or a channel says what it
# is in its link, and asking yt-dlp about one would read every entry.
IDENTIFIABLE = ("video", "in_playlist", "short", "live", "web", "vertical")


def describe_duration(seconds):
    s = int(seconds or 0)
    h, m = divmod(s // 60, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m {s % 60:02d}s"


# ---------------------------------------------------------------------------
# What the window has learned you like. Kept in the config file under
# "learned": for every kind of link, the modes you ran for it; for every
# mode, the values you ran it with. A choice is "learned" once it is the
# majority of your recent runs AND you made it at least twice -- one odd run
# never changes what Smart does next time.
# ---------------------------------------------------------------------------

LEARNABLE = ("output_dir", "audio_format", "audio_quality", "quality",
             "max_size", "merge_format", "embed_thumbnail", "embed_chapters",
             "embed_subs", "sub_langs", "audio_lang", "allow_unsynced",
             "template")
LEARN_KEEP = 10          # recent runs remembered per kind and per setting


def _norm(v):
    if isinstance(v, bool) or v is None:
        return bool(v)
    return str(v).strip()


def favourite(history, min_count=2):
    """The value chosen most often, if it is a real habit: a majority of the
    history and at least `min_count` times. Otherwise None."""
    from collections import Counter
    vals = [json.dumps(v, sort_keys=True) for v in (history or [])]
    if not vals:
        return None
    count = Counter(vals)
    top = max(count.values())
    # ties go to the most recent of the tied values
    best = next(v for v in reversed(vals) if count[v] == top)
    if top < min_count or top * 2 <= len(vals):
        return None
    return json.loads(best)


def learn(store, kind, mode, values):
    """Record one run you started: `mode` for this kind of link, with these
    values. Returns the store (a plain dict, saved as is)."""
    store = dict(store or {})
    mode = canonical_mode(mode, "")
    if mode not in MODES:
        return store
    kinds = dict(store.get("kinds") or {})
    if kind and kind not in ("empty", "text"):
        kinds[kind] = (list(kinds.get(kind) or []) + [mode])[-LEARN_KEEP:]
    modes = dict(store.get("modes") or {})
    per = dict(modes.get(mode) or {})
    for dest in LEARNABLE:
        if dest not in values:
            continue
        v = _norm(values[dest])
        if dest == "output_dir" and not v:
            continue
        per[dest] = (list(per.get(dest) or []) + [v])[-LEARN_KEEP:]
    modes[mode] = per
    store.update(kinds=kinds, modes=modes, runs=int(store.get("runs") or 0) + 1)
    return store


def favourite_mode(store, kind, default):
    fav = favourite(((store or {}).get("kinds") or {}).get(kind))
    return fav if fav in MODES else default


def learned_values(store, mode):
    """{dest: value} for the settings of `mode` you have made a habit of."""
    per = ((store or {}).get("modes") or {}).get(mode) or {}
    out = {}
    for dest, hist in per.items():
        if dest not in LEARNABLE:
            continue
        fav = favourite(hist)
        if fav is not None:
            out[dest] = fav
    return out


def smart_plan(links, store=None, learn_on=True, chosen=SMART, last=None):
    """Everything the mode card shows and applies.

    links:  classify_links(...) of what is in the box
    chosen: the mode button that is down (SMART, or a mode picked by hand)
    -> {"mode", "values", "learned": {dest: value}, "why"}
    """
    chosen = canonical_mode(chosen)
    kind = (links or {}).get("kind") or "empty"
    default = (links or {}).get("mode")
    why = ""
    if chosen != SMART:
        mode = chosen
    elif not default:
        mode = canonical_mode(last, "song") if last else "song"
        if mode == SMART:
            mode = "song"
        why = "waiting for a link"
    else:
        mode = favourite_mode(store, kind, default) if learn_on else default
        why = (f"you usually choose {MODE_LABELS[mode]} for a "
               f"{LINK_KINDS[kind][0]}" if mode != default
               else f"right for a {LINK_KINDS[kind][0]}")
    values = dict(MODE_VALUES[mode])
    learned = {}
    if learn_on:
        for dest, v in learned_values(store, mode).items():
            if dest in values and _same(values[dest], v):
                continue
            values[dest] = v
            learned[dest] = v
    return {"mode": mode, "values": values, "learned": learned, "why": why}


def describe_learned(learned):
    """'opus · up to 720p · folder Music' -- a short line, not a dump."""
    bits = []
    for dest, v in learned.items():
        if dest == "output_dir":
            name = os.path.basename(str(v).rstrip("/\\")) or str(v)
            bits.append(f"folder {name}")
        elif dest == "quality":
            bits.append(f"up to {v}p" if str(v).isdigit()
                        else (f"{v} quality" if v else "best quality"))
        elif dest in ("audio_format", "merge_format"):
            bits.append(str(v))
        elif dest == "audio_quality":
            bits.append(f"audio quality {v}")
        elif dest == "audio_lang":
            bits.append(f"spoken language {v}")
        elif dest == "template":
            bits.append("your file names")
        elif dest == "max_size":
            bits.append(f"under {v}" if v else "no size limit")
        elif isinstance(v, bool):
            bits.append(("" if v else "no ") + LABELS.get(dest, dest).lower())
        else:
            bits.append(f"{LABELS.get(dest, dest).lower()} {v}")
    return " \u00b7 ".join(bits)


def learned_report(store):
    """Plain lines for the Settings tab: what Smart will do, and why."""
    store = store or {}
    lines = []
    for kind, hist in sorted((store.get("kinds") or {}).items()):
        if kind not in LINK_KINDS:
            continue
        fav = favourite(hist)
        dflt = LINK_KINDS[kind][2]
        use = fav if fav in MODES else dflt
        n = sum(1 for m in hist if m == use)
        lines.append(f"{LINK_KINDS[kind][0]}  \u2192  {MODE_LABELS.get(use, use)}"
                     f"   ({n} of your last {len(hist)})"
                     + ("" if fav in MODES else "   [the default]"))
    for mode in MODES:
        got = learned_values(store, mode)
        if got:
            lines.append(f"{MODE_LABELS[mode]}:  {describe_learned(got)}")
    if not lines:
        lines.append("Nothing learned yet. Every download you start teaches "
                     "Smart a little; a choice counts once you have made it "
                     "at least twice.")
    return lines


# ---------------------------------------------------------------------------
# The window itself: never bigger than the screen it opens on.
# ---------------------------------------------------------------------------

WINDOW = (1080, 720)        # 90% of the old 800px height
MIN_WINDOW = (760, 470)


def fit_window(screen_w, screen_h, want=WINDOW, minimum=MIN_WINDOW,
               reserve_w=16, reserve_h=80):
    """Size and place the window inside the screen.

    reserve_h keeps the title bar and the taskbar clear (Windows' taskbar is
    40-48px, the title bar ~31px); reserve_w the borders. A screen smaller
    than the minimum shrinks the minimum too, so the window can always fit.
    -> {"w", "h", "x", "y", "min_w", "min_h", "max_w", "max_h", "geometry"}
    """
    sw, sh = int(screen_w or 0), int(screen_h or 0)
    if sw < 200 or sh < 200:             # no answer from the display: trust want
        sw, sh = want[0] + reserve_w, want[1] + reserve_h
    max_w, max_h = max(320, sw - reserve_w), max(240, sh - reserve_h)
    w, h = min(int(want[0]), max_w), min(int(want[1]), max_h)
    min_w, min_h = min(int(minimum[0]), max_w), min(int(minimum[1]), max_h)
    x = max(0, (sw - w) // 2)
    y = max(0, (sh - reserve_h - h) // 2)
    return {"w": w, "h": h, "x": x, "y": y, "min_w": min_w, "min_h": min_h,
            "max_w": max_w, "max_h": max_h, "geometry": f"{w}x{h}+{x}+{y}"}


# Human wording for the generated widgets. Anything missing here falls back to
# the dest with underscores turned into spaces, so a new flag is never nameless.
LABELS = {
    "part": "Only this part (start-end)",
    "allow_unsynced": "Accept plain (unsynced) lyrics if no synced found",
    "force": "Redo files that already have synced lyrics",
    "recursive": "Include subfolders",
    "dry_run": "Dry run (change nothing)",
    "no_embed": "Write .lrc sidecars only (do not embed)",
    "no_lyrics": "Download only, skip lyrics",
    "lyrics_only": "Lyrics only, skip download",
    "lyrics_dir": "Lyrics folder",
    "providers": "Provider order (comma separated)",
    "shift": "Shift timings (seconds)",
    "delay": "Delay between requests (s)",
    "limit": "Stop after N fixed files",
    "output_dir": "Output folder",
    "template": "Filename template",
    "audio_format": "Audio format",
    "audio_quality": "Audio quality (0 best)",
    "keep_video": "Keep video (no audio extract)",
    "embed_thumbnail": "Embed cover art",
    "embed_metadata": "Embed metadata",
    "embed_chapters": "Embed chapters",
    "just_download": "Just download: add nothing to the file afterwards",
    "ascii_names": "ASCII-only filenames",
    "cookies": "cookies.txt (optional)",
    "cookies_from_browser": "Cookies from browser",
    "proxy": "Proxy",
    "rate_limit": "Speed limit (e.g. 2M)",
    "concurrent": "Parallel fragments",
    "download_archive": "Download archive file",
    "playlist_items": "Playlist items (e.g. 1-50)",
    "no_playlist": "Single video only",
    "yes_playlist": "Whole playlist",
    "min_chars": "Ignore lyrics shorter than (chars)",
    "suspicious_only": "Only groups with different titles",
    "fix": "Re-fetch lyrics for the groups found",
    "json": "JSON output",
    "test": "Make a live probe request",
    "quality": "Video quality (height, e.g. 720)",
    "max_size": "Max file size (e.g. 250M)",
    "format_selector": "yt-dlp -f selector",
    "audio_lang": "Spoken language (en)",
    "merge_format": "Merge video+audio into",
    "exact": "Exactly these streams, copied (nothing converted)",
    "embed_subs": "Embed subtitles",
    "sub_langs": "Subtitle languages",
    "remote_components": "Remote components (e.g. ejs:github)",
    "js_runtimes": "JavaScript runtime (e.g. node)",
    "with_cookies": "Check using my cookies/login instead",
}

# Options that are used constantly and must be visible without scrolling. They
# are the same variables as the generated widgets -- this is a second view of
# them, not a second copy, so nothing can drift out of sync.
QUICK = {
    "lyrics": [
        ("allow_unsynced", "Accept plain (unsynced) lyrics if no synced found"),
        ("force", "Redo everything, even files that already have lyrics"),
        ("recursive", "Include subfolders"),
        ("dry_run", "Dry run (change nothing)"),
        ("no_embed", "Write .lrc only (do not embed)"),
    ],
    "get": [
        ("no_playlist", "This video only, not its playlist"),
        ("allow_unsynced", "Accept plain (unsynced) lyrics if no synced found"),
        ("no_lyrics", "Download only, skip lyrics"),
        ("lyrics_only", "Lyrics only, skip download"),
        ("keep_video", "Keep video (no audio extract)"),
        ("force", "Redo lyrics even if already synced"),
    ],
    "duplicates": [
        ("fix", "Re-fetch lyrics for the groups found"),
        ("suspicious_only", "Only groups with different titles"),
        ("allow_unsynced", "Accept plain (unsynced) lyrics if no synced found"),
        ("force", "Redo even if already synced"),
        ("recursive", "Include subfolders"),
    ],
    "scan": [
        ("recursive", "Include subfolders"),
        ("json", "JSON output"),
    ],
}


# --------------------------------------------------------------------------
# parser introspection
# --------------------------------------------------------------------------
def _subparsers(parser):
    for a in parser._actions:
        if isinstance(a, argparse._SubParsersAction):
            return a.choices
    return {}


def subcommands(parser=None):
    if parser is None:
        from .cli import build_parser
        parser = build_parser()
    return list(_subparsers(parser))


def sub_actions(sub, parser=None):
    """{dest: action} for one subcommand, in declaration order."""
    if parser is None:
        from .cli import build_parser
        parser = build_parser()
    sp = _subparsers(parser).get(sub)
    if sp is None:
        return {}
    return {a.dest: a for a in sp._actions if a.dest != argparse.SUPPRESS}


def option_dests(sub, parser=None):
    """Dests that have a flag (i.e. are settable options, not positionals)."""
    return [d for d, a in sub_actions(sub, parser).items() if a.option_strings]


def positional_dests(sub, parser=None):
    return [d for d, a in sub_actions(sub, parser).items() if not a.option_strings]


def is_toggle_true(a):
    return isinstance(a, argparse._StoreTrueAction)


def is_toggle_false(a):
    return isinstance(a, argparse._StoreFalseAction)


def long_flag(a):
    return next((o for o in a.option_strings if o.startswith("--")), a.option_strings[0])


def default_for(a, cfg=None):
    """Starting value for a widget: saved config first, then the CLI default."""
    if cfg and a.dest in cfg and cfg[a.dest] is not None:
        return cfg[a.dest]
    if is_toggle_false(a):
        return True if a.default is None else bool(a.default)
    if is_toggle_true(a):
        return bool(a.default)
    return "" if a.default is None else a.default


# --------------------------------------------------------------------------
# widget specs -- the GUI builds its widgets from these, so coverage of the CLI
# surface can be asserted headlessly without tkinter.
# --------------------------------------------------------------------------
SKIP_DESTS = {"help"}


def widget_specs(sub, cfg=None, parser=None):
    """One dict per settable option: dest, kind, label, default, group, choices."""
    actions = sub_actions(sub, parser)
    out = []
    for dest in option_dests(sub, parser):
        if dest in SKIP_DESTS:
            continue
        act = actions[dest]
        if is_toggle_true(act):
            kind = "check"
        elif is_toggle_false(act):
            kind = "check_off"
        elif act.choices:
            kind = "choice"
        else:
            kind = "text"
        out.append({
            "dest": dest,
            "kind": kind,
            "label": LABELS.get(dest, dest.replace("_", " ")),
            "flag": long_flag(act),
            "group": getattr(act.container, "title", None) or "options",
            "choices": list(act.choices) if act.choices else None,
            "default": default_for(act, cfg),
            "browse": PATH_DESTS.get(dest),
            "hint": HINTS.get(dest, ""),
        })
    return out


def quick_specs(sub, cfg=None, parser=None):
    """The always-visible subset of widget_specs for one subcommand.

    Only toggles qualify (a text field needs its own row), and every dest must
    really exist on the subcommand -- a stale entry raises KeyError here rather
    than silently producing a dead checkbox.
    """
    specs = {s["dest"]: s for s in widget_specs(sub, cfg, parser)}
    out = []
    for dest, label in QUICK.get(sub, []):
        s = dict(specs[dest])
        s["label"] = label
        if s["kind"] not in ("check", "check_off"):
            raise ValueError(f"{sub}: {dest} is not a toggle, cannot be a quick option")
        out.append(s)
    return out


# --------------------------------------------------------------------------
# argv construction
# --------------------------------------------------------------------------
def build_argv(sub, values, positionals=None, parser=None):
    """Build the argv list for `sub` from a dict keyed by argparse dest."""
    actions = sub_actions(sub, parser)
    argv = [sub] + [str(p) for p in (positionals or [])]
    for dest in option_dests(sub, parser):
        if dest == "help" or dest not in values:
            continue
        act = actions[dest]
        v = values[dest]
        flag = long_flag(act)
        if is_toggle_true(act):
            if v:
                argv.append(flag)
        elif is_toggle_false(act):
            if v is False:
                argv.append(flag)
        elif act.nargs in ("+", "*") or isinstance(v, (list, tuple)):
            items = v if isinstance(v, (list, tuple)) else str(v).split()
            if items:
                argv.append(flag)
                argv += [str(i) for i in items]
        elif v is None or (isinstance(v, str) and not v.strip()):
            continue
        else:
            argv += [flag, str(v)]
    return argv


def quote_cmd(argv):
    """Human-readable form of an argv, for display in the GUI."""
    out = ["musictool"]
    for a in argv:
        out.append(f'"{a}"' if (" " in a or a.startswith("-") and " " in a) else a)
    return " ".join(out)


# --------------------------------------------------------------------------
# path history
# --------------------------------------------------------------------------
def add_path(cfg, path, key="recent_paths"):
    """Remember a path, most-recent-first, deduplicated and capped."""
    if not path:
        return cfg
    p = os.path.abspath(os.path.expanduser(str(path)))
    hist = [x for x in (cfg.get(key) or []) if x != p]
    hist.insert(0, p)
    cfg[key] = hist[:MAX_RECENT]
    return cfg


def recent_paths(cfg, key="recent_paths"):
    return list(cfg.get(key) or [])


def add_url(cfg, url, key="recent_urls"):
    if not url:
        return cfg
    hist = [x for x in (cfg.get(key) or []) if x != url]
    hist.insert(0, url)
    cfg[key] = hist[:MAX_RECENT]
    return cfg


# --------------------------------------------------------------------------
# report parsing
# --------------------------------------------------------------------------
COUNT_RE = re.compile(r"^([a-z][a-z ]*?)\s*:\s+(\d+)\s*$")
SECTION_RE = re.compile(r"^(FIXED|UNRESOLVED|UNREADABLE)\s*\((\d+)\):\s*$")
ENTRY_RE = re.compile(r"^\s+\[(?P<prov>[^\]/]+)/(?P<kind>[^\]]+)\]\s+(?P<name>.+?)"
                      r"(?:\s{2,}(?P<note>.*))?$")
MISS_RE = re.compile(r"^\s+(?P<name>.+?)\s+--\s+(?P<why>.*)$")


def parse_report(text):
    """Parse lyrics-report.txt into counts + categorised entries."""
    out = {"counts": {}, "fixed": [], "unresolved": [], "unreadable": [],
           "by_provider": {}, "by_kind": {}}
    section = None
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        m = SECTION_RE.match(line)
        if m:
            section = m.group(1).lower()
            continue
        m = COUNT_RE.match(line)
        if m and section is None:
            out["counts"][m.group(1).strip()] = int(m.group(2))
            continue
        if not line.strip():
            continue
        if section == "fixed":
            e = ENTRY_RE.match(line)
            if e:
                d = e.groupdict()
                out["fixed"].append({"provider": d["prov"], "kind": d["kind"],
                                     "name": d["name"].strip(),
                                     "note": (d["note"] or "").strip()})
                out["by_provider"][d["prov"]] = out["by_provider"].get(d["prov"], 0) + 1
                out["by_kind"][d["kind"]] = out["by_kind"].get(d["kind"], 0) + 1
            else:
                out["fixed"].append({"provider": "?", "kind": "?",
                                     "name": line.strip(), "note": ""})
        elif section in ("unresolved", "unreadable"):
            m = MISS_RE.match(line)
            if m:
                out[section].append({"name": m.group("name").strip(),
                                     "why": m.group("why").strip()})
            else:
                out[section].append({"name": line.strip(), "why": ""})
    return out


def load_report(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return parse_report(fh.read())
    except Exception as e:
        return {"counts": {}, "fixed": [], "unresolved": [], "unreadable": [],
                "by_provider": {}, "by_kind": {}, "error": str(e)}


def headline(rep):
    """One-line summary suitable for a status bar."""
    c = rep.get("counts", {})
    total = c.get("scanned", 0)
    fixed = c.get("fixed", 0)
    unres = c.get("unresolved", 0)
    err = c.get("unreadable", 0)
    ok = c.get("already ok", 0)
    pct = f" ({fixed * 100 // total}%)" if total else ""
    return (f"{fixed} fixed{pct} · {ok} already ok · {unres} unresolved · "
            f"{err} unreadable · of {total} scanned")


def report_to_json(rep):
    return json.dumps(rep, ensure_ascii=False, indent=1)


# --------------------------------------------------------------------------
# file inventory + selection
#
# The GUI lets the user tick individual files instead of pointing the tool at a
# whole folder. The rules live here, not in tkinter, so they can be tested.
# --------------------------------------------------------------------------
NEEDS = ("partial", "plain", "empty")


def needs_fix(info):
    """True when this file is not already carrying usable synced lyrics."""
    if info.get("error"):
        return True
    return info.get("state") in NEEDS


def row_state(info):
    if info.get("error"):
        return "unreadable"
    return info.get("state") or "empty"


def inventory(paths, recursive=False, lyrics_dir=None, min_chars=40):
    """Scan `paths` offline -> {'root','rows','counts'}.

    Each row: id, path, name, state, needs, title, artist, duration, note, sel.
    `sel` starts True so "run" means "every file" until the user narrows it.
    """
    from . import lyrics as lyricsmod

    root = common_root(paths)
    files = lyricsmod.gather(paths, (lyrics_dir,) if lyrics_dir else (),
                             recursive=recursive)
    # Flag files whose lyrics are shared with another file: that is the
    # wrong-lyrics damage, and it is the thing worth re-doing.
    shared = {}
    for f in files:
        fp = lyricsmod.fingerprint(f.get("lyrics"), min_chars)
        if fp:
            shared.setdefault(fp, []).append(f["path"])
    rows = []
    for i, f in enumerate(files):
        fa, ft = from_filename(f["path"])
        fp = lyricsmod.fingerprint(f.get("lyrics"), min_chars)
        note = f.get("error") or ""
        if fp and len(shared.get(fp, ())) > 1:
            note = ("shares lyrics with %d other file(s)" % (len(shared[fp]) - 1)
                    + ((" -- " + note) if note else ""))
        rows.append({
            "id": i,
            "path": f["path"],
            "name": os.path.basename(f["path"]),
            "state": row_state(f),
            "needs": needs_fix(f),
            "title": f.get("title") or ft,
            "artist": f.get("artist") or fa,
            "duration": round(float(f.get("duration") or 0.0), 1),
            "note": note,
            "sel": True,
        })
    counts = {}
    for r in rows:
        counts[r["state"]] = counts.get(r["state"], 0) + 1
    counts["total"] = len(rows)
    counts["needing"] = sum(1 for r in rows if r["needs"])
    return {"root": root, "rows": rows, "counts": counts}


def pick_paths(rows, root):
    """What to hand the CLI: the folder when everything is ticked, else files.

    Passing the folder keeps argv short and lets the CLI use its own scan; a
    subset is passed as explicit file paths so only those are touched.
    -> (positionals, note)
    """
    rows = list(rows or [])
    if not rows:
        return [root], "whole folder"
    sel = [r for r in rows if r.get("sel")]
    if len(sel) == len(rows):
        return [root], f"all {len(rows)} files (whole folder)"
    if not sel:
        return [], "nothing selected"
    return [r["path"] for r in sel], f"{len(sel)} of {len(rows)} files"


def select(rows, mode):
    """Apply a bulk selection mode in place; returns the number now selected."""
    for r in rows:
        if mode == "all":
            r["sel"] = True
        elif mode == "none":
            r["sel"] = False
        elif mode == "invert":
            r["sel"] = not r.get("sel")
        elif mode == "needs":
            r["sel"] = bool(r.get("needs"))
        elif mode == "ok":
            r["sel"] = not r.get("needs")
        elif mode == "shared":
            r["sel"] = "shares lyrics" in (r.get("note") or "")
        else:
            raise ValueError(f"unknown selection mode: {mode}")
    return sum(1 for r in rows if r.get("sel"))


SELECTION_MODES = ("all", "none", "invert", "needs", "ok", "shared")


# --------------------------------------------------------------------------
# window layout
# --------------------------------------------------------------------------
def pane_split(total, log_frac=0.25, min_top=170, min_log=140):
    """Y of the sash between the tabs and the log, for a window `total` tall.

    The log sits BELOW the sash, so its height is total - y: to give the log a
    quarter of the window the sash has to go at three quarters. The tabs keep
    at least min_top and the log at least min_log -- 140px, enough for its
    progress bars and still a few lines of text on a small screen.

    A tk.PanedWindow that has not been laid out yet reports a height of 1,
    and a sash placed from that lands at min_top -- which is how the log used
    to open covering most of the window. The GUI therefore places it again on
    every resize until you move it yourself.
    """
    total = max(int(total), min_top + min_log)
    y = int(total * (1.0 - log_frac))
    y = max(y, min_top)
    return min(y, total - min_log)


def split_heights(window_h, chrome=58, **kw):
    """(tabs, log) heights to request for a window `window_h` tall, so the
    very first frame is already drawn at the right split. `chrome` is the
    status bar and padding around the split."""
    total = max(int(window_h) - int(chrome), 1)
    y = pane_split(total, **kw)
    return y, max(total, 260) - y


def move_sash(current_y, total, delta_y, min_top=170, min_log=90):
    """New sash Y after dragging it by `delta_y` px, clamped to the window.

    Positive delta_y moves the bar down, which makes the LOG shorter, so the
    GUI negates its "taller log" button. The bar cannot go above min_top (the
    tabs would vanish) nor below total - min_log (the log would).
    """
    y = int(current_y) + int(delta_y)
    y = max(y, min_top)
    return min(y, max(min_top, int(total) - min_log))
