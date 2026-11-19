"""Live streams: record one from its start, watch what is there, carry on later.

What goes wrong without this, and why
-------------------------------------
yt-dlp downloads a live stream in pieces (DASH fragments, one every few
seconds). With -N/--concurrent-fragments it gives every stream
ceil(N / streams) workers, and as soon as that is more than one it hands the
pieces to a thread pool whose map() first *reads the whole list of pieces* --
which, for a stream that is still live, does not end until the broadcast does.
So nothing is stitched together: thousands of NAME.f299.mp4.part-FragN files
pile up, nothing can be played, and a full disk leaves you with no video at
all. (yt-dlp 2026.10.26, downloader/fragment.py.)

With exactly one worker per stream the pieces are appended the moment they
land: the .part file always holds everything so far, it can be watched at any
time, and a run that stops (full disk, Stop, power cut) carries on from the
last piece. Video and audio still download side by side, because yt-dlp runs
one thread per stream. So a live recording here uses -N = number of streams
(2 for video + audio, 1 for audio only) -- no slower, and nothing piles up.

The folder tools (scan / report / join / watch_copy / resume) also rescue
recordings made the other way, by plain yt-dlp: they stitch the loose pieces
in the order and format yt-dlp itself would have, so yt-dlp can carry on from
there afterwards.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import time

from . import cancel, download, formats

FRAG = re.compile(r"^(?P<tmp>.+\.part)-Frag(?P<n>\d+)$")
HALF = re.compile(r"^(?P<tmp>.+\.part)-Frag(?P<n>\d+)\.part$")   # unfinished
TMP = re.compile(r"^(?P<title>.+)\.f(?P<fid>[^.]+)\.(?P<ext>[A-Za-z0-9]+)\.part$")
AUDIO_EXT = {"m4a", "aac", "opus", "ogg", "mp3", "oga"}
# YouTube's audio-only format ids. A live stream's audio can arrive as .mp4
# (f140.mp4), so the extension alone does not tell.
AUDIO_ITAGS = {"139", "140", "141", "171", "172", "233", "234", "249", "250",
               "251", "256", "258", "325", "327", "328", "338", "380", "599",
               "600", "773", "774"}
BATCH = 50          # pieces per step: copied, synced, recorded, then deleted
BUSY_SECONDS = 30   # a file changed this recently: a download is still running
LIVE = ("is_live", "post_live")
STATUS_TEXT = {
    "is_live": "LIVE now",
    "is_upcoming": "not started yet",
    "post_live": "just ended -- YouTube is still processing it",
    "was_live": "an ended live stream (a normal video now)",
    "not_live": "a normal video",
}


def retry(fn, *args, tries=40):
    """Run a file operation, waiting out Windows' brief locks.

    Defender, the search indexer and OneDrive open a file that has just been
    written, and while they hold it Windows refuses to replace or delete it
    ("[WinError 5] Access is denied"). It lasts a moment. The standalone
    livejoin.py gave up on it after 1000 pieces; this waits up to ~15 s.
    """
    for i in range(tries):
        try:
            return fn(*args)
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(min(0.05 * (i + 1), 0.5))


def human(n):
    return formats.human_bytes(n) if n else "0 B"


def hms(sec):
    sec = int(sec or 0)
    return f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def ffmpeg_exe():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:                                        # noqa: BLE001
        return None


# --------------------------------------------------------------------------
# what the stream is doing: read from a probe (download.probe)
# --------------------------------------------------------------------------
def status(info):
    """yt-dlp's live_status: is_live / is_upcoming / post_live / was_live /
    not_live, or None when the site does not say."""
    if not isinstance(info, dict) or info.get("error"):
        return None
    s = info.get("live_status")
    if s:
        return s
    if info.get("is_live"):
        return "is_live"
    if info.get("was_live"):
        return "was_live"
    return None


def started_ago(info, now=None):
    """Seconds since the broadcast began, or None."""
    t = info.get("release_timestamp") or info.get("timestamp")
    if not t:
        return None
    now = time.time() if now is None else now
    return max(0.0, now - float(t))


def _rate(f):
    """kbit/s of one format."""
    return f.get("tbr") or ((f.get("vbr") or 0) + (f.get("abr") or 0)) or 0


def estimate(info, selector=None, keep_video=True, now=None):
    """How big a from-the-start recording is.

    -> {"per_hour": bytes, "so_far": bytes or None, "seconds": s or None,
        "exact": the chosen formats were known} or None.
    With a concrete selector (e.g. 299+140 from the quality pick) the rate
    is theirs; otherwise the best video + best audio, i.e. an upper bound.
    """
    fmts = [f for f in (info.get("formats") or []) if isinstance(f, dict)]
    by_id = {str(f.get("format_id")): f for f in fmts}
    kbps, exact = 0, False
    ids = (str(selector or "").split("/")[0]).split("+") if selector else []
    if ids and all(i in by_id for i in ids):
        kbps, exact = sum(_rate(by_id[i]) for i in ids), True
    else:
        vids = [f for f in fmts if (f.get("vcodec") or "none") != "none"
                and (f.get("acodec") or "none") == "none"]
        auds = [f for f in fmts if (f.get("vcodec") or "none") == "none"
                and (f.get("acodec") or "none") != "none"]
        both = [f for f in fmts if (f.get("vcodec") or "none") != "none"
                and (f.get("acodec") or "none") != "none"]
        if keep_video:
            kbps = (max(map(_rate, vids), default=0)
                    + max(map(_rate, auds), default=0)) if vids else \
                max(map(_rate, both), default=0)
        else:
            kbps = max(map(_rate, auds), default=0) or \
                max(map(_rate, both), default=0)
    if not kbps:
        return None
    bps = kbps * 1000 / 8
    secs = started_ago(info, now) if status(info) == "is_live" else None
    if secs is None:
        secs = info.get("duration")
    return {"per_hour": bps * 3600, "so_far": bps * secs if secs else None,
            "seconds": secs, "exact": exact}


def stream_count(o):
    """How many streams yt-dlp will fetch side by side: 2 = video + audio."""
    sel = str(o.get("format_selector") or "")
    if sel:
        return max(1, len(sel.split("/")[0].split("+")))
    return 2 if o.get("keep_video") else 1


def live_template(template):
    """The file name for a live recording: the usual one plus [video id].

    A streamer often edits the title while live; with the id in the name the
    next run still finds the pieces it is meant to carry on from. A template
    that already has %(id)s, or does not end in .%(ext)s, is left alone.
    """
    t = template or download.DEFAULT_TEMPLATE
    if "%(id)" in t or not t.endswith(".%(ext)s"):
        return t
    return t[:-len(".%(ext)s")] + " [%(id)s].%(ext)s"


def literal(name):
    """A fixed file name as a yt-dlp output template (% doubled)."""
    return name.replace("%", "%%")


def leftover_base(folder, video_id):
    """The base name of unfinished pieces of this video in `folder`, or None.

    Re-using it exactly is what makes a second run carry on instead of
    starting again under a new name (the title may have changed since)."""
    if not video_id or not os.path.isdir(folder):
        return None
    tag = f"[{video_id}]"
    for s in scan(folder):
        if tag in s.title:
            return s.title
    return None


def free_bytes(folder):
    try:
        return shutil.disk_usage(folder).free
    except OSError:
        return None


def plan(info, o, log=print, now=None):
    """Set up a `get` of a live stream. Changes `o` in place. -> the status.

    is_live/post_live: from the very start (unless --live-from-now), one
    worker per stream, the video id in the file name, no lyrics, and a word
    about size and free space before anything is downloaded.
    is_upcoming: wait for it to begin, then do the above.
    was_live: nothing changes -- but unfinished live pieces of the same video
    in the folder are pointed out (`live --resume` fetches only the rest).
    """
    st = status(info)
    out = o.get("output_dir") or "."
    if st in ("was_live", "not_live"):
        base = leftover_base(out, info.get("id"))
        if base:
            log(f"  note: this folder has an unfinished live recording of this "
                f"video ({base}). To fetch only the part you are missing, "
                f"stop this and use the Live streams tab, or:\n"
                f"        musictool live \"{out}\" --resume URL")
        return st
    if st not in LIVE and st != "is_upcoming":
        return st
    o["no_lyrics"] = True                 # a broadcast, not a song
    if st == "is_live" and o.get("live_from_now"):
        log("  live stream: recording from NOW (--live-from-now), not from "
            "the start")
        return st
    o["live_from_start"] = True
    if st == "is_upcoming":
        o["wait_for_video"] = "60"
        when = info.get("release_timestamp")
        log("  live stream that has not started yet"
            + (f" (due {time.strftime('%Y-%m-%d %H:%M', time.localtime(when))})"
               if when else "")
            + " -- waiting for it, then recording from the start")
    k = stream_count(o)
    if st in ("is_live", "is_upcoming"):
        o["concurrent"] = k
    o["template"] = live_template(o.get("template"))
    base = leftover_base(out, info.get("id"))
    if base:
        o["template"] = literal(base) + ".%(ext)s"
        for s in scan(out):
            if s.title == base:
                said = s.recover()
                if said:
                    log(f"  {s.tmp}: {said}")
        log(f"  carrying on with the pieces already in the folder: {base}")
    if st == "is_live":
        ago = started_ago(info, now)
        log("  live stream" + (f", started {hms(ago)} ago" if ago else "")
            + " -- recording it from the very start")
    elif st == "post_live":
        log("  the stream has just ended and YouTube is still processing it "
            "-- taking it from the start (otherwise only the last 2 hours "
            "are offered)")
    if st in ("is_live", "is_upcoming"):
        log(f"  {k} stream(s) side by side, each piece added to the file as it "
            f"arrives -- whatever has downloaded can be watched at any time, "
            f"and a stopped run carries on where it was")
    _space(info, o, out, log, now)
    return st


def _space(info, o, out, log, now=None):
    est = estimate(info, o.get("format_selector"), o.get("keep_video"), now)
    free = free_bytes(out)
    where = os.path.abspath(out)
    if est:
        about = "about" if est["exact"] else "up to about"
        line = f"  size: {about} {human(est['so_far'])} so far" \
            if est.get("so_far") else "  size: unknown so far"
        if status(info) in ("is_live", "is_upcoming"):
            line += f", growing {about} {human(est['per_hour'])} an hour while it runs"
        log(line)
    if free is None:
        return
    log(f"  room: {human(free)} free in {where}. Joining video and audio at "
        f"the end needs about as much again as the recording.")
    if est and est.get("so_far") and free < est["so_far"]:
        log(f"  !! not enough room even for what has been broadcast so far. "
            f"The download stops when the disk is full -- nothing is lost: "
            f"free some space and run the same download again, or record to "
            f"another drive (-o). Meanwhile watch what you have with the "
            f"Live streams tab or: musictool live \"{where}\" --watch")
    elif est and est.get("so_far") and free < 2 * est["so_far"]:
        log("  !! the recording fits, but the final join of video and audio "
            "may not. If it stops there, nothing is lost: the Live streams "
            "tab (or musictool live --watch --out OTHER_DRIVE) writes the "
            "watchable file to another drive.")


def after_failure(o, err, log=print):
    """What to do when a live recording stopped with an error."""
    out = os.path.abspath(o.get("output_dir") or ".")
    log("  the recording stopped. Nothing downloaded so far is lost:")
    if download.classify_error(err) == "disk_full":
        log("    1. free some space on that drive (or pick another with -o)")
    log("    - watch what you have:  musictool live \"%s\" --watch" % out)
    log("    - carry on:  run the same download again (it continues from the "
        "last piece), or  musictool live \"%s\" --resume URL" % out)


# --------------------------------------------------------------------------
# the pieces in a folder
# --------------------------------------------------------------------------
class Stream:
    """One format's pieces: NAME.fID.EXT.part, its .ytdl and its -FragN files."""

    def __init__(self, folder, tmp):
        self.folder, self.tmp = folder, tmp
        m = TMP.match(tmp)
        self.title, self.fid, self.ext = (m.group("title"), m.group("fid"),
                                          m.group("ext")) if m else (tmp[:-5], "?", "?")
        self.frags = {}           # n -> path (complete pieces only)
        self.half = 0             # unfinished pieces yt-dlp would redo
        self.half_paths = {}
        self.empty_paths = []     # 0-byte pieces: yt-dlp would take them as
        #                           done and leave a hole, so they are removed
        self._audio = None

    @property
    def out(self):                 # NAME.f299.mp4
        return self.tmp[:-len(".part")]

    @property
    def part_path(self):
        return os.path.join(self.folder, self.tmp)

    @property
    def ytdl_path(self):
        return os.path.join(self.folder, self.out + ".ytdl")

    @property
    def note_path(self):
        return os.path.join(self.folder, self.out + ".livejoin")

    def index(self):
        """How many pieces are already inside the .part (yt-dlp's own count)."""
        try:
            with open(self.ytdl_path, encoding="utf-8") as fh:
                return int(json.load(fh)["downloader"]["current_fragment"]["index"])
        except Exception:                                    # noqa: BLE001
            return 0

    def set_index(self, n):
        data = json.dumps({"downloader": {"current_fragment": {"index": n}}})
        tmp = self.ytdl_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(data)
            retry(os.replace, tmp, self.ytdl_path)
        except PermissionError:
            # still locked after ~15 s: write it in place instead
            def direct():
                with open(self.ytdl_path, "w", encoding="utf-8") as fh:
                    fh.write(data)
            retry(direct)
            try:
                os.remove(tmp)
            except OSError:
                pass

    # A note of the .part size at the last recorded count. If a step is cut
    # off after copying but before recording, the extra bytes are pieces that
    # are still on disk, so the .part is cut back to this size and they are
    # copied again -- never twice.
    def write_note(self, index, size):
        def w():
            with open(self.note_path, "w", encoding="utf-8") as fh:
                json.dump({"index": index, "size": size}, fh)
        retry(w)

    def drop_note(self):
        try:
            retry(os.remove, self.note_path)
        except OSError:
            pass

    def recover(self):
        """Undo a step that was cut off. -> a line saying what was done, or ''.

        Two ways to get here: a join of ours stopped mid-step (the note says
        how big the file was), or yt-dlp / the old livejoin.py stopped between
        adding piece n and recording it -- the .part then ends with exactly
        piece n, which is still on disk. Either way it is not added twice.
        """
        idx, size = self.index(), self.part_size()
        said = ""
        note = None
        try:
            with open(self.note_path, encoding="utf-8") as fh:
                note = json.load(fh)
        except Exception:                                    # noqa: BLE001
            pass
        if note and int(note.get("index", -1)) == idx \
                and size > int(note.get("size", size)):
            with open(self.part_path, "r+b") as fh:
                fh.truncate(int(note["size"]))
            said = (f"cut off a half-finished step ({human(size - int(note['size']))}"
                    f" that will be copied again)")
        elif (idx + 1) in self.frags:
            nxt = self.frags[idx + 1]
            n = os.path.getsize(nxt)
            if 0 < n <= size:
                with open(self.part_path, "rb") as fh:
                    fh.seek(size - n)
                    tail = fh.read(n)
                with open(nxt, "rb") as fh:
                    if fh.read() == tail:
                        self.set_index(idx + 1)
                        try:
                            retry(os.remove, self.frags.pop(idx + 1))
                        except OSError:
                            pass
                        said = (f"piece {idx + 1} was already in the file (the "
                                f"last run stopped right after adding it) -- "
                                f"not adding it twice")
        leftover = self.ytdl_path + ".tmp"
        if os.path.exists(leftover):
            try:
                retry(os.remove, leftover)
            except OSError:
                pass
        # pieces at or below the recorded count are already inside the .part
        for n in [n for n in self.frags if n <= self.index()]:
            try:
                retry(os.remove, self.frags.pop(n))
            except OSError:
                pass
        return said

    def part_size(self):
        try:
            return os.path.getsize(self.part_path)
        except OSError:
            return 0

    def loose_bytes(self):
        return sum(os.path.getsize(p) for p in self.frags.values()
                   if os.path.exists(p))

    def next_run(self):
        """The pieces that follow the .part without a hole: idx+1, idx+2, ..."""
        n, run = self.index() + 1, []
        while n in self.frags:
            run.append(n)
            n += 1
        return run

    def last_change(self):
        """Newest modification time of any of this stream's files."""
        paths = [self.part_path, self.ytdl_path, *self.frags.values(),
                 *self.half_paths.values()]
        times = []
        for p in paths:
            try:
                times.append(os.path.getmtime(p))
            except OSError:
                pass
        return max(times, default=0)

    def is_audio(self, others=()):
        if self._audio is None:
            self._audio = self._detect_audio(others)
        return self._audio

    def _detect_audio(self, others):
        # Ask ffmpeg what is really inside: the surest answer.
        exe = ffmpeg_exe()
        sample = (self.frags[min(self.frags)] if self.frags
                  else self.part_path if self.part_size() else None)
        if exe and sample:
            try:
                r = subprocess.run([exe, "-hide_banner", "-i", sample],
                                   capture_output=True, text=True,
                                   errors="replace", timeout=60)
                kinds = re.findall(r"Stream #\S+.*?: (Video|Audio):", r.stderr or "")
            except Exception:                                # noqa: BLE001
                kinds = []
            if kinds:
                return "Video" not in kinds
        if self.fid in AUDIO_ITAGS:
            return True
        if self.ext.lower() in AUDIO_EXT:
            return True
        if self.ext.lower() in ("mp4", "mkv", "mov"):
            return False
        # webm can be either: the smaller of a pair is the audio
        mine = self.part_size() + self.loose_bytes()
        return any(o is not self and mine < o.part_size() + o.loose_bytes()
                   for o in others)


