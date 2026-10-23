"""Persistent JSON config, per-platform location."""

import json
import os

from .util import config_dir

DEFAULTS = {
    # download
    "output_dir": ".",
    "template": "%(uploader)s - %(title)s.%(ext)s",
    "audio_format": "m4a",
    "audio_quality": 4,
    "keep_video": False,
    "embed_metadata": True,
    "embed_thumbnail": True,
    "embed_chapters": False,
    "ascii_names": False,
    "cookies": "",
    # "auto" = use whichever installed browser has the newest cookie database
    # (Windows: usually Edge or Chrome). "off" = never touch the browser.
    "cookies_from_browser": "auto",
    "proxy": "",
    "rate_limit": "",
    "concurrent": 4,
    "retries": 5,
    "download_archive": "",
    # lyrics
    "lyrics_dir": "Lyrics",
    "provider_order": ["ytmusic", "lrclib", "netease"],
    "allow_unsynced": False,
    "delay": 0.35,
    "recursive": False,
    # GUI layout
    "progress_width": 0,
    # api keys (optional)
    "genius_token": "",
    "musixmatch_key": "",
}

PATH = os.path.join(config_dir(), "config.json")


def load():
    cfg = dict(DEFAULTS)
    if os.path.exists(PATH):
        try:
            cfg.update(json.load(open(PATH, encoding="utf-8")))
        except Exception:
            pass
    # environment overrides, handy on Termux where editing JSON is awkward
    env = {"MUSICTOOL_COOKIES": "cookies", "MUSICTOOL_OUT": "output_dir",
           "MUSICTOOL_PROVIDERS": "provider_order",
           "MUSICTOOL_GENIUS": "genius_token",
           "MUSICTOOL_MUSIXMATCH": "musixmatch_key"}
    for var, key in env.items():
        if os.environ.get(var):
            v = os.environ[var]
            cfg[key] = v.split(",") if key == "provider_order" else v
    return cfg


def remember(**kv):
    """Persist a few keys -- e.g. the output folder the user just used.

    Called with the resolved value so the next run (CLI or GUI) starts there;
    the GUI reads its widget defaults from this same file.
    """
    cfg = load()
    changed = False
    for k, v in kv.items():
        if v is None:
            continue
        if cfg.get(k) != v:
            cfg[k] = v
            changed = True
    if changed:
        save(cfg)
    return cfg


def save(cfg):
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, PATH)
    return PATH


def lyrics_dir_for(cfg, base):
    """Lyrics folder for a scan rooted at `base`.

    Never falls back to the current working directory: reading sidecars from an
    unrelated folder silently overrides the tags in the files being scanned.
    """
    d = cfg.get("lyrics_dir") or "Lyrics"
    if os.path.isabs(d):
        return d
    return os.path.join(base, os.path.basename(d.rstrip("/\\")) or "Lyrics")


def merged(cli_overrides):
    """Config file, then CLI flags on top (CLI wins when not None)."""
    cfg = load()
    for k, v in (cli_overrides or {}).items():
        if v is not None:
            cfg[k] = v
    if cfg.get("output_dir"):
        cfg["output_dir"] = os.path.abspath(os.path.expanduser(cfg["output_dir"]))
    # NOTE: lyrics_dir is deliberately NOT resolved here. It is a folder name
    # ("Lyrics") unless the user gave an absolute path; resolving it against
    # output_dir would bind it to the current working directory and make the
    # tool read/write sidecars in an unrelated folder.
    if cfg.get("cookies"):
        cfg["cookies"] = os.path.abspath(os.path.expanduser(cfg["cookies"]))
    return cfg
