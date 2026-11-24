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

Names and messages are in colour (colour_of), outlined with no box, with a
little space between messages. ASS, WebVTT and the text log also carry every
message and the settings, so `parse_own` reads such a file back exactly and
`run` can remake it -- another format, other settings -- without the JSON;
an SRT (or a file from before) is rebuilt from what it shows when.
"""

import html
import json
import os
import re
import zlib

from . import cancel


FORMATS = ("ass", "srt", "vtt", "txt")
SIDES = ("right", "left", "center")
PLACES = ("bottom", "middle", "top")
MARK = "musictool chat"          # how a subtitle file we wrote is recognised

# Author colours (ASS is &HBBGGRR&, the others #RRGGBB): YouTube's own for
# the channel owner, moderators and members; a Super Chat takes the colour
# YouTube gave it (its tier), "paid" is only the fallback
COLOURS = {"owner": "FFD600", "moderator": "5E84F1", "member": "2BA640",
           "verified": "AAAAAA", "paid": "FFCA28", "": "B0B0B0"}
# everyone else, with --colours name: a colour of their own, the same one
# every time (from the name). Light enough to read over a dark picture with an
# outline, and away from the owner's yellow, the moderators' blue and the
# members' green so those still stand out.
PALETTE = ("FF7A7A", "FF9E40", "C792EA", "F78FB3", "4DD0E1", "80CBC4",
           "FFB3A7", "D7A86E", "B39DDB", "FF80AB", "9FE2BF", "E6A8D7")
COLOUR_MODES = ("name", "role", "off")
TEXT = "FFFFFF"
ROLE_OF = {v: k for k, v in COLOURS.items() if k not in ("", "paid")}
OWN_EXTS = (".ass", ".ssa", ".srt", ".vtt", ".txt")
RANGE_SLACK = 15.0                # s: how far a part's name may be from --range
DATA = "[MusicTool Chat]"         # the ASS section with every message, as JSON

# what `chat` does when nothing says otherwise; a file musictool made keeps
# its own settings (see read_own), and what you type wins over both
DEFAULT_STYLE = {"fmt": "ass", "lines": 5, "hold": 15.0, "side": "right",
                 "place": "bottom", "width": 32, "wrap": 40, "pad": 0,
                 "names": True, "box": False, "gap": 0.4, "colours": "name",
                 "offset": 0.0, "font_size": 0}
LOOK = ("side", "place", "width", "wrap", "pad", "names", "font_size", "box",
        "gap", "colours")


# --------------------------------------------------------------------------
# reading the replay

class Message:
    __slots__ = ("t", "author", "text", "role", "paid", "id", "colour")

    def __init__(self, t, author, text, role="", paid="", id="", colour=""):
        self.t = t                    # seconds into the video
        self.author = author
        self.text = text
        self.role = role              # owner / moderator / member / verified / ""
        self.paid = paid              # "$5.00" for a Super Chat / Sticker
        self.id = id
        self.colour = colour          # RRGGBB YouTube gave a Super Chat

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


def _argb(v):
    """YouTube's colours are ARGB numbers: 4280150454 -> '1DE9B6'."""
    try:
        return f"{int(v) & 0xFFFFFF:06X}"
    except (TypeError, ValueError):
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
                       _runs(item.get("purchaseAmountText")) or "$", mid,
                       _argb(item.get("bodyBackgroundColor")))
    if kind == "liveChatPaidStickerRenderer":           # Super Sticker
        return Message(t, author, "(sticker)", _role(item),
                       _runs(item.get("purchaseAmountText")) or "$", mid,
                       _argb(item.get("backgroundColor")
                             or item.get("moneyChipBackgroundColor")))
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


def _mix(rgb, frac):
    """`rgb` moved `frac` of the way to white: a lighter tint of it."""
    c = [int(rgb[i:i + 2], 16) for i in (0, 2, 4)]
    return "".join(f"{int(round(v + (255 - v) * frac)):02X}" for v in c)


