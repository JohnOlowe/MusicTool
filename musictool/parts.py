"""Parts of a video (`get --range`) that stay playable when they stop early.

What went wrong, and why
------------------------
A part is fetched by ffmpeg (yt-dlp hands every `--download-sections`
download to it), and ffmpeg wrote an ordinary MP4. An ordinary MP4 keeps its
index -- the `moov` box that says where every frame is -- at the very end, and
writes it only when it finishes. Out of space, a dropped connection it gave up
on, Force stop, Android killing Termux: the file then has gigabytes of video
and no index, and nothing plays it ("moov atom not found"). Measured: a part
killed after 61 MB would not open at all.

What this does
--------------
* ffmpeg writes a *fragmented* MP4 (`-movflags +frag_keyframe+empty_moov+
  default_base_moof`, see download.build_args): an index up front, then the
  video in pieces of one keyframe each (2-5 s), every piece with its own
  little index. Cut it anywhere and everything up to the last whole piece
  plays: the same kill left 1:55 of 1080p60 with sound. After a normal finish
  yt-dlp's metadata step copies it into an ordinary MP4 anyway.
* A part starts ON a keyframe (`keyframe_before`). Copying (never
  re-encoding) can only start a picture at a keyframe, so ffmpeg used to start
  the picture up to a few seconds early and an ordinary MP4 hid those seconds
  with an edit list. A fragmented MP4 cannot hide them: the part opened with
  5 s of picture and a stretched first sound frame. Starting on the keyframe
  leaves nothing to hide (the sound starts 0.17 s in) and the name says where
  the part really starts.
* A part that stopped early is made into a normal file (`salvage`): the half-
  written last piece is cut off and the file is named after what it really
  holds, 'NAME [id] (2h24m55s-3h41m07s).mp4'.
* Running the same command again carries on (`coverage` / `continue_point`)
  instead of starting the part over -- ffmpeg is run with -y, so it used to
  overwrite what was there, and a network retry did the same mid-run.

Nothing here imports tkinter.
"""

import os
import re
import shutil
import struct
import sys
import time

from . import cancel

# 'NAME [id] (1h00m00s-2h30m00s).mp4' / 'NAME [id] (from 1h00m00s).mp4'
_T = r"(\d+)h(\d\d)m(\d\d)s"
LABEL_RE = re.compile(rf" \((?:from {_T}|{_T}-{_T})\)(\.[A-Za-z0-9]+)(\.part)?$")
MEDIA_EXT = (".mp4", ".m4a", ".mkv", ".webm", ".mov", ".mka", ".opus", ".mp3")
# ffmpeg: start just after the keyframe -- with B-frames it seeks 3/23 s
# *before* the time it is given (fftools/ffmpeg_demux.c), which landed on the
# keyframe before the one asked for
KEY_NUDGE = 0.15
FAT_LIMIT = 4 * 1024 ** 3 - 1           # FAT32: no file of 4 GiB or more


def _secs(h, m, s):
    return int(h) * 3600 + int(m) * 60 + int(s)


def parse_label(name):
    """-> (start, end or None, ext) from a part's file name, or None."""
    m = LABEL_RE.search(name)
    if not m:
        return None
    g = m.groups()
    if g[0] is not None:
        return _secs(*g[0:3]), None, g[9]
    return _secs(*g[3:6]), _secs(*g[6:9]), g[9]


def name_time(sec):
    s = int(round(sec or 0))
    return f"{s // 3600}h{s // 60 % 60:02d}m{s % 60:02d}s"


def relabel(path, start, end):
    """The same name with the part's real span: '... (START-END).ext'
    (without .part). Never an existing file's name."""
    folder, name = os.path.split(path)
    if name.endswith(".part"):
        name = name[:-5]
    m = LABEL_RE.search(name)
    ext = m.group(10) if m else os.path.splitext(name)[1]
    stem = name[:m.start()] if m else os.path.splitext(name)[0]
    base = f"{stem} ({name_time(start)}-{name_time(end)})"
    out = os.path.join(folder, base + ext)
    n = 2
    while os.path.exists(out) and os.path.abspath(out) != os.path.abspath(path):
        out = os.path.join(folder, f"{base} {n}{ext}")
        n += 1
    return out


# --------------------------------------------------------------- the boxes

def _boxes(f, size):
    """Top-level MP4 boxes: [(type, start, end)], the last one possibly
    running past the end of the file."""
    out, pos = [], 0
    while pos + 8 <= size:
        f.seek(pos)
        head = f.read(16)
        n, kind = struct.unpack(">I4s", head[:8])
        if n == 1 and len(head) >= 16:
            n = struct.unpack(">Q", head[8:16])[0]
        elif n == 0:
            n = size - pos                      # "to the end of the file"
        if n < 8:
            break
        out.append((kind.decode("latin-1"), pos, pos + n))
        pos += n
    return out


