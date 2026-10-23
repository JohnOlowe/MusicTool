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
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def strip_ansi(line):
    return ANSI_RE.sub("", line)


def parse_ytdlp_line(line):
    """Turn one yt-dlp line/segment into an event dict, or None if boring."""
    s = strip_ansi(line or "").strip()
    if not s:
        return None
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
        return {"type": "pct", "pct": float(m.group(1)),
                "size": re.sub(r"\s+", "", m.group(2)),
                "speed": (sp.group(1).strip() if sp else ""),
                "eta": (eta.group(1) if eta else ""),
                "elapsed": (el.group(1) if el else "")}
    return None


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
        self.filename = ""
        self.pct = 0.0           # current file 0..100
        self.speed = ""
        self.eta = ""            # current-file ETA text
        self.started = time.time()

    def handle(self, ev):
        t = (ev or {}).get("type")
        if t == "item":
            self.item = ev["i"]
            self.total = ev["n"]
            self.pct = 0.0
        elif t == "file":
            self.filename = ev["name"]
        elif t == "pct":
            self.pct = ev["pct"]
            if ev.get("speed"):
                self.speed = ev["speed"]
            if ev.get("eta"):
                self.eta = ev["eta"]
        elif t == "done":
            self.done_items = max(self.done_items, self.item)
            self.pct = 100.0

    def fraction(self):
        """0..1 of the whole playlist (current file's pct counted fractionally)."""
        if not self.total:
            return None
        return min(1.0, (self.done_items + self.pct / 100.0) / self.total)

    def elapsed(self):
        """Seconds since the run started."""
        return time.time() - self.started

    def elapsed_text(self):
        return fmt_minutes(self.elapsed())

    def eta_file_seconds(self):
        return parse_eta(self.eta)

    def eta_total(self):
        """Elapsed-time estimate of how long the whole run still has to go."""
        if not self.total:
            return ""
        frac = self.fraction() or 0.0
        elapsed = time.time() - self.started
        if frac <= 0.0:
            return ""
        remaining = elapsed * (1.0 - frac) / frac
        return fmt_minutes(remaining)


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