def colour_of(m, mode="name"):
    """-> (name colour, message colour), RRGGBB.

    name: the owner, moderators and members in YouTube's colours, a Super
          Chat in the colour YouTube gave it, everyone else a colour of
          their own (the same each time, from the name); the message in a
          light tint of it, so you can tell whose line is whose.
    role: only the owner, moderators, members and Super Chats coloured;
          everyone else grey names, white text.
    off:  no colour, white text (names still bold)."""
    if mode == "off":
        return TEXT, TEXT
    if m.paid:
        c = m.colour or COLOURS["paid"]
        return c, _mix(c, 0.35)
    if m.role in COLOURS and m.role:
        c = COLOURS[m.role]
        return c, (_mix(c, 0.6) if mode == "name" else TEXT)
    if mode == "role":
        return COLOURS[""], TEXT
    c = PALETTE[zlib.crc32(m.author.encode("utf-8")) % len(PALETTE)]
    return c, _mix(c, 0.6)


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


# --------------------------------------------------------------------------
# the messages themselves, kept inside the file so it can be read back
# (another format, other settings) without the .live_chat.json

DATA_NOTE = "NOTE musictool chat data"     # the same, in a WebVTT comment


def _json_line(d):
    # one line, whatever is in the message: JSON escapes the control
    # characters; these three are line breaks to some readers too, and ">"
    # keeps "-->" (which ends a WebVTT comment) out of it
    s = json.dumps(d, ensure_ascii=False, separators=(",", ":"))
    for ch in ("\u2028", "\u2029", "\x85", ">"):
        s = s.replace(ch, f"\\u{ord(ch):04x}")
    return s


def _pack(m):
    d = {"t": round(m.t, 3), "a": m.author, "x": m.text}
    if m.role:
        d["r"] = m.role
    if m.paid:
        d["p"] = m.paid
    if m.colour:
        d["c"] = m.colour
    return _json_line(d)


def _unpack(s):
    d = json.loads(s)
    role = d.get("r") or ""
    colour = str(d.get("c") or "").upper()
    return Message(float(d["t"]), str(d.get("a") or ""), str(d.get("x") or ""),
                   role if role in COLOURS else "", str(d.get("p") or ""), "",
                   colour if re.fullmatch(r"[0-9A-F]{6}", colour) else "")


def _kept(msgs, lines, hold, start, end):
    """The messages a file for [start, end) needs: those in it, and the few
    still on screen when it starts."""
    hold = float(hold or 0)
    before = [m for m in msgs if m.t < start and (hold <= 0 or m.t + hold > start)]
    return before[-max(1, int(lines)):] + [
        m for m in msgs if m.t >= start and (end is None or m.t < end)]


def _data(msgs, settings):
    return ([f"Settings: {_json_line(settings)}"]
            + [f"Message: {_pack(m)}" for m in msgs])


# --------------------------------------------------------------------------
# writing

def to_ass(cues, side="right", place="bottom", width=32, font_size=0,
           names=True, title="", res=(1920, 1080), box=False, gap=0.4,
           colours="name", data=(), **_):
    """ASS: a column `width`% of the screen wide on the chosen side; the
    player wraps long messages inside it. Names and messages in colour (see
    colour_of), outlined with a soft shadow so they read on any picture --
    or on a see-through dark box (`box`). `gap`: space between messages, in
    lines (0.4 = a little)."""
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
    al = _ass_align(side, place)
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
        # leaves the picture faintly visible through it. BorderStyle 1 = no
        # box: a black outline and a half-see-through shadow
        (f"Style: Chat,Arial,{fs},&H00FFFFFF,&H00FFFFFF,&H60000000,&H60000000,"
         f"0,0,0,0,100,100,0,0,3,{max(2, fs // 10)},0,{al},{ml},{mr},{mv},1")
        if box else
        (f"Style: Chat,Arial,{fs},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,"
         f"0,0,0,0,100,100,0,0,1,{max(2, round(fs / 14))},"
         f"{max(1, round(fs / 30))},{al},{ml},{mr},{mv},1"),
        "",
    ]
    if data:
        # players skip sections they do not know; this is what
        # `musictool chat FILE.ass` reads back (edit or delete Message lines
        # here, not the Dialogue lines below)
        head += [DATA] + list(data) + [""]
    head += [
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text",
    ]
    g = int(round(fs * float(gap or 0)))
    # a blank line `g` pixels high between two messages
    sep = f"\\N{{\\fs{g}}}\\h\\N{{\\fs{fs}}}" if g >= 1 else "\\N"
    body = []
    for a, b, shown in cues:
        parts = []
        for m in shown:
            who = _label(m, names)
            cn, ct = colour_of(m, colours)
            parts.append((f"{{\\b1\\c{_bgr(cn)}}}{_ass_text(who)}{{\\b0\\c{_bgr(ct)}}}"
                          if who else f"{{\\c{_bgr(ct)}}}") + _ass_text(m.text))
        body.append(f"Dialogue: 0,{_ts_ass(a)},{_ts_ass(b)},Chat,,0,0,0,,"
                    + sep.join(parts))
    return "\n".join(head + body) + "\n"


