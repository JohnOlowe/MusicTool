# Testing

```bash
python3 tests/test_musictool.py
```

**1107 assertions across 43 sections (plus 8b), 0 failures** — with or without
tkinter installed, with or without a display. About 40 seconds. No network.

Requirements: `mutagen`, and either `ffmpeg` on `PATH` or
`pip install imageio-ffmpeg`. Nothing else — no display, no yt-dlp login, no
YouTube.

**The `gui` subprocess check is hermetic.** Section 11 runs the real
`musictool.py gui` with a stand-in `tkinter` package first on `PYTHONPATH` that
refuses to import, and asserts exit 1, "GUI unavailable" and no traceback.
Section 35 covers the other case — tkinter present but no display (`TclError`
from `Tk()`) — in-process. (Until 2026-11-18 the check ran against whatever
tkinter the machine had, so it failed wherever tkinter was installed.)

Everything else in the GUI is tested through `tkstub` below and does not care
whether tkinter is installed.

---

## What the harness fakes

| thing | stand-in | why |
|---|---|---|
| tkinter | `tests/tkstub.py` in-process; for section 11's `gui` subprocess, a `$WORK/notk/tkinter` package that raises `ImportError` | there is no display in CI, and no tkinter on some targets |
| yt-dlp | an executable on `PATH` | downloads must be tested without downloading |
| the three lyrics APIs | a local `http.server` | providers must be tested against real HTTP, but not real providers |
| ffmpeg | the real thing | container behaviour is exactly what cannot be faked |

Working directory is `WORK = "/tmp/musictool-tests"`; subprocesses get
`XDG_CONFIG_HOME=$WORK/cfg` and `PYTHONPATH=<repo root>` so config writes never
escape the test run.

`tkstub` is not a mock — it builds the **real widget tree** with the real
geometry manager calls, then lets the tests inspect it:

- `w.geo` — `("pack"|"grid"|"place", kwargs, master)`, or `None` if never placed.
  **Visibility is `w.geo`, not a flag.**
- `w.children` — the real child list, in creation order.
- `bind(seq, func)` / `fire(seq)` — record and replay event bindings.
- `winfo_width()` returns **900**, so width-dependent layout code takes a
  deterministic branch.
- `sashpos(i, newpos)` records into `self.sashes`, so paned-window geometry is
  assertable.
- `after(ms, func)` appends to `w.pending`; **timers only run when the test
  drains them.**
- `close_window()` invokes the `WM_DELETE_WINDOW` handler, i.e. the X button.
- `tkstub.ROOTS[-1]` is the most recent window; `tkstub.reset()` clears state.

`tkstub.install()` patches only the `tkinter*` modules. **`threading` is real.**

---

## Traps that will cost you an afternoon

- **GUI jobs run inline.** Line ~775 of the test file sets
  `G.threading = _FakeThreading()` whose `Thread` is synchronous. Anything that
  needs a job *still running* — the queue, concurrency, the progress panel —
  must swap the real module back and restore it afterwards:
  ```python
  _ft = G.threading
  G.threading = threading
  ...
  G.threading = _ft
  ```
  This is the whole cause of most apparent "queue is broken" failures. It is
  not a GUI bug.

- **Section 31 presses the X**, which stops the pump. Everything after it runs
  against a dead window. Build a fresh one:
  ```python
  tkstub.reset(); G.run(); root = tkstub.ROOTS[-1]
  ```

- **The pump applies a `FINISH` event last within a single tick.** Drain once
  before asserting on what a run reported, or you will read the state from
  before the final event.

- **`contextlib.redirect_stdout` in `gui.worker` is process-global.** While a
  job runs, every thread's `print` lands in the GUI log and vanishes from the
  test's stdout. Debug by writing to a file; `2>&1 | tail` will hide the lines.

- **`areas` is rebound to a list** partway through the test file. Never index
  it as a dict.

- **Config isolation:** in-process tests patch `configmod.PATH` (the module is
  imported under the alias `_cfg`, not `configmod`).

- **`_resolve_quality` prints no `quality:` line** unless a size or format
  constraint exists — `--jobs` tests need `--max-size` to see it.

- **`--dry-run` never calls `download.download`.** Monkeypatch it to capture
  what would have been passed.

- **`"Download" in text` matches two buttons** (`Download + lyrics` and
  `Download this quality`). Select by exact text.