def scan(folder):
    """Every unfinished yt-dlp download in `folder` that is made of pieces."""
    streams = {}
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    for name in names:
        m = FRAG.match(name)
        if m:
            s = streams.setdefault(m.group("tmp"), Stream(folder, m.group("tmp")))
            path = os.path.join(folder, name)
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            if size > 0:
                s.frags[int(m.group("n"))] = path
            else:
                s.empty_paths.append(path)
            continue
        m = HALF.match(name)
        if m:
            s = streams.setdefault(m.group("tmp"), Stream(folder, m.group("tmp")))
            s.half += 1
            s.half_paths[int(m.group("n"))] = os.path.join(folder, name)
            continue
        if TMP.match(name):
            streams.setdefault(name, Stream(folder, name))
    return [s for s in streams.values()
            if s.frags or s.part_size() or os.path.exists(s.ytdl_path)]


def by_title(streams):
    groups = {}
    for s in streams:
        groups.setdefault(s.title, []).append(s)
    return groups


def busy_for(streams, now=None):
    """Seconds since the last write, if that is under BUSY_SECONDS, else None."""
    now = time.time() if now is None else now
    newest = max((s.last_change() for s in streams), default=0)
    ago = now - newest
    return ago if newest and ago < BUSY_SECONDS else None


def _probe_media(path):
    """ffmpeg's view of a file: (seconds or None, 'WxH' or None)."""
    exe = ffmpeg_exe()
    if not exe or not path or not os.path.exists(path):
        return None, None
    try:
        r = subprocess.run([exe, "-hide_banner", "-i", path], capture_output=True,
                           text=True, errors="replace", timeout=120)
    except Exception:                                        # noqa: BLE001
        return None, None
    err = r.stderr or ""
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", err)
    secs = (int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
            ) if m else None
    v = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", err)
    return secs, (f"{v.group(1)}x{v.group(2)}" if v else None)


