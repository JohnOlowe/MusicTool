"""YouTube live chat replay -> subtitles that play with the video.

yt-dlp downloads a finished stream's chat replay as `NAME.live_chat.json`
(`--write-subs --sub-langs live_chat`): one JSON object per line, each a
batch of chat actions with `videoOffsetTimeMsec`, the moment in the video.
This turns it into a subtitle file any player shows -- ASS, SRT, WebVTT or
a plain text log -- with several messages on screen at once:

  * `lines` messages at most. A message stays while the messages that came
    after it number fewer than `lines`; the next one pushes the oldest off.
    1 behaves like ordinary subtitles; 5-6 reads like the chat scrolling.
  * `hold` seconds at most on screen (0 = until pushed off), so a quiet chat
    does not leave the same line up for minutes.
  * placed on the left, right or centre, at the top, middle or bottom.

Nothing about the video is touched: the subtitle file sits next to it with
the same name (players load it by themselves), or `embed` copies the video
into an .mkv with the chat as a subtitle track -- copied, not re-encoded.
"""

import json
import os
import re


FORMATS = ("ass", "srt", "vtt", "txt")
SIDES = ("right", "left", "center")
PLACES = ("bottom", "middle", "top")
MARK = "musictool chat"          # how a subtitle file we wrote is recognised

# Author colours (ASS is &HBBGGRR&, the others #RRGGBB)
COLOURS = {"owner": "FFD600", "moderator": "5E84F1", "member": "2BA640",
           "verified": "AAAAAA", "paid": "FFCA28", "": "B0B0B0"}


# --------------------------------------------------------------------------
# reading the replay

class Message:
    __slots__ = ("t", "author", "text", "role", "paid", "id")

    def __init__(self, t, author, text, role="", paid="", id=""):
        self.t = t                    # seconds into the video
        self.author = author
        self.text = text
        self.role = role              # owner / moderator / member / verified / ""
        self.paid = paid              # "$5.00" for a Super Chat / Sticker
        self.id = id

    def __repr__(self):
        return f"Message({self.t:.1f}, {self.author!r}, {self.text!r})"


def _runs(obj, emoji="char"):
    """Text of a {"runs": [...]} / {"simpleText": ...} object. Emoji: a
    standard one is its own character ("char") or its :name: ("name"); a
    channel's custom emoji is always its :name: -- there is no character."""
    if not isinstance(obj, dict):
        return ""
    if "simpleText" in obj:
        return str(obj["simpleText"])
    out = []
    for r in obj.get("runs") or []:
        if "text" in r:
            out.append(str(r["text"]))
        elif "emoji" in r:
            e = r["emoji"]
            names = e.get("shortcuts") or []
            custom = e.get("isCustomEmoji")
            if emoji == "char" and not custom and e.get("emojiId"):
                out.append(str(e["emojiId"]))
            elif names:
                out.append(str(names[0]))
            else:
                lbl = (((e.get("image") or {}).get("accessibility") or {})
                       .get("accessibilityData") or {}).get("label")
                out.append(f":{lbl}:" if lbl else "")
    return "".join(out)


def _role(item):
    roles = set()
    for b in item.get("authorBadges") or []:
        r = b.get("liveChatAuthorBadgeRenderer") or {}
        icon = ((r.get("icon") or {}).get("iconType") or "").upper()
        tip = (r.get("tooltip") or "").lower()
        if icon == "OWNER" or tip == "owner":
            roles.add("owner")
        elif icon == "MODERATOR" or tip == "moderator":
            roles.add("moderator")
        elif icon == "VERIFIED" or tip == "verified":
            roles.add("verified")
        elif r.get("customThumbnail") or "member" in tip:
            roles.add("member")
    for k in ("owner", "moderator", "member", "verified"):
        if k in roles:
            return k
    return ""