def _plain_lines(shown, wrap, names, esc, gap=False):
    """[(message or None, name part, rest)] per screen line: the name part
    is only on a message's first line. With `gap`, a ("gap", "", "") line
    between messages."""
    out = []
    for k, m in enumerate(shown):
        if gap and k:
            out.append(("gap", "", ""))
        who = _label(m, names)
        for i, ln in enumerate(_wrap(who + m.text.replace("\n", " "), wrap)):
            if i == 0:
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


GAP_ROW = "\u00a0"        # SRT/WebVTT: a line that looks empty (a real empty
                          # line would end the cue)


def to_srt(cues, side="right", place="bottom", wrap=40, names=True, pad=0,
           colours="name", gap=0.4, **_):
    """SRT: {\\anN} puts it on the chosen side (VLC, mpv, MPC-HC, PotPlayer
    and most players honour it); names bold, names and messages in colour.
    SRT cannot make a small space: `gap` 1 or more puts an empty-looking
    line between messages. `pad` adds the no-break-space fallback."""
    an = {("left", "bottom"): 1, ("center", "bottom"): 2, ("right", "bottom"): 3,
          ("left", "middle"): 4, ("center", "middle"): 5, ("right", "middle"): 6,
          ("left", "top"): 7, ("center", "top"): 8, ("right", "top"): 9}[(side, place)]
    tag = "" if an == 2 else f"{{\\an{an}}}"

    def esc(s):
        return s.replace("<", "\u2039").replace(">", "\u203a").replace("{", "(") \
                .replace("}", ")")
    out = []
    for n, (a, b, shown) in enumerate(cues, start=1):
        rows, cur = [], None
        for m, who, rest in _plain_lines(shown, wrap, names, esc,
                                         float(gap or 0) >= 1):
            if m == "gap":
                rows.append(GAP_ROW)
                continue
            if m is not None:
                cur = m
            cn, ct = colour_of(cur, colours) if cur is not None else (TEXT, TEXT)
            row = (f'<font color="#{cn}"><b>{who}</b></font>' if who else "")
            if rest:
                row += (f'<font color="#{ct}">{rest}</font>' if ct != TEXT
                        else rest)
            rows.append(row)
        out.append(f"{n}\n{_ts_srt(a)} --> {_ts_srt(b)}\n{tag}"
                   + "\n".join(_pad(rows, pad)) + "\n")
    return "\n".join(out)


def to_vtt(cues, side="right", place="bottom", width=32, wrap=40, names=True,
           pad=0, colours="name", gap=0.4, box=False, data=(), **_):
    """WebVTT with cue settings for the side and height, and a STYLE block
    for the colours. Browsers and VLC honour them; some players show every
    cue centred at the bottom, in white."""
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
    used, body = set(), []
    for a, b, shown in cues:
        rows, cur = [], None
        for m, who, rest in _plain_lines(shown, wrap, names, esc,
                                         float(gap or 0) >= 1):
            if m == "gap":
                rows.append(GAP_ROW)
                continue
            if m is not None:
                cur = m
            cn, ct = colour_of(cur, colours) if cur is not None else (TEXT, TEXT)
            row = ""
            if who:
                used.add(cn)
                row = f"<c.c{cn}><b>{who}</b></c>"
            if rest:
                if ct != TEXT:
                    used.add(ct)
                    row += f"<c.c{ct}>{rest}</c>"
                else:
                    row += rest
            rows.append(row)
        body.append(f"{_ts_srt(a, '.')} --> {_ts_srt(b, '.')} {pos}{line}".rstrip())
        body.append("\n".join(_pad(rows, pad)))
        body.append("")
    out = ["WEBVTT", "", f"NOTE {MARK}", ""]
    style = ([] if box else
             ["::cue { background-color: transparent; text-shadow: 0 0 3px #000,"
              " 0 0 3px #000, 1px 1px 3px #000; }"])
    style += [f"::cue(.c{c}) {{ color: #{c}; }}" for c in sorted(used)]
    if style:
        out += ["STYLE"] + style + [""]
    out += body
    if data:
        out += [DATA_NOTE] + list(data) + [""]
    return "\n".join(out)