def media_seconds(path):
    """How long a file plays. A stitched live .part often has no duration in
    its header, so it is then read to the end (fast: nothing is decoded)."""
    secs, _ = _probe_media(path)
    if secs:
        return secs
    exe = ffmpeg_exe()
    if not exe or not os.path.exists(path):
        return None
    try:
        r = subprocess.run([exe, "-hide_banner", "-nostats", "-progress",
                            "pipe:1", "-i", path, "-map", "0", "-c", "copy",
                            "-f", "null", "-"], capture_output=True, text=True,
                           errors="replace", timeout=1800)
    except Exception:                                        # noqa: BLE001
        return None
    us = re.findall(r"out_time_(?:us|ms)=(\d+)", r.stdout or "")
    return int(us[-1]) / 1e6 if us else None


def report(folder, streams, log=print):
    if not streams:
        log(f"no unfinished live recording (yt-dlp *.part pieces) in {folder}")
        return
    for title, group in by_title(streams).items():
        log(f"\n{title}")
        for s in sorted(group, key=lambda s: s.is_audio(group)):
            kind = "audio" if s.is_audio(group) else "video"
            idx, run = s.index(), s.next_run()
            last = max(s.frags) if s.frags else idx
            holes = [n for n in range(idx + 1, last + 1) if n not in s.frags]
            done = f"pieces 1-{idx}" if idx else "none yet"
            log(f"  {kind} f{s.fid} ({s.ext}):  {human(s.part_size())} joined "
                f"({done}),  {len(s.frags)} loose piece(s) {human(s.loose_bytes())}")
            if run:
                log(f"      pieces {run[0]}-{run[-1]} can be joined now")
            if holes:
                more = f" ... ({len(holes)} missing)" if len(holes) > 6 else ""
                log(f"      missing piece(s): {', '.join(map(str, holes[:6]))}{more}"
                    f" -- joining stops before the first one; 'download the "
                    f"rest' fetches them")
            if s.half or s.empty_paths:
                log(f"      {s.half + len(s.empty_paths)} unfinished piece(s) -- "
                    f"'download the rest' redoes them")
    free = free_bytes(folder)
    if free is not None:
        log(f"\nfree space here: {human(free)}")


