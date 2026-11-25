# Changelog

Newest first. Every entry is a real commit — `git show <hash>` has the full
reasoning. The assertion count after each change is from running
`python3 tests/test_musictool.py`. Up to 808 the count is from a machine without
tkinter, because one check ("gui fails gracefully without tkinter") only passed
there. From 854 on the suite is hermetic and the count is the same everywhere.

---

## `PENDING` — chat: `--font-size`, and a size players can change
*2026-11-25 · 1298 assertions*

Asked: why the ASS chat subtitles don't get smaller when the size is changed
in VLC, then "Yes, do so" to both fixes offered.

- **Why.** VLC's subtitle size (Ctrl+wheel, the font size in its
  preferences) applies only to subtitles it draws itself (SRT, VTT). ASS is
  drawn by libass from the sizes in the file. On top of that, each gap
  between messages was written `\N{\fs18}\h\N{\fs45}`. The `\fs45` fixed
  the size of every message after a gap, so even a player that can rescale
  ASS styles (mpv `--sub-ass-override=force --sub-scale`) changed only the
  first message.
- **`chat --font-size PX`.** The size is in pixels on a 1080p picture, and
  players scale it with the video; the default is 45. The gap scales with
  it. Remaking a file (`chat FILE.ass --font-size 32`) downloads nothing,
  and the size is stored in the file's settings, so later remakes keep it.
  Values outside 8–300 are refused.
- **The app's chat box** has a *Text size* field. Empty means the file's
  own size (or 45).
- **The size is written once, in the style.** After the gap the text goes
  back to the style with `{\r}`. Older files still read back. Checked by
  rendering with libass: at 30 px every message shrank, and the gaps with
  them.
- Tests: 6 new checks in section 45, and two older checks changed (the gap
  text; the Live tab now has 8 text fields). README (chat section, with the
  VLC explanation), DECISIONS (a trap), HANDOFF, TESTING, ARCHITECTURE.

---

## `26e2e43` — `get --range` tested on real YouTube: four bugs fixed
*2026-11-24 · 1292 assertions*

Asked for: "Test it properly, make sure there are no bugs", with fresh
cookies. The fast way (`00eea3b`) was run against googlevideo.com for the
first time, along with every shape of part around it.

- **What worked as it was.** A 2-minute 1080p60 part came in 7 s. Parts were
  the same bytes as `--no-fast-range` (MP4), or the same packets (MKV). Also
  fine: format 18, audio alone, the default selection with title and cover,
  from 0:00, to the end, Ctrl+C, `kill -9` then the rerun, and a third run
  saying "already here".
- **Fixed: a picture that never came was taken for the whole part.** An
  expired link refused at ffmpeg's first request gave a file with an empty
  video stream and 2:35 of sound, named as the rest of the part, and nothing
  more was fetched. There were two holes:
  - `played_seconds` skipped a stream with no packets instead of counting
    it as 0 (a cover picture is still ignored: `0:V`).
  - `played_seconds(...) or secs` turned a measured 0 back into the header's
    length.

  Now the round is dropped and fetched again; on real YouTube a third round
  finished the part with no gap.
- **Fixed: HLS parts always failed** (`Operation not permitted`). YouTube gave
  a 3-hour video only as HLS that afternoon. Its AAC carries per-frame
  headers that ffmpeg can only remove before an `empty_moov` MP4's header is
  written: `-bsf:a aac_adtstoasc` is added when the part is HLS and all its
  sound is AAC (`download.needs_aac_fix`, `AAC_FIX`).
- **Fixed: parts named later than they start, which could leave a gap when
  carried on.** A WebM's Opus asked at 1:00 starts at 0:50. The keyframe
  look-up now covers sound alone, and it finds the formats by asking yt-dlp
  (`download.resolve_ids`, offline, `--load-info-json --simulate`) instead
  of giving up on `bv*+ba/b`. HLS start times cannot be read at all (they
  differed between two runs), so an HLS part carried on overlaps by 12 s
  instead of 2 (`parts.HLS_SLACK`).
