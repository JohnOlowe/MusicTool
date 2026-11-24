# Decisions and hard-won facts

Everything here was established by running code, usually after being wrong once.
If a future change contradicts one of these, re-measure it and update this file
rather than silently working around it.

---

## yt-dlp

### Language selection: only a format filter works

yt-dlp ranks the **original** language first. `--format-sort lang:en` does not
select English — the `lang` sort field is `language_preference`, a *number*,
not the language code. YouTube's extractor sets it to 10 for the original
track, 5 for the default track, −10 for descriptive audio and −1 for everything
else, which is exactly why the original wins. The only thing that works is a
format filter:

```
ba[language^=en]/ba/b              # audio-only downloads
bv*+ba[language^=en]/bv*+ba/b      # with --keep-video
```

Dubbed audio tracks share an itag base (`140-0`, `140-1`, `140-9`). If language
is not part of every grouping key, `formats.collapse_variants` merges the
dubs into one row and the language information is gone before the picker sees
it.

### Format-selector grammar binds tighter than it looks

`+` binds **tighter** than `/` and `,`, so `A+B/C` parses as `(A+B)/C`. Getting
this wrong does not error — it silently falls through to audio-only.

Suffixed ids (`140-0`, `251-1`, `251-17`) are real on YouTube's SABR/range
delivery, and **the bare itag may not exist**. A selector naming a bare itag
that does not exist makes the whole `+` branch unmatchable, and the fallback
silently takes over — which is how a "1080p + audio" request quietly becomes a
360p combined file.

`formats.py` therefore emits **one concrete id per stream** — the id yt-dlp
actually served, e.g. `137+251-17` or `248+140-0` — and never `base/variant`
(`137/137-0+251/251-17` parses as `137 / (137-0+251) / 251-17` and picks the
video-only `137` with no audio). `formats.check_selector` warns about a pasted
selector whose first branch cannot match. See `_ref()` and commit `4974eb3`.

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

- `--print` implies `--quiet` and `--simulate` **unless** `--no-simulate` is
  also given. `download.py` passes `--no-simulate --no-quiet` with its
  `--print after_move:…`, so the download still happens and the progress lines
  the GUI parses are still emitted.
- `--parse-metadata "uploader:(?P<artist>.+?) - Topic"` never sets `uploader`.
  `--replace-in-metadata uploader " - Topic$" ""` is the fix.
- `-x --audio-format m4a` on an Opus source transcodes Opus → AAC.
- A dead connection is **not** a block. `classify_error` separates
  bot / age / private / members / unavailable / network, and further separates
  the sign-in cascade (dead `cookies.txt`, DPAPI App-Bound Encryption, a locked
  cookie database, no JS runtime, a PoT server on 4416, the reload wall). Each
  has a different fix, and "unknown" is not a diagnosis.
- **`-J` still runs format selection.** When the JS challenge fails, YouTube's
  only formats are the storyboards (`mhtml`, no codecs), selection finds
  nothing, and `-J` dies with `Requested format is not available` — while the
  line that says why (`n challenge solving failed`) is a WARNING, which
  `--no-warnings` used to drop. `probe` passes `--ignore-no-formats-error` and
  keeps the warnings. (A long 4K video, e.g. a 20-hour walkthrough, behaves
  like any other here; it is the setup, not the video.)
- **The YouTube challenge needs two things** (yt-dlp 2026.10.26 `--help`): a JS
  runtime — only deno is enabled by default, node/quickjs/bun must be opted
  in with `--js-runtimes` — **and** the `yt-dlp-ejs` scripts. Plain
  `pip install yt-dlp` declares no dependencies, so it lacks `yt-dlp-ejs`;
  `yt-dlp[default]` (and the official executables) have it. Otherwise
  `--remote-components ejs:github` fetches them at run time. Hence
  `pyproject.toml` depends on `yt-dlp[default]`, `doctor` has an `ejs` line,
  and `runtime_args` enables node when it is the only runtime.


### Live streams

Read in yt-dlp 2026.10.26's source, then driven through its real downloader
with a local stream whose pieces arrive over time (`fragments` as a
generator, the way the YouTube extractor feeds a live stream):

