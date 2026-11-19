"""Live download-progress reporting, deliberately tkinter-free.

yt-dlp is normally run with capture_output and its text only surfaces when the
process exits -- on a 186-track playlist that means minutes of silence. So the
download layer now streams yt-dlp's output while it runs (splitting on both \\r
and \\n, because yt-dlp redraws its percentage with carriage returns) and feeds
the interesting lines through parse_ytdlp_line() into a State.

Two consumers:
  * the CLI / log: the raw (ANSI-stripped) lines are forwarded as-is, so you get
    textual progress in a terminal or the GUI log;
  * the GUI: it registers a hook with set_hook() and turns the parsed events
    into progress bars, the current filename, the speed and the ETAs.

The hook is a no-op unless the GUI sets one, so running from a terminal never
doubles the output.
"""

import os
import re
import time

# yt-dlp redraws the percentage with \r, not \n.
ITEM_RE = re.compile(r"Downloading item (\d+) of (\d+)")
DEST_RE = re.compile(r"Destination:\s*(.+)")
# Real yt-dlp progress lines (2026.08.19, verified against a live download):
#   [download]   0.0% of    7.63MiB at  Unknown B/s ETA Unknown
#   [download]  13.1% of    7.63MiB at    2.00MiB/s ETA 00:03
#   [download]   4.2% of ~  12.34MiB at    1.20MiB/s ETA 00:08
#   [download] 100% of    7.63MiB in 00:00:03 at 2.00MiB/s
# Speed/ETA/elapsed are each matched on their own so an "Unknown" token (which
# yt-dlp prints before the first sample) can never be mistaken for a value.
PCT_RE = re.compile(
    r"\[download\]\s+([\d.]+)%\s+of\s+~?\s*([\d.,]+\s*[KMGTP]?i?B)",
    re.IGNORECASE)
SPEED_RE = re.compile(r"\bat\s+([\d.,]+\s*[KMGTP]?i?B/s)", re.IGNORECASE)
ELAPSED_RE = re.compile(r"\bin\s+([\d:]+)(?=\s|$)")
ETA_RE = re.compile(r"\bETA\s+(\d[\d:]*)")
# A stream that is live (or taken from its start) has no total size, so yt-dlp
# prints how much it has instead of a percentage -- many times a second:
#   [download]    5.86MiB at  472.39KiB/s (00:00:12) (frag 119)
#   [download]    1.00KiB at      0.00B/s (00:00:00) (frag 0/120)
# These were not recognised, so every one of them went into the log (1481
# lines in 8 seconds) and the bar never moved.
GOT_RE = re.compile(r"\[download\]\s+([\d.,]+\s*[KMGTP]?i?B)\s+at\b", re.IGNORECASE)
FRAG_RE = re.compile(r"\(frag\s+(\d+)(?:/(\d+))?\)")
# With video and audio downloading side by side yt-dlp numbers the lines:
#   1: [download]  12.0% of ~ 2.00GiB at ...     2: [download] ...
STREAM_RE = re.compile(r"^(\d+):\s+(?=\[)")
# A dropped connection: one line per attempt, per piece, per worker.
RETRY_RE = re.compile(r"Got error:.*?(?:Retrying(?: fragment (\d+))? \((\d+)/(\d+)\)|"
                      r"Giving up after (\d+) retries|$)", re.IGNORECASE)
# ...and between attempts, one of these per worker
SLEEP_RE = re.compile(r"^Sleeping ([\d.]+) seconds", re.IGNORECASE)
SKIP_RE = re.compile(r"Skipping fragment (\d+)", re.IGNORECASE)
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def strip_ansi(line):
    return ANSI_RE.sub("", line)


def unglue(line):
    """A progress redraw with an error written straight after it (no newline
    in between, as yt-dlp does when a piece fails mid-redraw) -> just the
    error. Anything else is returned as it is."""
    s = line or ""
    i = s.find("ERROR:")
    return s[i:] if i > 0 else s


def parse_ytdlp_line(line):
    """Turn one yt-dlp line/segment into an event dict, or None if boring.
    An ERROR line is never an event: it is for the log."""
    s = unglue(strip_ansi(line or "")).strip()
    if not s or s.startswith("ERROR:"):
        return None
    stream = None
    m = STREAM_RE.match(s)
    if m:
        stream = int(m.group(1))
        s = s[m.end():]
    ev = _parse(s)
    if ev is not None and stream is not None:
        ev["stream"] = stream
    return ev