- **Fixed: `--subs` with a part was timed from the video's start** (every
  line 57 s early in a part from 0:57). For a part, subtitles go beside it:
  - the whole ones keep the video's plain name;
  - each part file gets its own, cut from its exact start
    (`parts.cut_subtitles`: VTT, SRT, ASS; `cli._part_subs`).
- **Less waste.** 98 MB were fetched for a 76 MB part. The proxy now reads
  the file's own index (an MP4's `sidx`, a WebM's Cues) and reads nothing
  ahead past the byte where the part ends (`Proxy.until`, `mp4_index`,
  `webm_index`, `stop_byte`). It also starts each connection at 256 KB, and
  keeps the socket's send buffer small so that "written to ffmpeg" means
  "read by ffmpeg". Result: 31 MB for a 25 MB part, 14 MB for a 10.5 MB WebM
  part.
- Tests: section 49 (28 checks), and in 43 one check split in two (sound
  alone is now looked up). README ("Verified": the real-YouTube results; the
  not-verified item is now only real network drops), HANDOFF, TESTING,
  DECISIONS (5 traps), ARCHITECTURE.

---

## `00eea3b` — `get --range` as fast as a whole download, with any ffmpeg
*2026-11-24 · 1263 assertions*

Asked for: a part (`--range`) that downloads at normal speed. ffmpeg fetched
it in one open-ended request, which YouTube slows to about 2x real time;
only ffmpeg 8.1+ could ask in pieces.

- **A local range proxy** (`musictool/rangeproxy.py`). For the length of a
  part, YouTube's plain links in yt-dlp's info point at a small HTTP server on
  127.0.0.1, and yt-dlp loads that info (`--load-info-json`) instead of the
  link. ffmpeg's "everything from byte N" becomes 10 MB requests, two ahead.
  ffmpeg still seeks, cuts and writes the fragmented MP4 itself, so the
  part logic (salvage, carry on, FAT32, full disk, 403, Stop) is unchanged.
  Against a server that throttles like YouTube: 1.2 s instead of 28.3 s for a
  1-minute 720p part, and the file is byte-for-byte the same.
- **Careful with the data**: 1 MB first per connection, growing as ffmpeg
  reads; 48 MB kept in memory for a jump back; a piece in flight shared, not
  fetched twice; at most 20 MB fetched past the end of the part.
- **Drops** are retried for about a minute while ffmpeg waits, so the part
  goes on in the same file (`! network problem -- waiting Ns; the part goes
  on where it was`).
- **A cut stream is caught.** Found while testing: when the links ran out
  mid-part, ffmpeg took the refused reconnect for the end, said 100% and
  exited 0, with the sound running on past the picture, so the file looked
  complete. The proxy remembers a cut; `parts.played_seconds` measures the
  shorter of picture and sound; the file is renamed to what it holds and the
  rest comes with fresh links. `parts.coverage` no longer counts a file past
  the end in its name, and a retry that gets nothing new leaves no file.
- **yt-dlp cannot start the part over**: `--load-info-json` retries a failed
  download from `webpage_url` (a fresh ffmpeg with `-y` over what came down),
  so the proxy's info leaves it out and keeps the link in the tags through
  `meta_purl` / `meta_comment`.
- Off with `--no-fast-range`, `--proxy` or `--limit-rate`; HLS and other sites
  as before. An unforeseen failure finishes the part the old way. The "this
  ffmpeg is slow" note now appears only when the proxy is off.
- Not yet run against real YouTube: both cookie files had expired.
- Tests: section 48 (46 new checks); section 41 adjusted.

---

## `09fd28e` — the window rebuilt: four tabs, Smart modes that learn, links recognised as you paste
*2026-11-24 · 1217 assertions*

Asked for: a cleaner window with just four tabs, most of the rest moved into
Download; auto modes that choose the best options from what the app has
learned you like, and a mode for each kind of video; the link type guessed
from the pasted link; the log lower at start; the window 90% of its height;
and the window kept within the display.