def join(folder, streams, log=print, force=False):
    """Stitch the loose pieces into each stream's .part, as yt-dlp would.

    In place: needs room for about two pieces, not for the whole video. Only
    pieces that follow the file without a hole are added; a piece is deleted
    only after it is inside the file and recorded.  -> 0, or 1 on refusal.
    """
    if not streams:
        log(f"nothing to join in {folder}")
        return 0
    ago = busy_for(streams)
    if ago is not None and not force:
        log(f"!! a download is still writing to these files (last change "
            f"{int(ago)} s ago). Joining now would take pieces it is about to "
            f"use. Making a copy to watch is safe: --watch. If you have just "
            f"stopped it, try again in half a minute.")
        return 1
    free = free_bytes(folder) or 0
    biggest = max((os.path.getsize(p) for s in streams for p in s.frags.values()),
                  default=0)
    if free < biggest * 2:
        log(f"!! only {human(free)} free; joining needs about {human(biggest * 2)}"
            f" (two pieces) of room. Free a little space first.")
        return 1
    for s in streams:
        for p in s.empty_paths:
            try:
                retry(os.remove, p)    # an empty piece is redone by a resume
            except OSError:
                pass
        s.empty_paths.clear()
        said = s.recover()
        if said:
            log(f"{s.tmp}: {said}")
        run = s.next_run()
        if not run:
            continue
        log(f"joining {len(run)} piece(s) into {s.tmp} ...")
        stuck = []
        # In steps of BATCH pieces: note the size, copy, sync, record the
        # count in .ytdl (exactly what yt-dlp does), and only then delete the
        # pieces. Cut off at any point, the next run (or yt-dlp) carries on
        # from a consistent state -- see Stream.recover.
        with open(s.part_path, "ab") as dest:
            done = 0
            for start in range(0, len(run), BATCH):
                if cancel.requested():
                    log(f"   {cancel.CANCELLED} after {done} piece(s) -- the "
                        f"rest stay as they are; run it again to carry on")
                    return 1
                step = run[start:start + BATCH]
                s.write_note(s.index(), dest.tell())
                for n in step:
                    with open(s.frags[n], "rb") as src:
                        shutil.copyfileobj(src, dest, 1 << 20)
                dest.flush()
                os.fsync(dest.fileno())
                s.set_index(step[-1])
                s.write_note(step[-1], dest.tell())
                for n in step:
                    p = s.frags.pop(n)
                    try:
                        retry(os.remove, p)
                    except OSError:
                        stuck.append(p)   # joined already; just not deleted
                done += len(step)
                if done % 500 < BATCH or done == len(run):
                    log(f"   {done}/{len(run)}")
        s.drop_note()
        if stuck:
            log(f"   ({len(stuck)} joined piece(s) could not be deleted -- "
                f"Windows kept them locked. They are not needed any more; "
                f"delete them by hand or join again.)")
    log("done joining.")
    return 0


