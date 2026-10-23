"""Quality listing and picking, from yt-dlp's own JSON -- no tkinter.

`musictool formats URL` prints every quality a video offers *with its size*, and
`get --quality/--max-size` turns "the biggest one that fits in 500 MB" or "720p"
into a concrete yt-dlp format selector.

Sizes come from yt-dlp's own numbers: `filesize` when it knows it,
`filesize_approx` when it has estimated it, and failing both, bitrate x duration.
YouTube reports all three; a plain file over HTTP may report none, in which case
the size column shows "?" rather than a guess.
"""

import re

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
            # YouTube serves a dubbed track as its own format, with the
            # language on it. Without this the picker cannot tell an English
            # track from a Spanish one, and yt-dlp's own default ranks the
            # *original* language first -- so a dubbed video quietly arrives
            # in the wrong language.
            "lang": (f.get("language") or "").strip(),
            "lang_pref": int(f.get("language_preference") or 0),
        })
    # biggest first is how people read a quality list
    out.sort(key=lambda r: (r["bytes"] or -1, r["height"] or -1), reverse=True)
    return out


COLUMNS = ("id", "what", "container", "video", "audio", "size")
COLUMNS_V = ("id", "what", "container", "video", "audio", "size", "variants")


def table(rows, title=None):
    """An aligned text table, biggest first, with a 'what is it' column."""
    body = []
    show_variants = any(r.get("variants", 1) > 1 for r in rows)
    show_lang = any((r.get("lang") or "") for r in rows)
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
        row = [(r.get("base") or r["id"]) if show_variants else r["id"],
               what, r["ext"], res, r["vcodec"] or r["acodec"] or "-"]
        if show_lang:
            row.append(_lang_tag(r))
        row.append(human_bytes(r["bytes"]))
        if show_variants:
            n = r.get("variants", 1)
            row.append(f"{n} merged" if n > 1 else "")
        body.append(row)
    head = list(COLUMNS[:5])
    if show_lang:
        head.append("lang")
    head.append("size")
    if show_variants:
        head.append("variants")
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


def _audio_pool(rows, lang=None):
    """Pure audio tracks first: pairing a video stream with a *combined*
    (video+audio) format would merge in a second video track for nothing.

    With `lang`, the tracks in that language only -- and only when the video
    actually offers one, so a single-language video is never left with an
    empty pool.
    """
    pure = [r for r in rows if r["kind"] == "audio"]
    if not pure:
        # No separate audio track (a plain file): the file itself carries it.
        pure = [r for r in rows if r["kind"] in ("both", "file")]
    if lang:
        wanted = [r for r in pure if _match_lang(r, lang)]
        if wanted:
            return wanted
    return pure


# Containers tag tracks with three-letter codes (mkv writes 'eng', 'spa'),
# while YouTube and the picker speak two-letter ones ('en', 'es'). 'spa' does
# not start with 'es', so comparing prefixes quietly mis-identifies half the
# languages -- these are the pairs that matter.
LANG_CODES = {
    "en": ("eng",), "es": ("spa",), "fr": ("fra", "fre"), "de": ("deu", "ger"),
    "pt": ("por",), "it": ("ita",), "ru": ("rus",), "ja": ("jpn",),
    "ko": ("kor",), "zh": ("zho", "chi"), "hi": ("hin",), "ar": ("ara",),
    "nl": ("nld", "dut"), "sv": ("swe",), "pl": ("pol",), "tr": ("tur",),
    "id": ("ind",), "vi": ("vie",), "th": ("tha",), "uk": ("ukr",),
    "cs": ("ces", "cze"), "el": ("ell", "gre"), "he": ("heb",), "ro": ("ron", "rum"),
    "hu": ("hun",), "fi": ("fin",), "da": ("dan",), "no": ("nor",),
    "fa": ("fas", "per"), "bn": ("ben",), "sw": ("swa",), "yo": ("yor",),
    "ig": ("ibo",), "ha": ("hau",), "am": ("amh",), "zu": ("zul",), "af": ("afr",),
}


def same_language(tag, want):
    """Is `tag` the language `want`? 'eng'/'en-US' are both 'en'; '' is not."""
    t = str(tag or "").strip().lower().replace("_", "-")
    w = str(want or "").strip().lower().replace("_", "-")
    if not t or not w or w in ("any", "auto", "original", "*"):
        return False
    t, w = t.split("-")[0], w.split("-")[0]
    return t == w or t in LANG_CODES.get(w, ())


