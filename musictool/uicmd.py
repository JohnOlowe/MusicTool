"""Command construction and report parsing shared by the GUI and its tests.

Deliberately free of any tkinter import so it can be unit-tested headless.

The GUI does not re-implement option handling: it builds the exact argv the CLI
would receive and calls cli.main(). Because the widgets are generated from the
parser itself, a new CLI flag automatically appears in the GUI.
"""

import argparse
import json
import os
import re

from .tags import from_filename
from .util import common_root

MAX_RECENT = 12

# Options whose value is a path, so the GUI puts a Browse button beside them.
# "dir" opens a folder chooser, "file" a file chooser.
PATH_DESTS = {
    "output_dir": "dir",
    "lyrics_dir": "dir",
    "cookies": "file",
    "download_archive": "file",
}

# One-line help shown under the field, for options that are easy to misuse.
HINTS = {
    "cookies": "optional: an exported cookies.txt -- the browser picker above is easier",
    "cookies_from_browser": "reads your YouTube login from the browser; 'auto' picks the one you use",
    "output_dir": "remembered between runs",
    "playlist_items": "e.g. 287-447 to resume a playlist where you stopped",
    "keep_video": "Keep video (no audio extract)",
    "all_formats": "Show every raw row (do not merge variants)",
    "container": "Only this container",
    "codec": "Only this codec (avc1, vp9, av01, opus)",
    "show_audio": "Include the audio-only tracks",
    "sort": "Sort by",
    "auto": "Suggest a quality for me",
    "max_size": "takes the best quality that fits under this, e.g. 250M or 1G",
    "format_selector": "an id from 'List qualities + sizes', e.g. 137+140",
    "merge_format": "mkv accepts every codec; mp4 is more compatible",
    "no_playlist": "download just this video, not the playlist it sits in",
    "js_runtimes": "needed for YouTube's n-challenge when deno is not installed",
    "all_formats": "yt-dlp splits one stream into ~18 CDN variants; those are merged by default",
    "auto": "best, compatible (H.264/mp4), fit (best under the size limit), smallest, audio",
    "sort": "size_desc = biggest file first",
    "show_audio": "the audio-only tracks, which normally get merged into the video choice",
}

# Human wording for the generated widgets. Anything missing here falls back to
# the dest with underscores turned into spaces, so a new flag is never nameless.
LABELS = {
    "allow_unsynced": "Accept plain (unsynced) lyrics if no synced found",
    "force": "Redo files that already have synced lyrics",
    "recursive": "Include subfolders",
    "dry_run": "Dry run (change nothing)",
    "no_embed": "Write .lrc sidecars only (do not embed)",
    "no_lyrics": "Download only, skip lyrics",
    "lyrics_only": "Lyrics only, skip download",
    "lyrics_dir": "Lyrics folder",
    "providers": "Provider order (comma separated)",
    "shift": "Shift timings (seconds)",
    "delay": "Delay between requests (s)",
    "limit": "Stop after N fixed files",
    "output_dir": "Output folder",
    "template": "Filename template",
    "audio_format": "Audio format",
    "audio_quality": "Audio quality (0 best)",
    "keep_video": "Keep video (no audio extract)",
    "embed_thumbnail": "Embed cover art",
    "embed_metadata": "Embed metadata",
    "embed_chapters": "Embed chapters",
    "ascii_names": "ASCII-only filenames",
    "cookies": "cookies.txt (optional)",
    "cookies_from_browser": "Cookies from browser",
    "proxy": "Proxy",
    "rate_limit": "Speed limit (e.g. 2M)",
    "concurrent": "Parallel fragments",
    "download_archive": "Download archive file",
    "playlist_items": "Playlist items (e.g. 1-50)",
    "no_playlist": "Single video only",
    "yes_playlist": "Whole playlist",
    "min_chars": "Ignore lyrics shorter than (chars)",
    "suspicious_only": "Only groups with different titles",
    "fix": "Re-fetch lyrics for the groups found",
    "json": "JSON output",
    "test": "Make a live probe request",
    "quality": "Video quality (height, e.g. 720)",
    "max_size": "Max file size (e.g. 250M)",
    "format_selector": "yt-dlp -f selector",
    "merge_format": "Merge video+audio into",
    "embed_subs": "Embed subtitles",
    "sub_langs": "Subtitle languages",
    "remote_components": "Remote components (e.g. ejs:github)",
    "js_runtimes": "JavaScript runtime (e.g. node)",
    "with_cookies": "Check using my cookies/login instead",
}