def watch_copy(folder, streams, out_dir=None, log=print):
    """Make "TITLE (so far).mp4" from the .part files -- nothing re-encoded.

    Only reads the pieces, so it is safe while a recording is still running
    (the copy then ends wherever the download had got to).
    -> (rc, [paths made]).
    """
    exe = ffmpeg_exe()
    rc, made = 0, []
    groups = by_title(streams)
    if not any(s.part_size() for s in streams):
        log("nothing joined yet to make a copy from"
            + (" -- join the pieces first" if any(s.frags for s in streams)
               else ""))
        return 1, made
    for title, group in groups.items():
        vids = [s for s in group if not s.is_audio(group) and s.part_size()]
        auds = [s for s in group if s.is_audio(group) and s.part_size()]
        if not vids and not auds:
            continue
        dest_dir = out_dir or folder
        try:
            os.makedirs(dest_dir, exist_ok=True)
        except OSError as e:
            log(f"!! can't use {dest_dir}: {e}")
            rc = 1
            continue
        webm = any(s.ext.lower() == "webm" for s in vids[:1] + auds[:1])
        dest = os.path.join(dest_dir, f"{title} (so far)."
                            + ("mkv" if webm else "mp4" if vids else "m4a"))
        need = sum(s.part_size() for s in vids[:1] + auds[:1])
        free = free_bytes(dest_dir) or 0
        v = vids[0].part_path if vids else None
        a = auds[0].part_path if auds else None
        if not exe:
            log("!! ffmpeg not found -- install it, or watch the pieces as "
                "they are (see below)")
        elif free < need * 1.02 + 50 * 2 ** 20:
            log(f"\n!! a copy to watch needs about {human(need)} free in "
                f"{dest_dir}; there is {human(free)}.")
            log("   Free some space, or write it to another drive (--out, or "
                "'copy to' on the Live streams tab -- a USB stick works).")
            exe = None
        if exe:
            args = [exe, "-hide_banner", "-loglevel", "error", "-y"]
            if v:
                args += ["-i", v]
            if a:
                args += ["-i", a]
            args += ["-map", "0:v:0" if v else "0:a:0"]
            if v and a:
                args += ["-map", "1:a:0"]
            tmp = dest + ".tmp"
            args += ["-c", "copy", "-f",
                     "matroska" if webm else "mp4" if v else "ipod", tmp]
            log(f"\nmaking {dest} ...")
            ok, err = _run_ffmpeg(args)
            if ok and os.path.exists(tmp):
                retry(os.replace, tmp, dest)
                secs, _ = _probe_media(dest)
                log(f"ready: {dest}  ({human(os.path.getsize(dest))}"
                    + (f", {hms(secs)} long" if secs else "") + ")")
                log("Delete it whenever you like -- the pieces it came from "
                    "are kept, so the download can still carry on.")
                made.append(dest)
                continue
            try:
                os.remove(tmp)
            except OSError:
                pass
            if err == cancel.CANCELLED:
                log(f"   {cancel.CANCELLED}")
                return 1, made
            log("!! ffmpeg could not make the copy: " + (err or "")[-400:])
            rc = 1
        log("\nYou can still watch it without making a copy -- with mpv "
            "(https://mpv.io):")
        if v and a:
            log(f'   mpv "{v}" --audio-file="{a}"')
        else:
            log(f'   mpv "{v or a}"')
        log("or in VLC: Media > Open Multiple Files > add the video piece > "
            "tick 'Show more options' > 'Play another media synchronously' > "
            "the audio piece.")
        rc = rc or 1
    return rc, made


