# Decisions and hard-won facts

Everything here was established by running code, usually after being wrong once.
If a future change contradicts one of these, re-measure it and update this file
rather than silently working around it.

---

## yt-dlp

### Language selection: only a format filter works

yt-dlp ranks the **original** language first. `--format-sort lang:en` has **no
effect** — `lang` maps to `language_preference`, which defaults to −1 for
everything. The only thing that works is a format filter:

```
ba[language^=en]/ba/b
```

Dubbed audio tracks share an itag base (`140-0`, `140-1`, `140-9`). If language
is not part of every grouping key, `formats.collapse_variants` merges the
dubs into one row and the language information is gone before the picker sees
it.

### Format-selector grammar binds tighter than it looks

`+` binds **tighter** than `/` and `,`, so `A+B/C` parses as `(A+B)/C`. Getting
this wrong does not error — it silently falls through to audio-only.

Suffixed ids (`140-0`, `251-1`, `401`) are real on YouTube's SABR/range
delivery, and **the bare itag may not exist**. A selector naming a bare itag
that does not exist makes the whole `+` branch unmatchable, and the fallback
silently takes over — which is how a "1080p + audio" request quietly becomes a
360p combined file.

`formats.py` therefore emits selectors as `137/137-0`: the plain itag with the
measured copy as fallback.

### Probe a selector offline, with no network

```python
from yt_dlp import YoutubeDL
sel_fn = YoutubeDL({"quiet": True, "simulate": True}).build_format_selector(sel)
sel_fn({"formats": [...], "extractor": "youtube"})
```

This is how the language and quality logic is tested. It needs no network and
no real YouTube response, and it catches grammar mistakes that would otherwise
only show up as a wrong download.

### `--skip-download` never touches a file you already have

With `skip_download`, yt-dlp runs only `MoveFilesAfterDownloadPP` and returns
before `post_process` — so `--skip-download --embed-metadata` on an existing
file is a silent no-op. Embedding into a file on disk needs a real ffmpeg pass.
See `RECIPES.md`.

### Smaller ones

- `--print` implies `--quiet` and `--simulate`.
- `--parse-metadata "uploader:(?P<artist>.+?) - Topic"` never sets `uploader`.
  `--replace-in-metadata uploader " - Topic$" ""` is the fix.
- `-x --audio-format m4a` on an Opus source transcodes Opus → AAC.
- A dead connection is **not** a block. `classify_error` separates
  bot / age / private / members / unavailable / network, and further separates
  the sign-in cascade (dead `cookies.txt`, DPAPI App-Bound Encryption, a locked
  cookie database, no JS runtime, a PoT server on 4416, the reload wall). Each
  has a different fix, and "unknown" is not a diagnosis.

---

## ffmpeg

### A normal mp4's index is written last

Atom order: `ftyp → free → mdat → moov`. A file killed before finalization is
unplayable — `moov atom not found` — even with 18 MB of media already on disk.
This matters for the resume-don't-restart requirement: a partial mp4 is not
resumable media, it is debris. `+faststart` performs a literal second pass
(`Starting second pass: moving the moov atom to the beginning of the file`).

### `-ss` / `-to` ordering changes the answer

| invocation | result on a 60 s file |
|---|---|
| `-ss 10 -to 40 -i in -map 0 -c copy` | 30.08 s ✓ |
| `-ss 10 -i in -t 30 -map 0 -c copy` | 30.08 s ✓ |
| `-ss 10 -i in -to 40 -map 0 -c copy` | **40.08 s ✗** |

`-to` **after** `-i` is measured from the seek point, not from the start of the
file. Put both before `-i`, or use `-t <duration>`.

`-c copy` cuts snap to the previous keyframe: a 10 s request produced 10.16 s
when the seek point was not a keyframe. Only a re-encode is frame-exact.

Input seeking is cheap because it uses the index — a 10-minute cut of a 2h05m
file took 734 ms against 4047 ms for the whole file, and seeking to 1:55:00
took 390 ms. Source length is irrelevant to cut time.