- **Four tabs.** Download, Library, Report, Settings. Quality, Audio tracks
  and Live streams are now foldable sections of Download, beside *More
  options* (every other `get` flag) and *Fix language*; which are open is
  remembered. Fix lyrics, Scan & duplicates and Lyrics files are the three
  jobs of Library, over one folder field and one file list. Tools is
  Settings.
- **Links recognised as you paste** (`uicmd.classify_link/classify_links`):
  YouTube Music song / album / playlist / artist, video, *a video in a
  playlist — just this one*, Short, live, playlist, channel, SoundCloud,
  Bandcamp, other sites; links pulled out of a pasted message once each;
  Spotify explained; lines without a link counted. **Identify online**
  (`download.probe` + `refine_kind`) finds live / scheduled / finished
  streams, music videos, vertical Shorts and long talks.
- **Nine modes and Smart.** Song, Album / music playlist, Music video, Video,
  Short, Podcast / talk, Video playlist, Whole channel, Live stream — each a
  different command; the big button says what it will do (*Record live
  stream* runs the recording). The old four mode names are still read.
- **Smart learns** (`uicmd.learn / smart_plan`): which mode you use for
  which kind of link and how you set each mode up; a choice counts once it is
  the majority of your last ten runs and you made it twice, and the window
  says so. Settings → Smart shows it, turns it off, or forgets it.
- **Window.** Opens at 1080x720 (was 1080x800; 720 = 90% of 800),
  centred, smaller on a small display, never larger than it
  (`uicmd.fit_window`, `maxsize`). The log starts at a quarter (was a third),
  asked for before the first frame (`split_heights`), placed again after every
  resize until you move it, with a 140 px floor; its buttons sit beside the
  progress bars. Long labels re-wrap to the visible width, button rows and the
  mode grid reflow on a narrow window, and the horizontal scrollbar only
  shows when something cannot fit.
- **Rendered and looked at** with real Tk under Xvfb at 1366x768 and
  1024x600, which found the three layout faults fixed above (rows wider than
  the window, a sash moved by the PanedWindow after being placed, labels that
  never re-wrapped).
- Tests: §§17, 28, 31 adapted (old tab names resolve to their new homes);
  §47 new — link kinds, extraction, refining, learning, Smart, window fit,
  and the window end to end (button per link, live recording, Identify
  online, learning twice, learning off, forget, restart memory, a 1024x600
  display).

---

## `4fc4c19` — chat `--range` no longer puts one part's chat on every part
*2026-11-23 · 1159 assertions*

Reported: `chat` with a range (e.g. `0-6:40:00`) said the chat replay was
already here, then wrote the same subtitles over the chat of every part in
the folder, so most parts showed chat that did not belong to them.

- **The cause.** `run` used `--range` in place of each video's own part, for
  every video of the stream it found, so all parts got the chat of 0:00 to
  6:40:00, timed from 0:00.
- **Now** a video whose name says its part is always timed from its name.
  `--range` only picks which parts get a subtitle: those inside it (with 15 s
  of slack for the keyframe start and the ~2 s overlap of pieces). The others
  are left alone, and each is named in the log. A range no part holds
  touches none of them, and the subtitle goes beside the chat file. A video
  without a part in its name, or one given with `--video`, still takes the
  range as what it holds.
- Section 46; one §40 check now passes `--video` (overriding the name is
  what it tests).

---

## `bf5b485` — chat: remake a chat file; colours; no box; space between messages
*2026-11-23 · 1153 assertions*

Asked for: a way to edit a chat that is already downloaded, which reads
musictool's own chat files (ass/srt/vtt/txt), for example to turn one into
another format. Also: colour fully in the chat subtitles, no background, and
a little space between messages.

