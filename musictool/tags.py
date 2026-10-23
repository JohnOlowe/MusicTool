"""Audio tag reading/writing via mutagen. Works for m4a/mp3/flac/ogg/opus."""

import os
import re

from .util import AUDIO_EXT, classify

TITLE_KEYS = ("\xa9nam", "TIT2", "title")
ARTIST_KEYS = ("\xa9ART", "TPE1", "artist")
ALBUM_KEYS = ("\xa9alb", "TALB", "album")
ALBUM_ARTIST_KEYS = ("\xa9ART", "TPE2", "albumartist")
LYRIC_KEYS = ("\xa9lyr", "LYRICS", "lyrics", "USLT::eng")


def open_audio(path):
    from mutagen import File as MFile
    return MFile(path)


def _first(tags, keys):
    for k in keys:
        v = tags.get(k)
        if v:
            if isinstance(v, list):
                v = v[0]
            # ID3 text frames are objects with .text
            text = getattr(v, "text", v)
            if isinstance(text, list) and text:
                text = text[0]
            s = str(text).strip()
            if s:
                return s
    return ""


def read(path):
    """-> dict(path, title, artist, album, duration, lyrics, state, kind, error)"""
    info = {"path": path, "title": "", "artist": "", "album": "",
            "duration": 0.0, "lyrics": "", "state": "empty",
            "kind": "?", "error": None}
    try:
        f = open_audio(path)
        if f is None:
            info["error"] = "mutagen could not identify this file"
            return info
        info["kind"] = type(f).__name__
        if f.info is not None:
            info["duration"] = float(getattr(f.info, "length", 0.0) or 0.0)
        t = f.tags or {}
        info["title"] = _first(t, TITLE_KEYS)
        info["artist"] = _first(t, ARTIST_KEYS)
        info["album"] = _first(t, ALBUM_KEYS)
        info["lyrics"] = _first(t, LYRIC_KEYS)
    except Exception as e:
        info["error"] = str(e)
        return info
    info["state"] = classify(info["lyrics"])
    return info


def write_lyrics(path, lyrics, backup_dir=None):
    """Embed lyrics. Returns 'backup path or None'. Raises on failure."""
    from mutagen.mp4 import MP4
    f = open_audio(path)
    if f is None:
        raise ValueError("unsupported file")
    if f.tags is None:
        f.add_tags()
    old = _first(f.tags, LYRIC_KEYS)
    if backup_dir and old.strip():
        os.makedirs(backup_dir, exist_ok=True)
        dst = os.path.join(backup_dir,
                           os.path.splitext(os.path.basename(path))[0] + ".old.lrc")
        with open(dst, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(old)
    else:
        dst = None
    if isinstance(f, MP4):
        f.tags["\xa9lyr"] = [lyrics]
    elif hasattr(f.tags, "add"):
        # ID3 / Vorbis comment
        try:
            f.tags.add("LYRICS=" + lyrics)
        except Exception:
            pass
    else:
        f.tags["LYRICS"] = [lyrics]
    f.save()
    return dst


def sidecar_candidates(path, extra_dirs=()):
    """Every place a .lrc for this file might already live."""
    base = os.path.splitext(os.path.basename(path))[0]
    dirs = [os.path.dirname(path)] + [d for d in extra_dirs if d]
    out = []
    for d in dirs:
        p = os.path.join(d, base + ".lrc")
        if p not in out:
            out.append(p)
    return out


def read_sidecar(path, extra_dirs=()):
    for p in sidecar_candidates(path, extra_dirs):
        if os.path.exists(p):
            try:
                return p, open(p, encoding="utf-8", errors="replace").read()
            except Exception:
                pass
    return None, ""


def write_sidecar(dirpath, basename, lyrics):
    os.makedirs(dirpath, exist_ok=True)
    p = os.path.join(dirpath, basename + ".lrc")
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(lyrics if lyrics.endswith("\n") else lyrics + "\n")
    return p


def from_filename(path):
    """'Artist - Title (feat. X).m4a' -> (artist, title)"""
    from .util import fix_chars
    base = fix_chars(os.path.splitext(os.path.basename(path))[0])
    if " - " in base:
        a, t = base.split(" - ", 1)
        return a.strip(), t.strip()
    return "", base


def list_audio(directory, recursive=False):
    out = []
    if os.path.isfile(directory):
        return [directory]
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "_backup"]
        for fn in sorted(files):
            if fn.lower().endswith(AUDIO_EXT) and not fn.startswith("."):
                out.append(os.path.join(root, fn))
        if not recursive:
            break
    return out