def fragmented(path):
    """Is this a fragmented MP4 (moof pieces)? False for anything else."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            kinds = [k for k, _a, _b in _boxes(f, size)]
    except (OSError, struct.error):
        return False
    return "moov" in kinds and "moof" in kinds


def trim(path):
    """Cut a fragmented MP4 back to its last whole piece (moof + its mdat).
    -> bytes removed, or None when it is not a fragmented MP4 (left alone)."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            boxes = _boxes(f, size)
    except (OSError, struct.error):
        return None
    kinds = [k for k, _a, _b in boxes]
    if "moov" not in kinds or "moof" not in kinds:
        return None
    good, i = 0, 0
    while i < len(boxes):
        kind, a, b = boxes[i]
        if b > size:
            break
        if kind == "moof":
            nxt = boxes[i + 1] if i + 1 < len(boxes) else None
            if not nxt or nxt[0] != "mdat" or nxt[2] > size:
                break                          # its pictures never landed
            good = nxt[2]
            i += 2
            continue
        good = b
        i += 1
    if good < size:
        try:
            with open(path, "r+b") as f:
                f.truncate(good)
        except OSError:
            return None
    return size - good


# ----------------------------------------------------- keyframe and timing

def _video_format(info, selector):
    """The video format a selector like '299+140' names, with an https URL
    ffmpeg can seek in. None for HLS, audio only, or a selector that is an
    expression rather than ids."""
    if not isinstance(info, dict) or not selector:
        return None
    ids = [s.strip() for s in str(selector).split("+")]
    if any(not re.fullmatch(r"[\w-]+", s) for s in ids):
        return None
    by_id = {str(f.get("format_id")): f for f in info.get("formats") or []}
    for fid in ids:
        f = by_id.get(fid)
        if (f and (f.get("vcodec") or "none") != "none"
                and str(f.get("url", "")).startswith("https://")
                and f.get("protocol") in ("https", "http")):
            return f
    return None


def keyframe_before(info, selector, t, exe, timeout=60):
    """The time of the keyframe a copy started at `t` would begin with, read
    from the video itself (one short request: ffmpeg seeks and reads one
    packet). None when it cannot be found -- the part then starts as before."""
    if not t or t <= 0 or not exe:
        return None
    f = _video_format(info, selector)
    if not f:
        return None
    # YouTube's links work only from a few seconds after they were given
    # out (`available_at`; yt-dlp sleeps for it too): before that, 403
    wait = (f.get("available_at") or 0) - time.time()
    if 0 < wait < 60 and not cancel.sleep(wait + 0.5):
        return None
    hdr = f.get("http_headers") or {}
    cmd = [exe, "-v", "error", "-nostdin"]
    if hdr:
        cmd += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in hdr.items())]
    cmd += ["-ss", f"{t:.3f}", "-i", f["url"], "-map", "0:v:0", "-c", "copy",
            "-copyts", "-frames:v", "1", "-f", "framemd5", "-"]
    try:
        r = cancel.run(cmd, capture_output=True, text=True, errors="replace",
                       timeout=timeout)
    except Exception:                                        # noqa: BLE001
        return None
    tb = re.search(r"^#tb 0: (\d+)/(\d+)", r.stdout or "", re.M)
    pk = re.search(r"^0,\s*-?\d+,\s*(-?\d+),", r.stdout or "", re.M)
    if not tb or not pk:
        return None
    k = int(pk.group(1)) * int(tb.group(1)) / int(tb.group(2))
    return k if t - 30 <= k <= t + 0.5 else None


# ---------------------------------------------------- what is already here

def seconds(path):
    from .live import media_seconds
    return media_seconds(path)


def leftovers(folder, vid):
    """Unfinished parts of video `vid` (ffmpeg's NAME [id] (...).mp4.part)."""
    out = []
    try:
        names = os.listdir(folder or ".")
    except OSError:
        return out
    for n in names:
        if f"[{vid}]" in n and n.endswith(".part") and parse_label(n) \
                and not re.search(r"\.f[\w-]+\.\w+\.part$", n):
            out.append(os.path.join(folder or ".", n))
    return sorted(out)


def drop_temps(folder, vid):
    """Remove the half-made copies yt-dlp leaves when adding the title and
    cover runs out of room (NAME [id] (...).temp.mp4). -> paths removed."""
    gone = []
    try:
        names = os.listdir(folder or ".")
    except OSError:
        return gone
    for n in names:
        if f"[{vid}]" in n and re.search(r"\)\.temp\.[A-Za-z0-9]+$", n):
            p = os.path.join(folder or ".", n)
            try:
                os.remove(p)
                gone.append(p)
            except OSError:
                pass
    return gone


def drop_cover(path):
    """Remove the cover image yt-dlp saved next to PATH to embed later."""
    stem = path[:-5] if path.endswith(".part") else path
    stem = os.path.splitext(stem)[0]
    for ext in (".webp", ".jpg", ".png", ".image"):
        try:
            os.remove(stem + ext)
        except OSError:
            pass


def finished(folder, vid, start):
    """The finished file of video `vid` whose name starts at `start`."""
    for s, _e, p in coverage(folder, vid):
        if abs(s - start) < 0.5:
            return p
    return None


def _has_box(path, kind):
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            return any(k == kind for k, _a, _b in _boxes(f, size))
    except (OSError, struct.error):
        return False