- **`chat FILE.ass|.srt|.vtt|.txt`** (or a video with the *Live chat* track)
  remakes that file with whatever you change, keeps the rest as the file had
  it, and downloads nothing. ASS, VTT and the text log now carry every
  message and the settings: an ASS `[MusicTool Chat]` section (players skip
  it, and it survives being muxed into an .mkv), a VTT comment, the log's `#`
  lines. They read back exactly, in the stream's time (`parse_own`). SRT has
  no room for that, so it is rebuilt from its cues (a message at the first
  cue showing it, roles from the colours), and so are files from older
  versions. The CLI options now default to "not given", so a file's own
  settings win over the defaults; a foreign subtitle is read but never
  overwritten. In the GUI: **Remake a chat file…**.
- **Colour.** Names bold and coloured, and the message in a lighter tint of
  the same colour. The owner, moderators and members in YouTube's colours,
  a Super Chat in the colour YouTube gave it (`bodyBackgroundColor`, read
  from the replay), everyone else a fixed colour from their name.
  `--colours role|off`. SRT gets `<font color>` on the message too, and VTT
  a STYLE block with a class per colour (it had none).
- **No box.** The default is outlined text with a soft shadow; `--box` puts
  the see-through dark box back (`--no-box` still works). VTT takes the cue
  background away in its STYLE block.
- **`--gap`** (default 0.4 of a line): in ASS, a small blank line between
  messages. SRT/VTT can only space whole lines, so there it is `--gap 1`+.
- The text log's times have milliseconds (`[0:01:02.345]`), so it converts
  back without moving anything.
- Drawn with ffmpeg's libass on R0GdEkPZSvM's real replay (README,
  "Verified"). Section 45.

---

## `3651ca9` — `--just-download`; `--exact --subs` keeps the chat
*2026-11-23 · 1119 assertions*

Asked for: a way to turn off the processing after a download, so nothing is
embedded and it stops almost as soon as the download does. Also: the chat
did not seem to come down.

- **The chat was being dropped.** `--exact` set `embed_subs` off without a
  word (subtitles inside an mp4 would be converted), so `--exact --subs` got
  no chat. Now `--exact` saves subtitles beside the video (`--write-subs`)
  and says so. For a stream that includes the chat, `NAME.live_chat.json`.
- **`--just-download`**: no `--embed-metadata`, `--embed-thumbnail`,
  `--embed-chapters` or `--embed-subs`, and no lyrics. `--subs` are written
  beside the file. Each embed step rewrites the file, and for a part that is a
  second full copy (minutes on a phone's memory card, and room for it). A part
  stays the fragmented MP4 it was written as; ffmpeg ends it with an `mfra`
  index, so it seeks. In the GUI it is generated from the parser like every
  option, and every mode turns it off.
- **The chat once per part.** It covers the whole video, and every yt-dlp
  run in `_download_part` would have fetched it again. Now it comes with the
  first piece only, and not at all when it is already in the folder
  (`parts.sidecars`).
- **`chat` on a part's chat file** (`TITLE [id] (1h00m00s-4h00m00s).live_chat.json`)
  found no video id: `json_id` expected `[id].live_chat.json`. Now it finds
  the part and writes the chat from where it starts.
- The "skipping lyrics" line names `--just-download` when that is the reason.
- Tested for real on eRUHPTKNgkI (README, "Verified").

---

## `5fb8d46` — Unfinished parts stay playable, and a rerun carries on
*2026-11-22 · 1107 assertions*

Asked for: after `get --range` stopped halfway on the phone (no space, or
anything else), the files it left should still play.

- **Why they did not play.** A part is copied by ffmpeg into an ordinary MP4,
  whose index (`moov`) is written only at the very end. Killed, out of space,
  or a crash: `moov atom not found`, nothing plays, and the next run's
  `ffmpeg -y` wrote over it.
- **Parts are now fragmented MP4** (`-movflags +frag_keyframe+empty_moov+
  default_base_moof`, as `ffmpeg_o` downloader args): a piece per keyframe,
  each playable once written. A 64 MB `.part` killed mid-write played 1:55;
  the old way gave nothing. When it finishes, the title/cover step turns it
  into an ordinary MP4 as before.
- **It starts on the keyframe** (`parts.keyframe_before`). The ordinary MP4
  hid the seconds before your time in its edit list; the fragmented one
  played them, with the first audio packet stretched over 5 s. ffmpeg reads
  one packet to find the keyframe (about 0.5 s, after YouTube's
  `available_at` wait), and the part is asked for from 0.15 s after it:
  starting exactly on it made ffmpeg (B-frames: it seeks 3/23 s early) take
  the keyframe before. The name says where the file really starts:
  `(2h24m55s-...)` for a `--range 2:25:00-...`.
- **Keep what came down, then carry on** (`cli._download_part`, replacing
  `_check_part`, which only printed a `--range` for the missing end). A
  `.part` left behind is cut to its last whole piece and renamed to what it
  holds; the rest is fetched from 2 s before its end into a new file. The
  same loop handles a short file ffmpeg called finished, a full disk (stops,
  says to free space and rerun), FAT32's 4 GB (`File too large`, or Windows'
  "not enough space" on a FAT drive: new file, goes on), 403 (fresh links),
  a network error (waits), and a title/cover step that fails for lack of room
  for a second copy (the whole part is kept untagged, the `.temp.mp4` goes).
  A rerun of a finished part says `already here, nothing to download`.
