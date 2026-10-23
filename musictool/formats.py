"""Quality listing and picking, from yt-dlp's own JSON -- no tkinter.

`musictool formats URL` prints every quality a video offers *with its size*, and
`get --quality/--max-size` turns "the biggest one that fits in 500 MB" or "720p"
into a concrete yt-dlp format selector.

Sizes come from yt-dlp's own numbers: `filesize` when it knows it,
`filesize_approx` when it has estimated it, and failing both, bitrate x duration.
YouTube reports all three; a plain file over HTTP may report none, in which case
the size column shows "?" rather than a guess.
"""

NO_CODEC = ("none", "", None)


def human_bytes(n):
    if not n:
        return "?"
    n = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:,.0f} {unit}" if unit == "B" else f"{n:,.1f} {unit}"
        n /= 1024.0
    return "?"


def parse_size(text):
    """'500M', '1.5G', '800k', '1024' -> bytes. None when unparseable."""
    if text is None:
        return None
    s = str(text).strip().lower().replace("b", "")
    if not s:
        return None
    mult = 1
    if s[-1] in "kmgt":
        mult = {"k": 1024, "m": 1024 ** 2, "g": 1024 ** 3, "t": 1024 ** 4}[s[-1]]
        s = s[:-1]
    try:
        return int(float(s) * mult)
    except ValueError:
        return None


def rows_from_info(info):
    """Normalise yt-dlp's format list into comparable rows."""
    dur = None
    try:
        dur = float(info.get("duration") or 0) or None
    except (TypeError, ValueError):
        dur = None
    out = []
    for f in info.get("formats") or []:
        v = (f.get("vcodec") or "").lower() or None
        a = (f.get("acodec") or "").lower() or None
        if v is None and a is None:
            # No codec information at all (a plain file over HTTP, e.g. the
            # generic extractor): that is one whole file, not "nothing".
            kind = "file"
            has_v = has_a = False      # codecs unknown; the columns show "-"
        else:
            has_v = v not in NO_CODEC
            has_a = a not in NO_CODEC
            if not has_v and not has_a:
                continue          # storyboards and other non-media entries
            kind = "both" if (has_v and has_a) else ("video" if has_v else "audio")
        size = f.get("filesize") or f.get("filesize_approx")
        if not size and f.get("tbr") and dur:
            try:
                size = int(float(f["tbr"]) * 1000.0 * dur / 8.0)
            except (TypeError, ValueError):
                size = None
        out.append({
            "id": f.get("format_id") or "?",
            "ext": f.get("ext") or "?",
            "kind": kind,
            "height": int(f.get("height") or 0) or None,
            "width": int(f.get("width") or 0) or None,
            "fps": round(float(f.get("fps") or 0)) or None,
            "vcodec": (v.split(".")[0] if has_v else ""),
            "acodec": (a.split(".")[0] if has_a else ""),
            "tbr": float(f.get("tbr") or 0) or None,
            "abr": float(f.get("abr") or 0) or None,
            "bytes": int(size) if size else None,
            "note": f.get("format_note") or "",
        })
    # biggest first is how people read a quality list
    out.sort(key=lambda r: (r["bytes"] or -1, r["height"] or -1), reverse=True)
    return out


COLUMNS = ("id", "what", "container", "video", "audio", "size")


def table(rows, title=None):
    """An aligned text table, biggest first, with a 'what is it' column."""
    body = []
    for r in rows:
        if r["kind"] == "audio":
            what = "audio only"
        elif r["kind"] == "both":
            what = "video+audio"
        elif r["kind"] == "file":
            what = "whole file"
        else:
            what = "video only"
        res = (f"{r['height']}p" if r["height"] else "-")
        if r["fps"] and r["fps"] not in (25, 30):
            res += f"{r['fps']}"
        body.append([r["id"], what, r["ext"], res,
                     r["vcodec"] or r["acodec"] or "-", human_bytes(r["bytes"])])
    head = list(COLUMNS)
    widths = [max(len(str(h)), *(len(str(b[i])) for b in body)) if body else len(str(h))
              for i, h in enumerate(head)]
    lines = []
    if title:
        lines.append(title)
    lines.append("  ".join(str(h).ljust(widths[i]) for i, h in enumerate(head)).rstrip())
    lines.append("  ".join("-" * w for w in widths))
    for b in body:
        lines.append("  ".join(str(b[i]).ljust(widths[i]) for i in range(len(head))).rstrip())
    return "\n".join(lines)