- `grep -E "PASS|FAIL"` truncates multi-line failure details. Read the raw
  tail; a missing summary line means the suite crashed, not that it passed.

---

## What the suite proves, and what it cannot

Proven by execution:

- filename normalisation against real messy names
- LRC parse / shift / classify, and synced-over-unsynced precedence
- tag read / write / backup on real `.m4a` files
- all three providers against stub APIs replaying documented response shapes,
  including `WEB_REMIX` returning no timings and `ANDROID_MUSIC` returning cues
- the candidate-rejection guard, so an unrelated sidecar cannot hijack a scan
- quality selection: 720p pairing, size caps, smallest-fit, audio-only,
  nothing-fits, storyboard filtering, offline format-selector probing
- block classification, and CLI runs of `check`/`formats`/`get` against a fake
  yt-dlp that answers `-J` the way YouTube does
- the whole GUI: built, buttons pressed, boxes ticked, argv captured,
  panes and scroll areas measured, intent modes applied, progress events
  rendered into labels and bars
- audio tracks (section 34): the per-language list on a dubbed video
  (original first, then by bitrate), 2- and 3-letter codes matching both ways
  (`en`/`eng`, `fr`/`fre`/`fra`, `pt-BR`/`por`), untagged tracks shown with a
  note, a language the video lacks giving an empty list that names the ones it
  has, the Audio tracks tab's download argv (`--format-selector <id>`, never
  `--keep-video`), pinning a track so the Quality tab pairs `<video>+<id>`,
  the pin being dropped for a new URL, and **all** not overwriting
  `--audio-lang`
- review fixes (section 35): `Retry-After` in seconds and as an HTTP date,
  the default and the 120 s cap, the lrclib-compliant User-Agent (and that
  it is what gets sent), Firefox-first cookie advice, the removed
  Genius/Musixmatch settings, the `pyproject.toml` URLs, and `gui` with tkinter
  but no display exiting 1 with a message instead of a traceback
- live-API drift (section 36), each from a real request on 2026-11-18: two
  POSTs with different bodies are two cache entries (the old key made every
  song share the first song's YouTube Music lyrics — this check fails against
  the old code); ytmusic rows without `cueRange` become an unsynced trusted
  candidate, used only with `--allow-unsynced`; ytmusic caches a compact
  per-videoId result, not the raw page; poisoned cache entries are dropped on
  load; NetEase's encrypted `/web` answer is "no results", `/search/get` is
  used, and its credit lines are stripped; lrclib records are trimmed to the
  fields read; `runtime_args` enables node when it is the only runtime, an
  explicit `--js-runtimes` wins, and `probe` passes both runtime flags
- audio in the format list (section 37), against a second fake yt-dlp that
  answers like the real one for a dubbed video and for a failed JS challenge
  (storyboards only, the warning on stderr, `Requested format is not
  available` unless `--ignore-no-formats-error`): `formats --show-audio`
  lists audio in `--audio-lang` only, says how many it hid, falls back to every
  language when there is none in yours, `any` shows all, music mode filters
  too and suggests an audio download; the empty list names the JS challenge in
  `formats`, `check` (`NO STREAMS`, its own count) and `get --quality`; a failed
  probe leads with the ERROR and carries the explaining warning; and in the GUI
  the **+ audio formats** tick beside **List qualities + sizes** adds
  `--show-audio` with the tab's `--audio-lang`, the Quality/Audio probe gets
  `--js-runtimes`, and an empty Audio tracks list explains itself. Undoing
  the tick or the probe flag fails six of these.
- live streams (section 38), on real fragmented mp4 made by ffmpeg and cut
  into the pieces yt-dlp leaves (`NAME.f299.mp4.part-FragN`, a `.ytdl`
  index): joining is byte-exact and plays; it survives Windows locks that
  clear (and reports one that does not), a cut between appending and
  recording (no piece added twice), a missing or empty piece (stops at the
  gap), and refuses a folder still being written unless `--force`; the watch
  copy plays and leaves the pieces alone; `plan` sets from-start, one worker
  per stream, the id in the name and the leftover name with `%` doubled;
  `build_args`/`probe` carry the live flags; a full disk is `disk_full` with
  advice; and end to end through `get` (no lyrics for a live recording),
  `check`, `formats`, `live --status/--join/--watch/--resume` (both the
  same-pieces and the `(rest)` path) and the Live streams tab's buttons.