def _match_lang(row, lang):
    """'en' matches a track tagged 'en', 'en-US', 'en-GB' or 'eng'."""
    return same_language(row.get("lang"), lang)


def _lang_tag(row):
    """What the lang column shows: the code, starred when it is the original."""
    lang = row.get("lang") or ""
    if not lang:
        return ""
    return lang + "*" if (row.get("lang_pref") or 0) > 0 else lang


def languages_in(rows):
    """Every spoken language this video offers, originals first."""
    out = []
    for r in sorted(rows or [], key=lambda r: -(r.get("lang_pref") or 0)):
        lang = (r.get("lang") or "").strip()
        if lang and lang not in [x[0] for x in out]:
            out.append((lang, (r.get("lang_pref") or 0) > 0,
                        (r.get("note") or "").strip()))
    return out


def best_audio_in(rows, lang):
    """The best audio track in one language, or None when there isn't one."""
    pool = [r for r in _audio_pool(rows, lang) if _match_lang(r, lang)]
    if not pool:
        return None
    return max(pool, key=lambda r: (r["abr"] or r["tbr"] or 0))


def preferred_language(rows, lang):
    """Which of the languages this video offers a preference lands on.

    -> the code ('en'), or None when the video has no track in it.
    """
    for code, _orig, _note in languages_in(rows):
        if _match_lang({"lang": code}, lang):
            return code
    return None


def audio_lang_selector(keep_video=False, lang="en"):
    """A default `-f` that prefers one spoken language, with a clean fallback.

    `--format-sort lang:en` does NOT do this: in yt-dlp the sort field called
    `lang` maps to `language_preference`, a number, not to the language code.
    Filtering on the `language` field is what actually works, and the `/`
    fallback keeps the old behaviour when no track is tagged.
    """
    lang = str(lang or "").strip()
    if not lang or lang.lower() in ("any", "auto", "original", "*"):
        return ""
    tag = f"[language^={lang}]"
    return (f"bv*+ba{tag}/bv*+ba/b" if keep_video else f"ba{tag}/ba/b")


def _best_audio(rows, lang=None):
    auds = _audio_pool(rows, lang)
    if not auds:
        return None
    return max(auds, key=lambda r: (r["abr"] or r["tbr"] or 0))


def _rank(r):
    return (r["height"] or 0, r["fps"] or 0, r["tbr"] or 0)


def pick(rows, height=None, max_bytes=None, audio_only=False, smallest=False,
         lang=None):
    """Choose a yt-dlp format selector.

    -> (selector, video_row_or_None, audio_row_or_None, note) or (None, ...) when
    nothing offered matches, so the caller can say so instead of guessing.
    """
    if not rows:
        return None, None, None, "no formats reported"
    if audio_only:
        auds = _audio_pool(rows, lang)
        if not auds:
            return None, None, None, "no audio track offered"
        chosen = min(auds, key=lambda r: (r["bytes"] or 0)) if smallest else \
            max(auds, key=lambda r: (r["abr"] or r["tbr"] or 0))
        if max_bytes:
            fit = [r for r in auds if r["bytes"] and r["bytes"] <= max_bytes]
            if fit:
                chosen = max(fit, key=lambda r: (r["abr"] or r["tbr"] or 0))
        return _selector(chosen, None), None, chosen, ""

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
        audio = _best_audio(rows, lang)
        if audio is None:
            return None, chosen, None, "video stream found but no audio track"
        if max_bytes and chosen["bytes"] and audio["bytes"] and \
                chosen["bytes"] + audio["bytes"] > max_bytes:
            small = [r for r in _audio_pool(rows, lang) if r["bytes"]
                     and chosen["bytes"] + r["bytes"] <= max_bytes]
            if small:
                audio = max(small, key=lambda r: (r["abr"] or r["tbr"] or 0))
        selector = _selector(chosen, audio)
    else:
        selector = _selector(chosen, None)
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