- **Before it starts:** the part's estimated size against the free space
  (and roughly where it will stop), and a warning when a FAT32 card gets a
  part over 4 GB. On Android the card's real file system is read from
  `/mnt/media_rw/XXXX-XXXX` behind the `/storage/XXXX-XXXX` FUSE mount.
- Found on the way: `:g` wrote a start of 14430.15 as `14430.1` (before the
  nudge), and would have written `1e+05` past 27 h. The section bounds are
  now written in full.
- Tested for real on YouTube (README, "Verified"): `kill -9` mid-part, a
  250 MB disk filling up, and a 60 MB file-size limit standing in for FAT32.
  `.part` files from before this change have no index and cannot be fixed by
  it; they are left alone and the part is fetched again.

---

## `9da2195` — Force stop; Stop no longer leaves ffmpeg downloading
*2026-11-19 · 1083 assertions*

Asked for: a way to stop *immediately* when it really has to stop.

- **Force stop** button beside Stop. Ctrl+C a second time in a terminal does
  the same. It ends every program musictool started, along with every
  program those started, at once. It also cuts waits short, ends anything
  started after it, and drops the queue. Every long-running subprocess now
  goes through `cancel.popen` / `cancel.run` (download, live, chat, the probes),
  so it can be found. A real `--range` download: the job ended 0.0 s after it,
  nothing left running.
- **Stop left yt-dlp's ffmpeg running.** It ended only yt-dlp, and the ffmpeg
  under it (every part, every merge) kept downloading: measured, 5 MB in the 5
  s after Stop. `stop_process` now ends the tree (`taskkill /F /T` on Windows;
  children from `/proc` or `ps` elsewhere, SIGTERM then SIGKILL). Same test:
  0.9 s, 0 bytes after.
- **Waits ignored Stop.** The network wait (up to 240 s) and the lyrics
  retry waits were plain `time.sleep`. `cancel.sleep` checks every 0.25 s.
- After a stopped `--range`, the log said the `.part` would resume. ffmpeg
  (`-y`) starts a part over, and it now says so.
- Tests: section 42 (16 assertions); probe tests patch `download.cancel.run`.

## `86be9ea` — a part no longer crawls at 2x real time
*2026-11-19 · 1067 assertions*

Reported: `get --range 1:50:00- --exact -f 299+140` on 45FqURpMNCs "stays
stuck on downloading, it never starts". It had started: the `.part` file grew,
but at about 2x real time. For the first minutes the bar read 0.0% with an
ETA of 45 hours, then settled at about 45 minutes for 1:30:30 of video.

- **Cause:** ffmpeg (which fetches every part) asks YouTube for the rest of
  the file in one request, and YouTube slows such a request to about 2x real
  time. yt-dlp's own downloader asks in 10 MB pieces, so a full download of
  the same 5.56 GiB format ran at 30-140 MiB/s.