# Options that are used constantly and must be visible without scrolling. They
# are the same variables as the generated widgets -- this is a second view of
# them, not a second copy, so nothing can drift out of sync.
QUICK = {
    "lyrics": [
        ("allow_unsynced", "Accept plain (unsynced) lyrics if no synced found"),
        ("force", "Redo everything, even files that already have lyrics"),
        ("recursive", "Include subfolders"),
        ("dry_run", "Dry run (change nothing)"),
        ("no_embed", "Write .lrc only (do not embed)"),
    ],
    "get": [
        ("no_playlist", "This video only, not its playlist"),
        ("allow_unsynced", "Accept plain (unsynced) lyrics if no synced found"),
        ("no_lyrics", "Download only, skip lyrics"),
        ("lyrics_only", "Lyrics only, skip download"),
        ("keep_video", "Keep video (no audio extract)"),
        ("force", "Redo lyrics even if already synced"),
    ],
    "duplicates": [
        ("fix", "Re-fetch lyrics for the groups found"),
        ("suspicious_only", "Only groups with different titles"),
        ("allow_unsynced", "Accept plain (unsynced) lyrics if no synced found"),
        ("force", "Redo even if already synced"),
        ("recursive", "Include subfolders"),
    ],
    "scan": [
        ("recursive", "Include subfolders"),
        ("json", "JSON output"),
    ],
}


# --------------------------------------------------------------------------
# parser introspection
# --------------------------------------------------------------------------
def _subparsers(parser):
    for a in parser._actions:
        if isinstance(a, argparse._SubParsersAction):
            return a.choices
    return {}


def subcommands(parser=None):
    if parser is None:
        from .cli import build_parser
        parser = build_parser()
    return list(_subparsers(parser))


def sub_actions(sub, parser=None):
    """{dest: action} for one subcommand, in declaration order."""
    if parser is None:
        from .cli import build_parser
        parser = build_parser()
    sp = _subparsers(parser).get(sub)
    if sp is None:
        return {}
    return {a.dest: a for a in sp._actions if a.dest != argparse.SUPPRESS}


def option_dests(sub, parser=None):
    """Dests that have a flag (i.e. are settable options, not positionals)."""
    return [d for d, a in sub_actions(sub, parser).items() if a.option_strings]


def positional_dests(sub, parser=None):
    return [d for d, a in sub_actions(sub, parser).items() if not a.option_strings]


def is_toggle_true(a):
    return isinstance(a, argparse._StoreTrueAction)


def is_toggle_false(a):
    return isinstance(a, argparse._StoreFalseAction)


def long_flag(a):
    return next((o for o in a.option_strings if o.startswith("--")), a.option_strings[0])


def default_for(a, cfg=None):
    """Starting value for a widget: saved config first, then the CLI default."""
    if cfg and a.dest in cfg and cfg[a.dest] is not None:
        return cfg[a.dest]
    if is_toggle_false(a):
        return True if a.default is None else bool(a.default)
    if is_toggle_true(a):
        return bool(a.default)
    return "" if a.default is None else a.default


# --------------------------------------------------------------------------
# widget specs -- the GUI builds its widgets from these, so coverage of the CLI
# surface can be asserted headlessly without tkinter.
# --------------------------------------------------------------------------
SKIP_DESTS = {"help"}


def widget_specs(sub, cfg=None, parser=None):
    """One dict per settable option: dest, kind, label, default, group, choices."""
    actions = sub_actions(sub, parser)
    out = []
    for dest in option_dests(sub, parser):
        if dest in SKIP_DESTS:
            continue
        act = actions[dest]
        if is_toggle_true(act):
            kind = "check"
        elif is_toggle_false(act):
            kind = "check_off"
        elif act.choices:
            kind = "choice"
        else:
            kind = "text"
        out.append({
            "dest": dest,
            "kind": kind,
            "label": LABELS.get(dest, dest.replace("_", " ")),
            "flag": long_flag(act),
            "group": getattr(act.container, "title", None) or "options",
            "choices": list(act.choices) if act.choices else None,
            "default": default_for(act, cfg),
            "browse": PATH_DESTS.get(dest),
            "hint": HINTS.get(dest, ""),
        })
    return out


def quick_specs(sub, cfg=None, parser=None):
    """The always-visible subset of widget_specs for one subcommand.

    Only toggles qualify (a text field needs its own row), and every dest must
    really exist on the subcommand -- a stale entry raises KeyError here rather
    than silently producing a dead checkbox.
    """
    specs = {s["dest"]: s for s in widget_specs(sub, cfg, parser)}
    out = []
    for dest, label in QUICK.get(sub, []):
        s = dict(specs[dest])
        s["label"] = label
        if s["kind"] not in ("check", "check_off"):
            raise ValueError(f"{sub}: {dest} is not a toggle, cannot be a quick option")
        out.append(s)
    return out


