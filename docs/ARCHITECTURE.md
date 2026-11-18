# Architecture

Module responsibilities, data flow, and the two designs that are easy to break
without meaning to: the generated GUI and the progress pipeline.

---

## The dependency direction

```
musictool.py ──▶ cli.py ──▶ download.py ──▶ (yt-dlp subprocess)
                   │            │
                   │            └──▶ progress.py ──▶ gui.py (via a hook)
                   │
                   ├──▶ formats.py ──▶ (yt-dlp -J output)
                   ├──▶ lyrics.py ──▶ providers.py ──▶ tags.py ──▶ mutagen
                   ├──▶ config.py      saved defaults, one JSON file
                   ├──▶ cancel.py      one shared stop request
                   └──▶ util.py        normalisation, LRC, HTTP, detection

gui.py ──▶ uicmd.py ──▶ cli.build_parser()
```

Nothing in the package imports `gui`, so the CLI has no tkinter dependency.
`gui` reaches the CLI through `cli_main(argv)` — it builds a command line and
runs it, rather than calling internal functions. That is deliberate: it means a
button cannot drift away from what the same options do on the command line.

---

## Module by module

| module | lines | responsibility |
|---|---|---|
| `cli.py` | 1163 | argparse for all 13 subcommands, and every `cmd_*` handler |
| `gui.py` | 1823 | tkinter GUI, 7 tabs, the log pane, the progress panel, the queue |
| `uicmd.py` | 665 | parser → widget specs, `build_argv`, intent modes, pane geometry |
| `formats.py` | 644 | quality rows, collapsing near-duplicates, filters, language picking |
| `download.py` | 544 | yt-dlp invocation, the videoId index, stream probing, `swap_audio` |
| `util.py` | 487 | filename normalisation, LRC parse/shift, HTTP, browser/runtime detection |
| `providers.py` | 316 | the three lyrics sources and candidate scoring |
| `lyrics.py` | 275 | gather → process → embed |
| `progress.py` | 260 | parses yt-dlp's progress lines into a `State` |
| `tags.py` | 143 | mutagen tag read/write with backup, `.lrc` sidecar handling |
| `config.py` | 116 | `DEFAULTS`, `load()`, `remember(**kv)`, `PATH` |
| `cancel.py` | 82 | one cooperative stop flag shared by every worker |

Two functions worth knowing by name because they are load-bearing:

- `formats.same_language(a, b)` — containers tag audio with 3-letter codes
  (`eng`, `spa`) while YouTube and the picker use 2-letter ones (`en`, `es`).
  `"spa".startswith("es")` is **False**. Every language comparison goes through
  this function.
- `download.streams(path)` — reads stream info by parsing
  `ffmpeg -hide_banner -i` stderr, because `ffprobe` is frequently absent
  (imageio-ffmpeg never ships it; stripped Windows and Termux installs often
  do not either).

---

## The GUI is generated, not written

This is the core design decision and the reason "add a CLI flag" is usually a
one-line change.

```
cli.build_parser()  ──▶  uicmd.widget_specs(sub, cfg, parser)  ──▶  one widget per flag
                                                                          │
user clicks a button  ──▶  uicmd.build_argv(sub, values)  ──▶  cli_main(argv)
```

Every flag on a subcommand becomes a widget. Verified counts at the time of
writing:

| subcommand | widgets |
|---|---|
| `get` | 42 |
| `formats` | 36 |
| `check` | 22 |
| `lyrics` | 12 |
| `relang` | 7 |
| `embed` | 4 |
| `scan` | 3 |
| `extract` | 3 |

Consequences you have to respect:

- **A clipped-off widget counts as a missing option.** The maintainer's display
  is ~1600×720. `OptionsPane` folds rarely-used rows behind a *More options*
  disclosure; a fresh Download pane shows 11 of 42 (31 folded). Folding is by
  "non-empty or changed" — folding by "differs from default" showed 1 of the then-41
  rows, which is not a UI, it is a hiding place.
- **Nothing in a worker thread touches a widget.** `gui.worker` puts events on
  a queue; `pump()` on the main thread drains it every 120 ms. A swallowed
  `AttributeError` in a worker thread looks exactly like a frozen UI.
- **`pump()` reschedules itself before it does any work** (`gui.py:534`). If it
  reschedules last, one bad event stops the reschedule and freezes the log and
  progress bar for the rest of the session.
- **Every tab body is a `ScrollArea`** with a pinned path row above a scrolling
  body, and the mouse wheel is bound with `bind_all` plus an ancestry filter
  (`_over_me`) so scrolling works regardless of focus.
- **Pane geometry is computed, not guessed:**
  `uicmd.pane_split(total, log_frac=0.34, min_top=170, min_log=90)`, and the
  progress pane width is persisted as `progress_width` with a 300 px floor and
  a `BARS_MIN = 160` minimum so the bars can never be dragged out of view.

### Intent modes

Checkboxes are driven by intent, not by the user. `uicmd.MODE_VALUES` holds a
dict per mode (`music_single`, `music_playlist`, `video_single`,
`video_playlist`) and `apply_mode` writes those values into the option widgets.

**A blank in `MODE_VALUES` means "no opinion", and only the three keys in
`MODE_RESET` may ever be cleared by a mode switch:**

```python
MODE_RESET = ("format_selector", "playlist_items", "quality")
```

An earlier version treated a missing key as `None` and wiped the user's format
selector on every mode switch. Do not widen `MODE_RESET` casually.

`scope_of()` / `mode_for()` decide the mode from the URL: a
`music.youtube.com/watch?v=…&list=PL…` link is a **single**, not a playlist.

---

## The progress pipeline

yt-dlp is a subprocess writing progress to a pipe. The path to a pixel is:

```
yt-dlp stdout ─▶ download._make_on_line(log, tag)
             ─▶ progress.parse_ytdlp_line  ─▶ progress.report(ev)
             ─▶ progress.set_hook(pq.put)  ─▶ gui.pump()  ─▶ widgets
```

Two facts that shaped this:

- **yt-dlp only ever sees one URL**, so it cannot tell you how many videos are
  queued behind it. `cmd_get` emits its own `{"type":"batch","i":k,"n":N}`
  event per URL, and `State.remaining()` uses `i-1` finished — because yt-dlp
  prints no per-item "done" line.
- **The hook is a plain function, not a tkinter call**, so `progress.py` stays
  importable and testable with no display. `report()` is the only entry point;
  worker threads only ever `put`.

`State` derives everything from those events: `fraction()`, `remaining()`,
`eta_total()`, `elapsed_text()`. `eta_total()` needs a floor — at t≈0 an
extrapolation returns `0m 00s`, so it gates on `elapsed >= 5.0` and falls back
to the per-file ETA until then.

Parallel downloads (`--jobs N`) tag each URL's log lines `[1] `, `[2] ` so the
panel can keep the slots apart. `--jobs` is capped at 8.

---

## The videoId index

`download.py` writes `.musictool-index.json` (`INDEX_NAME`) into the output
folder, mapping each downloaded file to its videoId and URL. This is what makes
`relang` possible: repairing a file's audio language needs to know which video
it came from, and deriving that from filenames alone is unreliable.

`relang --list` reads only that index plus each file's own tags — no network,
no yt-dlp. That is why it works offline on a folder you already have.
