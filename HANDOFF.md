# Handoff context — MusicTool

For whoever picks this up cold: a human maintainer or another agent. Everything
marked **verified** was established by running the code in this repository;
everything else is flagged as reported-by-the-maintainer or unverified.

Read this and `docs/ARCHITECTURE.md` before changing anything. `README.md` is
the manual for people *using* the tool; this file is for people *working on* it.

---

## 1. What this is, in one paragraph

MusicTool downloads YouTube videos and music, picks the quality you actually
want, fetches **synced** (LRC) lyrics and embeds them, and fixes things up
afterwards — wrong audio language, missing metadata, no cover art. It is a CLI
plus a tkinter GUI generated from the same parser, so a flag added to the CLI
appears in the GUI. It wraps `yt-dlp` and `mutagen`; everything else is stdlib.

It exists because a shell script kept failing: lyrics came back untimed, the
wrong file got matched, and quality choices were silently ignored. Those three
failures shaped most of the design. See §5 — they are still the rules.

The maintainer is **not a developer**. They want commands they can paste,
honest reporting of misses, and no invented answers. That constraint is why
error messages name the specific failure instead of saying "unknown".

---

## 2. Environments

### The target machine (as the maintainer reported it)

| | |
|---|---|
| OS | Windows |
| Python / yt-dlp | 3.14 / 2026.08.19 *(as last reported)* |
| CPU | Intel Celeron N3xxx (Braswell, 2 cores, ~1.6–2.16 GHz) |
| iGPU | Intel HD Graphics 500 (Gen8LP) with Quick Sync |
| Display | ~1600×720 landscape — **vertical space is scarce** |
| Playback target | Tecno Android phone |
| Music folder | `/storage/67FE-19FE/Music/New Gospel Songs` |
| Test playlist | `PLVuMx2J3_K6w` |

**Braswell media capabilities** (sourced, not guessed):
- H.264 **encode**: hardware ✓ — the useful one
- H.264 / VP8 / MPEG-2 / VC-1 decode: hardware ✓
- **VP9 decode: hybrid only** — through ffmpeg on Windows that means the CPU
  does it. Pure hardware VP9 decode arrived on later generations.
- HEVC / VP9 / AV1 encode: not available
- Audio: no hardware path anywhere. There is no hardware audio encoder in
  ffmpeg on any platform (no `*_qsv`/`*_nvenc`/`*_amf`/`*_vaapi` audio encoder).

The 1600×720 display is the single biggest GUI constraint. Prefer compact
pinned rows with scrolling bodies over tall stacked panels.

### Working on it in a sandbox

This project was developed in an ephemeral Linux sandbox that **resets without
warning**: local `HEAD` falls back, tracked files become untracked, and
`ffmpeg`/`yt-dlp`/`mutagen` vanish. If you are in the same kind of environment,
check `git status` and `git rev-parse HEAD` before believing anything, and
reinstall before running the suite:

```bash
pip install --quiet --break-system-packages yt-dlp mutagen imageio-ffmpeg
ln -sf "$(python3 -c 'import imageio_ffmpeg;print(imageio_ffmpeg.get_ffmpeg_exe())')" /usr/local/bin/ffmpeg
```

Facts about that sandbox that cost time to learn:
- imageio-ffmpeg ships `ffmpeg` but **no `ffprobe`** — and so does a stripped
  Windows/Termux install. Anything reading stream info must fall back to parsing
  `ffmpeg -hide_banner -i` stderr. `download.streams()` already does this.
- No tkinter, no Xvfb, no display. **You cannot see pixels. Say so rather than
  implying you checked the UI.**
- Outbound network reaches package registries and nothing else. **No YouTube,
  no lyrics providers.** Anything network-dependent must be faked — the test
  suite already does this, so extend it rather than reaching for the network.
- Bash stdout/stderr are captured, but the GUI redirects both globally while a
  job runs (`contextlib.redirect_stdout`), so a `print` from a test disappears
  mid-job. Write to a file to debug that.

---

## 3. Repository

```
branch   main
layout   flat — the package sits at the repository root
tests    python3 tests/test_musictool.py
```

Current as of the latest commit on `main`: **808 assertions, 0 failures**.

