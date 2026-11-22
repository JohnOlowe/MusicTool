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
                   ├──▶ live.py ──▶ download.py, (ffmpeg for the watch copy)
                   ├──▶ parts.py ──▶ (ffmpeg: one packet for the keyframe)
                   ├──▶ chat.py ──▶ (yt-dlp for the replay, ffmpeg to embed)
                   ├──▶ formats.py ──▶ (yt-dlp -J output)
                   ├──▶ lyrics.py ──▶ providers.py ──▶ tags.py ──▶ mutagen
                   ├──▶ config.py      saved defaults, one JSON file
                   ├──▶ cancel.py      Stop / Force stop, every subprocess
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
| `cli.py` | 1799 | argparse for all 15 subcommands, and every `cmd_*` handler; `_plan_part` / `_download_part` for `get --range` |
| `gui.py` | 2621 | tkinter GUI, 9 tabs, the log pane, the progress panel, the queue, Stop / Force stop |
| `uicmd.py` | 672 | parser → widget specs, `build_argv`, intent modes, pane geometry |
| `formats.py` | 887 | quality rows, collapsing near-duplicates, filters, language picking, the audio-track list, `exact_plan` / `merge_container` / `matching_audio` for `--exact` |
| `download.py` | 893 | yt-dlp invocation (`runtime_args` for every call; `ffmpeg_reconnect_args` / `http_opts` for a part: reconnect, and 10 MB requests when ffmpeg has `-request_size`), retry pacing, `_error_text` / `retryable` (is a failure a network one), the videoId index, stream probing, `swap_audio` |
| `live.py` | 990 | live streams: `plan` (what `get` changes for one), `scan`/`join`/`watch_copy` for the leftover pieces, `resume`, `after_failure` |
| `chat.py` | 874 | chat replay: `load` (yt-dlp's JSON lines), `windows` (which messages are on screen when), `to_ass`/`to_srt`/`to_vtt`/`to_txt`, `fetch`, `find_videos`/`part_from_name`, `first_times` (where the sound starts), `embed`, `run` |
| `parts.py` | 438 | `get --range` pieces: `keyframe_before` (where a part really starts), `trim`/`salvage` (a fragmented `.part` cut to its last whole piece and renamed to what it holds), `coverage`/`continue_point` (how far the files already go), `drop_temps`, `estimate_bytes`/`free_bytes`/`fs_type`/`is_fat` (the Android card behind FUSE) |
| `util.py` | 614 | filename normalisation, LRC parse/shift, HTTP (User-Agent, `Retry-After`, the response cache), browser/runtime detection, `parse_time`/`parse_range`/`part_label` |
| `providers.py` | 379 | the three lyrics sources and candidate scoring |
| `lyrics.py` | 275 | gather → process → embed |
| `progress.py` | 431 | parses yt-dlp's progress lines (per stream, retries, skips), and ffmpeg's for a part (position, its noise), into a `State` |
| `tags.py` | 143 | mutagen tag read/write with backup, `.lrc` sidecar handling |
| `config.py` | 111 | `DEFAULTS`, `load()`, `remember(**kv)`, `PATH` |
| `cancel.py` | 303 | the stop flag shared by every worker; `popen`/`run` (every long-running program goes through them so Force stop can find it), `force`, `kill_tree` (with children: `taskkill /T` on Windows, `/proc` or `ps` elsewhere), `stop_process`, a `sleep` that Stop cuts short |

Functions worth knowing by name because they are load-bearing:

- `formats.same_language(a, b)` — containers tag audio with 3-letter codes
  (`eng`, `spa`) while YouTube and the picker use 2-letter ones (`en`, `es`).
  `"spa".startswith("es")` is **False**. Every language comparison goes through
  this function, and it works **in both directions**: `same_language("en",
  "eng")` and `same_language("eng", "en")` are both True, as are
  `fre`/`fra`/`fr` and `pt-BR`/`por` (helper `_codes_for`). Until 2026-11-18 it
  only mapped a 2-letter *wanted* code to 3-letter tags, so `--audio-lang
  eng` did not match YouTube's `en` tracks.
- `formats.audio_tracks(rows, lang)` → `(tracks, note)` — the audio-only rows
  in one language (`"all"`/`"any"` = every language), original first, then by
  bitrate. Untagged tracks are kept with a note; a language the video lacks
  gives `[]` and a note naming the ones it has. The Audio tracks tab and
  `pair_with_audio`/`auto_pick(…, lang=, pinned=)` build on it, so the Quality
  tab pairs video with audio in the right language, or with the pinned track.
- `util.Http.json(url, …)` — every lyrics request. The response cache is keyed
  on method, URL, params **and body**; until 2026-11-18 a dict body was left
  out of the key, so every YouTube Music POST shared one entry and each song
  after the first got the first song's lyrics. Large answers are kept out of
  `http-cache.json`: `ytmusic` passes `use_cache=False` and caches its own
  compact per-videoId result, `lrclib` passes `shrink=` to keep only the
  fields it reads. Entries written under the old key are dropped on load.
- `download.probe(url, o)` — the `-J` read behind `check`, `formats`, the
  quality/language picks and the Quality/Audio tracks tabs. It passes
  `--ignore-no-formats-error` (so a failed JS challenge returns the info with
  only storyboards instead of dying in format selection) and keeps yt-dlp's
  warnings in `info["_warnings"]`; `download.no_media_reason(info)` turns
  "no audio/video rows" into the fix. Every caller checks it.
- `download.runtime_args(o)` — `--remote-components`/`--js-runtimes` for every
  yt-dlp call (`build_args`, `probe`, `print_target_name`); adds
  `--js-runtimes node` when node is the only runtime. `probe` used to omit
  both, so `formats --js-runtimes node` did nothing.
- `live.plan(info, o, log)` — called by `get` with the probe of a single URL.
  For a live, upcoming or just-ended stream it sets `live_from_start`, one
  worker per stream (`concurrent`), `[%(id)s]` in the template, and — when
  pieces of the same video are already in the folder — their exact name
  (`literal()` doubles any `%`), after `recover()` has undone a half-finished
  join. It prints the size and room. Returns nothing for a normal video.
- `live.join(folder, log, force)` — appends only the pieces that follow the
  `.part` without a gap, 50 at a time: append, fsync, write the `.ytdl` index
  and a `.livejoin` note, then delete the pieces. Every rename/remove goes
  through `retry()` (Windows locks). `watch_copy` remuxes the `.part` plus the
  contiguous pieces into `TITLE (so far).mp4` without changing them.
- `cli._download_part(url, per, info, say, tag, on_file)` — the loop behind
  `get --range`. Each round: keep any `.part` left behind (`parts.salvage`),
  see how far the files go (`coverage` + `continue_point`), start 2 s before
  that on the keyframe (`keyframe_before`; the name from the keyframe, ffmpeg
  asked for `KEY_NUDGE` = 0.15 s after it), and download with
  `download.FRAGMENTED` output and `no_rerun` (download's own retry would
  start the part over). Then decide: done; short (rename, go on); full disk
  (stop, say so); FAT32's 4 GB (go on, new file); 403 (fresh links, go on);
  a network error (wait, go on); "Postprocessing" with the file whole (keep it
  untagged). Three rounds without getting further ends it.
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
- **`pump()` reschedules itself before it does any work** (`gui.py:538`). If it
  reschedules last, one bad event stops the reschedule and freezes the log and
  progress bar for the rest of the session.
- **`log(msg)` adds the newline if the message lacks one.** GUI-side notes
  used to run together on one line; text arriving from `QueueStream` (the CLI's
  own output) already ends in newlines and is unchanged.
- **The Audio tracks tab shares state with the Quality tab.** It uses the same
  URL box and the same loaded format rows (`q_load`). Pinning a track stores
  it per URL (`q_pinned`); loading a different URL clears it. Picking a real
  language writes the Download tab's `audio_lang`; "all" does not.
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

**Redraws never reach the log.** `_make_on_line` sends every event to the
hook but logs only item/file/done, errors and unknown lines; `pct` events
are the bar's alone (in a terminal with no hook they redraw one line with
`\r`, at most 5 a second), retry lines are logged once per 10 s with a
count. `gui.pump()` keeps the newest `pct` per `(tag, stream)` -- video and
audio side by side each keep theirs, and `State` shows the slower one.
yt-dlp writes `ERROR: ` and its message across a `\r`; the two halves are
joined, and an error glued onto a redraw is cut out of it (`unglue`).

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