### ffmetadata cannot round-trip newlines

`\n` comes back as the letter `n`. Flatten newlines to spaces, and escape
`\`, `;` and `=` — an unescaped `;` or `=` corrupts the parse silently.

### Re-encoding a file with an attached cover fails

`-map 0 -c:v <codec>` on a file with cover art dies with
`Could not find tag for codec h264 in stream #3`, because the cover is a video
stream. Either `-c:v:0 <codec> -c:v:1 copy -disposition:v:1 attached_pic`, or
`-map -0:v:1` to drop it.

Also: `-map 0:v:0` **drops attachments**. To keep cover art through a re-mux,
map the stream explicitly and set the disposition.

### Containers are pickier than they look

- **AAC cannot go in WebM at all:** `Only VP8 or VP9 or AV1 video and Vorbis or
  Opus audio and WebVTT subtitles are supported for WebM`.
- VP9 + Opus → mp4 with `-c copy` muxes but warns
  `track 1: codec frame size is not set`. Do not ship that to a phone.
- VP9 + AAC → mp4 with `-tag:v vp09 -c copy` muxes cleanly.

### There is no hardware audio encoder

Every QSV encoder and decoder in ffmpeg is video-only. No `*_qsv`, `*_nvenc`,
`*_amf` or `*_vaapi` **audio** encoder exists on any platform. This is why the
advice for a 20-hour file is "let the CPU do the audio, it is not the
bottleneck" — software AAC runs 66–250x realtime.

### Throughput, measured on a 2-core Xeon @ 2.6 GHz

| job | speed | 20 h of video |
|---|---|---|
| VP9 1080p60 **decode** only | 9.84x | 2.0 h |
| → H.264 `-preset ultrafast` | 1.81x | 11.0 h |
| → H.264 `-preset veryfast` | 1.15x | 17.4 h |
| → H.264 `-preset medium` | 0.78x | 25.7 h |
| **audio only** (video copied) | ~66–250x | **5–20 min** |

Audio-only timing varied 14 s (warm cache) to 54 s (cold) for one hour — quote
a range, not a number. A 2-core Braswell Celeron is slower than this machine;
hand the maintainer a calibration command, not a figure.

---

## Code traps

These all caused a real bug at some point.

- **A shared options dict makes per-URL decisions global.** `_resolve_quality`
  memoises into `o["format_selector"]`; passing the same dict to every URL made
  videos 2..N inherit video 1's format ids, so dubbed videos arrived in the
  wrong language while everything reported success. Any per-URL decision needs
  `per = dict(o)`.
- `targets += [...]` inside a nested function raises `UnboundLocalError` — use
  `.extend()` under the lock.
- A `ttk.Checkbutton` added to the options pane breaks the "more boxes than
  toggles" parity assertion; use `ttk.Button`.
- `ttk.PanedWindow.add()` accepts **only** `weight` — no `minsize`. Enforce a
  floor in code (`place_bars` + `BARS_MIN`).
- `ttk.PanedWindow` leaves its sash at 0 until layout. Placing it during
  construction silently does nothing, which is why the progress bars opened
  invisible; the first `<Configure>` places it.
- `ScrollArea._over_me` returned False for any `tk.Text`, so the URL box ate
  the mouse wheel.
- A stale rebinding can hide behind a working-looking control: the Stop button
  was dead because a later line set `stop_btn = None`, so `busy()`'s
  `if stop_btn is not None` never fired. Assert what a control *does*, not that
  it exists.
- `gui.get_label()` is defined *after* the build-time `bget` button, which must
  keep its literal text — `apply_mode` reconfigures the label every pump tick.
- Filename-derived `syncedlyrics` queries match nothing.
- A patch script whose `old` text does not match **silently no-ops**. Assert on
  the match, and note that writing a script to disk does not run it.
- Existing assertions can encode the bug. When a test fails, check whether the
  test is right before changing the code — and the reverse.