- **More than one worker per stream means nothing is joined until the
  broadcast ends.** `download_and_append_fragments_multiple` gives each format
  `ceil(concurrent_fragment_downloads / formats)` workers; with more than one
  it uses `pool.map`, which for a live generator downloads every piece to its
  own `-FragN` file and appends none until the stream is over. Measured: `-N 4`
  on video+audio left 78 loose pieces and a 0-byte `.part` halfway; `-N 2`
  never had a loose piece and the `.part` was growing, in the same time. So
  a live recording runs with `-N` = number of streams. A full disk under
  `-N 4` is the worst case: thousands of pieces and no playable file.
- **`--live-from-start` for a stream that just ended, too.** In "post-live"
  mode YouTube's manifests cover only the last 2 hours (yt-dlp labels them
  "(Last 2 hours)") unless from-start is asked for. Rewind is limited to
  120 h.
- **Without `--live-from-start` the title gets the date appended**
  (`Title 2026-11-18 21:05`), and with it, formats are filtered to the
  from-start ones; asking for from-start on a stream that has none is an
  error suggesting `--no-live-from-start`. `probe` therefore passes it unless
  `--live-from-now`.
- **Resuming is exact.** The `.ytdl` file holds
  `{"downloader": {"current_fragment": {"index": N}}}` and the DASH generator
  skips `frag_index <= N`. A piece is deleted only after it is appended. An
  index > 0 with a 0-byte `.part` is treated as inconsistent and restarts.
  Measured: a recording killed with `kill -9` after 17 of 40 s fetched exactly
  the 46 missing pieces of 80 when run again under the same name.
- **yt-dlp has no handling for a full disk** (no ENOSPC anywhere); the OSError
  text is matched instead (`No space left on device`, WinError 112).
- **On Windows a just-written file is often briefly locked** (Defender, the
  indexer, OneDrive): `os.replace`/`os.remove` raise WinError 5 "Access is
  denied". Every rename and delete in `live.py` retries.

### Dropped connections, and copying exact streams

Measured through real yt-dlp 2026.10.26 against a local server that drops
the connection on cue:

- **Default retries skip pieces silently.** The 5 fragment retries fire
  with no pause, all within the same second of a drop; the piece is then
  skipped, the progress reaches `100%`, the merge succeeds and the exit code
  is 0 -- 64 pieces missing in one run. During a long drop the run instead
  sat at `Destination:` with the speed still shown. Now: `--retry-sleep
  http:exp=1:30 --retry-sleep fragment:exp=1:30` (1+2+4+8+16 s, ~30 s) and
  `--abort-on-unavailable-fragments`; when that still fails, musictool's own
  network wait resumes the same pieces. A 20 s, 25 s, 40 s and 45 s drop all
  produced complete files: every video packet identical to the source, the
  audio identical apart from the last packet's container side data, as in an
  undisturbed run.