# --------------------------------------------------------------------------
# Making the list readable
#
# yt-dlp lists the same stream many times: YouTube's SABR/range delivery shows
# up as 140-0 ... 140-17, i.e. eighteen rows for one audio track, and a video
# often offers the same resolution twice at different bitrates. Both are noise
# when you are trying to choose, so they are collapsed by default -- and the
# collapse is a filter you can switch off, not something done behind your back.
# --------------------------------------------------------------------------
VARIANT_RE = re.compile(r"^(.+)-\d+$")

RESOLUTIONS = (2160, 1440, 1080, 720, 480, 360, 240, 144)
CONTAINERS = ("mp4", "webm", "mkv", "m4a")
CODECS = ("avc1", "vp9", "vp09", "av01", "hev1", "hvc1", "opus", "mp4a")

# Auto presets, in the order they appear in the picker.
AUTO_MODES = ("best", "compatible", "fit", "smallest", "audio")
AUTO_LABELS = {
    "best": "Best quality",
    "compatible": "Most compatible (H.264/mp4 -- plays anywhere)",
    "fit": "Best that fits my size limit",
    "smallest": "Smallest file",
    "audio": "Audio only",
}


def variant_base(fid):
    """'140-7' -> '140'; an ordinary id is its own base."""
    m = VARIANT_RE.match(str(fid or ""))
    return m.group(1) if m else (fid or "?")


def collapse_variants(rows):
    """One row per real stream, keeping the biggest variant of each."""
    groups = {}
    for r in rows:
        # Language is part of the identity: a dubbed video serves the same
        # itag once per spoken language, and merging those would hide every
        # language but one behind a single row.
        groups.setdefault((variant_base(r["id"]), r["kind"], r["ext"],
                           (r.get("lang") or "")), []).append(r)
    out = []
    for (base, _kind, _ext, _lang), g in groups.items():
        best = dict(max(g, key=lambda r: (r["bytes"] or 0, r["tbr"] or 0)))
        best["base"] = base
        best["variants"] = len(g)
        out.append(best)
    out.sort(key=lambda r: (r["bytes"] or -1, r["height"] or -1), reverse=True)
    return out


def dedupe_quality(rows):
    """Same resolution/fps/codec/container twice -> keep the smaller file.

    Two rows with identical video characteristics are the same quality, so the
    smaller one is simply the better encode. Audio is matched on codec and
    bitrate for the same reason.
    """
    seen = {}
    for r in rows:
        if r["kind"] == "audio":
            # Two dubs at the same bitrate are the same quality in different
            # languages -- they are not duplicates of each other.
            key = ("audio", r["acodec"], r["ext"],
                   round(r["abr"] or r["tbr"] or 0), (r.get("lang") or ""))
        else:
            key = (r["height"], r["fps"], r["vcodec"], r["ext"])
        cur = seen.get(key)
        if cur is None or (r["bytes"] or 0) < (cur["bytes"] or 1 << 62):
            r = dict(r)
            r["also"] = (cur.get("also", 0) + 1) if cur else r.get("also", 0)
            seen[key] = r
    out = list(seen.values())
    out.sort(key=lambda r: (r["bytes"] or -1, r["height"] or -1), reverse=True)
    return out


SORTS = ("size_desc", "size_asc", "resolution", "container")


def apply_filters(rows, max_height=None, container=None, codec=None,
                  max_bytes=None, include_audio=False, collapse=True,
                  dedupe=True, sort="size_desc"):
    """The readable list: collapsed, de-duplicated, then narrowed."""
    out = list(rows or [])
    if collapse:
        out = collapse_variants(out)
    if dedupe:
        out = dedupe_quality(out)
    if not include_audio:
        out = [r for r in out if r["kind"] != "audio"]
    if max_height:
        out = [r for r in out if not r["height"] or r["height"] <= max_height]
    if container and container != "any":
        out = [r for r in out if r["ext"] == container]
    if codec and codec != "any":
        out = [r for r in out
               if (r["vcodec"] or "").startswith(codec)
               or (r["acodec"] or "").startswith(codec)]
    if max_bytes:
        out = [r for r in out if not r["bytes"] or r["bytes"] <= max_bytes]
    if sort == "size_asc":
        out.sort(key=lambda r: (r["bytes"] is None, r["bytes"] or 0))
    elif sort == "resolution":
        out.sort(key=lambda r: (r["height"] or 0, r["fps"] or 0), reverse=True)
    elif sort == "container":
        out.sort(key=lambda r: (r["ext"], -(r["height"] or 0)))
    else:
        out.sort(key=lambda r: (r["bytes"] or -1, r["height"] or -1), reverse=True)
    return out