def _clock_ms(t):
    ms = int(round(t * 1000))
    s = ms // 1000
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}.{ms % 1000:03d}"


def to_txt(msgs, start=0.0, end=None, offset=0.0, names=True, settings=None,
           **_):
    """A readable log: [h:mm:ss.mmm] name: message, in the clip's time. The
    header keeps what the lines cannot show (settings, who is a moderator
    ...), so the log can be turned back into subtitles -- edit or delete
    lines first if you like."""
    out = [f"# {MARK}: live chat replay"]
    sel = [m for m in msgs if m.t >= start and (end is None or m.t < end)]
    if settings is not None:
        out.append(f"# settings: {_json_line(settings)}")
        extra = {}
        for m in sel:
            if m.role and names:
                extra.setdefault("roles", {})[m.author] = m.role
            if m.colour:
                extra.setdefault("colours", {})[_clock_ms(
                    max(0.0, m.t - start + offset))] = m.colour
        for k, v in extra.items():
            out.append(f"# {k}: {_json_line(v)}")
    for m in sel:
        t = max(0.0, m.t - start + offset)
        out.append(f"[{_clock_ms(t)}] " + _label(m, names)
                   + m.text.replace("\n", " "))
    return "\n".join(out) + "\n"


def render(msgs, fmt="ass", lines=5, hold=15.0, start=0.0, end=None,
           offset=0.0, sound=0.0, video_id="", **style):
    """The subtitle file's text in `fmt`, ready to write (UTF-8). `offset`
    is your shift, `sound` the file's own (see first_times); both move the
    chat later. ASS, WebVTT and the text log carry the messages and these
    settings, for read_own."""
    look = {k: v for k, v in style.items() if k in LOOK}
    settings = {"v": 1, "id": video_id or "", "lines": int(lines),
                "hold": float(hold or 0), "start": start, "end": end,
                "offset": float(offset or 0), "sound": float(sound or 0), **look}
    total = float(offset or 0) + float(sound or 0)
    if fmt == "txt":
        return to_txt(msgs, start, end, total, settings=settings, **style)
    data = (_data(_kept(msgs, lines, hold, start, end), settings)
            if fmt in ("ass", "vtt") else ())
    cues = windows(msgs, lines, hold, start, end, total)
    return {"ass": to_ass, "srt": to_srt, "vtt": to_vtt}[fmt](
        cues, data=data, **style)


# --------------------------------------------------------------------------
# reading a subtitle file musictool made back into messages

def _parse_label(who):
    """'[$5.00] name: ' -> (paid, author)."""
    paid = ""
    m = re.match(r"\[([^\]]*)\]\s*", who)
    if m and (re.search(r"\d", m.group(1)) or m.group(1) == "$"):
        paid, who = m.group(1), who[m.end():]
    return paid, who.rstrip().rstrip(":").rstrip()


def _from_colour(rgb, paid):
    rgb = (rgb or "").upper()
    if paid:
        return "", ("" if rgb in ("", COLOURS["paid"]) else rgb)
    return ROLE_OF.get(rgb, ""), ""


