# Testing

```bash
python3 tests/test_musictool.py
```

**808 assertions across 33 sections, 0 failures.** About 30 seconds. No network.

Requirements: `mutagen`, and either `ffmpeg` on `PATH` or
`pip install imageio-ffmpeg`. Nothing else — no display, no tkinter, no
yt-dlp login, no YouTube.

---

## What the harness fakes

| thing | stand-in | why |
|---|---|---|
| tkinter | `tests/tkstub.py` | there is no display in CI, and no tkinter on some targets |
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

**Not proven:** pixels. `tkstub` reproduces widget trees, geometry and command
wiring, but renders nothing. Scroll feel, real clipping, real window resizing
and anything platform-specific to Windows are outside what this suite can say.
When you change GUI code, say so out loud.