def _audio_pool(rows):
    """Pure audio tracks first: pairing a video stream with a *combined*
    (video+audio) format would merge in a second video track for nothing."""
    pure = [r for r in rows if r["kind"] == "audio"]
    if pure:
        return pure
    # No separate audio track (a plain file): the file itself carries the audio.
    return [r for r in rows if r["kind"] in ("both", "file")]


def _best_audio(rows):
    auds = _audio_pool(rows)
    if not auds:
        return None
    return max(auds, key=lambda r: (r["abr"] or r["tbr"] or 0))


def _rank(r):
    return (r["height"] or 0, r["fps"] or 0, r["tbr"] or 0)


def pick(rows, height=None, max_bytes=None, audio_only=False, smallest=False):
    """Choose a yt-dlp format selector.

    -> (selector, video_row_or_None, audio_row_or_None, note) or (None, ...) when
    nothing offered matches, so the caller can say so instead of guessing.
    """
    if not rows:
        return None, None, None, "no formats reported"
    if audio_only:
        auds = _audio_pool(rows)
        if not auds:
            return None, None, None, "no audio track offered"
        chosen = min(auds, key=lambda r: (r["bytes"] or 0)) if smallest else \
            max(auds, key=lambda r: (r["abr"] or r["tbr"] or 0))
        if max_bytes:
            fit = [r for r in auds if r["bytes"] and r["bytes"] <= max_bytes]
            if fit:
                chosen = max(fit, key=lambda r: (r["abr"] or r["tbr"] or 0))
        return chosen["id"], None, chosen, ""

    vids = [r for r in rows if r["kind"] in ("video", "both", "file")]
    if not vids:
        return None, None, None, "no video stream offered"
    pool = vids
    if height:
        under = [r for r in pool if r["height"] and r["height"] <= height]
        if under:
            pool = under
    if max_bytes:
        fit = [r for r in pool if r["bytes"] and r["bytes"] <= max_bytes]
        if fit:
            pool = fit
        elif height is None:
            # Nothing fits on its own: try the smallest video + smallest audio.
            pool = [min(pool, key=lambda r: (r["bytes"] or 0))]
    chosen = min(pool, key=lambda r: (r["bytes"] or 0)) if smallest \
        else max(pool, key=_rank)
    audio = None
    if chosen["kind"] == "video":
        audio = _best_audio(rows)
        if audio is None:
            return None, chosen, None, "video stream found but no audio track"
        if max_bytes and chosen["bytes"] and audio["bytes"] and \
                chosen["bytes"] + audio["bytes"] > max_bytes:
            small = [r for r in rows if r["kind"] == "audio" and r["bytes"]
                     and chosen["bytes"] + r["bytes"] <= max_bytes]
            if small:
                audio = max(small, key=lambda r: (r["abr"] or r["tbr"] or 0))
        selector = f"{chosen['id']}+{audio['id']}"
    else:
        selector = chosen["id"]
    note = ""
    if height and chosen["height"] and chosen["height"] > height:
        note = (f"nothing at or below {height}p; smallest available is "
                f"{chosen['height']}p")
    if max_bytes:
        total = (chosen["bytes"] or 0) + ((audio["bytes"] or 0) if audio else 0)
        if total and total > max_bytes:
            note = ((note + "; ") if note else "") + (
                f"nothing fits in {human_bytes(max_bytes)} -- closest is "
                f"{human_bytes(total)}")
    return selector, chosen, audio, note


def describe_choice(chosen, audio=None):
    """One line saying what was picked and how big it will be."""
    chosen = chosen or audio
    if not chosen:
        return "nothing matched"
    total = (chosen["bytes"] or 0) + (
        (audio["bytes"] or 0) if audio and audio is not chosen else 0)
    if chosen["kind"] == "audio":
        return f"audio {chosen['acodec']} {chosen['ext']} ~ {human_bytes(total)}"
    if chosen["kind"] == "file":
        return f"whole file {chosen['ext']} ~ {human_bytes(total)}"
    res = (f"{chosen['height']}p" if chosen["height"]
           else (chosen["vcodec"] or chosen["ext"]))
    if chosen["fps"] and chosen["fps"] not in (25, 30):
        res += str(chosen["fps"])
    return (f"{res} {chosen['vcodec']} + {audio['acodec'] if audio else 'audio'} "
            f"~ {human_bytes(total)}")