def _parse(s):
    m = SLEEP_RE.search(s)
    if m:
        return {"type": "retry", "k": None, "n": None, "sleep": float(m.group(1)),
                "gave_up": False, "msg": s}
    m = RETRY_RE.search(s)
    if m:
        # a progress redraw and the error can arrive glued on one line
        s = s[s.find("Got error:"):] if "Got error:" in s else s
        if s.startswith("Got error:"):
            s = "[download] " + s
        if m.group(4):
            return {"type": "retry", "k": int(m.group(4)), "n": int(m.group(4)),
                    "gave_up": True, "msg": s}
        return {"type": "retry", "k": int(m.group(2)) if m.group(2) else None,
                "n": int(m.group(3)) if m.group(3) else None,
                "frag": int(m.group(1)) if m.group(1) else None,
                "gave_up": False, "msg": s}
    m = SKIP_RE.search(s)
    if m:
        return {"type": "skip", "frag": int(m.group(1)), "msg": s}
    m = ITEM_RE.search(s)
    if m:
        return {"type": "item", "i": int(m.group(1)), "n": int(m.group(2))}
    m = DEST_RE.search(s)
    if m:
        return {"type": "file", "name": m.group(1).strip()}
    if "[download]" in s and ("100%" in s and "ETA" not in s):
        return {"type": "done"}
    m = PCT_RE.search(s)
    if m:
        sp = SPEED_RE.search(s)
        eta = ETA_RE.search(s)
        el = ELAPSED_RE.search(s)
        ev = {"type": "pct", "pct": float(m.group(1)),
              "size": re.sub(r"\s+", "", m.group(2)),
              "speed": (sp.group(1).strip() if sp else ""),
              "eta": (eta.group(1) if eta else ""),
              "elapsed": (el.group(1) if el else "")}
        _frags(s, ev)
        return ev
    m = GOT_RE.search(s)
    if m:
        # no total: the size so far, and the share of the pieces when yt-dlp
        # knows how many there are (a stream that has just ended), else no %
        sp = SPEED_RE.search(s)
        ev = {"type": "pct", "pct": None, "got": re.sub(r"\s+", "", m.group(1)),
              "size": "", "speed": (sp.group(1).strip() if sp else ""),
              "eta": "", "elapsed": ""}
        el = re.search(r"\((\d[\d:]*)\)", s)
        if el:
            ev["elapsed"] = el.group(1)
        _frags(s, ev)
        if ev.get("frags") and ev["frags"][1]:
            ev["pct"] = min(100.0, 100.0 * ev["frags"][0] / ev["frags"][1])
        return ev
    return None


def _frags(s, ev):
    m = FRAG_RE.search(s)
    if m:
        ev["frags"] = (int(m.group(1)), int(m.group(2)) if m.group(2) else None)


def iter_segments(fd):
    """Yield decoded progress segments from a binary stream fd, split on
    both \\r and \\n so \\r-redrawn percentages arrive as they happen."""
    buf = b""
    while True:
        chunk = os.read(fd, 4096)
        if not chunk:
            break
        buf += chunk
        parts = re.split(rb"[\r\n]+", buf)
        buf = parts.pop()
        for p in parts:
            if p.strip():
                yield p.decode("utf-8", "replace")
    if buf.strip():
        yield buf.decode("utf-8", "replace")


