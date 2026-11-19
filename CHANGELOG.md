# Changelog

Newest first. Every entry is a real commit — `git show <hash>` has the full
reasoning. The assertion count after each change is from running
`python3 tests/test_musictool.py`. Up to 808 the count is from a machine without
tkinter, because one check ("gui fails gracefully without tkinter") only passed
there. From 854 on the suite is hermetic and the count is the same everywhere.

---

## `24d5a93` — exact streams, copied; one progress line; drops resume
*2026-11-19 · 1002 assertions*

Reported on R0GdEkPZSvM (a finished stream): a way to take exactly the
chosen stream so nothing is converted; the log filled with lines every
second instead of one line updating; and after a network drop it showed
100% and stayed at `Destination:` while the speed kept showing.

- **`get --exact -f 299+140`**: exactly those ids, checked against the video
  before anything downloads. A missing id is refused (rc 1, the real ids
  listed), and so is a selector yt-dlp could choose from (`bv+ba`, `299/18`,
  filters; rc 2). Video + audio are merged by copy into mp4 / webm / mkv by
  their containers (mkv for a webm pair with the thumbnail on, since yt-dlp
  fails a webm cover after downloading); audio alone keeps its codec
  (`--audio-format best`, no `--audio-quality`); no subtitles are added.
  Verified with packet checksums through real yt-dlp: identical streams.
- **`live --status` lists every stream with its id**, and the **Live streams
  tab has Video stream / Audio stream pickers** filled by "Is it live? How
  big?"; a picked stream records with `--exact` (a picked video gets the
  audio that keeps it a copy). `--exact` is also on the Download tab.
- **Progress no longer floods the log.** Redraws without a % (a stream:
  `SIZE at SPEED (frag i/n)`) were not recognised and each became a line --
  1481 lines in 8 s. They are progress now, and progress is never logged:
  the bar takes it (per stream; the slower one shows), a terminal redraws one
  line. Retry lines: once per 10 s with `(+N more like it)`. A bare `ERROR:`
  is joined to its message; an error glued to a redraw is cut out.
- **A dropped connection no longer leaves holes.** yt-dlp fired its 5 piece
  retries within a second, skipped the piece, printed 100% and merged a file
  with holes at exit code 0 (64 pieces in one test). Now the retries back off
  (`--retry-sleep exp=1:30`), an unfetchable piece stops the run
  (`--abort-on-unavailable-fragments`) and musictool waits and resumes the same
  pieces; a 403 after progress refreshes the links (up to 3 times). Network vs
  other failures are told apart by yt-dlp's ERROR lines (`retryable`), not by
  the last 8 lines, which had misread a merge failure as a network one.
- Fixed: `swap_audio` without ffmpeg raised NameError (`ffmpeg_hint` was not
  imported).
- Tests: section 39 (45 checks); two source checks updated for the per-stream
  pump.

## `c2fee9d` — live streams: record from the start, watch it so far, carry on
*2026-11-19 · 957 assertions*

Reported: a live stream recorded with `yt-dlp --live-from-start -N 4`
stopped when the disk filled, leaving ~2000 `.part-FragN` pieces per stream
and nothing to play. A stand-alone `livejoin.py` rescued it; this puts all of
it into musictool.

- **`get` recognises a live stream** (one probe of a single URL, reused by the
  quality pick) and records it from the start, with **one worker per
  stream**: yt-dlp gives each stream `ceil(N/streams)` workers, and with more
  than one it appends nothing until the broadcast ends. Measured through the
  real downloader: `-N 4` left 78 loose pieces and an empty file halfway,
  `-N 2` none. The file now always holds everything so far, so a stop loses
  nothing and a rerun carries on exactly.
- The video id goes into the name (`[%(id)s]`), leftover pieces of the same
  video are found and their exact name reused (`%` doubled), a half-finished
  join is undone first; size and free space are reported before starting;
  upcoming streams are waited for; a just-ended stream is also taken from the
  start (otherwise YouTube serves its last 2 hours); no lyrics are fetched.
  `--live-from-now` records from now. `probe` asks for from-start formats.
