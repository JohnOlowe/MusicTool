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
| CPU | Intel Celeron N3350 (Apollo Lake, 2 cores, 1.1 GHz base / 2.4 GHz burst) |
| iGPU | Intel HD Graphics 500 (Gen9 LP) with Quick Sync |
| Display | ~1600×720 landscape — **vertical space is scarce** |
| Playback target | Tecno Android phone |
| Music folder | `/storage/67FE-19FE/Music/New Gospel Songs` |
| Test playlist | `PLVuMx2J3_K6w` |

**Apollo Lake media capabilities** (Intel Quick Sync Video tables, Gen9 LP):
- H.264 encode + decode: hardware ✓ — the useful one
- HEVC 8-bit encode + decode: hardware ✓; HEVC 10-bit (Main10): decode only
- VP9 8-bit decode: hardware ✓; VP9 10-bit decode, VP9 encode: no
- VP8 / MPEG-2 / VC-1 decode: hardware ✓
- AV1: neither decode nor encode — anything AV1 is CPU work
- Audio: no hardware path anywhere. There is no hardware audio encoder in
  ffmpeg on any platform (no `*_qsv`/`*_nvenc`/`*_amf`/`*_vaapi` audio encoder).

The 1600×720 display is the single biggest GUI constraint. The window now
sizes itself from the display (`uicmd.fit_window`: 1080×720, smaller on a
small screen, never larger than it). Prefer compact pinned rows with
scrolling bodies over tall stacked panels.

### Working on it in a sandbox

This project was developed in an ephemeral Linux sandbox that **resets without
warning**: local `HEAD` falls back, tracked files become untracked, and
`ffmpeg`/`yt-dlp`/`mutagen` vanish. If you are in the same kind of environment,
check `git status` and `git rev-parse HEAD` before believing anything, and
reinstall before running the suite:

```bash
pip install --quiet --break-system-packages "yt-dlp[default]" mutagen imageio-ffmpeg
ln -sf "$(python3 -c 'import imageio_ffmpeg;print(imageio_ffmpeg.get_ffmpeg_exe())')" /usr/local/bin/ffmpeg
```

Facts about that sandbox that cost time to learn:
- imageio-ffmpeg ships `ffmpeg` but **no `ffprobe`** — and so does a stripped
  Windows/Termux install. Anything reading stream info must fall back to parsing
  `ffmpeg -hide_banner -i` stderr. `download.streams()` already does this.
- No display (and often no tkinter or Xvfb). **You cannot see pixels. Say so
  rather than implying you checked the UI.** If Xvfb can be installed
  (`Xvfb :98 &`, `DISPLAY=:98`), a real Tk window can be drawn and
  screenshotted with ImageMagick `import -window root` — that is how the Audio
  tracks tab was checked. The suite passes either way.
- Outbound network: since 2026-11-18 the sandbox reaches the open web —
  lrclib, NetEase and YouTube Music's lyrics API all answered, which is how
  three provider bugs were found (README, "Verified live"). **YouTube itself
  refuses the address** (`Sign in to confirm you're not a bot`; other player
  clients get storyboards only, even with a PO-token server), so downloads
  and format lists still cannot be tried for real. Earlier sandboxes had
  registries only. Either way the suite must not depend on the network — the test
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

Current as of the latest commit on `main`: **1217 assertions, 0 failures**, with
or without tkinter installed (see `docs/TESTING.md`).

Everything outside `handoff/` is MusicTool. `handoff/` (added in `076d1ce`) is
separate ffmpeg / Quick Sync / Android / Premiere material for the same
maintainer's machines — no MusicTool code depends on it, and the test suite
does not touch it. There is no subdirectory prefix to remember when quoting
MusicTool paths.