- **Classify by yt-dlp's ERROR lines, never by the last lines printed.** The
  old "last 8 lines" read a merge failure as a network error (earlier retry
  lines were still in the window) and a video network error as nothing (the
  audio's progress came after it).
- **`--exact` needs plain ids.** In yt-dlp's grammar `/`, `[...]` and the
  `b/bv/ba/w...` keywords all let it pick another stream, so they are
  refused. `-x --audio-format best` keeps the source codec (FFmpegExtractAudio
  copies; `--audio-quality` is not passed). Merging mp4-family streams into
  mp4, webm into webm and a mix into mkv is the same choice yt-dlp makes by
  itself; it is passed explicitly so the log can say where they go.
- **webm cannot hold a thumbnail.** yt-dlp switches a webm merge to mkv only
  when `--merge-output-format` is unset; with it set to webm,
  `EmbedThumbnail` raises after the download ("Supported filetypes for
  thumbnail embedding are ..."). So `--exact` uses mkv for a webm pair when
  the thumbnail is on, and turns the thumbnail off for a single webm stream.

### Parts of a video, and the chat replay

Read in yt-dlp 2026.10.26's source, then measured against a local server
that drops the connection on cue and against R0GdEkPZSvM with cookies:

- **`--download-sections` hands the download to ffmpeg.** With a section set,
  yt-dlp picks `FFmpegFD` (any other downloader refuses: "This format cannot
  be partially downloaded"). It runs `ffmpeg -ss START -t LEN -i URL -c copy`
  with stderr passed through, so musictool sees ffmpeg's `time=` lines, not
  yt-dlp's percentages. `duration` becomes end − start.
- **ffmpeg stops quietly at a dropped connection.** Without reconnect options
  a 15 s drop made a 4-minute part 6.03 s long, while yt-dlp printed
  `[download] 100%` and exited 0 (ffmpeg: "Stream ends prematurely"). With
  `--downloader-args "ffmpeg_i:-reconnect 1 -reconnect_streamed 1
  -reconnect_on_network_error 1 -reconnect_delay_max 30"` the same drop gave
  a part bit-identical to an undisturbed one. Only the options this ffmpeg
  lists are passed (`ffmpeg -h protocol=http`), because an unknown option
  would fail every part. The file's length is checked afterwards anyway.
- **Ending yt-dlp does not end its ffmpeg.** A part (and a merge) runs ffmpeg
  as a child of yt-dlp. `Popen.terminate()` on yt-dlp alone (what Stop did) left
  two ffmpeg processes downloading, 5 MB in the next 5 s. On Windows,
  `terminate()` is `TerminateProcess` on one process, and `yt-dlp.exe` may be a
  launcher. So the whole tree is ended: `taskkill /F /T /PID` on Windows, and
  elsewhere children read from `/proc/*/stat` (or `ps -A -o pid= -o ppid=`)
  *before* the parent goes, because orphans lose their parent id. Stop sends
  SIGTERM first (ffmpeg then closes its file properly); Force stop sends SIGKILL.
- **YouTube throttles one open-ended request to about 2x real time.** ffmpeg
  normally reads the whole URL with a single `Range: bytes=N-` request.
  Measured on 45FqURpMNCs (itag 299, 1080p60, plain https DASH): 1:00 of
  video in 30 s (2x), 1.3% of a 1:30:30 part after 50 s, an ETA of 45 min,
  while yt-dlp's own full download of the same format ran at 30-140 MiB/s.
  ffmpeg's `-request_size` (http protocol, added Feb 2026, in 8.1) caps each
  request. With `-request_size 10485760 -multiple_requests 1` the same part
  ran at 17-70x, and a 5:00 cut came out 5:00.02 with both streams. Its docs
  pair it with `-multiple_requests` and `-short_seek_size`, so all three are
  passed, and only when this ffmpeg lists `-request_size`. `--concurrent` does
  not help a part: FFmpegFD uses one connection per stream.
- **So ffmpeg reads a part through musictool** (`rangeproxy.py`, 2026-11-24),
  which works with any ffmpeg. yt-dlp runs FFmpegFD on whatever links the
  info holds: with `--load-info-json` and the format links rewritten to
  `http://127.0.0.1:PORT/p/N`, the ffmpeg command line read from the proxy
  (checked with `-v`). The proxy turns each `bytes=N-` into bounded 10 MB
  requests. Three traps found on the way:
  - **`--load-info-json` starts over from `webpage_url` when the download
    fails** (`YoutubeDL.download_with_info_file`: on `DownloadError` it
    warns "The info failed to download ... trying with URL" and runs
    `download([webpage_url])`). For a part that is a fresh ffmpeg with `-y`
    over what came down. Only `webpage_url is None` stops it, and it is set
    only during extraction, never on loaded info, so the proxy's info leaves
    it out and keeps the link in the tags through `meta_purl` /
    `meta_comment` (FFmpegMetadataPP takes `meta_*` fields as they are).
  - **ffmpeg takes a refused reconnect for the end of the file.** With
    `-reconnect 1`, a stream that ends early is asked for again from where it
    stopped. A 403 to that request is logged (`HTTP error 403 Forbidden`), and
    then ffmpeg finishes the file, says 100% and exits 0. The other input goes
    on to the end, so with the sound fetched early the file's duration is the
    full part while the picture stops at 34 s of 60. That is why the proxy
    remembers a cut (`broken`) and `parts.played_seconds` measures each stream.
  - **ffmpeg jumps around before it settles**: for a 720p fragmented MP4 it
    read 0-1 MB, the last 800 KB (the `mfra` index), then the part, opening a
    new connection for each jump, often back to bytes still in flight. A
    piece being fetched for one connection is waited for by the others, not
    fetched again (64 MB fetched for a 29 MB part before, 45 MB after).
- **YouTube served the part from HLS** (itag 605 etc.,
  `.../playlist/index.m3u8`). ffmpeg then logs one `[https @ 0x…] Opening
  '…' for reading` line per piece. Lines from an ffmpeg component that do not
  say something failed are dropped.
- **A cut starts at the keyframe before it.** In the MP4 the extra seconds
  have negative timestamps (−3.8 s on R0GdEkPZSvM at 1:00:00) and players
  skip them. An MKV cannot store negative times, so the muxer shifts the
  video. It shifts only packets after the first negative one it meets, so a
  subtitle muxed beside it landed 3.8 s early in one run and right in
  another. `embed` therefore shifts both inputs itself (`-itsoffset`), and
  for a sidecar next to an MKV the chat is timed to the first audio packet
  (`chat.first_times`).
- **The chat replay is a "subtitle" called `live_chat`** (ext `json`, one
  JSON object per line): `--skip-download --write-subs --sub-langs
  live_chat`. For a finished stream `videoOffsetTimeMsec` is the time in
  the video. Messages sent before the stream began have 0. While a stream is
  live the offsets count from when the download started instead, so `chat`
  is meant for finished streams.
- **mpv loads a subtitle by itself** when its name is the video's exactly,
  or the video's plus a language tag (`sub-auto=exact`, the default). That
  is why the sidecar is `NAME.ass`, not `NAME.live_chat.ass`.
- **SRT cannot carry a "written by musictool" mark** (no comments), so the
  files it wrote are listed in `.musictool-chat.json` in the folder. ASS and
  VTT carry a comment too. Only such files are ever replaced.

---

## Lyrics APIs, as seen live (2026-11-18)

The test stubs replay these shapes; when a provider "finds nothing", check
the live answer before the code.

- **NetEase `/api/search/get/web` now returns `{"result": "<hex string>"}`** —
  encrypted. `/api/search/get` returns the old plain JSON with the same
  fields (`id`, `name`, `artists[].name`, `album.name`, `duration` in ms).
  `/api/search/pc` asks for phone verification (code -462). Lyrics
  (`/api/song/lyric?lv=-1&kv=1&tv=-1`) open with credit lines such as
  `[00:00.000] 作词 : …`, `作曲`, `制作人`; strip them.
- **YouTube Music lyrics rows usually come without times.** `next` (WEB_REMIX)
  still yields the `MPLY…` browseId; `browse` as ANDROID_MUSIC still returns
  `timedLyricsModel.lyricsData.timedLyricsData`, but the rows are
  `{"lyricLine": …}` with no `cueRange` — on every request tried (clients
  ANDROID_MUSIC 7.03.52, 7.27.52, 8.10.52, 8.47.54, IOS_MUSIC 7.03.3/8.47.4,
  with/without `visitorData` or a full Android context), except one early
  response that had `cueRange.startTimeMilliseconds` on every row.
  ANDROID_MUSIC 6.42.52 gets no lyrics model at all. A `browse` answer is
  ~650 KB; never cache it raw.
- **lrclib `/api/search`** returns up to 40 records, each with
  `syncedLyrics` *and* `plainLyrics` (~200 KB per search). musictool only
  reads `plainLyrics` when there is no `syncedLyrics`, so that is all it
  caches.
- **YouTube from a cloud address**: `Sign in to confirm you're not a bot` for
  every video with the default clients; `mweb`/`web_safari`/`ios` get past it
  but are given storyboards only — also with a bgutil PO-token server (2.0.2)
  running. Metadata (title) is readable; streams are not.

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
a range, not a number. The maintainer's 2-core Celeron N3350 (Apollo Lake) is
slower than this machine; hand the maintainer a calibration command, not a
figure.

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