def _run_ffmpeg(args):
    """Run ffmpeg, honouring Stop. -> (ok, error text)."""
    with tempfile.TemporaryFile() as errf:
        try:
            p = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=errf)
        except OSError as e:
            return False, str(e)
        while p.poll() is None:
            if cancel.requested():
                cancel.stop_process(p)
                return False, cancel.CANCELLED
            time.sleep(0.2)
        errf.seek(0)
        text = errf.read().decode("utf-8", "replace")
    return p.returncode == 0, text


def resume(folder, url, o, log=print, force=False):
    """Download the rest of a stopped recording in `folder`.

    While YouTube still serves the stream as a live recording, the very same
    pieces carry on (the existing name is reused, so nothing already here is
    fetched again). Once it is a normal video those pieces no longer fit, so
    only the missing end is fetched, as "TITLE (rest).mp4".  -> exit code.
    """
    streams = scan(folder)
    if any(s.next_run() for s in streams):
        rc = join(folder, streams, log, force=force)
        if rc:
            return rc
        streams = scan(folder)
    groups = by_title(streams)
    if len(groups) != 1:
        log("!! 'download the rest' needs exactly one unfinished recording in "
            f"the folder; found {len(groups)}"
            + (": " + ", ".join(groups) if groups else ""))
        return 1
    base, group = next(iter(groups.items()))
    for s in group:
        s.recover()
    log("asking YouTube what state the stream is in ...")
    info = download.probe(url, dict(o, live_from_now=False))
    if not isinstance(info, dict) or info.get("error"):
        err = str((info or {}).get("error") or "no answer")
        log("!! could not read the stream: " + err.splitlines()[0][:300])
        hint = download.ADVICE.get(download.classify_error(err))
        if hint:
            log("-> " + hint)
        return 1
    st = status(info)
    log(f"it is {STATUS_TEXT.get(st, st or 'of unknown status')}")
    vids = [s for s in group if not s.is_audio(group)]
    auds = [s for s in group if s.is_audio(group)]
    fids = [s.fid for s in vids + auds]
    protos = {str(f.get("format_id")): str(f.get("protocol") or "")
              for f in info.get("formats") or []}
    same = all(protos.get(fid, "").startswith("http_dash_segments") for fid in fids)
    per = dict(o)
    for k in ("playlist_items", "playlist_start", "playlist_end", "yes_playlist",
              "download_archive", "quality", "max_size"):
        per.pop(k, None)
    per.update(no_playlist=True, output_dir=folder, no_lyrics=True,
               embed_thumbnail=False, embed_subs=False, embed_chapters=False,
               keep_video=bool(vids))
    if vids:
        per["merge_format"] = "mkv" if any(
            s.ext.lower() == "webm" for s in group) else "mp4"
    if st in LIVE and same:
        per.update(format_selector="+".join(fids), live_from_start=True,
                   template=literal(base) + ".%(ext)s")
        if not vids:
            per["audio_format"] = per.get("audio_format") or "m4a"
        if st == "is_live":
            per["concurrent"] = len(fids)
        need = sum(s.part_size() for s in group)
        free = free_bytes(folder)
        log(f"carrying on from piece {min(s.index() for s in group) + 1} -- "
            f"pieces already here are not downloaded again")
        if free is not None and free < need:
            log(f"!! {human(free)} free: joining video and audio at the end "
                f"needs about {human(need)} more than the pieces. The download "
                f"itself can go on; free space before it finishes.")
        files, err = download.download(url, per, log=log)
        if err:
            log("!! " + err)
            if err != cancel.CANCELLED:
                after_failure(per, err, log)
            return 130 if err == cancel.CANCELLED else 1
        for p, _vid in files:
            log(f"finished: {p}")
        return 0
    if st in LIVE:
        log("!! YouTube no longer offers the formats these pieces belong to ("
            + ", ".join("f" + f for f in fids) + "), so they cannot be "
            "continued. Make a copy to watch (--join), then try again once it "
            "is a normal video.")
        return 1
    if st == "is_upcoming":
        log("!! it has not started (again?) -- nothing to carry on yet")
        return 1
    # A normal video now: fetch only the part after what you have.
    main = vids[0] if vids else auds[0]
    have = media_seconds(main.part_path) if main.part_size() else 0
    if have is None:
        log("!! could not tell how long your part is (ffmpeg missing?) -- "
            "install ffmpeg and try again")
        return 1
    start = max(0, int(have) - 5)          # 5 s overlap, so nothing is lost
    _, size = _probe_media(main.part_path) if vids else (None, None)
    h = int(size.split("x")[1]) if size else None
    cap = f"[height<={h}]" if h else ""
    if vids:
        per["format_selector"] = (f"bv*{cap}[vcodec^=avc1]+ba[ext=m4a]/"
                                  f"bv*{cap}+ba/b")
        per["merge_format"] = "mp4"
    else:
        per["format_selector"] = "ba[ext=m4a]/ba"
    per.update(download_sections=f"*{hms(start)}-inf",
               template=literal(base) + " (rest).%(ext)s", live_from_start=False)
    log(f"It is a normal video now. Yours ends at {hms(have)}; fetching from "
        f"{hms(start)} to the end only (5 s overlap).")
    files, err = download.download(url, per, log=log)
    if err:
        log("!! " + err)
        return 130 if err == cancel.CANCELLED else 1
    rest = files[0][0] if files else os.path.join(folder, base + " (rest).mp4")
    log(f"finished: {rest}")
    log("Watch the two files one after the other, or make one file of them "
        "(needs room for both; re-encodes, slow):")
    so_far = os.path.join(folder, base + " (so far).mp4")
    log(f'   ffmpeg -i "{so_far}" -i "{rest}" -filter_complex '
        f'"[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1" "{base} (full).mp4"')
    return 0