def _message(kind, item, t, emoji):
    """One chat item -> a Message, or None for things that are not chat
    (placeholders, "welcome to live chat" notices, tickers)."""
    author = _runs(item.get("authorName"), emoji) or ""
    mid = item.get("id", "")
    if kind == "liveChatTextMessageRenderer":
        text = " ".join(_runs(item.get("message"), emoji).split())
        return Message(t, author, text, _role(item), "", mid) if text else None
    if kind == "liveChatPaidMessageRenderer":           # Super Chat
        return Message(t, author, _runs(item.get("message"), emoji), _role(item),
                       _runs(item.get("purchaseAmountText")) or "$", mid)
    if kind == "liveChatPaidStickerRenderer":           # Super Sticker
        return Message(t, author, "(sticker)", _role(item),
                       _runs(item.get("purchaseAmountText")) or "$", mid)
    if kind == "liveChatMembershipItemRenderer":        # joined / milestone
        head = (_runs(item.get("headerPrimaryText"), emoji)
                or _runs(item.get("headerSubtext"), emoji) or "new member")
        msg = _runs(item.get("message"), emoji)
        return Message(t, author, f"[{head}]" + (f" {msg}" if msg else ""),
                       "member", "", mid)
    if kind == "liveChatSponsorshipsGiftPurchaseAnnouncementRenderer":
        hdr = ((item.get("header") or {})
               .get("liveChatSponsorshipsHeaderRenderer") or {})
        author = author or _runs(hdr.get("authorName"), emoji)
        return Message(t, author, f"[{_runs(hdr.get('primaryText'), emoji)}]",
                       "member", "", mid)
    return None


def load(path, emoji="char"):
    """Every chat message in a .live_chat.json, in order, deleted ones left
    out. Lines that are not valid JSON (a download cut short) are skipped."""
    msgs, deleted = [], set()
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            rep = d.get("replayChatItemAction") or {}
            try:
                t = int(rep.get("videoOffsetTimeMsec",
                                d.get("videoOffsetTimeMsec", 0)) or 0) / 1000.0
            except (TypeError, ValueError):
                continue
            for act in rep.get("actions") or []:
                if "addChatItemAction" in act:
                    item = (act["addChatItemAction"] or {}).get("item") or {}
                    for kind, body in item.items():
                        m = _message(kind, body or {}, t, emoji)
                        if m is not None:
                            msgs.append(m)
                elif "markChatItemAsDeletedAction" in act:
                    deleted.add((act["markChatItemAsDeletedAction"] or {})
                                .get("targetItemId"))
                elif "removeChatItemAction" in act:
                    deleted.add((act["removeChatItemAction"] or {})
                                .get("targetItemId"))
    if deleted:
        msgs = [m for m in msgs if m.id not in deleted]
    msgs.sort(key=lambda m: m.t)             # stable: same-time order kept
    return msgs


# --------------------------------------------------------------------------
# which messages are on screen when

def windows(msgs, lines=5, hold=15.0, start=0.0, end=None, offset=0.0):
    """-> [(t0, t1, [Message, ...])]: what is on screen from t0 to t1, oldest
    first, in the clip's time (video time minus `start`, plus `offset`).

    A message appears when it was sent and stays while fewer than `lines`
    messages came after it -- the `lines`-th newer one pushes it off -- and
    for at most `hold` seconds (0 = no limit). Messages outside
    [start, end) are left out: that is the part of the video you have.
    """
    lines = max(1, int(lines))
    hold = float(hold or 0)
    sel = [m for m in msgs if m.t >= start and (end is None or m.t < end)]
    # a part that starts mid-conversation: what was on screen at its first
    # moment is still shown (the last `lines` messages, if within `hold`)
    before = [m for m in msgs if m.t < start and (hold <= 0 or m.t + hold > start)]
    sel = before[-lines:] + sel
    if not sel:
        return []
    clip_end = (end - start) if end is not None else None
    # change points: every arrival, and every expiry
    # (messages carried over from before the part start with the part)
    points = {max(m.t, start) for m in sel}
    if hold > 0:
        points |= {m.t + hold for m in sel if m.t + hold > start}
    out = []
    times = sorted(points)
    i_next = 0                       # first message not yet arrived
    for k, t in enumerate(times):
        while i_next < len(sel) and sel[i_next].t <= t:
            i_next += 1
        shown = sel[max(0, i_next - lines):i_next]
        if hold > 0:
            shown = [m for m in shown if m.t + hold > t]
        t1 = times[k + 1] if k + 1 < len(times) else (
            t + hold if hold > 0 else (end if end is not None else t + 10.0))
        if not shown or t1 <= t:
            continue
        a, b = t - start + offset, t1 - start + offset
        if clip_end is not None:
            b = min(b, clip_end + offset)
        if b <= max(a, 0):
            continue
        a = max(a, 0.0)
        if out and out[-1][2] == shown and abs(out[-1][1] - a) < 1e-6:
            out[-1] = (out[-1][0], b, shown)          # same lines: one cue
        else:
            out.append((a, b, shown))
    return out