def _ass_cues(text):
    out = []
    for ln in text.split("\n"):
        if not ln.startswith("Dialogue:"):
            continue
        f = ln[len("Dialogue:"):].split(",", 9)
        if len(f) < 10:
            continue
        try:
            a, b = (parse_clock(x) for x in f[1:3])
        except ValueError:
            continue
        shown = []
        for part in f[9].split("\\N"):
            part = re.sub(r"\{\\fs\d+\}", "", part)    # the spacing's size
            plain = re.sub(r"\{[^}]*\}", "", part).replace("\\h", "").strip()
            if not plain:
                continue                     # the space between two messages
            m = re.match(r"\{[^}]*\\b1[^}]*?(?:\\c&H([0-9A-Fa-f]{6})&)?[^}]*\}"
                         r"(.*?)\{[^}]*\\b0[^}]*\}(.*)$", part)
            if m:
                bgr, who, rest = m.group(1) or "", m.group(2), m.group(3)
            else:
                bgr, who, rest = "", "", part
            rgb = bgr[4:6] + bgr[2:4] + bgr[0:2] if bgr else ""

            def un(s):
                s = re.sub(r"\{[^}]*\}", "", s).replace("\\h", " ")
                return (s.replace("\\\u2060", "\\").replace("\uff5b", "{")
                        .replace("\uff5d", "}"))
            paid, author = _parse_label(un(who))
            role, colour = _from_colour(rgb, paid)
            shown.append(Message(0.0, author, un(rest).strip(), role, paid, "",
                                 colour))
        out.append((a, b, shown))
    return out


def _rows_cues(text, vtt):
    """SRT / WebVTT cues -> [(t0, t1, [Message])], from the markup we write:
    a new message at a bold name or after a spacing line."""
    out = []
    for block in re.split(r"\n\s*\n", text):
        rows = block.split("\n")
        k = next((i for i, r in enumerate(rows) if "-->" in r), None)
        if k is None:
            continue
        ta, _, tb = rows[k].partition("-->")
        try:
            a, b = parse_clock(ta.strip()), parse_clock(tb.strip().split(" ")[0])
        except ValueError:
            continue
        shown, cur, fresh = [], None, True
        for r in rows[k + 1:]:
            r = re.sub(r"^\{\\an\d\}", "", r)
            m = re.match(r"^[\u00a0 ]*(?:<c\.c([0-9A-Fa-f]{6})>|"
                         r"<font color=\"#([0-9A-Fa-f]{6})\">)?<b>(.*?)</b>"
                         r"(?:</c>|</font>)?(.*)$", r)

            def un(s):
                s = re.sub(r"<[^>]*>", "", s)
                s = html.unescape(s) if vtt else (s.replace("\u2039", "<")
                                                  .replace("\u203a", ">"))
                return s.strip("\u00a0 ")
            if not un(r):
                fresh = True                 # the space between two messages
                continue
            if m:
                paid, author = _parse_label(un(m.group(3)))
                role, colour = _from_colour(m.group(1) or m.group(2), paid)
                cur = Message(0.0, author, un(m.group(4)), role, paid, "", colour)
                shown.append(cur)
            elif cur is None or fresh:
                cur = Message(0.0, "", un(r))
                shown.append(cur)
            else:
                cur.text = f"{cur.text} {un(r)}".strip()
            fresh = False
        out.append((a, b, shown))
    return out


def _rebuild(cues):
    """Messages from cues that each show the last few: a message is new in a
    cue unless the cue just before (and touching it) ended with it."""
    out, prev, prev_end = [], [], None
    for a, b, shown in cues:
        keys = [(m.author, m.text, m.paid) for m in shown]
        last = prev if prev_end is not None and a <= prev_end + 0.002 else []
        j = min(len(last), len(keys))
        while j and last[-j:] != keys[:j]:
            j -= 1
        for m in shown[j:]:
            m.t = a
            out.append(m)
        prev, prev_end = keys, b
    return out


def parse_clock(s):
    """'1:02:03.45' / '01:02:03,450' / '02:03.450' -> seconds."""
    p = s.strip().replace(",", ".").split(":")
    if not 2 <= len(p) <= 3 or not all(p):
        raise ValueError(s)
    v = 0.0
    for x in p:
        v = v * 60 + float(x)
    return v