# --------------------------------------------------------------------------
# argv construction
# --------------------------------------------------------------------------
def build_argv(sub, values, positionals=None, parser=None):
    """Build the argv list for `sub` from a dict keyed by argparse dest."""
    actions = sub_actions(sub, parser)
    argv = [sub] + [str(p) for p in (positionals or [])]
    for dest in option_dests(sub, parser):
        if dest == "help" or dest not in values:
            continue
        act = actions[dest]
        v = values[dest]
        flag = long_flag(act)
        if is_toggle_true(act):
            if v:
                argv.append(flag)
        elif is_toggle_false(act):
            if v is False:
                argv.append(flag)
        elif act.nargs in ("+", "*") or isinstance(v, (list, tuple)):
            items = v if isinstance(v, (list, tuple)) else str(v).split()
            if items:
                argv.append(flag)
                argv += [str(i) for i in items]
        elif v is None or (isinstance(v, str) and not v.strip()):
            continue
        else:
            argv += [flag, str(v)]
    return argv


def quote_cmd(argv):
    """Human-readable form of an argv, for display in the GUI."""
    out = ["musictool"]
    for a in argv:
        out.append(f'"{a}"' if (" " in a or a.startswith("-") and " " in a) else a)
    return " ".join(out)


# --------------------------------------------------------------------------
# path history
# --------------------------------------------------------------------------
def add_path(cfg, path, key="recent_paths"):
    """Remember a path, most-recent-first, deduplicated and capped."""
    if not path:
        return cfg
    p = os.path.abspath(os.path.expanduser(str(path)))
    hist = [x for x in (cfg.get(key) or []) if x != p]
    hist.insert(0, p)
    cfg[key] = hist[:MAX_RECENT]
    return cfg


def recent_paths(cfg, key="recent_paths"):
    return list(cfg.get(key) or [])


def add_url(cfg, url, key="recent_urls"):
    if not url:
        return cfg
    hist = [x for x in (cfg.get(key) or []) if x != url]
    hist.insert(0, url)
    cfg[key] = hist[:MAX_RECENT]
    return cfg


# --------------------------------------------------------------------------
# report parsing
# --------------------------------------------------------------------------
COUNT_RE = re.compile(r"^([a-z][a-z ]*?)\s*:\s+(\d+)\s*$")
SECTION_RE = re.compile(r"^(FIXED|UNRESOLVED|UNREADABLE)\s*\((\d+)\):\s*$")
ENTRY_RE = re.compile(r"^\s+\[(?P<prov>[^\]/]+)/(?P<kind>[^\]]+)\]\s+(?P<name>.+?)"
                      r"(?:\s{2,}(?P<note>.*))?$")
MISS_RE = re.compile(r"^\s+(?P<name>.+?)\s+--\s+(?P<why>.*)$")


def parse_report(text):
    """Parse lyrics-report.txt into counts + categorised entries."""
    out = {"counts": {}, "fixed": [], "unresolved": [], "unreadable": [],
           "by_provider": {}, "by_kind": {}}
    section = None
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        m = SECTION_RE.match(line)
        if m:
            section = m.group(1).lower()
            continue
        m = COUNT_RE.match(line)
        if m and section is None:
            out["counts"][m.group(1).strip()] = int(m.group(2))
            continue
        if not line.strip():
            continue
        if section == "fixed":
            e = ENTRY_RE.match(line)
            if e:
                d = e.groupdict()
                out["fixed"].append({"provider": d["prov"], "kind": d["kind"],
                                     "name": d["name"].strip(),
                                     "note": (d["note"] or "").strip()})
                out["by_provider"][d["prov"]] = out["by_provider"].get(d["prov"], 0) + 1
                out["by_kind"][d["kind"]] = out["by_kind"].get(d["kind"], 0) + 1
            else:
                out["fixed"].append({"provider": "?", "kind": "?",
                                     "name": line.strip(), "note": ""})
        elif section in ("unresolved", "unreadable"):
            m = MISS_RE.match(line)
            if m:
                out[section].append({"name": m.group("name").strip(),
                                     "why": m.group("why").strip()})
            else:
                out[section].append({"name": line.strip(), "why": ""})
    return out