# --------------------------------------------------------------------------
# writing

def _wrap(text, width):
    """Break a line at spaces so it is at most `width` characters (longer
    words are left whole)."""
    if not width or len(text) <= width:
        return [text]
    out, cur = [], ""
    for word in text.split(" "):
        if cur and len(cur) + 1 + len(word) > width:
            out.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}" if cur else word
    if cur:
        out.append(cur)
    return out


def _label(m, names=True):
    who = (f"[{m.paid}] " if m.paid else "") + (f"{m.author}: " if names and m.author
                                                 else "")
    return who


def _ts_ass(s):
    cs = int(round(s * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _ts_srt(s, sep=","):
    ms = int(round(s * 1000))
    return (f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d}"
            f"{sep}{ms % 1000:03d}")


def _ass_text(s):
    # "{" opens an override block and "\N" is a line break in ASS: neither
    # may come through from someone's chat message
    return (s.replace("\\", "\\\u2060").replace("{", "\uff5b")
            .replace("}", "\uff5d").replace("\n", " "))


def _bgr(rgb):
    return f"&H{rgb[4:6]}{rgb[2:4]}{rgb[0:2]}&"


def _ass_align(side, place):
    # numpad layout; the text in a side column stays left-aligned like a chat
    row = {"bottom": 1, "middle": 4, "top": 7}[place]
    return row + 1 if side == "center" else row


def to_ass(cues, side="right", place="bottom", width=32, font_size=0,
           names=True, title="", res=(1920, 1080), box=True, **_):
    """ASS: a column `width`% of the screen wide on the chosen side; the
    player wraps long messages inside it. Names in their role's colour, on a
    see-through dark box (`box`) so they read over a bright picture."""
    W, H = res
    fs = int(font_size) or int(H / 24)
    col = int(W * max(10, min(100, width)) / 100)
    edge = int(W * 0.02)
    if side == "right":
        ml, mr = W - col - edge, edge
    elif side == "left":
        ml, mr = edge, W - col - edge
    else:
        ml = mr = (W - col) // 2
    mv = int(H * 0.04)
    head = [
        "[Script Info]",
        f"; {MARK} -- live chat replay as subtitles",
        f"Title: {title or 'Live chat'}",
        "ScriptType: v4.00+",
        f"PlayResX: {W}",
        f"PlayResY: {H}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding",
        # BorderStyle 3 = a box behind each line, in OutlineColour; alpha 0x60
        # leaves the picture faintly visible through it
        (f"Style: Chat,Arial,{fs},&H00FFFFFF,&H00FFFFFF,&H60000000,&H60000000,"
         f"0,0,0,0,100,100,0,0,3,{max(2, fs // 10)},0,{_ass_align(side, place)},"
         f"{ml},{mr},{mv},1") if box else
        (f"Style: Chat,Arial,{fs},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,"
         f"0,0,0,0,100,100,0,0,1,{max(2, fs // 16)},1,{_ass_align(side, place)},"
         f"{ml},{mr},{mv},1"),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text",
    ]
    body = []
    for a, b, shown in cues:
        parts = []
        for m in shown:
            who = _label(m, names)
            c = COLOURS["paid" if m.paid else m.role]
            parts.append((f"{{\\b1\\c{_bgr(c)}}}{_ass_text(who)}{{\\b0\\c&HFFFFFF&}}"
                          if who else "") + _ass_text(m.text))
        body.append(f"Dialogue: 0,{_ts_ass(a)},{_ts_ass(b)},Chat,,0,0,0,,"
                    + "\\N".join(parts))
    return "\n".join(head + body) + "\n"


def _plain_lines(shown, wrap, names, esc):
    """[(message or None, name part, rest)] per screen line: the name part
    is only on a message's first line."""
    out = []
    for m in shown:
        who = _label(m, names)
        for i, ln in enumerate(_wrap(who + m.text, wrap)):
            if i == 0 and who:
                out.append((m, esc(who), esc(ln[len(who):])))
            else:
                out.append((None, "", esc(ln)))
    return out


def _pad(lines_, pad):
    """Fallback for players that ignore position tags: every line moves
    right by `pad` no-break spaces (plain spaces get trimmed). The player
    still centres each line, so this makes a centred column on the right
    half -- lining up a left edge would need the font's letter widths, which
    a subtitle file cannot know. Keep `wrap` short so lines are not
    re-wrapped by the player."""
    if not pad:
        return lines_
    return ["\u00a0" * pad + t for t in lines_]


def to_srt(cues, side="right", place="bottom", wrap=40, names=True, pad=0,
           colour=True, **_):
    """SRT: {\\anN} puts it on the chosen side (VLC, mpv, MPC-HC, PotPlayer
    and most players honour it); names bold, in colour. `pad` adds the
    no-break-space fallback."""
    an = {("left", "bottom"): 1, ("center", "bottom"): 2, ("right", "bottom"): 3,
          ("left", "middle"): 4, ("center", "middle"): 5, ("right", "middle"): 6,
          ("left", "top"): 7, ("center", "top"): 8, ("right", "top"): 9}[(side, place)]
    tag = "" if an == 2 else f"{{\\an{an}}}"

    def esc(s):
        return s.replace("<", "\u2039").replace(">", "\u203a").replace("{", "(") \
                .replace("}", ")")
    out = []
    for n, (a, b, shown) in enumerate(cues, start=1):
        rows = []
        raw = _plain_lines(shown, wrap, names, esc)
        texts = _pad([w + t for _, w, t in raw], pad)
        for (m, who, rest), full in zip(raw, texts):
            if m is not None and who and not pad:
                c = COLOURS["paid" if m.paid else m.role]
                w = (f'<font color="#{c}"><b>{who}</b></font>' if colour
                     else f"<b>{who}</b>")
                rows.append(w + rest)
            else:
                rows.append(full)
        out.append(f"{n}\n{_ts_srt(a)} --> {_ts_srt(b)}\n{tag}"
                   + "\n".join(rows) + "\n")
    return "\n".join(out)


def to_vtt(cues, side="right", place="bottom", width=32, wrap=40, names=True,
           pad=0, **_):
    """WebVTT with cue settings for the side and height. Browsers and VLC
    honour them; some players show every cue centred at the bottom."""
    width = max(10, min(100, int(width)))
    if side == "right":
        pos = f"position:{100 - width - 2}% align:start size:{width}%"
    elif side == "left":
        pos = f"position:2% align:start size:{width}%"
    else:
        pos = "align:center"
    line = {"bottom": "", "top": " line:0", "middle": " line:50%"}[place]

    def esc(s):
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    out = ["WEBVTT", "", f"NOTE {MARK}", ""]
    for a, b, shown in cues:
        raw = _plain_lines(shown, wrap, names, esc)
        if pad:
            rows = _pad([w + t for _, w, t in raw], pad)
        else:
            rows = [(f"<b>{w}</b>{t}" if w else t) for _, w, t in raw]
        out.append(f"{_ts_srt(a, '.')} --> {_ts_srt(b, '.')} {pos}{line}".rstrip())
        out.append("\n".join(rows))
        out.append("")
    return "\n".join(out)


def to_txt(msgs, start=0.0, end=None, offset=0.0, names=True, **_):
    """A readable log: [h:mm:ss] name: message, in the clip's time."""
    out = [f"# {MARK}: live chat replay"]
    for m in msgs:
        if m.t < start or (end is not None and m.t >= end):
            continue
        t = max(0.0, m.t - start + offset)
        s = int(t)
        out.append(f"[{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}] "
                   + _label(m, names) + m.text)
    return "\n".join(out) + "\n"


def render(msgs, fmt="ass", lines=5, hold=15.0, start=0.0, end=None,
           offset=0.0, **style):
    """The subtitle file's text in `fmt`, ready to write (UTF-8)."""
    if fmt == "txt":
        return to_txt(msgs, start, end, offset, **style)
    cues = windows(msgs, lines, hold, start, end, offset)
    return {"ass": to_ass, "srt": to_srt, "vtt": to_vtt}[fmt](cues, **style)


NOTE_NAME = ".musictool-chat.json"


def _note(folder):
    try:
        with open(os.path.join(folder, NOTE_NAME), encoding="utf-8") as fh:
            return set(json.load(fh))
    except (OSError, ValueError, TypeError):
        return set()


def remember_written(path):
    """List a subtitle file we wrote in the folder's .musictool-chat.json, so
    a later run (other settings) may replace it -- and nothing else."""
    folder = os.path.dirname(os.path.abspath(path))
    names = _note(folder) | {os.path.basename(path)}
    tmp = os.path.join(folder, NOTE_NAME + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(sorted(names), fh, ensure_ascii=False, indent=0)
        os.replace(tmp, os.path.join(folder, NOTE_NAME))
    except OSError:
        pass


def is_ours(path):
    """Did musictool write this subtitle file? (Only those are replaced.)
    ASS and VTT carry a comment saying so; SRT cannot, so the folder's note
    is checked as well."""
    if os.path.basename(path) in _note(os.path.dirname(os.path.abspath(path))):
        return True
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return MARK in fh.read(600)
    except OSError:
        return False


# --------------------------------------------------------------------------
# names, parts and finding the video

PART_RE = re.compile(r"\((?:from )?(\d+h\d\dm\d\ds)(?:-(\d+h\d\dm\d\ds|end))?\)\s*$")
VIDEO_EXTS = (".mp4", ".mkv", ".webm", ".mov", ".m4v")


def part_from_name(path):
    """'TITLE (from 1h00m00s).mp4' -> (3600.0, None); '(1h00m00s-2h30m00s)' ->
    (3600.0, 9000.0); a name without a part -> None."""
    from .util import parse_time
    base = os.path.splitext(os.path.basename(path))[0]
    m = PART_RE.search(base)
    if not m:
        return None
    a = parse_time(m.group(1))
    b = parse_time(m.group(2)) if m.group(2) and m.group(2) != "end" else None
    return (a, b)


def find_videos(folder, video_id):
    """Videos in `folder` that are this stream: '[ID]' in the name, or listed
    under this id in musictool's .musictool-index.json. Parts included."""
    from .download import load_index
    if not folder or not os.path.isdir(folder):
        return []
    idx = load_index(folder)
    out = []
    for name in sorted(os.listdir(folder)):
        p = os.path.join(folder, name)
        if not name.lower().endswith(VIDEO_EXTS) or not os.path.isfile(p):
            continue
        if name.endswith((".part", ".ytdl")) or ".part-Frag" in name:
            continue
        if f"[{video_id}]" in name or (idx.get(name) or {}).get("video_id") == video_id:
            out.append(p)
    return out


def find_json(folder, video_id):
    """A chat replay of this video already downloaded into `folder`."""
    if not folder or not os.path.isdir(folder):
        return None
    for name in sorted(os.listdir(folder)):
        if name.endswith(".live_chat.json") and (
                f"[{video_id}]" in name or name.startswith(video_id + ".")):
            return os.path.join(folder, name)
    return None


def sub_path(video_or_base, fmt):
    """Where the subtitle goes: next to the video with the same name, so the
    player loads it by itself -- unless a subtitle we did not write is
    already there; then NAME.chat.EXT."""
    base = os.path.splitext(video_or_base)[0] if video_or_base.lower().endswith(
        VIDEO_EXTS) else video_or_base
    p = f"{base}.{fmt}"
    if os.path.exists(p) and not is_ours(p):
        p = f"{base}.chat.{fmt}"
    return p


def write(path, text):
    tmp = path + ".tmp"
    # SRT gets a byte-order mark: older Windows players read it as ANSI
    # otherwise, and every emoji and accent turns into garbage
    with open(tmp, "w", encoding="utf-8-sig" if path.endswith(".srt")
              else "utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, path)
    remember_written(path)


# --------------------------------------------------------------------------
# getting the replay, and the whole job

ID_RE = re.compile(r"(?:[?&]v=|youtu\.be/|/live/|/shorts/|/embed/)([\w-]{11})")


def video_id(text):
    """The 11-character YouTube id in a link (or the id itself), else None."""
    m = ID_RE.search(text or "")
    if m:
        return m.group(1)
    t = (text or "").strip()
    return t if re.fullmatch(r"[\w-]{11}", t) else None


def json_id(path):
    """The video id in a replay file's name: 'TITLE [ID].live_chat.json'."""
    m = re.search(r"\[([\w-]{11})\]\.live_chat\.json$", os.path.basename(path))
    if m:
        return m.group(1)
    m = re.match(r"([\w-]{11})\.live_chat\.json$", os.path.basename(path))
    return m.group(1) if m else None


def fetch(url, folder, o, log=print):
    """Download the chat replay of `url` into `folder` (only the chat -- no
    video). One already there is used instead. -> (path or None, error)."""
    import subprocess
    from . import cancel
    from .download import cookie_args, runtime_args
    from .util import ytdlp_cmd
    vid = video_id(url)
    have = find_json(folder, vid) if vid else None
    if have:
        log(f"chat replay already here, not downloading it again: "
            f"{os.path.basename(have)}")
        return have, None
    os.makedirs(folder, exist_ok=True)
    cmd = (ytdlp_cmd() + cookie_args(o, log) + runtime_args(o)
           + (["--proxy", o["proxy"]] if o.get("proxy") else [])
           + ["--skip-download", "--write-subs", "--sub-langs", "live_chat",
              "--no-playlist", "--newline", "--print", "after_move:%(id)s",
              "-o", os.path.join(folder, "%(title)s [%(id)s].%(ext)s"), url])
    log("downloading the chat replay (text only, a few MB for a long stream)")
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace")
    except OSError as e:
        return None, f"could not run yt-dlp: {e}"
    errors, got_id = [], vid
    for line in proc.stdout:
        line = line.rstrip()
        if cancel.requested():
            cancel.stop_process(proc)
            return None, cancel.CANCELLED
        if re.fullmatch(r"[\w-]{11}", line):
            got_id = line
        elif line.startswith("ERROR:"):
            errors.append(line[6:].strip())
            log("!! " + line[6:].strip())
        elif line.startswith("WARNING:") or "live chat" in line.lower() or \
                line.startswith("[info]"):
            log("   " + line)
    rc = proc.wait()
    path = find_json(folder, got_id) if got_id else None
    if path:
        return path, None
    if errors:
        return None, errors[-1]
    return None, ("this video has no chat replay (it was not a live stream, "
                  "or the streamer turned the replay off)"
                  if rc == 0 else f"yt-dlp stopped (exit code {rc})")


def _ffmpeg():
    from .live import ffmpeg_exe
    return ffmpeg_exe()


def _streams(path):
    """[(index, kind, title)] of a media file, read from `ffmpeg -i`."""
    import subprocess
    exe = _ffmpeg()
    try:
        err = subprocess.run([exe, "-hide_banner", "-i", path], capture_output=True,
                             text=True, encoding="utf-8", errors="replace",
                             timeout=60).stderr
    except Exception:                                        # noqa: BLE001
        return []
    out, cur = [], None
    for line in err.splitlines():
        m = re.match(r"\s+Stream #0:(\d+)\S*: (\w+):", line)
        if m:
            kind = m.group(2).lower()
            if "(attached pic)" in line:
                kind = "cover"           # the thumbnail yt-dlp embeds
            cur = [int(m.group(1)), kind, ""]
            out.append(cur)
            continue
        m = re.match(r"\s+title\s*:\s*(.*)$", line)
        if m and cur is not None and not cur[2]:
            cur[2] = m.group(1).strip()
    return [tuple(s) for s in out]


TRACK_TITLE = "Live chat"


def first_times(path):
    """(earliest packet, first audio packet) of a video, in seconds; either
    None when unknown. A part cut from the middle of a video starts at the
    keyframe before the cut: in an MP4 those extra seconds sit below zero and
    are skipped by the player, but an MKV starts the picture there and the
    sound a few seconds in. The chat belongs to the sound's start."""
    import subprocess
    exe = _ffmpeg()
    if not exe or not os.path.exists(path):
        return None, None
    try:
        proc = subprocess.Popen(
            [exe, "-v", "error", "-nostdin", "-i", path, "-map", "0:V?",
             "-map", "0:a?", "-c", "copy", "-f", "framemd5", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            errors="replace")
    except OSError:
        return None, None
    tb, first, kinds = {}, {}, {}
    try:
        for n, line in enumerate(proc.stdout):
            m = re.match(r"#tb (\d+): (\d+)/(\d+)", line)
            if m:
                tb[int(m.group(1))] = int(m.group(2)) / int(m.group(3))
                continue
            m = re.match(r"#media_type (\d+): (\w+)", line)
            if m:
                kinds[int(m.group(1))] = m.group(2)
                continue
            m = re.match(r"\s*(\d+),\s*(-?\d+),\s*(-?\d+),", line)
            if m and int(m.group(1)) not in first:
                i = int(m.group(1))
                first[i] = int(m.group(3)) * tb.get(i, 0.001)
            if (tb and len(first) >= len(tb)) or n > 2000:
                break
    finally:
        try:
            proc.kill()
            proc.wait(timeout=5)
        except Exception:                                    # noqa: BLE001
            pass
    if not first:
        return None, None
    audio = [t for i, t in first.items() if kinds.get(i) == "audio"]
    return min(first.values()), (audio[0] if audio else None)


def embed(video, sub, log=print):
    """Copy `video` with the chat added as a subtitle track. Nothing is
    re-encoded. An .mkv is replaced by the new one (a chat track put in by an
    earlier run is swapped out, not doubled); anything else gives a new
    NAME.mkv next to it and the original stays -- MP4 cannot hold an ASS
    track, and its own subtitle type loses the placement. -> (path, error)."""
    import shutil
    import subprocess
    import time
    exe = _ffmpeg()
    if not exe:
        return None, "ffmpeg is needed to add the chat to the video"
    base, ext = os.path.splitext(video)
    in_place = ext.lower() == ".mkv"
    out = video if in_place else base + ".mkv"
    if not in_place and os.path.exists(out):
        out = base + " (chat).mkv"
    tmp = base + ".chat-tmp.mkv"
    need = os.path.getsize(video) + os.path.getsize(sub) + 50 * 2 ** 20
    try:
        free = shutil.disk_usage(os.path.dirname(os.path.abspath(video))).free
    except OSError:
        free = None
    if free is not None and free < need:
        return None, (f"not enough free space to copy the video with the chat "
                      f"({need / 2**30:.1f} GB needed, {free / 2**30:.1f} GB "
                      f"free). The subtitle file next to it works without this.")
    streams = _streams(video)
    # nothing below zero in an MKV: shift the video AND the chat by the same
    # amount, or the muxer moves only the video (and only from some packet on)
    low, _snd = first_times(video)
    shift = -low if low is not None and low < 0 else 0.0
    # everything is copied except: a chat track an earlier run put in (it is
    # replaced), and an MP4's cover picture -- an .mkv would make it a second
    # video track, which some players then show instead of the video
    keep = [s for s in streams if not (s[1] == "subtitle" and s[2] == TRACK_TITLE)
            and s[1] != "cover"]
    sh = ["-itsoffset", f"{shift:.6f}"] if shift else []
    cmd = [exe, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
           *sh, "-i", video, *sh, "-i", sub]
    if keep:
        for s in keep:
            cmd += ["-map", f"0:{s[0]}"]
    else:                                # could not read it: take it all
        cmd += ["-map", "0"]
    n = len(keep) if keep else len(streams)   # the new track's output index
    cmd += ["-map", "1", "-c", "copy", f"-metadata:s:{n}", f"title={TRACK_TITLE}",
            f"-metadata:s:{n}", "language=und", f"-disposition:{n}", "default",
            tmp]
    log(f"adding the chat to {os.path.basename(out)} (copying, not "
        f"re-encoding; {os.path.getsize(video) / 2**30:.2f} GB)")
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if r.returncode != 0 or not os.path.exists(tmp):
        try:
            os.remove(tmp)
        except OSError:
            pass
        return None, "ffmpeg: " + ((r.stderr or "").strip().splitlines() or
                                   ["failed"])[-1][:300]
    for attempt in range(10):            # Windows: a player may hold it a moment
        try:
            os.replace(tmp, out)
            break
        except PermissionError:
            if attempt == 9:
                return None, (f"could not replace {out} -- is it open in a "
                              f"player? The new copy is {tmp}")
            time.sleep(0.5)
    return out, None


def run(src, folder, o, style, log=print):
    """The `chat` job. `src` is a link, a video id or a .live_chat.json file.
    `style`: fmt, lines, hold, side, place, width, wrap, pad, names, offset,
    range (start, end|None) or None for "from the video's name", video (a
    file), embed. Writes a subtitle per matching video. -> exit code."""
    from . import cancel
    from .util import part_label, clock
    if src.lower().endswith(".json") and os.path.isfile(src):
        path, vid = os.path.abspath(src), json_id(src)
        folder = folder or os.path.dirname(path)
    else:
        vid = video_id(src)
        if not vid:
            log(f"!! not a YouTube link or a .live_chat.json file: {src}")
            return 2
        path, err = fetch(src, folder, o, log)
        if err:
            log("!! " + err)
            return 130 if err == cancel.CANCELLED else 1
    msgs = load(path)
    if not msgs:
        log(f"!! no chat messages in {os.path.basename(path)}")
        return 1
    log(f"{len(msgs)} messages, {clock(msgs[-1].t)} of chat")
    fmt = style.get("fmt") or "ass"
    if style.get("video"):
        videos = [style["video"]]
    else:
        videos = find_videos(folder, vid) if vid else []
    # 'NAME.mp4' and the 'NAME.mkv' an earlier --embed made of it share one
    # subtitle name: take the .mkv (it is the one with the chat track)
    by_base = {}
    for v in videos:
        b_, e_ = os.path.splitext(v)
        if b_ not in by_base or e_.lower() == ".mkv":
            by_base[b_] = v
    targets = []
    for v in by_base.values():
        a, b = style.get("range") or part_from_name(v) or (0.0, None)
        if b is None:                    # no cues past the end of the file
            from .live import media_seconds
            secs = media_seconds(v)
            if secs:
                b = a + secs + 1
        targets.append((v, (a, b)))
    if not targets:
        rng = style.get("range") or (0.0, None)
        base = path[:-len(".live_chat.json")] if path.endswith(".live_chat.json") \
            else os.path.splitext(path)[0]
        if rng != (0.0, None):
            base += f" ({part_label(*rng)})"
        targets.append((base, rng))
        log("no downloaded video of it found in the folder -- the subtitle "
            "is saved next to the chat file; give it the video's name to "
            "have the player load it")
    keys = ("side", "place", "width", "wrap", "pad", "names", "font_size", "box")
    look = {k: style[k] for k in keys if style.get(k) is not None}
    rc = 0
    for v, (a, b) in targets:
        title = os.path.splitext(os.path.basename(v))[0]
        off = float(style.get("offset") or 0)
        if v.lower().endswith(VIDEO_EXTS):
            # the chat starts with the sound (see first_times)
            _low, snd = first_times(v)
            if snd and snd > 0.05:
                off += snd
                log(f"   (the picture starts {snd:.1f} s before the sound in "
                    f"this file -- the chat is timed to the sound)")
        text = render(msgs, fmt, lines=int(style.get("lines") or 5),
                      hold=float(style.get("hold", 15) or 0), start=a, end=b,
                      offset=off, title=title, **look)
        out = sub_path(v, fmt)
        write(out, text)
        part = f" (chat from {clock(a)})" if a else ""
        log(f"wrote {os.path.basename(out)}{part}")
        if style.get("embed") and v.lower().endswith(VIDEO_EXTS):
            if fmt == "txt":
                log("   (a text log cannot be a subtitle track -- not added)")
                continue
            made, err = embed(v, out, log)
            if err:
                log("!! " + err)
                rc = 1
                continue
            try:
                os.remove(out)           # it is in the video now; two copies
            except OSError:              # would show twice in the player
                pass
            log(f"chat track added: {os.path.basename(made)}")
            if made != v:
                log(f"   the original {os.path.basename(v)} is kept; delete "
                    f"it if the .mkv plays well for you")
    return rc