- **New `musictool live`**: what a stopped recording holds (also one from plain
  yt-dlp), `--join` the loose pieces in place (gap-free only, 50 at a time,
  fsynced and recorded before deleting; restartable at any point; Windows
  "Access is denied" locks retried; refuses while a download still writes
  unless `--force`), `--watch` a remuxed `TITLE (so far).mp4` without touching
  the pieces, `--out` for another drive, `--status URL`, and `--resume URL`
  (same pieces while live/just ended; afterwards only the missing end as
  `TITLE (rest).mp4`).
- **A full disk is its own error** (`disk_full`: ENOSPC / WinError 112) with
  advice instead of "unknown".
- **GUI: a Live streams tab** (ninth): is it live / how big, record from the
  start, what's in the folder, watch what's there so far (also during a
  recording), download the rest, open the copy.
- Tests: section 38 (61 checks) on real fragmented mp4 pieces; the tab list
  and scroll-area checks updated.

## `6368070` — audio in the quality list; empty lists explained
*2026-11-18 · 896 assertions*

- **Download tab: a "+ audio formats" tick beside "List qualities + sizes".**
  Ticked, the list includes the audio-only tracks, in the spoken language set
  on the tab (`formats --show-audio --audio-lang …`). Remembered between runs.
- **`formats --show-audio` filters the audio rows by `--audio-lang`** (it
  showed every language). It says how many it hid; with no track in your
  language it shows all of them and says so; `any` shows all. Music mode
  filters too, and its suggested command no longer adds `--keep-video` to an
  audio pick.
- **An empty format list is explained.** Reported: the Audio tracks tab
  returned nothing for a 20-hour 4K video. The likely cause is a JS challenge
  that was not solved (e.g. `pip install yt-dlp` without `yt-dlp-ejs`): yt-dlp
  then has only storyboard pictures, `-J` died with "Requested format is not
  available", and musictool's advice sent you to `formats` — the command that
  had just failed — while `--no-warnings` dropped the warning naming the cause.
  `probe` now passes `--ignore-no-formats-error`, keeps the warnings, and
  `no_media_reason()` gives the fix in `formats`, `check` (a new `NO STREAMS`
  verdict), `get --quality`, `relang` and both GUI tabs. "Only images are
  available" and "Signature solving failed" now classify as `js_runtime`.
- The Quality/Audio tracks lookup now uses the Download tab's
  `--js-runtimes`/`--remote-components` (it used cookies only), and one tab
  failing to draw no longer leaves the other saying "reading …".

## `4d63131` — first live run: three provider bugs, yt-dlp setup, README
*2026-11-18 · 870 assertions*

The sandbox could finally reach the lyrics APIs, so they were run for real.
**Every provider had a problem that the stub-based tests could not see.**

- **Every song after the first got the first song's YouTube Music lyrics.**
  `Http.json` left a dict request body out of the cache key, so all POSTs to
  `/next` and `/browse` shared one entry. They were `trusted` (exact videoId),
  so the wrong-song guard never looked, and the entry lived on in
  `http-cache.json`. The key now includes the body; old entries are dropped on
  load. A test fails against the old key.
- **NetEase crashed on every search** (`'str' object has no attribute 'get'`):
  `/api/search/get/web` now answers with an encrypted string. Switched to
  `/api/search/get` (same fields), an unexpected answer is "no results", and
  the `作词`/`作曲`/`制作人` credit lines are removed before embedding.
- **YouTube Music's lyrics were all thrown away**: the rows now mostly arrive
  without times. They become an unsynced exact-match candidate (used with
  `--allow-unsynced`; a synced hit elsewhere still wins). ytmusic caches a
  compact per-videoId result instead of the ~650 KB page, and
  `providers --test` now probes a sample video and says whether times came.
- **`http-cache.json` ~4× smaller**: lrclib records are cached with only the
  fields read (no `plainLyrics` next to `syncedLyrics`). Measured: 1.43 MB →
  358 KB for three songs, same picks.
- **`formats --js-runtimes/--remote-components` did nothing**: `probe` (the
  lookup behind `check`, `formats` and `--quality`/`--max-size`/
  `--audio-lang`) never passed them. One `runtime_args()` now serves every
  yt-dlp call and enables node when it is the only JS runtime.