def _read_data(text):
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        if ln.strip() in (DATA, DATA_NOTE):
            vtt = ln.strip() == DATA_NOTE
            break
    else:
        return None, None
    meta, msgs = {}, []
    for ln in lines[i + 1:]:
        if ln.startswith("[") or (vtt and not ln.strip()):
            break
        key, _, val = ln.partition(": ")
        try:
            if key == "Settings":
                meta = json.loads(val)
            elif key == "Message":
                msgs.append(_unpack(val))
        except (ValueError, KeyError, TypeError):
            continue
    msgs.sort(key=lambda m: m.t)
    return msgs, (meta if isinstance(meta, dict) else {})


def _read_txt(text):
    meta, extra, msgs = {}, {}, []
    for ln in text.split("\n"):
        if ln.startswith("#"):
            key, _, val = ln[1:].strip().partition(": ")
            if key in ("settings", "roles", "colours"):
                try:
                    (extra.__setitem__(key, json.loads(val)) if key != "settings"
                     else meta.update(json.loads(val)))
                except (ValueError, TypeError, AttributeError):
                    pass
            continue
        m = re.match(r"\[(\d+:\d\d:\d\d(?:\.\d+)?)\] ?(.*)$", ln)
        if not m:
            continue
        t, rest = parse_clock(m.group(1)), m.group(2)
        paid, author, body = "", "", rest
        pm = re.match(r"\[([^\]]*)\] ", rest)
        if pm and (re.search(r"\d", pm.group(1)) or pm.group(1) == "$"):
            paid, body = pm.group(1), rest[pm.end():]
        if meta.get("names", True) and ": " in body:
            author, _, body = body.partition(": ")
        role = (extra.get("roles") or {}).get(author, "")
        colour = str((extra.get("colours") or {}).get(m.group(1), ""))
        msgs.append(Message(t, author, body, role if role in COLOURS else "",
                            paid, "", colour if paid else ""))
    if meta:
        # back to the video's time: the log is in the clip's
        shift = float(meta.get("start") or 0) - float(meta.get("offset") or 0) \
            - float(meta.get("sound") or 0)
        for m in msgs:
            m.t = round(m.t + shift, 3)
    else:
        meta = {"clip": True}
    msgs.sort(key=lambda m: m.t)
    return msgs, meta


def parse_own(text, ext=""):
    """(messages, settings) from the text of a chat subtitle musictool
    wrote. Files from this version carry every message and the settings;
    older ones (and SRT) are rebuilt from what is on screen when -- then the
    settings say {"clip": True}: the times are the clip's, already shifted,
    and only `lines` is known."""
    text = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    ext = ext.lower().lstrip(".")
    if ext == "txt" or text.startswith(f"# {MARK}"):
        return _read_txt(text)
    msgs, meta = _read_data(text)
    if msgs is not None:
        return msgs, meta
    if "[Events]" in text or ext in ("ass", "ssa"):
        cues = _ass_cues(text)
    else:
        cues = _rows_cues(text, text.lstrip().startswith("WEBVTT"))
    meta = {"clip": True}
    if cues:
        meta["lines"] = max(len(s) for _a, _b, s in cues) or 1
    return _rebuild(cues), meta