- exact streams, quiet progress, drops (section 39): `--exact` accepts only
  `+`-joined ids, refuses missing ids before downloading (rc 1) and
  selectors yt-dlp could choose from (rc 2), picks mp4/webm/mkv by the
  streams (mkv for webm with a thumbnail), keeps audio's own codec with no
  `--audio-quality`; redraws without a % and with a `2:` stream prefix
  parse; 300 redraws + 40 retries give at most 5 log lines; a bare or
  glued `ERROR:` is logged whole; the terminal redraw is one `\r` line;
  `retryable` follows yt-dlp's final ERROR; the bar follows the slower
  stream; the Live streams tab's stream pickers produce `--exact -f 315+251`.
  The copy-only claim itself (packet checksums) was checked outside the
  suite against real yt-dlp; see README.
- chat replay and parts (section 40), on a synthetic replay in yt-dlp's
  shape (no real user's messages are committed): times (`60:00` is 60
  minutes; `1:60:00` is refused) and ranges; loading text, emoji (character vs
  a channel's `:name:`), Super Chat and roles, with placeholders, deleted
  messages and a broken line left out; `windows` for lines 1 (one at a time),
  3 (the 4th pushes the 1st off), hold 15 (a quiet chat clears) and hold 0,
  a part carrying over what was on screen, and offset; the ASS column,
  alignment and escaping, SRT `{\an3}`/pad/BOM, VTT positions, the TXT log;
  finding the video by `[id]`, never overwriting someone else's subtitle,
  replacing our own; `chat FILE.json` timing from the name, `--range`
  overriding it; `chat URL` downloading only the chat and never twice
  (fake yt-dlp); `--embed` making an mkv from an mp4 and replacing, not
  doubling, the track; a cut MP4 (picture below zero) giving an mkv whose
  chat starts at the cut, and a sidecar next to such an mkv timed to its
  sound; `get --range` building `*3600-inf` / `*1800-3600` with the ffmpeg
  reconnect args and the `(from 1h00m00s)` name, refusing past the end and on
  a live stream; ffmpeg's status line becoming a percentage with time left,
  and its chatter kept out of the log; and the Live streams tab's **Download this part** / **Get the chat** argv.
- parts in pieces (section 41): an ffmpeg that lists `-request_size` gets
  `-multiple_requests 1 -request_size 10485760 -short_seek_size 10485760`
  after the reconnect options; an older one gets only the reconnect options,
  and `get --range` then warns that the part comes at about 2x real time,
  with how long that is.
- Force stop (section 42), on real processes that start a child of their own
  which keeps writing a heartbeat file (yt-dlp starting ffmpeg, in miniature;
  portable, with no signals used to check): Stop ends the child too;
  `cancel.force()` from another thread ends a blocking `cancel.run` and its
  child at once; a program started after it is ended as it starts; `reset()`
  clears it; `run` returns and times out like `subprocess.run`; `cancel.sleep(60)`
  returns as soon as Stop is pressed; Ctrl+C once is a normal stop and twice is
  Force stop; the GUI's Force stop button is never greyed out, arms nothing when
  idle, and mid-run ends what the job started and logs it.
- unfinished parts (section 43): names parsed and relabelled without ever
  taking an existing file's name; `continue_point` over overlaps and gaps;
  the size estimate; an Android card's file system found behind FUSE (a
  sample `/proc/mounts`); `keyframe_before` reading ffmpeg's one-packet answer
  (a fake ffmpeg), and refusing HLS, audio only, expressions and a far-off
  answer; `get --range` asking for fragmented MP4. With real ffmpeg-made
  files: a `.part` cut mid-write is trimmed to whole pieces, renamed to what
  it holds, its cover removed, and reads through with no error; an old-style
  `.part` is left alone and said so; an empty shell is removed. Then
  `_download_part` with `download.download` faked but the files real: the
  keyframe name and `*99.15-103`; the start written in full (`*14430.15`);
  a rerun fetching nothing; disk full keeping a playable file and the rerun
  carrying on with overlap, no gap; FAT32's "File too large", and Windows'
  "not enough space" on a FAT32 drive, both going on into a new file; 403
  fetching fresh links; a short file without an error renamed and continued;
  a failed title/cover step keeping the whole file and removing the half
  copy; any other error passed on.

**Not proven:** pixels. `tkstub` reproduces widget trees, geometry and command
wiring, but renders nothing. Scroll feel, real clipping, real window resizing
and anything platform-specific to Windows are outside what this suite can say.
When you change GUI code, say so out loud.