Everything in this repository is MusicTool. There is no unrelated code sharing
the tree, and no subdirectory prefix to remember when quoting paths.

```
./
├── README.md            the manual — keep it current with every change
├── HANDOFF.md           this file
├── CHANGELOG.md         what changed and why, per commit
├── pyproject.toml       installable; provides the `musictool` console script
├── musictool.py         15   shim: `python musictool.py <cmd>`, no install needed
├── musictool/
│   ├── cli.py         1163   argparse + every cmd_* handler
│   ├── gui.py         1823   tkinter GUI, 7 tabs
│   ├── uicmd.py        665   parser -> widget specs, build_argv, intent modes
│   ├── formats.py      644   quality table, filters, language picking
│   ├── download.py     544   yt-dlp invocation, videoId index, streams, swap_audio
│   ├── util.py         487   classify, detect_browser, js_runtime, have_ffmpeg
│   ├── providers.py    316   lyrics sources (ytmusic, lrclib, netease)
│   ├── lyrics.py       275   gather / process / embed
│   ├── progress.py     260   yt-dlp line parsing + batch State
│   ├── tags.py         143   mutagen tag + .lrc handling
│   ├── config.py       116   DEFAULTS, load(), remember(), PATH
│   └── cancel.py        82   cooperative stop
├── tests/
│   ├── test_musictool.py 3394   33 sections, 808 assertions
│   └── tkstub.py          723   headless tkinter stub
└── docs/
    ├── ARCHITECTURE.md    module responsibilities and data flow
    ├── DECISIONS.md       verified facts about yt-dlp and ffmpeg, and the traps
    ├── TESTING.md         how the harness works and what it fakes
    └── RECIPES.md         ffmpeg commands that were measured, not guessed
```

### Commands

`get` · `check` · `formats` · `lyrics` · `scan` · `duplicates`(`dupes`) ·
`extract` · `embed` · `providers` · `config` · `relang` · `doctor` · `gui`

Two that are easy to get wrong:

```
relang PATH... [--audio-lang en] [--cookies] [--cookies-from-browser]
               [--proxy] [--force] [--dry-run] [--list]
```
Replaces a downloaded file's audio with another language **without
re-downloading the video**: fetches only the audio track and re-muxes.
`--list` prints each file's current language plus the link from
`.musictool-index.json`, reading files only — no network.

`get --jobs N` (1–8) runs N *videos* in parallel. Distinct from `--concurrent`,
which splits *one* video into fragments.

---

## 4. How to work on it

- **Run the suite after every change.** `python3 tests/test_musictool.py` —
  it is hermetic (no network) and takes about half a minute.
- **Keep every CLI flag reachable in the GUI.** A clipped-off widget counts as
  missing. `uicmd.widget_specs()` is generated from the parser, so this is
  usually free — unless you added a widget that the layout pushes off screen.
- **Update `README.md` in the same commit** as the feature. It is the manual,
  and it has a "Verified / not verified" section at the end that must stay
  honest.
- **Say what you could not verify.** The GUI has never been rendered in CI.
  Say "verified against the widget stub, not against pixels" rather than
  letting a green test suite imply otherwise.

Deep dives: `docs/ARCHITECTURE.md` for structure, `docs/TESTING.md` for the
harness, `docs/DECISIONS.md` for the hard-won facts.

---

## 5. Standing requirements

These came from the maintainer as explicit directions, not inferences. They
are the acceptance criteria, and several of them exist because a previous
attempt violated them. Do not drop them.

**Matching and lyrics**
- No yt-dlp playlist enumeration for matching — derive identity from local
  tags and filenames. (`get` may call yt-dlp; that is downloading, not matching.)
- "Always tries to match as much as it possibly can" — aggressive, never fail fast.
- Untimed/plain lyrics count as **broken**. Prefer synced. Report misses
  honestly; never silently substitute plain for synced.
- Match to the *intended* file, never folder order or recency.
- Lyrics must be embedded **as each file downloads**, per file, immediately.

**GUI**
- Every CLI option must be reachable. A clipped-off widget counts as missing.
- Stop must **never** be disabled.
- Checkboxes driven by *intent*, not by the user. Videos ≠ music and
  playlists ≠ singles must be visible distinctions.