def read_own(path):
    """parse_own for a file on disk."""
    with open(path, encoding="utf-8-sig", errors="replace") as fh:
        return parse_own(fh.read(), os.path.splitext(path)[1])


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
    """The video id in a replay file's name: 'TITLE [ID].live_chat.json', or
    a part's 'TITLE [ID] (1h00m00s-4h00m00s).live_chat.json' (get --range
    --subs saves the chat under the part's name)."""
    m = re.search(r"\[([\w-]{11})\](?: \([\w -]+\))?\.live_chat\.json$",
                  os.path.basename(path))
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
        proc = cancel.popen(cmd, stdout=subprocess.PIPE,
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
    cancel.done(proc)
    if cancel.requested():
        return None, cancel.CANCELLED
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
    exe = _ffmpeg()
    try:
        err = cancel.run([exe, "-hide_banner", "-i", path], capture_output=True,
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
        proc = cancel.popen(
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
        cancel.done(proc)
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
    r = cancel.run(cmd, capture_output=True, text=True, encoding="utf-8",
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


def extract_track(video):
    """The text of the chat track `embed` put in a video (titled "Live
    chat"), or None."""
    subs = [s for s in _streams(video) if s[1] == "subtitle"]
    pick = [s for s in subs if s[2] == TRACK_TITLE]
    if not pick:
        return None
    exe = _ffmpeg()
    for f in ("ass", "srt", "webvtt"):
        try:
            r = cancel.run([exe, "-hide_banner", "-loglevel", "error", "-i", video,
                            "-map", f"0:{pick[0][0]}", "-c", "copy", "-f", f,
                            "pipe:1"], capture_output=True, timeout=300)
        except Exception:                                    # noqa: BLE001
            return None
        if r.returncode == 0 and r.stdout:
            return r.stdout.decode("utf-8", "replace")
    return None


def _own_base(path):
    """'NAME.chat.ass' / 'NAME.ass' -> 'NAME' (where the video would be)."""
    base = os.path.splitext(path)[0]
    return base[:-len(".chat")] if base.endswith(".chat") else base


def _beside(path):
    """The video a chat subtitle belongs to: same name, .mkv first."""
    base = _own_base(path)
    for ext in (".mkv",) + VIDEO_EXTS:
        if os.path.isfile(base + ext):
            return base + ext
    return None


def source_kind(src):
    """'json' (a .live_chat.json), 'own' (a subtitle or log `chat` wrote),
    'video' (one with a chat track in it) or 'link'."""
    if not os.path.isfile(src):
        return "link"
    low = src.lower()
    if low.endswith(".json"):
        return "json"
    if low.endswith(VIDEO_EXTS):
        return "video"
    if low.endswith(OWN_EXTS):
        return "own"
    return "link"


def run(src, folder, o, style, log=print):
    """The `chat` job. `src` is a link, a video id, a .live_chat.json -- or
    a chat file this wrote before (.ass/.srt/.vtt/.txt, or a video with the
    chat track in it), to make it again in another format or with other
    settings. `style`: fmt, lines, hold, side, place, width, wrap, pad,
    names, box, gap, colours, offset, range (start, end|None), video (a
    file), embed; None or missing = the file's own setting, else
    DEFAULT_STYLE. Writes a subtitle per matching video. -> exit code."""
    from . import cancel
    from .util import part_label, clock
    kind = source_kind(src)
    meta, own_fmt = {}, None
    if kind == "json":
        path, vid = os.path.abspath(src), json_id(src)
        folder = folder or os.path.dirname(path)
        msgs = load(path)
    elif kind in ("own", "video"):
        path = os.path.abspath(src)
        folder = folder or os.path.dirname(path)
        if kind == "video":
            text = extract_track(path)
            if text is None:
                log(f"!! {os.path.basename(path)} has no \"{TRACK_TITLE}\" "
                    f"track (the one `chat --embed` adds)")
                return 1
            msgs, meta = parse_own(text, "ass" if "[Events]" in text else "")
        else:
            try:
                msgs, meta = read_own(path)
            except OSError as e:
                log(f"!! cannot read {path}: {e}")
                return 1
            own_fmt = {"ssa": "ass"}.get(path.lower().rsplit(".", 1)[-1],
                                         path.lower().rsplit(".", 1)[-1])
            if not is_ours(path):
                log(f"   ({os.path.basename(path)} does not say musictool made "
                    f"it -- reading it as a chat file anyway)")
        idm = re.search(r"\[([\w-]{11})\]", os.path.basename(path))
        vid = meta.get("id") or (idm.group(1) if idm else None)
        if meta.get("clip"):
            log("   (an older file, or SRT: messages rebuilt from what was on "
                "screen when; times are kept as they were)")
    else:
        vid = video_id(src)
        if not vid:
            log(f"!! not a YouTube link, a .live_chat.json or a chat file: {src}")
            return 2
        path, err = fetch(src, folder, o, log)
        if err:
            log("!! " + err)
            return 130 if err == cancel.CANCELLED else 1
        msgs = load(path)
    if not msgs:
        log(f"!! no chat messages in {os.path.basename(path)}")
        return 1
    log(f"{len(msgs)} message{'' if len(msgs) == 1 else 's'}, "
        f"{clock(msgs[-1].t)} of chat")
    # what you asked for, else what the file was made with, else the defaults
    eff = dict(DEFAULT_STYLE)
    eff.update({k: v for k, v in meta.items() if k in DEFAULT_STYLE and v is not None})
    if own_fmt:
        eff["fmt"] = own_fmt
    eff.update({k: v for k, v in style.items() if k in DEFAULT_STYLE and v is not None})
    fmt = eff["fmt"]
    clip = bool(meta.get("clip"))       # times already the clip's
    if style.get("video"):
        videos = [style["video"]]
    elif kind == "video":
        videos = [path]
    elif kind == "own":
        videos = [v for v in [_beside(path)] if v]
    else:
        videos = find_videos(folder, vid) if vid else []
    # 'NAME.mp4' and the 'NAME.mkv' an earlier --embed made of it share one
    # subtitle name: take the .mkv (it is the one with the chat track)
    by_base = {}
    for v in videos:
        b_, e_ = os.path.splitext(v)
        if b_ not in by_base or e_.lower() == ".mkv":
            by_base[b_] = v
    kept = None
    if kind in ("own", "video") and not clip and "start" in meta:
        kept = (float(meta.get("start") or 0),
                None if meta.get("end") is None else float(meta["end"]))
    rng = style.get("range")
    # --range says which part of the stream you want the chat for. A video
    # whose name says what part it holds is always timed from its own name:
    # the range only picks which of them get a subtitle -- it must never put
    # one part's chat on every part in the folder
    pick = bool(rng) and not clip and not style.get("video")
    targets = []
    for v in by_base.values():
        named = None if clip else part_from_name(v)
        if clip:
            a, b = rng or (0.0, None)
        elif pick and named:
            a, b = named
        else:
            a, b = rng or kept or named or (0.0, None)
        if b is None:                    # no cues past the end of the file
            from .live import media_seconds
            secs = media_seconds(v)
            if secs:
                b = a + secs + 1
        targets.append((v, (a, b), named))
    if pick:
        def inside(ab):
            # a few seconds of slack: a part starts at the keyframe before
            # the time asked for, and pieces of one part overlap by ~2 s
            a, b = ab
            if a < rng[0] - RANGE_SLACK:
                return False
            if rng[1] is None:
                return True
            return a < rng[1] if b is None else b <= rng[1] + RANGE_SLACK
        parts = [t for t in targets if t[2] and inside(t[1])]
        # a video without a part in its name holds what --range says, but
        # only when no part of this range is here
        keep = parts or [t for t in targets if not t[2]]
        for v, (a, b), _n in targets:
            if all(v != k[0] for k in keep):
                log(f"   left alone: {os.path.basename(v)} (another part, "
                    f"not {part_label(*rng)})")
        if not keep and targets:
            log(f"none of this stream's videos here holds {part_label(*rng)}")
        targets = keep
    targets = [(v, ab) for v, ab, _n in targets]
    if not targets:
        rng = rng or (None if clip else kept) or (0.0, None)
        if kind in ("own", "video"):
            base = _own_base(path)
        else:
            base = path[:-len(".live_chat.json")] if path.endswith(
                ".live_chat.json") else os.path.splitext(path)[0]
            if rng != (0.0, None):
                base += f" ({part_label(*rng)})"
            log(("no video of that part found" if by_base else
                 "no downloaded video of it found in the folder")
                + " -- the subtitle is saved next to the chat file; give it "
                "the video's name to have the player load it")
        targets.append((base, rng))
    look = {k: eff[k] for k in LOOK}
    rc = 0
    for v, (a, b) in targets:
        title = os.path.splitext(os.path.basename(v))[0]
        snd = 0.0
        if clip:
            pass                         # already in the times
        elif v.lower().endswith(VIDEO_EXTS):
            # the chat starts with the sound (see first_times)
            _low, s_ = first_times(v)
            if s_ and s_ > 0.05:
                snd = s_
                log(f"   (the picture starts {snd:.1f} s before the sound in "
                    f"this file -- the chat is timed to the sound)")
        elif kind in ("own", "video"):
            snd = float(meta.get("sound") or 0)
        text = render(msgs, fmt, lines=max(1, int(eff["lines"])),
                      hold=float(eff["hold"] or 0), start=a, end=b,
                      offset=float(eff["offset"] or 0), sound=snd,
                      video_id=vid or "", title=title, **look)
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