def with_audio_pool(rows, all_rows):
    """`rows` (a filtered table) plus the audio tracks it filtered out.

    A table that hides audio-only tracks must not stop the picker from
    pairing a video with a real audio track -- otherwise yt-dlp is told to
    mux a 1080p stream with the 360p combined format just because that was
    the only thing left in the pool.
    """
    keep = {id(r) for r in rows}
    return list(rows) + [r for r in all_rows
                         if id(r) not in keep and r["kind"] != "video"]


def pair_with_audio(row, rows, max_bytes=None):
    """Public wrapper over _pair: the audio track to go with `row`."""
    return _pair(row, rows, max_bytes)


def selector_for(row, audio):
    """Public wrapper over _selector: the -f string for `row` (+ `audio`)."""
    return _selector(row, audio)


def total_bytes(row, audio=None):
    """Public wrapper over _total: the bytes the download will take."""
    return _total(row, audio)


def _pair(row, rows, max_bytes=None, lang=None):
    """A video row plus the best audio track to go with it."""
    if row["kind"] != "video":
        return row, None
    audio = _best_audio(rows, lang)
    if audio is None:
        return row, None
    if max_bytes and row["bytes"] and audio["bytes"] and \
            row["bytes"] + audio["bytes"] > max_bytes:
        small = [r for r in _audio_pool(rows, lang) if r["bytes"]
                 and row["bytes"] + r["bytes"] <= max_bytes]
        if small:
            audio = max(small, key=lambda r: (r["abr"] or r["tbr"] or 0))
    return row, audio


def _ref(r):
    """How to name one stream on a -f line: by the id yt-dlp actually served.

    `base` ('140' out of '140-7') is only a grouping key for the collapsed
    table -- for a video like that, YouTube serves no format called '140' at
    all. Offering `140/140-0` on a `-f` line is worse than useless, because
    `/` binds looser than `+`:

        -f 248+140/140-0   ->  (248+140) / (140-0)

    The first branch cannot match (there is no `140`), so yt-dlp silently
    falls through to the second and hands back the audio track on its own.
    That is how "1080p vp9 + mp4a ~ 644.7 MiB" arrived as a 52 MiB .m4a while
    `--keep-video` was set and nothing looked wrong. One concrete id per
    stream, so the pair either matches or yt-dlp says so out loud.
    """
    return str(r["id"])


def check_selector(selector, rows):
    """Judge a raw -f expression against the formats this video really serves.

    -> a list of warnings, empty when the selector is fine. yt-dlp happily
    accepts a selector that cannot match and then falls back to something you
    did not ask for, so the check happens here, before the download, where it
    can still be acted on.
    """
    out = []
    have = {str(r["id"]) for r in rows or []}
    if not have:
        return out                      # nothing to judge it against
    text = str(selector or "")
    ids = [t for t in re.findall(r"[0-9][0-9A-Za-z._-]*", text)]
    missing = [t for t in ids if t not in have]
    if missing:
        out.append("this video serves no format called "
                   + ", ".join(sorted(set(missing)))
                   + " (it has " + ", ".join(sorted(have)[:6]) + ")")
    if "/" in text:
        head = text.split("/")[0]
        if any(t not in have for t in re.findall(r"[0-9][0-9A-Za-z._-]*", head)):
            out.append("nothing before the '/' can match, so yt-dlp will "
                       "silently take the fallback after it")
        elif not any(r["kind"] in ("video", "both", "file")
                     for r in rows or []
                     if str(r["id"]) in re.findall(
                         r"[0-9][0-9A-Za-z._-]*", text.split("/")[-1])):
            out.append("the fallback is an audio track on its own")
    return out


SELECTOR_CHARS = "+/,[]()"


def looks_like_selector(text):
    """True when a value is a raw yt-dlp -f expression, not a height.

    `--quality` wants 720 or 1080; the picker and our own `formats` output
    print things like `248+140-0`. Telling those apart lets a pasted selector
    be used instead of quietly ignored.
    """
    t = str(text or "").strip()
    return bool(t) and any(c in t for c in SELECTOR_CHARS)


def _selector(row, audio):
    return f"{_ref(row)}+{_ref(audio)}" if audio else _ref(row)