```
./
├── README.md            the manual — keep it current with every change
├── HANDOFF.md           this file
├── CHANGELOG.md         what changed and why, per commit
├── pyproject.toml       installable; provides the `musictool` console script
├── musictool.py         15   shim: `python musictool.py <cmd>`, no install needed
├── musictool/
│   ├── cli.py         1859   argparse + every cmd_* handler
│   ├── gui.py         3476   tkinter GUI: 4 tabs, Smart + 9 modes, foldable sections
│   ├── uicmd.py       1280   parser -> widgets, build_argv, modes, link kinds, learning, window fit
│   ├── formats.py      887   quality table, filters, language picking, audio tracks, --exact plan
│   ├── download.py     899   yt-dlp invocation, retries, error text, videoId index, swap_audio
│   ├── live.py         990   live streams: plan, scan, join, watch copy, resume
│   ├── parts.py        457   get --range: keyframe, salvage a .part, coverage, disk/FAT32
│   ├── chat.py        1446   chat replay -> ass/srt/vtt/txt (and back), find the video, embed as a track
│   ├── util.py         614   classify, detect_browser, js_runtime, have_ffmpeg, parse_time/range
│   ├── providers.py    379   lyrics sources (ytmusic, lrclib, netease)
│   ├── lyrics.py       275   gather / process / embed
│   ├── progress.py     431   yt-dlp + ffmpeg line parsing (per stream) + batch State
│   ├── tags.py         143   mutagen tag + .lrc handling
│   ├── config.py       111   DEFAULTS, load(), remember(), PATH
│   └── cancel.py       303   Stop / Force stop, process tree kill
├── tests/
│   ├── test_musictool.py 6737   47 sections (+8b), 1217 assertions
│   └── tkstub.py          755   headless tkinter stub
├── docs/
│   ├── ARCHITECTURE.md    module responsibilities and data flow
│   ├── DECISIONS.md       verified facts about yt-dlp and ffmpeg, and the traps
│   ├── TESTING.md         how the harness works and what it fakes
│   └── RECIPES.md         ffmpeg commands that were measured, not guessed
└── handoff/               not MusicTool: hardware-acceleration notes and scripts
    ├── README.md          what is in there
    ├── CONTEXT_MASTER.md  machines, rules, past jobs
    ├── guides/            7 guides + .sh/.bat/.py helpers
    └── context/pdfs-and-state/   job-state notes for specific encodes
```

### Commands

`get` · `check` · `formats` · `live` · `chat` · `lyrics` · `scan` ·
`duplicates`(`dupes`) · `extract` · `embed` · `providers` · `config` · `relang` ·
`doctor` · `gui`

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

```
live [FOLDER] [--status URL] [--join] [--watch] [--out DIR]
     [--resume URL] [--force] [--cookies] [--cookies-from-browser] [--proxy]
```
The leftovers of a live recording, including one made by plain yt-dlp:
what is there, `--join` the loose `.part-FragN` pieces in place (crash-safe,
50 at a time, recorded in a `.livejoin` note), `--watch` a remuxed
`TITLE (so far).mp4` without touching the pieces, `--resume` the rest. `get`
itself records a live stream from its start with one worker per stream (see
`docs/DECISIONS.md`, "Live streams"). `--status` also lists every stream with
its id.

`get --exact -f 299+140` takes exactly those stream ids and converts nothing:
a missing id or a selector yt-dlp could choose from is refused, video+audio
are merged by copy into mp4/webm/mkv, audio alone keeps its codec
(`--audio-format best`). See README, "Exactly the streams you chose".

`get --range 1:00:00-` takes only that part of a finished video
(`--download-sections` plus ffmpeg reconnect options, because ffmpeg otherwise
stops quietly at a drop, and 10 MB requests when ffmpeg has `-request_size`,
because YouTube slows one long request to about 2x real time). The file is named `TITLE [id] (from 1h00m00s)`, and
its length is checked afterwards.

```
chat URL|FILE [-o DIR] [--video FILE] [--format ass|srt|vtt|txt] [--lines 5]
     [--hold 15] [--side right|left|center] [--place bottom|middle|top]
     [--width 32] [--wrap 40] [--pad N] [--no-names|--names]
     [--colours name|role|off] [--box] [--gap 0.4]
     [--range S-E] [--offset S] [--embed] [--cookies ...]
```
A finished stream's chat replay becomes a subtitle beside the matching video
(found by `[id]` or by the index), timed to the part named in the file. A
message stays until `--lines` newer ones are below it, or for at most
`--hold` s. Names and messages are in colour, outlined with no box, with a
0.4-line gap between messages. `--embed` copies it in as a track: an mkv is
updated in place, an mp4 gets an mkv copy. `FILE` can also be a chat file it
made (ass/srt/vtt/txt, or a video with the chat track): it is remade with
what you change and keeps the rest. ASS, VTT and txt carry every message and
the settings (`parse_own`); SRT is rebuilt from its cues. See README, "Only
part of a video, and the live chat as subtitles".

---

## 4. How to work on it