def coverage(folder, vid):
    """Finished parts of video `vid` in `folder`: [(start, end, path)], with
    the end measured from the file, not trusted from its name."""
    out = []
    try:
        names = os.listdir(folder or ".")
    except OSError:
        return out
    for n in sorted(names):
        lab = parse_label(n)
        if not lab or f"[{vid}]" not in n or n.endswith(".part") \
                or lab[2].lower() not in MEDIA_EXT:
            continue
        p = os.path.join(folder or ".", n)
        secs = seconds(p)
        if secs:
            out.append((float(lab[0]), lab[0] + secs, p))
    return out


def continue_point(have, start, end=None, slack=1.0):
    """How far the parts in `have` cover the video from `start` without a
    gap. -> (position, [paths that cover it])."""
    pos, used = float(start), []
    while True:
        nxt = [(e, p) for s, e, p in have if s <= pos + slack and e > pos + slack]
        if not nxt:
            return pos, used
        e, p = max(nxt)
        pos = e
        used.append(p)
        if end is not None and pos >= end:
            return pos, used


def salvage(path, say=print):
    """Make an unfinished part a normal, playable file named after what it
    holds. -> (new path, start, end) or None when nothing in it plays."""
    lab = parse_label(os.path.basename(path))
    if not lab or not os.path.exists(path):
        return None
    cut = trim(path)
    secs = seconds(path)
    if not secs or secs < 1:
        if not _has_box(path, "mdat") or (cut is not None
                                          and not _has_box(path, "moof")):
            # nothing came down (it failed at once -- e.g. the disk was
            # already full): an empty shell, not worth keeping
            try:
                os.remove(path)
            except OSError:
                pass
            drop_cover(path)
            return None
        if cut is None:
            say(f"  !! {os.path.basename(path)} cannot be played: it was "
                f"written by an older MusicTool (an ordinary MP4 has its index "
                f"at the end, and that never got written). It is left as it "
                f"is; this part is fetched again.")
        return None
    start = float(lab[0])
    out = relabel(path, start, start + secs)
    for attempt in range(10):
        try:
            os.replace(path, out)
            break
        except PermissionError:          # Windows: a player may hold it
            if attempt == 9:
                return None
            time.sleep(0.5)
        except OSError:
            return None
    # the cover yt-dlp saved to embed at the end: that end never came
    drop_cover(path)
    say(f"  kept what came down as a playable file: {os.path.basename(out)} "
        f"({_hms(secs)})")
    return out, start, start + secs


def _hms(sec):
    s = int(sec or 0)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


# ------------------------------------------------------------ disk checks

def estimate_bytes(info, selector, span):
    """About how big a part of `span` seconds is, from the sizes YouTube
    gives for the whole video. None when unknown."""
    if not isinstance(info, dict) or not selector or not span:
        return None
    total = info.get("duration")
    by_id = {str(f.get("format_id")): f for f in info.get("formats") or []}
    size = 0
    for fid in str(selector).split("+"):
        f = by_id.get(fid.strip())
        n = f and (f.get("filesize") or f.get("filesize_approx"))
        if not n:
            return None
        size += n
    return size * span / total if total else None


MOUNTS = "/proc/mounts"          # a module setting so tests can point it away


def fs_type(path):
    """The file system `path` is on ('vfat', 'exfat', 'ntfs', 'FAT32', ...),
    or '' when it cannot be told. On Android a memory card shows up as
    /storage/XXXX-XXXX through FUSE; the card itself is /mnt/media_rw/XXXX-XXXX."""
    try:
        p = os.path.realpath(path if os.path.isdir(path) else
                             os.path.dirname(os.path.abspath(path)))
    except OSError:
        return ""
    if sys.platform.startswith("win"):
        try:
            import ctypes
            root = os.path.splitdrive(p)[0] + "\\"
            buf = ctypes.create_unicode_buffer(64)
            ok = ctypes.windll.kernel32.GetVolumeInformationW(
                ctypes.c_wchar_p(root), None, 0, None, None, None, buf, 64)
            return buf.value if ok else ""
        except Exception:                                    # noqa: BLE001
            return ""
    try:
        with open(MOUNTS, encoding="utf-8", errors="replace") as f:
            mounts = [ln.split()[:3] for ln in f if len(ln.split()) >= 3]
    except OSError:
        return ""
    def kind(where):
        best = ("", "")
        for _dev, mnt, typ in mounts:
            mnt = mnt.replace("\\040", " ")
            if (where == mnt or where.startswith(mnt.rstrip("/") + "/")) \
                    and len(mnt) > len(best[0]):
                best = (mnt, typ)
        return best[1]
    typ = kind(p)
    m = re.match(r"^/storage/([0-9A-Fa-f]{4}-[0-9A-Fa-f]{4})(/|$)", p)
    if m and typ in ("fuse", "sdcardfs", "esdfs", ""):
        typ = kind(f"/mnt/media_rw/{m.group(1)}") or typ
    return typ


def is_fat(typ):
    return str(typ).lower() in ("vfat", "msdos", "fat", "fat32", "fat16")


def free_bytes(folder):
    try:
        return shutil.disk_usage(folder or ".").free
    except OSError:
        return None