def fmt_seconds(sec):
    sec = int(max(0, sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def fmt_minutes(sec):
    """Wall-clock durations in minutes, never bare seconds.

    '7' -> '0m 07s', '1084' -> '18m 04s', '3725' -> '1h 02m'. A run over a
    447-track playlist is measured in minutes, and '42s' / '2563s' are both
    harder to read than '0m 42s' / '42m 43s'.
    """
    sec = int(max(0, sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m"
    return f"{m}m {s:02d}s"


def parse_eta(text):
    """'00:40' / '01:02:03' -> seconds, else None."""
    if not text:
        return None
    parts = str(text).strip().split(":")
    try:
        nums = [int(x) for x in parts]
    except ValueError:
        return None
    total = 0
    for n in nums:
        total = total * 60 + n
    return total


class State:
    """Accumulates events and derives the numbers a progress UI wants."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.item = 0            # current 1-based item
        self.total = None        # playlist length once yt-dlp tells us
        self.done_items = 0      # fully finished items
        self.batch_i = 0         # 1-based URL this run is on (batch count)
        self.batch_n = 0         # URLs in this run -- yt-dlp never sees these
        self.slots = {}          # tag -> live stats, one per parallel download
        self.filename = ""
        self.pct = 0.0           # current file 0..100
        self.speed = ""
        self.eta = ""            # current-file ETA text
        self.got = ""            # size so far, when there is no total (live)
        self.frags = None        # (piece, pieces or None)
        self.note = ""           # "connection lost -- retrying", "waiting ..."
        self.streams = {}        # numbered side-by-side streams -> pct
        self.started = time.time()

    def handle(self, ev):
        t = (ev or {}).get("type")
        if t == "item":
            self.item = ev["i"]
            self.total = ev["n"]
            self.pct = 0.0
            # Being on item 3 means 1 and 2 are done, even though yt-dlp has
            # not printed a "done" line for them yet.
            self.done_items = max(self.done_items, ev["i"] - 1)
        elif t == "file":
            self.filename = ev["name"]
            if ev.get("tag"):
                self.slots.setdefault(ev["tag"], {})["name"] = ev["name"]
        elif t == "pct":
            self.note = ""
            pct = ev.get("pct")
            if ev.get("stream") is not None and pct is not None:
                # video and audio side by side: the bar shows the one behind
                self.streams[ev["stream"]] = pct
                pct = min(self.streams.values())
            if pct is not None:
                self.pct = pct
            self.got = ev.get("got") or ""
            self.frags = ev.get("frags")
            if ev.get("speed"):
                self.speed = ev["speed"]
            if ev.get("eta"):
                self.eta = ev["eta"]
            if ev.get("tag"):
                slot = self.slots.setdefault(ev["tag"], {})
                slot["pct"] = ev["pct"]
                if ev.get("speed"):
                    slot["speed"] = ev["speed"]
                if ev.get("name"):
                    slot["name"] = ev["name"]
        elif t == "batch":
            # musictool counts the URLs it was handed; yt-dlp only ever sees
            # one at a time, so without this a batch of four reports no total.
            self.batch_i = ev["i"]
            self.batch_n = ev["n"]
            if not self.total:
                self.pct = 0.0
        elif t == "retry":
            if ev.get("gave_up"):
                self.note = "connection lost -- gave up on this try"
            elif ev.get("sleep"):
                self.note = (f"connection trouble -- trying again in "
                             f"{ev['sleep']:g}s")
            elif ev.get("k"):
                self.note = f"connection trouble -- retrying ({ev['k']}/{ev['n']})"
            else:
                self.note = "connection trouble -- retrying"
            self.speed = ""
        elif t == "wait":
            # musictool's own wait before running yt-dlp again
            self.note = f"no connection -- trying again in {int(ev['secs'])}s"
            self.speed = ""
        elif t == "skip":
            self.note = f"piece {ev['frag']} was skipped"
        elif t == "done":
            if ev.get("stream") is not None:
                self.streams[ev["stream"]] = 100.0
                if min(self.streams.values()) < 100.0:
                    return          # the other stream is still going
            self.streams = {}
            self.done_items = max(self.done_items, self.item)
            self.pct = 100.0
            if ev.get("tag"):
                self.slots.pop(ev["tag"], None)

    def fraction(self):
        """0..1 of the whole run (current file's pct counted fractionally).

        A playlist tells us its own length; a pasted list of separate links
        does not, so the batch count stands in for it.
        """
        if self.total:
            return min(1.0, (self.done_items + self.pct / 100.0) / self.total)
        if self.batch_n:
            done = max(0, self.batch_i - 1)
            return min(1.0, (done + self.pct / 100.0) / self.batch_n)
        return None

    def remaining(self):
        """How many videos are still to come, including the one in flight."""
        if self.total:
            return max(0, self.total - self.done_items)
        if self.batch_n:
            return max(0, self.batch_n - max(0, self.batch_i - 1))
        return 0

    def file_text(self):
        """What the current download is doing, in one line."""
        if self.note:
            return self.note
        bits = [f"{self.got} so far"] if self.got else []
        if self.frags:
            i, n = self.frags
            bits.append(f"piece {i}/{n}" if n else f"piece {i}")
        if self.speed:
            bits.append(self.speed)
        if self.eta:
            bits.append(f"ETA {self.eta}")
        return "   ".join(bits)

    def elapsed(self):
        """Seconds since the run started."""
        return time.time() - self.started

    def elapsed_text(self):
        return fmt_minutes(self.elapsed())

    def eta_file_seconds(self):
        return parse_eta(self.eta)

    def eta_total(self):
        """How long the whole run still has to go, as far as we can tell.

        Best case we know the total, so elapsed time scales up. Failing that,
        the average time per finished video times what is left, plus this
        file's own ETA. With nothing finished yet, the file's ETA is all there
        is -- and saying so beats printing a confident wrong number.
        """
        elapsed = time.time() - self.started
        frac = self.fraction() or 0.0
        # Scaling elapsed time up needs a few seconds of elapsed time to be
        # worth anything: at t=0 it would promise "0m 00s left" for a run that
        # has barely started, which is worse than saying it does not know yet.
        if frac > 0.0 and (self.total or self.batch_n) and elapsed >= 5.0:
            return fmt_minutes(elapsed * (1.0 - frac) / frac)
        left = self.remaining()
        file_eta = self.eta_file_seconds() or 0
        if self.batch_i > 1 and left:
            per = elapsed / max(1, self.batch_i - 1)
            return fmt_minutes(per * (left - 1) + file_eta)
        return fmt_minutes(file_eta) if file_eta else ""


# ---------------------------------------------------------------- hook
_HOOK = None


def set_hook(fn):
    """The GUI registers a callback(event) here; CLI leaves it None (no-op)."""
    global _HOOK
    _HOOK = fn


def get_hook():
    """The registered hook, so a caller can put it back after a test."""
    return _HOOK


def report(ev):
    if ev is not None and _HOOK is not None:
        _HOOK(ev)