- **Run the suite after every change.** `python3 tests/test_musictool.py` —
  no network, about half a minute, fully hermetic — the result does not depend
  on whether tkinter or a display is present (`docs/TESTING.md`).
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
- Stop and Force stop must **never** be disabled. Force stop must end everything
  at once, children included. Start every long-running program through
  `cancel.popen` / `cancel.run`, never `subprocess` directly, or Force stop
  cannot find it.
- Checkboxes driven by *intent*, not by the user. Videos ≠ music and
  playlists ≠ singles must be visible distinctions.
- Save previous paths and selections automatically — nothing retyped after restart.
- Log user-resizable; tabs must not eat the window. The log starts low (a
  quarter of the window), applied from the first frame.
- Four tabs only — Download, Library, Report, Settings; the rest lives in
  Download as foldable sections.
- Smart, learned auto mode plus a mode per kind of video; the link type is
  recognised as it is pasted.
- The window is 90% of the old height (720) and must know and stay within the
  display's size.
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
- Their Celeron N3350 (Apollo Lake) can hardware-**encode** H.264 and
  hardware-**decode** 8-bit VP9 (per Intel's Apollo Lake codec tables), so in
  principle a VP9 → H.264 transcode can stay on the iGPU (`-hwaccel qsv` before
  `-i`, `h264_qsv` out). That path has not been run on their machine; if QSV
  init fails, decode in software and keep `h264_qsv` or use `libx264`. Nobody has measured its speed on that chip
  at 1080p60 — run the calibration command below rather than trusting any
  estimate. (An earlier "7–20 h" figure assumed CPU VP9 decode, which was
  wrong.)
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

Calibration command for their hardware (corrected: the version first given to
them put `-hwaccel qsv` after `-i`, which ffmpeg rejects):
```bat
ffmpeg -hwaccel qsv -ss 00:10:00 -t 60 -i "file.webm" -c:v h264_qsv -preset veryfast -global_quality 23 -c:a aac -b:a 128k -ar 48000 test.mp4
```
Read `speed=X.XXx`, divide 20 by it.

---

## 7. Honest gaps — things nobody has verified

- **GUI pixels.** No display anywhere in CI. All GUI work
  is verified against `tests/tkstub.py`, which reproduces widget trees, geometry
  and command wiring but not rendering. The four-tab window was rendered by
  hand with real Tk under Xvfb (1366×768 and 1024×600, every tab and section,
  resized) on Linux; Windows fonts, DPI scaling and the title bar are
  unverified.
- **A real download.** Every download test uses a fake `yt-dlp` on `PATH`.
  By hand, with the maintainer's cookies, one 30 s part of R0GdEkPZSvM
  (`get --range`) and its chat replay (`chat --embed`) were fetched from
  YouTube on 2026-11-19, and both worked. On 2026-11-22, parts of c88810jEKMw
  (1080p60) were fetched to check unfinished parts: killed, a full disk, a
  file-size limit standing in for FAT32 (README, "Verified"). A real FAT32
  card and a real Termux run were not tried; the Android card detection was
  checked against a sample `/proc/mounts`.
- **A real YouTube live stream.** The live handling was driven through the
  real yt-dlp downloader with a local live-style stream (README, "Verified"),
  never against YouTube, and the Windows file locks it waits out were
  simulated. The same goes for the network-drop handling and `--exact`: they
  were run through real yt-dlp against a local server that drops the
  connection on cue. The only things downloaded from YouTube are the parts and
  the chat above.
- **Live lyrics providers.** Tests use a local HTTP server replaying the
  documented response shapes.
- **Cookie extraction** from the maintainer's Chrome/Edge — never tested on
  Windows. DPAPI / App-Bound Encryption is expected to defeat it.
- **Quick Sync.** The encoders are listed by the ffmpeg build used for testing,
  but no Intel device was present, so an actual QSV encode fails there. All QSV
  advice is inference from where the time goes.
- **The Tecno phone.** No way to test playback. The 44.1 kHz silence theory is
  unverified.
- **The maintainer's Celeron throughput.** Never measured; they were given a
  calibration command instead of a number.

---

## 8. Where to pick up

Nothing is half-finished — `main` is green at 1217 assertions.

Likely next asks:
1. Whether their video has an English Opus track, and the resulting mux command.
2. Turning the metadata-embed recipe in `docs/RECIPES.md` into a real
   `musictool meta URL FILE` command — it is currently three pasted commands.
3. Anything else about the 20-hour file once they have run the calibration.

If you change GUI code: run the suite, keep every CLI flag reachable in the
GUI, and say plainly that you could not see it rendered.