- **Fix:** when `ffmpeg -h protocol=http` lists `-request_size` (ffmpeg
  8.1+), a part is fetched with `-multiple_requests 1 -request_size 10485760
  -short_seek_size 10485760` after the reconnect options (`download.http_opts`).
  Same command, same stream: 39% in 44 s at up to 70x, against 1.3% in 50 s.
- With an older ffmpeg nothing extra is passed, because an unknown option
  would stop the download. `get --range` instead says that the part comes at
  about 2x real time, how long that will be, and that ffmpeg 8.1 fixes it.
- Tests: section 41 (6 assertions).

## `476d533` — live chat replay as subtitles; only part of a video
*2026-11-19 · 1061 assertions*

Asked for, on R0GdEkPZSvM (a finished 3h39m stream with one hour already
watched): the chat replay playing with the video, with several messages on
screen at once, a settable count, on the right, in a chosen subtitle format,
and a way to download only the rest of the stream.

- **`musictool chat URL|FILE`** (15th command) downloads only the chat replay
  (`--skip-download --write-subs --sub-langs live_chat`, never twice) and
  writes a subtitle next to the matching video (found by `[id]` or the
  index), with the video's name so players load it by themselves.
  - `--lines N` (default 5): a message stays until N newer ones are below
    it. 1 = ordinary subtitles; 6 = a scrolling chat.
  - `--hold S` (default 15, 0 = until pushed off) is the longest a message
    stays.
  - `--side right|left|center` and `--place bottom|middle|top`. In ASS this
    is a column (`--width`) of names coloured by role on a dark box; SRT
    uses `{\anN}`, with `--pad` no-break spaces for players that ignore
    it; VTT uses cue positions.
  - `--format ass|srt|vtt|txt`, `--no-names`, `--no-box`, `--offset`.
  - Only files musictool wrote are replaced (SRT ones listed in
    `.musictool-chat.json`).
  - `--embed` copies the chat in as a track named *Live chat*: an mkv is
    updated in place (the old chat track is swapped, not doubled); an mp4
    gets an mkv copy and is kept. The free space is checked first, and the
    mp4's cover is left out (it would be a second video track).
- **`get --range START-END`** takes only that part of a finished video
  (`1:00:00-`, `1:00:00-2:30:00`, `-45:00`, `1h-2h30m`), saved as
  `TITLE [id] (from 1h00m00s)`. `chat` reads that name and times the chat
  from 1:00:00. A range on a live stream, or past the end, is refused before
  downloading.
  - ffmpeg fetches a part and used to stop quietly at a dropped connection:
    a 15 s drop gave 6 s of a 4-minute part, "100%", exit 0. It now gets
    reconnect options (only the ones it lists), the result is checked for
    length, and a short file prints the `--range` for the missing end.
  - ffmpeg's `time=` lines become the progress (`13.3% of the part … ETA`),
    and its chatter is kept out of the log, including the one line per
    piece of YouTube's HLS streams.
- **A cut starts at the keyframe before it.** An MP4 hides those seconds;
  in an MKV the chat had been 3.8 s early on the real stream. `embed` shifts
  the video and the chat together, and a sidecar next to an MKV is timed to
  its sound.
- **Live streams tab:** an *Only this part* field, *Download this part*, and
  a *Live chat replay as subtitles* box with format, messages at once,
  longest on screen, side, height, names, SRT spaces, *add as a track* and
  *Get the chat*. The fields are remembered. The Download tab gets *Only this
  part* from the parser.
- `util.parse_time` / `parse_range` / `part_label` / `clock`.
- Verified with the maintainer's cookies on real YouTube: a 30 s part came
  out at exactly 30.02 s, and `chat --embed` put the first cue on the same
  millisecond as the sound (README, "Verified").
- Tests: section 40 (59 checks), on a synthetic replay; the Live tab's entry
  count; `tkstub` has `Spinbox`.

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