def _total(row, audio):
    return (row["bytes"] or 0) + ((audio["bytes"] or 0) if audio else 0)


def auto_pick(rows, mode="best", max_bytes=None, lang=None):
    """Choose for the user and say why. -> (selector, row, audio, why)

    `rows` should already be filtered. Nothing here guesses: when the pool is
    empty the answer is None plus the reason, so the UI can say so.
    """
    all_rows = rows or []
    if mode == "audio":
        auds = _audio_pool(all_rows, lang)
        if not auds:
            return None, None, None, "no audio track offered"
        best = max(auds, key=lambda r: (r["abr"] or r["tbr"] or 0))
        if max_bytes:
            fit = [r for r in auds if r["bytes"] and r["bytes"] <= max_bytes]
            if fit:
                best = max(fit, key=lambda r: (r["abr"] or r["tbr"] or 0))
        return (_selector(best, None), best, None,
                f"best audio: {best['acodec']} {best['ext']} "
                f"{_spoken(best)}~ {human_bytes(best['bytes'])}")

    vids = [r for r in all_rows if r["kind"] in ("video", "both", "file")]
    if not vids:
        return None, None, None, ("nothing matches those filters"
                                  if all_rows else "no formats reported")
    if mode == "smallest":
        chosen = min(vids, key=lambda r: (r["bytes"] is None, r["bytes"] or 0))
        why = f"smallest file ~ {human_bytes(chosen['bytes'])}"
    elif mode == "compatible":
        compat = [r for r in vids
                  if (r["vcodec"] or "").startswith(("avc1", "hvc1", "hev1"))
                  or r["kind"] in ("both", "file")]
        pool = compat or vids
        chosen = max(pool, key=_rank)
        why = (f"tallest H.264/mp4 (plays on anything, no conversion) "
               f"~ {human_bytes(chosen['bytes'])}")
        if not compat:
            why = ("no H.264 in this list; took the best available "
                   f"{chosen['vcodec'] or chosen['ext']} instead")
    else:                                   # best / fit
        pool = vids
        if max_bytes:
            fit = [r for r in pool if r["bytes"] and r["bytes"] <= max_bytes]
            if fit:
                pool = fit
        chosen = max(pool, key=_rank)
        if mode == "fit" and max_bytes:
            why = (f"best under {human_bytes(max_bytes)}: "
                   f"{chosen['height'] or '?'}p ~ {human_bytes(chosen['bytes'])}")
        else:
            why = (f"best quality: {chosen['height'] or '?'}p "
                   f"{chosen['vcodec'] or chosen['ext']} "
                   f"~ {human_bytes(chosen['bytes'])}")
    row, audio = _pair(chosen, all_rows, max_bytes, lang)
    total = _total(row, audio)
    if audio:
        why += (f"  +  {audio['acodec']} audio {_spoken(audio)}"
                f"-> ~ {human_bytes(total)} total")
    if max_bytes and total and total > max_bytes:
        why += f"  (over the {human_bytes(max_bytes)} limit -- nothing fits)"
    return _selector(row, audio), row, audio, why


def _spoken(row):
    """'English ' when the track says what it is spoken in, else ''."""
    lang = ((row or {}).get("lang") or "").strip()
    return f"[{lang}] " if lang else ""


def describe_choice(chosen, audio=None):
    """One line saying what was picked and how big it will be."""
    chosen = chosen or audio
    if not chosen:
        return "nothing matched"
    total = (chosen["bytes"] or 0) + (
        (audio["bytes"] or 0) if audio and audio is not chosen else 0)
    if chosen["kind"] == "audio":
        return (f"audio {chosen['acodec']} {chosen['ext']} {_spoken(chosen)}"
                f"~ {human_bytes(total)}")
    if chosen["kind"] == "file":
        return f"whole file {chosen['ext']} ~ {human_bytes(total)}"
    res = (f"{chosen['height']}p" if chosen["height"]
           else (chosen["vcodec"] or chosen["ext"]))
    if chosen["fps"] and chosen["fps"] not in (25, 30):
        res += str(chosen["fps"])
    return (f"{res} {chosen['vcodec']} + "
            f"{audio['acodec'] if audio else 'audio'} "
            f"{_spoken(audio if audio else chosen)}~ {human_bytes(total)}")