def load_report(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return parse_report(fh.read())
    except Exception as e:
        return {"counts": {}, "fixed": [], "unresolved": [], "unreadable": [],
                "by_provider": {}, "by_kind": {}, "error": str(e)}


def headline(rep):
    """One-line summary suitable for a status bar."""
    c = rep.get("counts", {})
    total = c.get("scanned", 0)
    fixed = c.get("fixed", 0)
    unres = c.get("unresolved", 0)
    err = c.get("unreadable", 0)
    ok = c.get("already ok", 0)
    pct = f" ({fixed * 100 // total}%)" if total else ""
    return (f"{fixed} fixed{pct} · {ok} already ok · {unres} unresolved · "
            f"{err} unreadable · of {total} scanned")


def report_to_json(rep):
    return json.dumps(rep, ensure_ascii=False, indent=1)


# --------------------------------------------------------------------------
# file inventory + selection
#
# The GUI lets the user tick individual files instead of pointing the tool at a
# whole folder. The rules live here, not in tkinter, so they can be tested.
# --------------------------------------------------------------------------
NEEDS = ("partial", "plain", "empty")


def needs_fix(info):
    """True when this file is not already carrying usable synced lyrics."""
    if info.get("error"):
        return True
    return info.get("state") in NEEDS


def row_state(info):
    if info.get("error"):
        return "unreadable"
    return info.get("state") or "empty"


def inventory(paths, recursive=False, lyrics_dir=None, min_chars=40):
    """Scan `paths` offline -> {'root','rows','counts'}.

    Each row: id, path, name, state, needs, title, artist, duration, note, sel.
    `sel` starts True so "run" means "every file" until the user narrows it.
    """
    from . import lyrics as lyricsmod

    root = common_root(paths)
    files = lyricsmod.gather(paths, (lyrics_dir,) if lyrics_dir else (),
                             recursive=recursive)
    # Flag files whose lyrics are shared with another file: that is the
    # wrong-lyrics damage, and it is the thing worth re-doing.
    shared = {}
    for f in files:
        fp = lyricsmod.fingerprint(f.get("lyrics"), min_chars)
        if fp:
            shared.setdefault(fp, []).append(f["path"])
    rows = []
    for i, f in enumerate(files):
        fa, ft = from_filename(f["path"])
        fp = lyricsmod.fingerprint(f.get("lyrics"), min_chars)
        note = f.get("error") or ""
        if fp and len(shared.get(fp, ())) > 1:
            note = ("shares lyrics with %d other file(s)" % (len(shared[fp]) - 1)
                    + ((" -- " + note) if note else ""))
        rows.append({
            "id": i,
            "path": f["path"],
            "name": os.path.basename(f["path"]),
            "state": row_state(f),
            "needs": needs_fix(f),
            "title": f.get("title") or ft,
            "artist": f.get("artist") or fa,
            "duration": round(float(f.get("duration") or 0.0), 1),
            "note": note,
            "sel": True,
        })
    counts = {}
    for r in rows:
        counts[r["state"]] = counts.get(r["state"], 0) + 1
    counts["total"] = len(rows)
    counts["needing"] = sum(1 for r in rows if r["needs"])
    return {"root": root, "rows": rows, "counts": counts}


def pick_paths(rows, root):
    """What to hand the CLI: the folder when everything is ticked, else files.

    Passing the folder keeps argv short and lets the CLI use its own scan; a
    subset is passed as explicit file paths so only those are touched.
    -> (positionals, note)
    """
    rows = list(rows or [])
    if not rows:
        return [root], "whole folder"
    sel = [r for r in rows if r.get("sel")]
    if len(sel) == len(rows):
        return [root], f"all {len(rows)} files (whole folder)"
    if not sel:
        return [], "nothing selected"
    return [r["path"] for r in sel], f"{len(sel)} of {len(rows)} files"


def select(rows, mode):
    """Apply a bulk selection mode in place; returns the number now selected."""
    for r in rows:
        if mode == "all":
            r["sel"] = True
        elif mode == "none":
            r["sel"] = False
        elif mode == "invert":
            r["sel"] = not r.get("sel")
        elif mode == "needs":
            r["sel"] = bool(r.get("needs"))
        elif mode == "ok":
            r["sel"] = not r.get("needs")
        elif mode == "shared":
            r["sel"] = "shares lyrics" in (r.get("note") or "")
        else:
            raise ValueError(f"unknown selection mode: {mode}")
    return sum(1 for r in rows if r.get("sel"))


SELECTION_MODES = ("all", "none", "invert", "needs", "ok", "shared")


# --------------------------------------------------------------------------
# window layout
# --------------------------------------------------------------------------
def pane_split(total, log_frac=0.34, min_top=170, min_log=90):
    """Y of the sash between the tabs and the log, for a window `total` tall.

    The log sits BELOW the sash, so its height is total - y: to give the log a
    third of the window the sash has to go at two thirds. The tabs keep at
    least min_top and the log at least min_log.
    """
    total = max(int(total), min_top + min_log)
    y = int(total * (1.0 - log_frac))
    y = max(y, min_top)
    return min(y, total - min_log)


def move_sash(current_y, total, delta_y, min_top=170, min_log=90):
    """New sash Y after dragging it by `delta_y` px, clamped to the window.

    Positive delta_y moves the bar down, which makes the LOG shorter, so the
    GUI negates its "taller log" button. The bar cannot go above min_top (the
    tabs would vanish) nor below total - min_log (the log would).
    """
    y = int(current_y) + int(delta_y)
    y = max(y, min_top)
    return min(y, max(min_top, int(total) - min_log))