- **`pip install .` got a yt-dlp that cannot solve YouTube's challenge**:
  plain `yt-dlp` has no dependencies, so no `yt-dlp-ejs`. `pyproject.toml`
  now depends on `yt-dlp[default]`; `doctor` has an `ejs` line.
- **README**: contents line; install with `yt-dlp[default]` and a JS runtime
  (Termux: `pkg update && pkg upgrade` before `pkg install deno`); lyrics
  sources as they behave today; what a cloud address gets from YouTube;
  "Verified / not verified" rewritten from the live results. DECISIONS gains
  "Lyrics APIs, as seen live".

Live results: lrclib gave synced lyrics for *Shape of You* and Sinach's
*Way Maker*; an invented title was refused; a forced re-run made 0 calls; a
real yt-dlp download (local server) produced live progress events. YouTube
itself refuses this cloud address, so real downloads remain unverified.

## `e0110df` — Audio tracks tab, and the code fixes from the docs review
*2026-11-18 · 854 assertions*

**New: see the audio tracks in your language and download one (GUI).**
A dubbed YouTube video can carry a dozen audio rows, one per language, codec
and bitrate. The new **Audio tracks** tab (the eighth) lists the ones in the
chosen language: original first, then by bitrate, with YouTube's own name for
each. **Download this audio** fetches exactly that track as music
(`get URL --format-selector <id>`). **Use with a video quality** pins it for the
Quality tab, which then pairs every video row with it (`248+251-1`). Picking a
language also sets the Download tab's `--audio-lang`. The tab shares the URL
box and the loaded formats with the Quality tab. Code: `formats.audio_tracks`,
`track_bitrate`, `describe_track`; `pair_with_audio`/`auto_pick` take `lang=`
and `pinned=`.

**Fixed (found while checking the docs against the code):**

- **The Quality tab ignored `--audio-lang`.** It paired video rows with the
  best audio in any language, so on a dubbed video it could pick the wrong
  one. It now uses the audio language, or the pinned track.
- **`same_language` only worked one way.** `en` matched an `eng` tag, but
  `--audio-lang eng` did not match YouTube's `en`. It now works in both
  directions, including `fre`/`fra` and `pt-BR`/`por`.
- **lrclib etiquette.** The User-Agent was
  `musictool/1.0 (personal script; contact: you@example.com)`, a placeholder.
  It is now `musictool/<version> (https://github.com/JohnOlowe/MusicTool)`.
  `Retry-After` on a 429 is now honoured, in seconds or as an HTTP date
  (5 s default, 120 s cap, up to 30 % jitter); it used to be ignored.
- **The bot-wall advice sent Windows users to Edge/Chrome,** whose cookies
  yt-dlp cannot decrypt there (App-Bound Encryption). `ADVICE` and `BOT_HINT`
  now say Firefox first, or a `cookies.txt`.
- **`gui` with tkinter but no display** printed a `TclError` traceback. It now
  exits 1 with "GUI unavailable: …".
- **Dead settings removed:** `genius_token`/`musixmatch_key` and the
  `MUSICTOOL_GENIUS`/`MUSICTOOL_MUSIXMATCH` overrides. No provider used them.
- **`pyproject.toml`** URLs pointed at `github.com/OWNER/...`.
- **GUI log:** notes written by the GUI itself ran together on one line;
  `log()` now ends each with a newline.
- **Tests:** the `gui` subprocess check is now hermetic (a stand-in `tkinter`
  that refuses to import), so the suite gives 854/0 with or without tkinter.
  New sections 34 (audio tracks) and 35 (these fixes).

**`handoff/guides` scripts** (not MusicTool code, but they were wrong):

- `burn_parallel_flexible.sh` and `burnfix-home.sh`: MediaCodec gets
  `-level 4 -bitrate_mode cbr` (`-level 4.0` meant level 1.1; `-maxrate`/
  `-bufsize` are ignored by that encoder). The font check reads the font after
  `->` in libass's `fontselect` line and stops on a fallback. The old check
  grepped for the requested name, which is always printed. Each part is
  waited for separately and a failed part stops the join; a bare `wait`
  returned 0 and the old scripts would join broken parts.
  `burn_parallel_flexible.sh` also gains `--encoder libx264` (for a PC, with
  `-pix_fmt yuv420p`) and uses `$TMPDIR`. Tested end to end with libx264:
  3 parts against a single-pass burn, average PSNR 59.4 dB, subtitles in
  sync across the cuts. The MediaCodec path could not be run here.
