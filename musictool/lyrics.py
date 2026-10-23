"""The lyrics pipeline: scan local files, fetch from providers, write + embed."""

import hashlib
import os
import re

from . import cancel
from . import download, providers, tags
from .util import classify, shift_lrc


def _related(a, b):
    """True if path a is b, inside b, or the parent of b."""
    a = os.path.abspath(a)
    b = os.path.abspath(b)
    return a == b or a.startswith(b + os.sep) or b.startswith(a + os.sep)


def safe_lyrics_dirs(paths, lyrics_dirs):
    """Drop any lyrics folder unrelated to the folders being scanned."""
    roots = [os.path.abspath(p) if os.path.isdir(p)
             else os.path.dirname(os.path.abspath(p)) for p in paths]
    out = []
    for d in lyrics_dirs:
        if not d:
            continue
        if any(_related(d, r) for r in roots) and d not in out:
            out.append(d)
    return tuple(out)


def gather(paths, lyrics_dirs=(), recursive=False, states=None):
    """Expand paths/dirs into a list of file dicts with current state."""
    lyrics_dirs = safe_lyrics_dirs(paths, lyrics_dirs)
    out = []
    for p in paths:
        for f in tags.list_audio(p, recursive=recursive):
            info = tags.read(f)
            _, side = tags.read_sidecar(f, lyrics_dirs)
            info["sidecar"] = side or ""
            info["sidecar_state"] = classify(side) if side else "empty"
            # The sidecar must not mask a bad embedded tag: remember both so the
            # caller can tell "everything is fine" from "the file needs repairing".
            if side and info["state"] == "empty":
                info["lyrics"] = side
                info["state"] = info["sidecar_state"]
            out.append(info)
    if states:
        out = [i for i in out if i["state"] in states]
    return out


def _video_id(path):
    idx = download.load_index(os.path.dirname(path) or ".")
    rec = idx.get(os.path.basename(path)) or {}
    return rec.get("video_id", "")


def open_session(opts):
    """Shared (http, providers) so a batch of files reuses one cache/connection."""
    from .util import Http, config_dir
    http = Http(cache_path=opts.get("cache") or
                os.path.join(config_dir(), "http-cache.json"),
                delay=opts.get("delay", 0.35))
    provs = providers.build(opts.get("provider_order") or providers.DEFAULT_ORDER)
    return http, provs


def process_file(info, opts, http, provs, log=print, tag=""):
    """Fetch + embed lyrics for ONE already-gathered file dict.

    Returns (bucket, entry) where bucket is one of fixed/skipped/unresolved/
    errors. Split out of fix() so `get` can run it the instant each download
    lands instead of waiting for the whole batch.
    """
    name = os.path.basename(info["path"])
    if info["error"]:
        log(f"{tag} ! {name}: {info['error']}")
        return "errors", (name, info["error"])

    if info["state"] == "synced" and not opts.get("force"):
        log(f"{tag} ok       {name}")
        return "skipped", name
    # Embedded lyrics are bad but a good sidecar exists: just repair the tag.
    if (info["sidecar_state"] == "synced" and info["state"] != "synced"
            and not opts.get("force") and not opts.get("dry_run")):
        log(f"{tag} {info['state']:<8} {name}")
        log("         -> repairing tag from existing .lrc sidecar (no network)")
        try:
            tags.write_lyrics(info["path"], info["sidecar"])
            return "fixed", (name, "sidecar", "synced", "re-embedded from .lrc")
        except Exception as e:
            log(f"         ! embed failed: {e}")
            return "errors", (name, str(e))
    log(f"{tag} {info['state']:<8} {name}")

    fa, ft = tags.from_filename(info["path"])
    q = providers.Query(artist=info["artist"] or fa,
                        title=info["title"] or ft,
                        album=info["album"],
                        duration=info["duration"],
                        video_id=_video_id(info["path"]),
                        path=info["path"])
    if not q.title:
        return "unresolved", (name, "no title in tags or filename")

    cand, tried = providers.find_best(
        q, provs, http,
        allow_unsynced=opts.get("allow_unsynced", False),
        log=lambda m: log(f"         {m}"),
        fname_artist=fa)
    if not cand:
        log(f"         -> no match ({tried[-1] if tried else 'unknown'})")
        return "unresolved", (name, tried[-1] if tried else "unknown")

    lyrics = cand.lyrics or ""
    if opts.get("shift"):
        lyrics = shift_lrc(lyrics, float(opts["shift"]))
    log(f"         -> {cand.describe()} score {cand.score:.2f}")

    if opts.get("dry_run"):
        return "fixed", (name, cand.provider,
                         "synced" if cand.synced else "unsynced", "dry-run")

    base = os.path.splitext(name)[0]
    lyrics_dir = opts.get("lyrics_dir")
    if lyrics_dir:
        tags.write_sidecar(lyrics_dir, base, lyrics)
    if not opts.get("no_embed"):
        try:
            tags.write_lyrics(info["path"], lyrics,
                              backup_dir=os.path.join(lyrics_dir, "_backup")
                              if lyrics_dir else None)
        except Exception as e:
            log(f"         ! embed failed: {e}")
    return "fixed", (name, cand.provider,
                     "synced" if cand.synced else "unsynced", cand.note)