- Save previous paths and selections automatically — nothing retyped after restart.
- Log user-resizable; tabs must not eat the window.
- The progress bars must not open collapsed.
- Show how many videos remain, and project when the whole run finishes.
- Must be able to add more videos to the queue mid-run.
- Quality and language choices must be **honoured end to end** — a silently
  ignored option is the defect.
- The language a download will arrive in must be **shown** and **choosable**.

**Workflow**
- Outages must not restart work — downloads resume, never begin again.
- Deliverables are commands they can paste. They say "just give me the commands"
  when they want brevity; match that register.
- They deliver screenshots and logs by committing to the branch or pasting raw
  terminal output into chat. Fetch before claiming a file is missing.

---

## 6. Open thread: a 20-hour walkthrough file

The maintainer has a **20 h / ~33 GB** walkthrough as two files —
`webm VP9 1080p60 dash` video + `m4a AAC 44.1 kHz dash iso6mp41` audio — and
wants it playable on a Tecno phone. Another tool advised muxing to mp4 with
`-tag:v vp09` and re-encoding the audio to 48 kHz.

What was established (see `docs/DECISIONS.md` for the measurements behind it):
- Audio has **no** hardware acceleration anywhere, and does not need it.
  Software AAC runs 66–250x realtime, so all 20 h of audio is 5–20 minutes even
  on a Celeron.
- Their Braswell chip **can** hardware-encode H.264 but **cannot** hardware-decode
  VP9 (hybrid only), so a transcode is CPU-decode-bound. Expect roughly 7–20 h
  for 20 h of video — a range, not a measurement.
- The zero-re-encode path: download the **English Opus** track (48 kHz, webm's
  native audio) and mux into webm with `-c copy`. Verified selector:
  `ba[language^=en][ext=webm]/ba[ext=webm]/ba[language^=en]/ba` — against a fake
  format list with en/es dubs in both containers it picks the en Opus 48 kHz
  track, not the en m4a.
- **Unresolved:** whether an English *Opus* dub exists for their specific video.
  Only `yt-dlp -F <url>` on the real URL will tell them. If English exists only
  as m4a, they either accept 44.1 kHz in an mp4 or re-encode audio to Opus.
- **Unresolved:** whether 44.1 kHz AAC actually caused silent audio on their
  Tecno. The advice was to cut 60 s and test on the phone before committing
  33 GB. Never tested by us.

Calibration command given to them (exact for their hardware):
```bat
ffmpeg -ss 00:10:00 -t 60 -i "file.webm" -hwaccel qsv -c:v h264_qsv -preset veryfast -global_quality 23 -c:a aac -b:a 128k -ar 48000 test.mp4
```
Read `speed=X.XXx`, divide 20 by it.

---

## 7. Honest gaps — things nobody has verified

- **GUI pixels.** No tkinter, no Xvfb, no display anywhere in CI. All GUI work
  is verified against `tests/tkstub.py`, which reproduces widget trees, geometry
  and command wiring but not rendering. Scroll feel, actual clipping and real
  window resizing are unverified.
- **A real download.** Every download test uses a fake `yt-dlp` on `PATH`.
- **Live lyrics providers.** Tests use a local HTTP server replaying the
  documented response shapes.
- **Cookie extraction** from the maintainer's Chrome/Edge — never tested on
  Windows. DPAPI / App-Bound Encryption is expected to defeat it.
- **Quick Sync.** The encoders are listed by the ffmpeg build used for testing,
  but no Intel device was present, so an actual QSV encode fails there. All QSV
  advice is inference from where the time goes.
- **The Tecno phone.** No way to test playback. The 44.1 kHz silence theory is
  unverified.
- **The maintainer's Celeron throughput.** Extrapolated from a 2-core Xeon;
  they were given a calibration command instead of a number.

---

## 8. Where to pick up

Nothing is half-finished — `main` is green at 808 assertions.

Likely next asks:
1. Whether their video has an English Opus track, and the resulting mux command.
2. Turning the metadata-embed recipe in `docs/RECIPES.md` into a real
   `musictool meta URL FILE` command — it is currently three pasted commands.
3. Anything else about the 20-hour file once they have run the calibration.

If you change GUI code: run the suite, keep every CLI flag reachable in the
GUI, and say plainly that you could not see it rendered.