- The same per-part `wait` in the inline loops of `phone-first-ffmpeg.md`
  and `subtitle-hard-burn.md`.
- `burn_subtitles.bat`: `TOTAL` is now the duration from ffprobe (it was the
  file's date and time, `%%~tA`); the subtitle name is quoted, which covers
  spaces and commas but not `:` or `\`, so the script still wants a bare
  filename. `vlc-seek-fix*.bat` find VLC
  themselves instead of hard-coding one path each; the `.mka` fallback no
  longer passes the mp4-only `-movflags +faststart`. The `.bat` files were
  not run (no Windows here).

## `1cbbebc` — docs checked against the code
*2026-11-18 · 808 assertions (no code touched)*

Every claim in the `.md` files was checked against the code, the tests or a
real ffmpeg run, and the wrong or stale ones were corrected (widget counts,
defaults, ffmpeg flags, the hardware notes in `handoff/`). The code problems
it found are fixed in the next entry.

## `076d1ce` — handoff material from the hardware-acceleration sessions
*2026-11-18 · 808 assertions (no code touched)*

Adds `handoff/`: `CONTEXT_MASTER.md`, seven ffmpeg/hardware guides with their
scripts (`handoff/guides/`) and three job-state notes
(`handoff/context/pdfs-and-state/`). None of it is MusicTool code — it is
ffmpeg, Quick Sync, Android and Premiere know-how for the same owner's
machines, kept here so it is not lost. See `handoff/README.md`.

## `7bedd12` — make it a repository in its own right
*2026-11-18 · 808 assertions*

`pyproject.toml` (so `pip install .` puts a `musictool` command on the PATH),
this changelog, `HANDOFF.md`, `docs/` (ARCHITECTURE, DECISIONS, TESTING,
RECIPES) and `.gitignore`. No behaviour change.

## `65af4a2` — progress bars open visible, a queue, remaining count + ETA, `--jobs`
*2026-10-23 · 808 assertions*

Four things the GUI was getting wrong.

- **The bars opened collapsed.** `ttk.PanedWindow` leaves its sash at 0 until it
  lays out, so the progress panel was invisible until you dragged the slider.
  The first layout now places the sash, never below 160 px; dragging still
  works and is still remembered.
- **A batch of four reported as "Single download 42.0%"**, because yt-dlp only
  sees one URL at a time and cannot know three are queued behind it. `cmd_get`
  now emits a batch event per URL, `State` tracks it, and the panel reads
  `video 2 of 5 · 3 still to go after this · 2m 10s elapsed · about 6m 30s left
  in total`. The projection scales elapsed time by the fraction done; before
  the first file finishes there is nothing to scale, so it falls back to that
  file's own ETA instead of promising zero.
- **Adding links mid-run was impossible** — the button was greyed out. Download
  now stays live and queues: the button reads *Add to queue*, the log says how
  many are waiting, and each job starts when the one before it ends. Stop drops
  the queue rather than starting jobs into a stop.
- **`--jobs N`** runs N videos in parallel, capped at 8. Each still resolves its
  own quality and language, log lines are numbered so parallel output does not
  interleave, and each download tags its progress events so the panel shows one
  line per file instead of whichever reported last.

## `6f349c4` — `relang --list`: which files are in the wrong language, and their links
*2026-10-23 · 780 assertions*

"Give me the links to the ones that got the wrong audio" should not mean
scrolling back through an old terminal log. MusicTool already wrote each link
into `.musictool-index.json` when it downloaded the file, so read them back:

```
musictool relang "C:\Music\Videos" --list
```

It reads the language tag inside each file and the index, and nothing else — no
downloads, no network — so it works while YouTube is refusing to talk to you.
Files it never downloaded have no entry, and it says so rather than guessing a
link from the filename. GUI: *List wrong-language files*.

## `6102667` — resolve quality per video, and repair language without re-downloading
*2026-10-23 · 773 assertions*

"Some of them still aren't english" had two causes.

- **The bug:** `cmd_get` resolved quality once for the whole batch.
  `_resolve_quality` writes `o["format_selector"]` on the shared options dict
  and returns early on later URLs, so videos 2..N reused video 1's format ids.
  Format ids are not global — `140-4` was English on one video and the Spanish
  original on the next, and `614` did not exist at all. Each URL now gets its
  own copy of the options dict, so every one prints its own decision.
- **The repair:** `musictool relang PATH...` fetches only the audio track it
  needs (tens of MiB, not hundreds) and re-muxes it into the file already on
  disk, keeping video, cover, chapters and tags. Stream info comes from
  `ffprobe` when present and from parsing `ffmpeg -i` when it is not.
- Language tags are compared properly now: containers write three-letter codes
  (`eng`, `spa`) while the picker speaks two-letter ones, and `spa` does not
  start with `es`, so prefix matching mis-identified half the languages.
  `formats.same_language()` carries the pairs.

## `e22c0d3` — one scroll, folded options, and settings that come back
*2026-10-23 · 755 assertions*

Three complaints about the window, and one real bug found while fixing them.

- **One scroll.** The Download tab had two: the URL box, the mode and the
  fine-tune strip were pinned and only the option grid scrolled, so the wheel
  did nothing unless you first moved the pointer down into the options. The
  whole tab body now lives in a single `ScrollArea`. `_over_me` also stops
  treating a short `Text` as self-scrolling: a three-line URL field with one
  line in it passes the wheel to the tab, while one holding thirty URLs keeps it.
- **Folded.** *More options* dumped all 41 settings at once. The grid now shows
  rows that have something in them plus anything changed, and folds the blank
  rest behind one button — 10 of 41 on screen instead of 41. (`--jobs`, added
  in `65af4a2`, makes it 42 settings with 11 on screen and 31 folded.)
- **Saved.** Every field on every tab, the mode, the open URLs and whether the
  options were expanded are written to `gui_opts`/`gui_extra` as you change
  them, on every run, and on close.
- **The bug.** Choosing a mode called `apply()` with the mode's full value dict,
  and a blank there meant "empty this out" — so picking Video wiped
  `--max-size`. That is the "I have to insert 800M again" complaint. A blank now
  means *no opinion*, and `uicmd.MODE_RESET` lists the only keys a mode switch
  may clear.

## `c2783bc` — which language a dubbed video arrives in, shown and chosen
*2026-10-23 · 733 assertions*

yt-dlp ranks the **original** language first, so on a video whose original is
Spanish the default download is the Spanish audio even when an English dub is
sitting right there. Nothing showed the language and nothing let you ask for one.

`--audio-lang` (default `en`) is now on `get` and `formats`, and `formats`
prints what is on offer with a `lang` column (`*` marks the original, and the
column only appears when something is tagged). The preference reaches yt-dlp as
a format filter — `ba[language^=en]/ba/b` for audio, `bv*+ba[language^=en]/bv*+ba/b`
with `--keep-video` — not `--format-sort lang:en`, which
looks right and does nothing.

## `4974eb3` — the quality you ask for is the quality you get
*2026-10-23 · 705 assertions*

Two ways the announced quality was not the downloaded one, both from a real run.

1. `_ref()` offered `base/variant` on a `-f` line — `248+140/140-0`. YouTube
   serves no format called `140` for range-delivered streams, and `/` binds
   looser than `+`, so yt-dlp read it as `(248+140)/(140-0)`, failed the first
   branch and silently took the second: the audio track alone. "1080p vp9 +
   mp4a ~ 644.7 MiB" arrived as a 52 MiB `.m4a` with `--keep-video` set and
   nothing looking wrong. A selector now names one concrete id per stream.
2. `--quality` swallowed anything it did not understand, so a selector pasted
   from our own `formats` output produced no `-f` at all and yt-dlp picked
   1.74 GiB of 2160p av01. It now adopts a selector-shaped value, checks it
   against the real format list, and refuses nonsense out loud.

Also: when qualities cannot be read, the log says yt-dlp will pick its own
default, so an unapplied `--quality` is never a silent one.

## `b9b35c0` — the mode chooses the checkboxes, not the other way round
*2026-10-23 · 690 assertions*

The Download tab opened on nine checkboxes and a generated grid, and every run
started with the same ritual. It now asks the two questions that decide all of
it — music or video, one item or a playlist — and sets the rest itself, with a
live one-line summary that names the drift once you edit a checkbox by hand.

`is_playlist_url` treats `watch?v=X&list=PL` as a **single**: that `list=` is
the up-next queue, and reading it as a playlist is how one song becomes ninety
files. `pump()` also reschedules itself *before* doing any work — one bad event
used to be able to stop the loop and freeze the log for the session.

## `8e04fa5` — `--js-runtimes`, so `doctor`'s advice is advice you can follow
*2026-10-23 · 652 assertions*

The previous commit made `doctor` say "add `--js-runtimes node`", which
MusicTool itself could not do. It is now a real flag on `get`/`check`/`formats`,
listed in the `_opts` key tuple — where a flag goes to be silently dropped
otherwise — passed to yt-dlp, and generated as a GUI widget. The parity harness
picked the new flag up on its own.

## `0cb80c2` — name every failure in the YouTube sign-in cascade
*2026-10-23 · 648 assertions*

A long chain of *different* failures that all look like the same problem, each
with its own fix, so `classify_error` now tells them apart instead of reporting
"unknown": `session`, `dpapi` (Chrome/Edge App-Bound Encryption),
`cookies_locked`, `oauth_gone`, `js_runtime`, `pot_server` (port 4416),
`reload` (the bot wall itself) and `no_format` (an itag, not a subscription).
Each carries advice. `doctor` gained the probes behind the middle of that list.

## `4d1c511` — Stop is always clickable
*2026-10-23 · 624 assertions*

The Stop button was not "half the time" dead — it was dead all the time. The
status bar created it with `state="disabled"` and `busy()` was meant to enable
it while a job ran, but fifteen lines later the shared-state block rebound the
name (`stop_btn = None`), so `busy()`'s `if stop_btn is not None` never fired.

Stop is now never greyed out (since `65af4a2` the Download button also stays
live, as *Add to queue*), `stop_now()` knows whether anything is running, and the in-flight count is
data rather than a widget so it is accurate the moment a job starts and ends.

## `5586876` — a quality picker: collapsed list, auto/manual choice, filters
*2026-10-23 · 618 assertions*

YouTube offers one 4K video as 127 rows — every itag split into ~18 near
identical CDN copies, plus doubles of the same resolution. `formats` printed
all of it, which is no way to choose.

`collapse_variants` merges the copies into one row sized by the biggest,
`dedupe_quality` drops same resolution/codec/container duplicates in favour of
the smaller encode, `apply_filters` adds height/container/codec/size-cap/sort,
and `auto_pick` chooses for you and says why. `with_audio_pool` keeps the audio
tracks the table hid available to the picker — without it a filtered table
paired a 1080p video with the 360p combined format just because the real audio
had been filtered out. New seventh GUI tab: Quality.

## `8c9e286` — initial commit
*2026-10-23 · 560 assertions*

A CLI and a tkinter GUI around yt-dlp that downloads music and video, picks
quality by resolution or by size, and fetches synced lyrics for what it
downloads. Commands: `get`, `check`, `formats`, `lyrics`, `scan`, `duplicates`,
`extract`, `embed`, `providers`, `config`, `doctor`, `gui`.

- `check` asks YouTube with **no** cookies, so "am I blocked?" gets an honest
  answer, and classifies what comes back into bot / age / private / members /
  unavailable / network.
- `formats` lists every quality with its real size, codec, container and fps,
  plus the exact command to take it.
- `get` honours `--quality`/`--max-size`/`-f` and embeds metadata, thumbnails
  and chapters. (It resolved quality once per batch, not per video — a bug
  fixed in `6102667`.)
- The GUI is generated from the same argparse parser, so every CLI flag is
  reachable in it.