def fix(paths, opts, log=print, progress=None):
    """Fetch/fix lyrics for paths. Returns a summary dict."""
    lyrics_dir = opts.get("lyrics_dir")
    http, provs = open_session(opts)
    log("providers: " + ", ".join(p.name for p in provs))

    files = gather(paths, (lyrics_dir,), recursive=opts.get("recursive", False))
    if not files:
        return {"error": "no audio files found"}
    log(f"scanning {len(files)} file(s)")

    res = {"fixed": [], "skipped": [], "unresolved": [], "errors": [],
           "total": len(files), "api_calls": 0}
    limit = opts.get("limit") or 0

    for i, info in enumerate(files, 1):
        if cancel.requested():
            log(f"\n{cancel.CANCELLED} after {i - 1} file(s)")
            res["total"] = i - 1
            break
        bucket, entry = process_file(info, opts, http, provs, log,
                                     tag=f"[{i}/{len(files)}]")
        res[bucket].append(entry)
        if bucket == "fixed" and limit and len(res["fixed"]) >= limit:
            log(f"\nhit --limit {limit}")
            break

    res["api_calls"] = http.calls
    http.save()
    return res


def write_report(directory, res):
    lines = ["musictool lyrics report", "=" * 60, ""]
    lines.append(f"scanned    : {res['total']}")
    lines.append(f"fixed      : {len(res['fixed'])}")
    lines.append(f"already ok : {len(res['skipped'])}")
    lines.append(f"unresolved : {len(res['unresolved'])}")
    lines.append(f"unreadable : {len(res['errors'])}")
    lines.append(f"api calls  : {res['api_calls']}")
    lines += ["", f"FIXED ({len(res['fixed'])}):"]
    lines += [f"  [{prov}/{kind}] {n}  {note}" for n, prov, kind, note in res["fixed"]]
    lines += ["", f"UNRESOLVED ({len(res['unresolved'])}):"]
    lines += [f"  {n}  -- {why}" for n, why in res["unresolved"]]
    if res["errors"]:
        lines += ["", f"UNREADABLE ({len(res['errors'])}):"]
        lines += [f"  {n}  -- {e}" for n, e in res["errors"]]
    p = os.path.join(directory, "lyrics-report.txt")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return p


def summarise(files):
    out = {}
    for f in files:
        k = f["state"] if not f["error"] else "error"
        out[k] = out.get(k, 0) + 1
    return out


# --------------------------------------------------------------------------
# duplicate detection
#
# A previous script assumed a download would land, then grabbed whatever file was
# most recently modified in the folder and took lyrics for THAT. So one song's
# lyrics ended up embedded in several unrelated files. Grouping by lyrics content
# finds the damage without trusting filenames.
# --------------------------------------------------------------------------
LRC_META = re.compile(r"^\[(?:ti|ar|al|au|by|offset|length|re|ve|hash|sign)[:#]",
                      re.IGNORECASE)
LRC_TS = re.compile(r"^\[\d{1,2}:\d{2}(?:[.:]\d{1,3})?\]\s*")


def fingerprint(text, min_chars=40):
    """Content hash of the lyric *words*, ignoring timestamps and LRC headers.

    Timestamps are dropped on purpose: the same wrong lyrics pasted into several
    files may carry slightly different timings and must still group together.
    Returns None for text too short to be a meaningful match.
    """
    from .util import norm_key
    if not text:
        return None
    words = []
    for line in text.splitlines():
        s = line.strip()
        if not s or LRC_META.match(s):
            continue
        s = LRC_TS.sub("", s).strip()
        if s:
            words.append(norm_key(s))
    joined = " ".join(w for w in words if w)
    if len(joined) < min_chars:
        return None
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def find_duplicates(paths, lyrics_dirs=(), recursive=False, min_chars=40):
    """-> list of groups, each {'fingerprint','members','distinct_titles',
    'suspicious','synced_count','preview'} sorted largest-first."""
    from .util import norm_key
    files = gather(paths, tuple(lyrics_dirs), recursive=recursive)
    groups = {}
    for f in files:
        fp = fingerprint(f.get("lyrics"), min_chars)
        if fp:
            groups.setdefault(fp, []).append(f)

    out = []
    for fp, members in groups.items():
        if len(members) < 2:
            continue
        titles = set()
        for m in members:
            t = m["title"] or tags.from_filename(m["path"])[1]
            if t:
                titles.add(norm_key(t))
        preview = ""
        for line in (members[0]["lyrics"] or "").splitlines():
            s = LRC_TS.sub("", line.strip())
            if s and not LRC_META.match(s):
                preview = s
                break
        out.append({
            "fingerprint": fp,
            "members": members,
            "distinct_titles": len(titles),
            # Different titles sharing one set of lyrics = the bug. Identical
            # titles are usually genuinely the same song (duplicate uploads).
            "suspicious": len(titles) > 1,
            "synced_count": sum(1 for m in members if m["state"] == "synced"),
            "preview": preview[:70],
        })
    out.sort(key=lambda g: (-len(g["members"]), -g["distinct_titles"]))
    return out