def describe(info, folder=".", log=print, now=None, keep_video=True):
    """`live --status URL`: is it live, since when, how big, is there room."""
    st = status(info)
    log(f"\"{info.get('title') or info.get('id')}\"  --  "
        f"{STATUS_TEXT.get(st, st or 'not a live stream')}")
    if st == "is_live":
        ago = started_ago(info, now)
        if ago:
            log(f"  started {hms(ago)} ago" + (
                "  (YouTube keeps the last 120 hours to rewind)"
                if ago > 100 * 3600 else ""))
    elif st == "is_upcoming" and info.get("release_timestamp"):
        log("  due " + time.strftime("%Y-%m-%d %H:%M", time.localtime(
            info["release_timestamp"])))
    elif info.get("duration"):
        log(f"  {hms(info['duration'])} long")
    est = estimate(info, keep_video=keep_video, now=now)
    if est and st in ("is_live", "post_live", "was_live", "not_live"):
        so = f"up to about {human(est['so_far'])}" if est.get("so_far") else ""
        if so:
            log(f"  size of the whole thing so far: {so}"
                + (f", growing up to about {human(est['per_hour'])} an hour"
                   if st == "is_live" else ""))
    free = free_bytes(folder)
    if free is not None:
        log(f"  free in {os.path.abspath(folder)}: {human(free)}"
            "  (the final join needs about as much again as the recording)")
    if st in ("is_live", "post_live", "is_upcoming"):
        log("  record it from the start: Live streams tab > Record, or\n"
            "    musictool get URL --keep-video   (detects the live stream "
            "and does the rest)")
    elif st == "was_live":
        log("  it is an ordinary video now: download it as usual, or finish a "
            "stopped recording with  musictool live FOLDER --resume URL")
    return st
