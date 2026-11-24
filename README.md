# musictool

Download music from YouTube/YouTube Music and fetch **synced** lyrics for it.
Replaces a `get_music.sh` shell script with something that handles playlists,
multiple lyrics sources, and both a CLI and a GUI.

- **Cross-platform:** Windows, macOS, Linux, Termux/Android
- **Python 3.8+, stdlib only** plus two dependencies: `yt-dlp` and `mutagen`
- Works offline for scanning; the only network calls are to yt-dlp and lyrics APIs

**Contents:** [Install](#install) · [Quick start](#quick-start) · [The window: Smart and modes](#the-window-paste-a-link-smart-does-the-rest) · [Commands](#commands) · [Am I blocked? (`check`)](#am-i-blocked----check) · [Picking a quality by size (`formats`)](#picking-a-quality-by-its-size----formats) · [Cookies and the bot wall](#cookies-sign-in-to-confirm-youre-not-a-bot) · [Surviving WiFi drops](#surviving-wifi-drops) · [Stopping a run](#stopping-a-run) · [Live streams](#live-streams-from-the-start-watch-it-so-far-carry-on-after-a-stop) · [Only part of a video, and the live chat as subtitles](#only-part-of-a-video-and-the-live-chat-as-subtitles) · [Lyrics are included as it downloads](#lyrics-are-included-as-it-downloads) · [Finding wrongly-embedded lyrics](#finding-wrongly-embedded-lyrics) · [Lyrics sources](#lyrics-sources) · [What it writes](#what-it-writes) · [GUI](#gui) · [Config](#config) · [Platform notes](#platform-notes) · [Tests](#tests) · [Verified / not verified](#verified--not-verified)

```
./
├── README.md             this manual
├── HANDOFF.md            context for whoever works on this next
├── CHANGELOG.md          what changed and why, per commit
├── pyproject.toml        installable; provides the `musictool` command
├── musictool.py          entry point — runs with no install at all
├── musictool/
│   ├── cli.py            command line
│   ├── gui.py            Tkinter interface
│   ├── uicmd.py          parser -> widgets, argv, modes, link kinds, learning
│   ├── formats.py        quality table, filters, language picking
│   ├── download.py       yt-dlp wrapper + videoId index
│   ├── live.py           live streams: record from the start, watch, carry on
│   ├── parts.py          get --range: keyframe start, keep what came down, carry on
│   ├── chat.py           a stream's chat replay -> ass/srt/vtt/txt subtitles, and back
│   ├── progress.py       yt-dlp progress lines -> the GUI's bars
│   ├── lyrics.py         scan / fetch / embed pipeline
│   ├── providers.py      ytmusic, lrclib, netease
│   ├── tags.py           mutagen tag + .lrc handling
│   ├── cancel.py         Stop / Force stop
│   ├── config.py         saved defaults
│   └── util.py           normalisation, LRC, HTTP
├── tests/                1217 assertions, offline, headless (see docs/TESTING.md)
├── docs/                 architecture, decisions, testing, ffmpeg recipes
└── handoff/              not part of musictool: ffmpeg / Quick Sync / Android notes
```

## Install

```bash
pip install -U "yt-dlp[default]" mutagen

# a JavaScript runtime: YouTube downloads need one to solve its challenge
winget install DenoLand.Deno                # Windows
pkg update && pkg upgrade && pkg install deno   # Termux (upgrade first, or deno fails to link)
curl -fsSL https://deno.land/install.sh | sh    # macOS/Linux (or: brew install deno)
# node works too -- musictool enables it automatically when it is the only one

# ffmpeg is needed to extract audio and embed cover art
pkg install ffmpeg                          # Termux
winget install Gyan.FFmpeg                  # Windows
sudo apt install ffmpeg                     # Debian/Ubuntu

# GUI (optional — the CLI needs none of this)
pkg install python-tkinter                  # Termux
sudo apt install python3-tk                 # Debian/Ubuntu
# Windows: the python.org installer already includes tkinter

# check everything
python musictool.py doctor
```

**Why `yt-dlp[default]` and not just `yt-dlp`:** plain `pip install yt-dlp`
installs no dependencies at all, and so leaves out `yt-dlp-ejs`, the scripts
yt-dlp runs (with deno/node) to solve YouTube's challenge. The `[default]`
extra brings it (plus brotli, pycryptodomex, websockets). The official
`yt-dlp.exe`/standalone builds already include it. `doctor` has an `ejs` line
that tells you which case you are in; without it you need
`--remote-components ejs:github`, which lets yt-dlp fetch the scripts from
GitHub at run time.

Nothing needs installing to run it — `python musictool.py …` works straight
from a clone. If you would rather have a `musictool` command on your `PATH`:

```bash
pip install .          # from the repository root
musictool doctor
```

## Quick start

```bash
# one video
python musictool.py get "https://music.youtube.com/watch?v=XXXX"

# a whole playlist -- each file gets its lyrics the moment it finishes,
# so the folder is already tagged while the rest is still downloading
python musictool.py get "https://music.youtube.com/playlist?list=PLVuMx2J3_K6w" \
    -o ~/Music/Gospel --items 1-186 --cookies cookies.txt

# fix lyrics for files you already have
python musictool.py lyrics ~/Music/Gospel

# am I blocked, or can I download with no cookies at all?
python musictool.py check "https://youtube.com/watch?v=XXXX"

# what qualities does this video have, and how big is each?
python musictool.py formats "https://youtube.com/watch?v=XXXX" --keep-video

# one video, at a chosen quality, with a size ceiling
python musictool.py get "https://youtube.com/watch?v=XXXX" --keep-video \
    --no-playlist --quality 720 --max-size 250M --merge-format mkv --no-lyrics

# what needs fixing? (offline, instant)
python musictool.py scan ~/Music/Gospel

python musictool.py gui          # graphical interface
```

## The window: paste a link, Smart does the rest

`musictool gui` opens on **Download**. Paste a link — or several, or a whole
message with links in it — and the window says what each one is *as you
paste it*: a YouTube Music song or album, a YouTube video, a video in a
playlist (just that one), a Short, a live stream, a playlist, a channel, a
SoundCloud or Bandcamp track or album, a video on another site. A Spotify
link is explained rather than attempted (its streams are encrypted), and a
line without a link is pointed out instead of silently dropped.

**✦ Smart (auto)** then picks one of nine modes, each made for one kind of
thing, and the checkboxes set themselves — no reading, no ritual:

```
song            get URL --format m4a --audio-quality 4 --no-playlist
                    --template '%(uploader)s - %(title)s.%(ext)s'
album           get URL --format m4a --audio-quality 4
                    --template '%(playlist_title)s/%(playlist_index)03d - %(title)s.%(ext)s'
music_video     get URL --keep-video --quality 1080 --merge-format mp4 --no-lyrics --no-playlist
video           get URL --keep-video --quality 1080 --merge-format mkv --chapters --subs
                    --no-lyrics --no-playlist --template '%(title)s.%(ext)s'
short           get URL --keep-video --quality best --merge-format mp4 --no-lyrics --no-playlist
podcast         get URL --format m4a --audio-quality 4 --chapters --no-lyrics --no-playlist
video_playlist  get URL --keep-video --quality 1080 --merge-format mkv --chapters --subs --no-lyrics
                    --template '%(playlist_title)s/%(playlist_index)03d - %(title)s.%(ext)s'
channel         get URL --keep-video --quality 1080 --merge-format mkv --chapters --subs --no-lyrics
                    --template '%(uploader)s/%(upload_date)s - %(title)s.%(ext)s'
live            recorded from the stream's first minute (see Live streams)
```

Five details make it behave:

1. **It learns what you like.** Every download you start is noted: which mode
   you used for which kind of link, and how you set that mode up (quality,
   size cap, container, audio language, file names, folder). Once a choice is
   the majority of your last ten runs for that kind of link *and* you have
   made it at least twice, Smart makes it for you and says so — *you usually
   choose Music video for a YouTube Music song · learned from you: up to
   720p*. Settings → Smart shows everything it has learned, turns learning
   off, or forgets it.
2. **A plain link can hide what it is.** `youtu.be/…` could be a live stream,
   a premiere, a finished stream, a music video, a Short or a two-hour talk.
   **Identify online** asks yt-dlp once and the mode follows the answer
   (Settings → Smart can do it for every paste).
3. **`watch?v=…&list=…` stays one video.** The `list=` there is the "up next"
   queue, not the thing you asked for. A `/playlist?list=…` link is a
   playlist.
4. **You can always take the wheel.** Pick a mode yourself and it is obeyed
   (only one-or-many still follows the link: *Song* with an album link is
   *Album*). Tick something by hand and the window stops adjusting and says
   *changed by hand*.
5. **The button says what will happen** — *Download song + lyrics*,
   *Download album + lyrics*, *Download Short*, *Record live stream*,
   *Download channel* … — so the mode is never a surprise when you press it.

### One scroll, folded options, and nothing to retype

Three things about the window that used to cost clicks:

**The whole tab scrolls as one.** The link box, the modes, the fine-tune strip
and the foldable sections all live inside a single scrolling surface, so the
wheel moves the tab wherever the pointer is — you do not have to aim at the
options first. Only the run buttons stay pinned. (A text box that genuinely cannot scroll —
the three-line URL field — passes the wheel on; one with thirty URLs in it
keeps it.)

**Options you have not touched are folded away.** The *More options* section
is folded until you open it, and inside it the grid shows the rows that
have something in them — folder, filename template, audio format, language,
speed limit if you set one — plus anything you have changed, and folds the
blank rest behind one button:

```
   [ show all 42 options ]   31 more folded away
   ┌ files, folders and access ────────────────┐
   │ Folder: [...]      Filename: [...]         │
   │ Audio format: m4a  Audio quality: 4        │
   └────────────────────────────────────────────┘
```

Sections are named for a reader (*files, folders and access*, *quality and
format*, *lyrics*), and a setting that is already on the fine-tune strip is not
repeated in the grid.

**Your values come back.** Every field on every tab, the mode you were in, the
URLs you had open and which sections you left open are written to the
config file as you change them and again when you close the window or start a
run — so a restart is not a form to fill in. `800M` is still `800M`.

```
~/.config/musictool/config.json     "gui_opts": { "get": { "max_size": "800M", … } }
```

Choosing a mode no longer wipes what you typed: a blank in a mode's settings
means "no opinion", so your size ceiling, cookies and speed limit survive a
switch from Song to Video. The single exception is a raw format selector,
which overrides everything else — a mode always clears that rather than
inheriting a stale one.

### "Some of them still aren't English"

Two separate things were going on.

**The bug.** When you pasted four URLs, `musictool` picked a quality and a language
from the *first* video and then used those exact format ids for the other three.
Format ids are not global: `140-4` was the English track on one video and the
Spanish original on the next, and `614` did not exist at all on the others. So the
first file came out right and the rest arrived in whatever language that id
happened to be. You could see it in the log: only the first line printed
`quality:`, the others went straight to `-f`.

Every URL is now resolved on its own, and every one of them prints its own
`quality:` line. Pasting four links now means four decisions, each made against
the tracks that video actually offers:

```
quality: -f 614+140-4   (1080p vp09 + mp4a [en] ~ 331.9 MiB)
quality: -f 137+140-9   (1080p avc1 + mp4a [en] ~ 340.5 MiB)
```

**The files you already have.** You do not have to download them again. The video
is fine — only the audio track is in the wrong language, and a dubbed video
offers each language as its own separate track. So `musictool` fetches just the
track it needs (tens of megabytes, not hundreds) and folds it into the file you
already have:

```
musictool relang "C:\Music\Videos"
```

```
==> show.mkv
    offered: es (original), en
    will take format 140-9 (en, 44.8 MiB) and keep the video
    done -- show.mkv now has the en audio
1 file(s) re-muxed
```

It finds the video from the index musictool wrote when it downloaded the file
(`.musictool-index.json` in that folder), works out which tracks exist, and
re-muxes with `ffmpeg` keeping everything else — the picture, the embedded cover,
the chapters, the tags. Nothing is re-encoded, so it takes seconds per file.

* It is conservative: a file already in that language is left alone, and so is a
  file whose audio carries no language tag (say `--force` if you know better).
* A file it did not download has no videoId to look up, and it says so rather
  than guessing from the filename.
* `--dry-run` shows what it would do; `--audio-lang es` goes the other way;
  pass `--cookies-from-browser auto` if YouTube wants a session again.
* In the GUI it is the **Fix language in existing files** button in the
  Download tab's **Fix language** section — the Save to folder, the same
  language setting.

**Which files are affected?** You do not need to dig the links out of an old
terminal log — musictool wrote each one down when it downloaded the file
(`.musictool-index.json`, in the same folder). So it can read them straight back
off your disk:

```
musictool relang "C:\Music\Videos" --list
```

```
3 file(s) checked, looking for en audio

NOT in en -- these are the ones to fix (1):
  spa   Sermon 04.mkv
        https://youtu.be/vid1

already en (1):
  eng   Sermon 03.mkv

musictool never downloaded these, so there is no link to give (1):
  ?     something-else.m4a
  re-download them by hand, or add them to .musictool-index.json

Put the en audio into those 1 file(s) without re-downloading the video:
  musictool relang "C:\Music\Videos"
```

It reads the language tag inside each file and nothing else — **no downloads,
no network** — so it works even while YouTube is refusing to talk to you, and it
will not hand you a link it does not actually have. Files musictool did not
download have no entry in the index, and it says so rather than guessing one
from the filename.

`--list` is also the **List wrong-language files** button in the Download tab's
**Fix language** section.


### The progress panel, a queue, and more than one video at a time

**The bars are there when you open the window.** They used to open collapsed —
`ttk` leaves the divider at zero until it lays the window out, so you had to
drag the slider to see anything. The first layout now gives them their width,
and never less than 160px. Dragging still works and is still remembered; it just
cannot start from nothing.

**How many are left, and when will it be done.** Pasting four links used to
report each one as a "single download", because yt-dlp only ever sees one URL
at a time and cannot know three are queued behind it. musictool knows, so the
panel now says:

```
video 2 of 5    26.0%   ·   3 still to go after this   ·   2m 10s elapsed   ·   about 6m 30s left in total
Second.mp4    30.0%   2.0MiB/s   ETA 00:20
```

The projection is elapsed time scaled by how far through the run you are. On a
playlist it uses the length YouTube reports; on a pasted list it uses the number
of links. Before the first file has finished there is nothing to scale from, so
it falls back to that file's own ETA rather than printing a confident zero.

**Add more while it is downloading.** The Download button stays live during a
run and turns into **Add to queue**: paste more links, click, and they wait
their turn instead of being refused. The log says how many are waiting, and each
one starts the moment the one before it finishes. Pressing Stop drops the queue
rather than starting jobs into a stop.

**Several videos at once.** `--jobs 2` runs two videos in parallel — worth
trying when one download is not using your connection:

```
musictool get URL1 URL2 URL3 --jobs 3
```

```
==> 3 video(s), 3 at a time
[1] ==> https://youtu.be/...
[2] ==> https://youtu.be/...
```

Each video still resolves its own quality and language, and every line is
numbered so the logs do not interleave into noise. Capped at 8 — past three or
four the gain flattens out and YouTube starts throttling. Note the difference
from `--concurrent`: that splits *one* video into pieces, this runs *several*
videos. In the GUI it is the **jobs** field under More Options.


## Commands

| command | what it does |
|---|---|
| `get URL...` | download, embedding each file's lyrics as it lands |
| `check URL...` | is this reachable, or does YouTube want a login? (no cookies used) |
| `formats URL...` | the qualities on offer (variant splits merged), with sizes, plus the command to take one |
| `live [FOLDER]` | live streams: `--status URL` (live? how big? room?), what is in a stopped recording, `--join`/`--watch` to watch it so far, `--resume URL` for the rest |
| `chat URL\|FILE` | a finished stream's chat replay as subtitles beside the video (or the part of it you have), optionally added to it as a track (copied) |
| `relang PATH...` | put the language you want into files already downloaded (audio only, video kept) |
| `lyrics PATH...` | fetch/fix lyrics for existing files or folders |
| `scan PATH...` | offline state report (`--json` for scripting) |
| `duplicates PATH...` | find files sharing the same lyrics; `--fix` re-fetches |
| `extract PATH...` | write each file's embedded lyrics to a `.lrc` you can edit |
| `embed PATH...` | put a `.lrc` you made/downloaded back into the matching song |
| `providers` | list lyrics sources; `--test` probes them live |
| `config [key] [value]` | show or set saved defaults |
| `doctor` | verify yt-dlp, ffmpeg, tkinter, mutagen |
| `gui` | graphical interface |

### Download options

`-o/--output-dir` · `-t/--template` · `--format m4a|mp3|opus|flac|aac|ogg|wav` ·
`-q/--audio-quality 0-10` · `--keep-video` · `--no-thumbnail` · `--no-metadata` ·
`--chapters` · `--just-download` (see below) ·
`--ascii` (ASCII-only filenames, helps picky players) ·
`--cookies FILE` · `--cookies-from-browser auto|off|edge|chrome|firefox|brave|...` ·
`--proxy` · `--limit-rate 2M` · `--concurrent N` (fragments of one video) ·
`--jobs N` (videos in parallel, 1–8) ·
`--archive FILE` (skip already-downloaded videos) · `--items 1-50` ·
`--no-playlist` · `--yes-playlist` · `--retries N` (attempts across a network
drop) · `--net-max-wait SECONDS` (how long to keep retrying, default 240) ·
`--live-from-now` (a stream that is live is recorded from its start unless
this is given) · `--range 1:00:00-` (only that part of a finished video; see
[below](#only-part-of-a-video-and-the-live-chat-as-subtitles))

**`--just-download`: done the moment the download is.** Normally, once a
file is down, yt-dlp adds the title and other details, the cover, the chapters
and the subtitles to it, and musictool adds lyrics. Each of those rewrites the
file. For a long video that means a second full copy, which takes minutes on a
phone's memory card and needs room for it. `--just-download` adds none of
them. `--subs` are then saved as files beside the video, and a stream's chat
too (`NAME.live_chat.json`, which `musictool chat` turns into subtitles). A
part (`--range`) stays the fragmented MP4 it was written as. ffmpeg ends it
with a seek index (`mfra`), so players seek in it normally. Tested on a 30 s
part of a real 1080p60 stream: no step ran after the download, and seeking
to 0:20 worked.

### Quality options (video downloads)

`--quality 720` (or `1080`, `best`, `smallest`) · `--max-size 250M` (best quality
that fits under it) · `-f/--format-selector 137+140` (a raw yt-dlp selector, overrides both) ·
`--audio-lang en` (spoken language to prefer; `any` for yt-dlp's default) ·
`--merge-format mkv|mp4|webm|mov` · `--exact` (see below) · `--subs` · `--sub-langs en,es` ·
`--remote-components ejs:github` · `--js-runtimes node` (YouTube's n-challenge
needs a JS runtime; yt-dlp only enables deno by default) · `--keep-video`
(without it you get audio only)

### Listing options (`formats`)

`--auto best|compatible|fit|smallest|audio` (it says why) · `--max-size 1G` ·
`--quality 1080` · `--container mp4|webm|mkv` · `--codec avc1|vp9|av01|opus` ·
`--sort size_desc|size_asc|resolution|container` · `--show-audio` ·
`--all-formats` (every raw row, nothing merged)

### Lyrics options

`--no-lyrics` · `--lyrics-only` · `--lyrics-dir DIR` · `--providers lrclib,netease` ·
`--allow-unsynced` · `--shift -2.5` (offset drifting timestamps) · `--delay 0.35` ·
`--force` · `--recursive` · `--limit N` · `--dry-run` · `--no-embed`

## Am I blocked? -- `check`

Before touching cookies at all, ask:

```bash
python musictool.py check "https://youtube.com/watch?v=XXXX"
python musictool.py check "https://youtube.com/playlist?list=PLrOFa8sDv6jf..."
```

`check` deliberately uses **no cookies** -- a request that succeeds while carrying
your login proves nothing about whether you are blocked, so it goes in as a
stranger and tells you what came back:

```
checking with no cookies, as a stranger -- this is the honest answer to 'am I blocked?'

==> https://youtube.com/playlist?list=PLrOFa8sDv6jf...
    reachable  playlist of 447 items; first is "Elevation Worship - Praise"
    -> no login needed for this one

============================================================
checked 1 URL(s): 1 reachable, 0 blocked
you are not blocked: download with no cookies at all
```

A third verdict, `NO STREAMS`, means YouTube answered but yt-dlp got no audio
or video out of it — only the storyboard pictures. That is your yt-dlp setup
(the JS challenge went unsolved), not a login, and it comes with the fix:
`pip install -U "yt-dlp[default]"` and a JS runtime; `doctor` shows which is
missing. It also exits non-zero.

When it *is* blocked it says which kind of block and what would fix it --
`bot` (needs a login), `age`, `private`, `members`, `unavailable`, or `network`
(a dead connection, which is not a block at all) -- and exits non-zero so you can
script around it. `--with-cookies` runs the same check *with* your login, for
comparing the two.

**From a server or VPS** the answer is usually `bot` for everything. Checked on
2026-11-18 from a cloud machine: every video answered *Sign in to confirm
you're not a bot*; yt-dlp's other player clients got past that page but were
given only storyboards, no audio or video, and so were they with a PO-token
provider running. That is a block on the address, not on the tool — at home
the same command is the honest test.

## Picking a quality by its size -- `formats`

```bash
python musictool.py formats "https://youtube.com/watch?v=XXXX" --keep-video
```

```
    Long worship mix  (60:00)
id   what        container  video  audio  size       variants
---  ----------  ---------  -----  -----  ---------  --------
625  video only  mp4        2160p  vp09   7.9 GiB    3 merged
313  video only  webm       2160p  vp9    5.9 GiB    3 merged
401  video only  mp4        2160p  av01   1.7 GiB
137  video only  mp4        1080p  avc1   619.9 MiB
609  video only  mp4        720p   vp09   896.5 MiB
136  video only  mp4        720p   avc1   314.7 MiB
160  video only  mp4        144p   avc1   14.3 MiB
    (107 rows from yt-dlp collapsed to 7: variant splits and same-quality
     doubles are merged -- --all-formats shows every row)

    to take this one:
      musictool get "https://youtube.com/watch?v=XXXX" --keep-video -f 625-2+251-17
```

Sizes are yt-dlp's own numbers: `filesize`, or `filesize_approx`, or bitrate x
duration when YouTube reports neither. Storyboards and other non-media entries
are filtered out. Video-only streams are paired with a real audio track -- never
with another combined file, which would drag a second video track along.

### Why the list is shorter than yt-dlp's

YouTube does not serve one file per quality. Every stream is offered as a set of
near-identical CDN copies with suffixed ids (`140-0`, `140-1`, ... `140-17`), so a
single 4K video reports **127 rows** for what is really a dozen choices. That list
is unusable, so musictool merges it before showing it to you:

- **variant splits** -- the 18 copies of one stream become one row, sized by the
  biggest of them, with a `variants` column saying how many were merged;
- **same-quality doubles** -- two rows at the same resolution, codec and
  container are the same quality twice, so the smaller (better) encode stays;
- **audio-only tracks** are hidden, because they are not something you pick on
  their own -- they are what the video row gets paired with.

Nothing is thrown away: `--all-formats` prints every raw row, and
`--show-audio` puts the audio tracks back in the table — **the ones in your
`--audio-lang`** (default `en`), so a dubbed video does not bury you in eight
languages. A line under the table says how many other-language tracks it hid;
`--audio-lang any` shows them all. If the video has no track in your language
it shows every language and says so (the download would take the best one).
In music mode (no `--keep-video`) the audio rows are always listed, filtered
the same way, and the suggested command is an audio download.

If the table comes back **empty**, `formats` says why instead of printing
nothing. Nearly always it is the JS challenge: yt-dlp then sees only preview
pictures, and the line names the fix (and quotes yt-dlp's own warning).

### Filtering it, or letting it choose

```bash
musictool formats URL --keep-video --auto best          # tallest available
musictool formats URL --keep-video --auto compatible    # H.264/mp4: plays anywhere
musictool formats URL --keep-video --auto fit --max-size 1G
musictool formats URL --keep-video --auto smallest
musictool formats URL --keep-video --auto audio
musictool formats URL --keep-video --container mp4 --codec vp9
musictool formats URL --keep-video --max-size 700M --sort size_asc --show-audio
```

`--auto` prints the choice **and the reason**:

```
    auto (compatible): tallest H.264/mp4 (plays on anything, no conversion)
                       ~ 619.9 MiB  +  opus audio -> ~ 673.3 MiB total
      musictool get "https://youtube.com/watch?v=XXXX" --keep-video -f 137+251-17
```

Each id in the printed selector is one this video actually serves: the table
shows the merged id (`625`), the command names the exact copy behind it
(`625-2`). Either way yt-dlp downloads the whole stream, never a slice of one —
and there is deliberately no `base/variant` fallback (see the trap below).

The **Quality** tab in the GUI does all of this with dropdowns and one click;
see [GUI](#gui).

Or skip the reading and just say what you want:

```bash
musictool get URL --keep-video --quality 720              # tallest 720p-or-less
musictool get URL --keep-video --max-size 250M            # best that fits
musictool get URL --keep-video --max-size 250M --quality 1080
musictool get URL --keep-video -f 137+140 --merge-format mkv   # an id from the list
```

Each costs one metadata request and prints what it chose
(`quality: -f 136+251   (720p avc1 + opus ~ 35.7 MiB)`). If nothing fits the
ceiling it says so rather than quietly taking something bigger. An explicit `-f`
always wins and skips the lookup.

### The `-f` line, and the trap that handed back audio instead of video

`formats` prints a ready command, and `get --quality/--max-size` prints the
selector it chose:

```
  quality: -f 248+251-1   (1080p vp9 + opus ~ 634.4 MiB)
```

Every id there is one this video actually serves. That matters more than it
looks, because YouTube does not serve the plain itag for range-delivered
streams: the audio track is `140-0` … `140-17`, and there is **no** format
called `140`. An older build offered `base/variant` as a convenience:

```
-f 248+140/140-0        # looks reasonable, is not
```

`/` binds looser than `+`, so yt-dlp reads that as `(248+140) / (140-0)`.
The first branch cannot match — there is no `140` — and it silently falls
through to the second, which is the audio track alone. The result was a
52 MiB `.m4a` where a 644 MiB 1080p video was promised, with `--keep-video`
set and no error anywhere. Asking yt-dlp's own parser confirms it:

| `-f` | yt-dlp downloads |
|---|---|
| `248+140/140-0` | `140-0` — **audio only** |
| `248+140-0` | `248+140-0` — video + audio |

So a selector now names one concrete id per stream, with no `/` fallback to
fall through. If a stream has gone by the time you download, yt-dlp says
`requested format is not available` out loud, which is what you want.

Two related guards, both added after a real 1.74 GiB surprise:

- **`--quality` can no longer be ignored.** It takes a height (`720`, `1080`)
  or a preset (`best`, `smallest`). Paste a raw selector into it and it is
  used as `-f`, with a note saying so; paste nonsense and it says
  `!! ignoring --quality 'banana'` instead of quietly downloading yt-dlp's
  own default.
- **An adopted selector is checked against the real format list** before the
  download starts:

```
  note: '248+140/140-0' is a raw format selector, not a height -- using it as -f
  !! this video serves no format called 140 (it has 137, 140-0, 140-7, 248, 251-1, 401)
  !! nothing before the '/' can match, so yt-dlp will silently take the fallback after it
```

When the qualities cannot be read at all — no network, a blocked video — the
log says `yt-dlp will pick its own default`, so an unapplied `--max-size` is
never a silent one.

### Dubbed videos: which language you get

YouTube serves a dubbed track as its own format, with the language on it, and
**yt-dlp's default ranks the *original* language first** — so on a video whose
original is Spanish, the default download is the Spanish audio even when an
English dub is sitting right there. `--audio-lang` (default `en`) is how you
say which one you want, and `formats` shows you what is on offer:

```
id     what        container  video  audio  lang  size
-----  ----------  ---------  -----  -----  ----  ---------
248    video only  webm       1080p  vp9          152.6 MiB
140-0  audio only  m4a        -      mp4a   es*   17.2 MiB
140-1  audio only  m4a        -      mp4a   en    13.7 MiB
140-2  audio only  m4a        -      mp4a   fr    13.7 MiB

    spoken languages: es (original), en, fr
    will take: en

    to take this one:
      musictool get "…" --keep-video -f 248+140-1     # 1080p vp9 + mp4a [en] ~ 166.3 MiB
```

`*` marks the original track. The `lang` column appears only when the video
actually has tagged languages, so a normal single-language video looks exactly
as it did before.

- `--audio-lang en` (the default) — take the English track when there is one.
- `--audio-lang es` — take a different one.
- `--audio-lang any` — no preference; take whatever is best, which is yt-dlp's
  old behaviour (the original language).
- If the language you asked for is not offered, it says
  `will take: the best available -- nothing here is tagged de` rather than
  silently giving you something else.

Under the hood this is a **format filter**, not a sort. `--format-sort lang:en`
looks like the obvious answer and does nothing: in yt-dlp the sort field called
`lang` maps to `language_preference`, a number, not to the language code. What
works is

```
-f "bv*+ba[language^=en]/bv*+ba/b"      # video downloads
-f "ba[language^=en]/ba/b"              # audio-only downloads
```

which musictool adds for you when no explicit `-f` is in play — with the `/…/b`
fallback, so a video with no tagged languages behaves exactly as before. An
explicit `--format-selector` still wins over everything.

Two bugs this exposed while being built: a dubbed video's three audio tracks
shared one itag base (`140-0`, `140-1`, `140-2`), so the variant collapse
merged all three into one row and the de-duplication merged them again — you
literally could not see that an English track existed. Language is now part of
both grouping keys.

### Your previous yt-dlp command, in musictool

```bash
musictool get "https://youtube.com/playlist?list=PLrOFa8sDv6jfKx9poMArMUV2MGbZoXrCT" \
    --keep-video --merge-format mkv --chapters --subs --quality 1080 \
    -t "%(playlist_title)s/%(playlist_index)02d - %(title)s.%(ext)s"
```

That is `-f bestvideo+bestaudio/best` chosen by size instead of by guesswork, with
no `--cookies` at all. You do not need `--remote-components ejs:github` if
yt-dlp came with `yt-dlp-ejs` — the `[default]` install above, or the official
executable; `musictool doctor` says which (`ejs` line).

## Cookies: "Sign in to confirm you're not a bot"

YouTube refuses playlist downloads from an ordinary IP address unless the request
carries a logged-in session. That is the wall you hit part-way through a big
playlist, and it is not something the tool can talk its way around -- it needs your
login. Two ways to give it one:

**1. Let it read your browser (the default).**

```bash
python musictool.py get "https://music.youtube.com/playlist?list=..." -o "~/Music/Gospel Songs"
```

`--cookies-from-browser` defaults to **`auto`**: musictool looks at the browsers
installed on your machine, finds the ones with a cookie database, and uses the one
whose database was touched most recently -- i.e. the browser you actually watch
YouTube in. On Windows that is normally Edge or Chrome, in
`%LOCALAPPDATA%\Microsoft\Edge\User Data` or `...\Google\Chrome\User Data`.
Nothing is exported, nothing is uploaded; yt-dlp reads the cookie store locally.
The log tells you what it picked:

```
cookies: reading your chrome login (C:\Users\you\AppData\Local\Google\Chrome\...\Cookies)
```

> **Windows caveat.** Current Chrome (and other Chromium browsers that have
> adopted it) protects cookies with App-Bound Encryption, which yt-dlp cannot
> decrypt (yt-dlp issue #10927 — still the case in 2026.10.26). `auto` does not
> know that: it picks the most recently used browser even when that is Chrome,
> and the run then fails with `Failed to decrypt with DPAPI` (see the table
> below). On Windows the dependable choices are `--cookies-from-browser firefox`
> or an exported `cookies.txt`.

Pick one yourself with `--cookies-from-browser chrome` (or `edge`, `firefox`,
`brave`, `vivaldi`, `opera`, `chromium`, `whale`, `safari` -- the browsers yt-dlp
can read), or switch it off with `--cookies-from-browser off`. `musictool doctor`
prints the browser it would use.

If no browser is found the download still runs, and says so instead of failing
silently. And if YouTube still refuses, the error you get now says what to do
rather than leaving you with a bare `ERROR:`: log in to YouTube in Firefox and
use `--cookies-from-browser firefox` (Chrome/Edge only on macOS/Linux), or pass
a `cookies.txt`.

**2. Or point it at an exported `cookies.txt`.**

That is what the **cookies.txt** box is for: the *path* to a Netscape-format
cookies file that a browser extension (e.g. "Get cookies.txt LOCALLY") exported
while you were signed in to YouTube. It is optional, and the browser picker above
is easier -- use a file when the browser you use is one yt-dlp cannot read, or
when you want a fixed login that survives browser updates. A file always wins when
both are set.

### The sign-in cascade, decoded

Adding cookies sets off a chain of *different* failures that all look like "YouTube
hates me". Each has its own fix, so `check`, `get` and `formats` name the one you
actually hit instead of printing a bare `ERROR:`:

| what YouTube / yt-dlp said | what it really means | the fix |
|---|---|---|
| `Sign in again to continue` | the exported `cookies.txt` was flagged as a hijacked session and killed. Refreshing the page will not bring it back. | re-export from a fresh incognito login, or drop the file and use `--cookies-from-browser firefox` |
| `Failed to decrypt with DPAPI` | Chrome/Edge lock their cookies with App-Bound Encryption; yt-dlp cannot open them | log in to YouTube in **Firefox** and use `--cookies-from-browser firefox` |
| `Could not copy Chrome cookie database` | the browser still holds the file open (on Windows, Edge keeps background processes alive via Startup Boost) | close it *properly*, kill the leftovers in Task Manager, retry |
| `Login with OAuth is no longer supported` | yt-dlp dropped the old TV/OAuth login | `--cookies-from-browser firefox`, or a `cookies.txt` |
| `No supported JavaScript runtime` / `n challenge solving failed` | YouTube's n-challenge needs a JS engine (yt-dlp only enables deno by default) **and** the `yt-dlp-ejs` scripts | install deno (or node — musictool enables node itself when it is the only runtime); if `doctor` shows `ejs: MISSING`, `pip install -U "yt-dlp[default]"` or add `--remote-components ejs:github` |
| `The page needs to be reloaded` | the bot wall, usually because the JS challenge went unsolved | same as above |
| `[pot:bgutil:http] ... 127.0.0.1:4416` | the **bgutil PO-token plugin** is installed but its server is not running. It is not yt-dlp's own solver. | start the plugin's server, or uninstall the plugin |
| `Requested format is not available` | that itag is not offered to your account/client -- nothing to do with Premium. **If `formats` itself lists nothing**, it is the JS challenge instead (yt-dlp found only preview pictures); musictool now says so | `musictool formats URL --show-audio --all-formats` to see what is really there |

`musictool doctor` reports the three things that cause the middle of that table:
whether a JS runtime is on your PATH, whether `yt-dlp-ejs` is installed, and
whether a PO-token server is answering on port 4416. `--js-runtimes` and
`--remote-components` (on `get` and `formats`) apply to every yt-dlp call
those make — the download, and the quality/language lookup before it
(`--quality`, `--max-size`, `--audio-lang`); the automatic node fallback
applies to `check` and `relang` as well.

**Resuming where you stopped.** With 286 of 447 already downloaded, either let
yt-dlp skip what exists (`--archive archive.txt` from the start, so it knows), or
ask for the rest by position: `--items 287-447`. A file interrupted mid-download
keeps its `.part`, and `--continue` (always on) resumes it byte-for-byte rather
than starting again.

## Missing or mistyped paths

Every command that takes paths (`lyrics`, `scan`, `duplicates`, `extract`, `embed`)
checks them first: a path that doesn't exist is reported and skipped, and the rest
still run. If *none* of the given paths exist it says so clearly and exits non-zero
instead of silently doing nothing. In the GUI, loading a file list for a folder that
doesn't exist pops a "that path doesn't exist" warning.

## Your own lyrics (extract / embed)

Listened to a song and corrected its lyrics, or downloaded a better `.lrc`?
Round-trip it:

```bash
musictool extract "/storage/67FE-19FE/Music/New Gospel Songs"   # audio -> .lrc
# edit the .lrc: add [mm:ss.xx] timings to make unsynced -> synced
musictool embed   "/storage/67FE-19FE/Music/New Gospel Songs"   # .lrc -> audio
```

`extract` writes `<name>.lrc` next to each file (`--out DIR`, `--force`).
`embed` reads the matching `.lrc` (next to the file, or `--lyrics-dir`) and embeds
it, also refreshing the sidecar; or pass an explicit pair
`musictool embed song.m4a my.lrc`. `--dry-run` shows what would happen. The same
two buttons live on the GUI's **Lyrics files** tab.

## Surviving WiFi drops

A power cut that kills the WiFi for a few minutes should not mean re-downloading
everything. Two layers:

- **Downloads resume, never restart.** yt-dlp runs with `--continue`, so a partial
  file is kept and resumed. If yt-dlp dies with a *connectivity* error, musictool
  waits (exponential back-off up to `--net-max-wait`, default 240 s — enough for a
  two-to-three-minute drop) and runs it again, which resumes from where it left
  off. A genuinely bad URL fails immediately instead of spinning.
- **Lyrics requests retry too.** The HTTP layer treats name-resolution failures,
  timeouts, resets and 5xx as retryable and sleeps through them; 404s and bad
  responses still fail fast.

So in practice you can "forget about it": start a batch, lose the WiFi, and when it
comes back the same run continues. No OS-level WiFi/MAC auto-connect is needed (and
on Android/Termux the OS won't allow an app to force-connect anyway).

## Stopping a run

Press **Stop** (bottom right of the window) or **Ctrl+C** in a terminal. Stop is
**never** greyed out: it works while a run is going, and if you press it with
nothing running it says so and clears any request left over from the last run,
rather than quietly arming a stop that the next run would inherit.

The stop is cooperative, which is what makes it safe:

- the file currently downloading is not deleted -- its `.part` stays, so the next
  run resumes it;
- every file that already finished keeps its lyrics, because lyrics are embedded
  the moment each file lands, not at the end;
- yt-dlp is ended *together with the ffmpeg it started*, so nothing keeps
  downloading in the background. Before, only yt-dlp was ended: in a test its two
  ffmpeg processes went on writing 5 MB in the next 5 seconds. On Windows,
  `yt-dlp.exe` can also be a launcher with the real yt-dlp inside it;
- an ffmpeg step that is writing a file (a merge, adding a chat track) is allowed
  to finish it;
- a summary and `lyrics-report.txt` are still written for the part that completed,
  so you can see exactly what is done and what is left.

Stop acts at the next safe point: when yt-dlp next prints a line, between files,
at the end of a wait. A network wait (up to `--net-max-wait`) and a lyrics retry
are now cut short by it.

### Force stop

**Force stop** (next to Stop) is for when the next safe point is not soon enough.
It ends **everything musictool started, at once**: yt-dlp, ffmpeg, a merge, and
every program those started (`taskkill /F /T` on Windows; on Linux, macOS and
Termux the children are found through `/proc` or `ps`). Waits end, anything
started after it is ended as it starts, and nothing more is done: no lyrics, no
merge. Queued jobs are dropped. In a test on a real `--range` download, the job
had ended 0.0 s after Force stop, nothing was left running, and nothing more was
written.

What is left behind: unfinished downloads stay as `.part` / `.fNNN` files, and
running the same command again carries on from them. What a part (`--range`)
had fetched is kept as a playable file, and running the same command again
fetches only the rest, into a new file (see "If a part does not finish"). An
ffmpeg killed mid-merge leaves the two separate streams, and the next run merges
them again.

In a terminal: **Ctrl+C** once is Stop ("stopping... press Ctrl+C again to force
stop"). Ctrl+C a second time is Force stop, and then musictool quits (exit 130).

### Exactly the streams you chose, copied (`--exact`)

`musictool get URL --exact -f 299+140` downloads exactly those streams and
**converts nothing**: the ids come from `musictool formats URL` (or from
`musictool live --status URL`, which lists them with ids), one per stream,
joined by `+`.

- An id the video does not have is **refused** -- nothing is downloaded and the
  ids it does have are listed. A selector that lets yt-dlp choose (`bv+ba`,
  `299/18`, `[height<=720]`, `best`) is refused too: those are what could swap
  in a different stream.
- **Video + audio** are put together as they are, in a container that holds
  both unchanged: `.mp4` for avc1/av01 + m4a, `.webm` for vp9/av01 webm + opus,
  `.mkv` for a mixed pair (e.g. 299 + 251). `--merge-format` overrides it. webm
  cannot carry a cover picture (yt-dlp fails the run after downloading), so
  with the thumbnail on a webm pair goes into `.mkv` -- the same streams;
  `--no-thumbnail` keeps `.webm`.
- **Audio alone** (`-f 251`) is taken out with its own codec: Opus stays Opus in
  `.opus`, AAC stays AAC in `.m4a` -- never re-encoded to `--format`.
- No subtitle streams are put inside the file (mp4 would need them converted).
  With `--subs` they are saved beside it, and a stream's chat
  (`NAME.live_chat.json`) too. They used to be dropped without a word, so
  `--exact --subs` got no chat.
- Checked against the video before anything starts. In a playlist (no list
  per video), only the form is checked, and yt-dlp stops if an id is missing.

Verified by comparing packet checksums of the source streams with the output
(`ffmpeg -map 0:v -c copy -f framemd5`): identical for 315+251 (webm), 299+251
(mkv), 299+140 (mp4) and the audio of 251 and 140 (only the container's last
packet header differs).

In the window: in the Download tab's Live stream section, **Is it live? How big?** fills a
**Video stream** and an **Audio stream** picker; a stream picked there is
recorded with `--exact`. Picking only the video pairs it with the audio that
keeps the pair a plain copy. On the Download tab the same switch is under
More options, in the *quality and format* group beside the selector box.

### Progress, and a dropped connection

The progress redraws (several a second per stream) are no longer written to
the log: the window's bar and file line take them, and in a terminal they
redraw one line in place (at most 5 times a second). A stream whose total is
not known shows the size so far and `piece i/n`. A dropped connection's
`Got error ... Retrying` lines are logged at most once every 10 s, with
`(+N more like it)`; a bare `ERROR:` is joined to its message.

After a drop yt-dlp used to fire all 5 piece retries within a second, then
**skip** the piece, print `100%` and merge a file with holes -- or sit at
`Destination:` with the speed still ticking. Now retries wait longer each time
(`--retry-sleep exp=1:30`, about 30 s in all), a piece that still cannot be
fetched stops the run instead of being skipped
(`--abort-on-unavailable-fragments`), and musictool waits for the network and
resumes from the same pieces -- `! network problem -- waiting 2s, then resuming
(not restarting)`. Whether a failure is a network one is decided by yt-dlp's
own ERROR lines, not by whatever happened to be printed last.

## Live streams: from the start, watch it so far, carry on after a stop

`get` notices by itself when a link is a live stream (it reads the video's
details first -- one request, which a quality pick reuses) and then:

- **records it from the very start**, even if it began hours ago (YouTube
  keeps 120 hours to rewind); `--live-from-now` takes it from now instead;
- **adds every piece to the file as it arrives.** yt-dlp gives each stream
  `ceil(N / streams)` workers, and with more than one worker it does not
  join a single piece of a live stream until the broadcast ends -- with the
  usual `-N 4` you get thousands of `NAME.f299.mp4.part-FragN` files, nothing
  to play, and a full disk leaves nothing at all. A live recording therefore
  runs with one worker per stream (2 for video + audio, 1 for audio only):
  video and audio still download side by side, the file always holds
  everything so far, and a stop -- full disk, Stop, power cut -- loses
  nothing;
- puts the **video id in the file name** (`Chan - Title [xMXp80Cntxw].mp4`), so
  a second run finds the pieces to carry on from even if the streamer renamed
  the stream, and a later download of the finished video never lands on them;
- says **how big it is and how much room there is** before downloading
  (`size: up to about 2.6 GiB so far, growing up to about 2.6 GiB an hour
  while it runs`, `room: ...`), and warns when the disk cannot hold it -- joining video and
  audio at the end needs about as much space again;
- waits for a stream that has **not started yet** (`--wait-for-video`), and
  takes a stream that has **just ended** from the start too (otherwise
  YouTube offers only its last 2 hours);
- fetches no lyrics -- a broadcast is not a song.

After a stop, **run the same download again**: it continues from the last
piece. Or use `musictool live`, which also rescues a recording made by plain
yt-dlp:

```bash
musictool live --status URL                    # live? since when? how big? room?
musictool live "D:\Videos\Stream"                # what is there, how far it got
musictool live "D:\Videos\Stream" --join         # join loose pieces + "TITLE (so far).mp4"
musictool live "D:\Videos\Stream" --join --out E:\   # the copy on another drive
musictool live "D:\Videos\Stream" --watch        # copy only: safe while it records
musictool live "D:\Videos\Stream" --resume URL   # download the rest
```

- **Joining** works in place: it needs room for about two pieces, not for the
  video. It adds only pieces that follow the file without a gap, in steps of
  50 that are synced and recorded before any piece is deleted -- so it can be
  cut off at any moment, and the next run (or yt-dlp) carries on without
  adding anything twice. It also undoes a run that stopped between adding a
  piece and recording it. Windows' brief "Access is denied" locks (Defender,
  the indexer, OneDrive) are waited out, and a piece that stays locked is
  reported, not fatal. It refuses while a download is still writing to the
  files (changed in the last 30 s); `--watch` never touches them.
- **The copy to watch** is a remux (`ffmpeg -c copy`, nothing re-encoded) and
  needs about as much free space as the recording -- `--out` puts it on
  another drive. Without ffmpeg or room it prints the `mpv`/VLC way to play
  the pieces as they are.
- **`--resume`**: while the stream is live or has just ended, the same pieces
  carry on under the existing name -- nothing already there is fetched again.
  Once it is a normal video those pieces no longer fit, so only the part
  after yours is fetched (`--download-sections`, 5 s overlap, same height) as
  `TITLE (rest).mp4`, with the ffmpeg line to make one file of the two.

A full disk is recognised (`No space left on device`, `There is not enough
space on the disk`) and answered with what to do, not a bare error.

## Only part of a video, and the live chat as subtitles

**Only the part you have not watched.** `--range` takes a time range of a
finished video (a stream that has ended included) and downloads only that:

```bash
musictool get URL --keep-video --range 1:00:00-          # from 1 hour to the end
musictool get URL --keep-video --range 1:00:00-2:30:00   # 1 h to 2 h 30
musictool get URL --keep-video --range -45:00            # the first 45 minutes
```

Times can be `1:00:00`, `60:00`, `3600`, `1h`, `1h30m` or `90m`. The file
is called `TITLE [id] (from 0h59m55s).mp4` (or `(0h59m55s-2h30m00s)`): the
part starts on the keyframe at or before the time you asked for, a few seconds
early, and the name says where the file really starts. `chat` reads that name
to know which part of the chat goes with it. A range
on a stream that is still live, or past the video's end, is refused before
anything downloads, and parts get no lyrics.

- The part is fetched by ffmpeg straight from YouTube, not by yt-dlp's own
  downloader. ffmpeg gives up at the first dropped connection and *still
  reports success*: in a test a 15 s drop turned a 4-minute part into 6
  seconds, with `100%` and exit code 0. So musictool tells ffmpeg to
  reconnect (`--downloader-args "ffmpeg_i:-reconnect 1 ..."`, using only the
  options your ffmpeg has). With that, the same drop gave a part
  bit-identical to an undisturbed one. Afterwards the length of the file is
  compared with what was asked for. If it is short, the file is renamed to
  what it holds and the rest is fetched into a new file.
- **Speed: use ffmpeg 8.1 or newer.** YouTube slows one long, open-ended
  request to about 2x real time, so with an older ffmpeg 1:30:30 of a 1080p
  stream takes about 45 minutes, and for the first minutes the percentage
  sits near 0.0% with an ETA of hours: it looks stuck, but the `.part` file
  is growing. When `ffmpeg -h protocol=http` lists `-request_size`, musictool
  asks for 10 MB at a time (`-request_size 10485760 -multiple_requests 1
  -short_seek_size 10485760`). On the same stream that ran at 17-70x: 39% of
  the 1:30:30 part in 44 s, against 1.3% in 50 s before. With an older ffmpeg,
  `get --range` says so before it starts and estimates how long it will take.
- Progress: ffmpeg gives a position rather than a percentage, so it is turned
  into `13.3% of the part  4m 00s of 30m 00s  2.42x  ETA 10m 44s`, on one
  line. ffmpeg's description of its input and output, and the one line it
  prints per piece of YouTube's HLS streams, are kept out of the log.
- **It starts on a keyframe.** A copied part has to start on a keyframe, and
  ffmpeg used to take the one before your time and hide the extra seconds in
  the MP4's index. That trick does not survive a file that is written to be
  playable while it grows (below): the sound's first packet was stretched over
  the hidden 5 s. So musictool first asks the video where that keyframe is
  (ffmpeg reads one packet, about half a second) and starts the part there.
  It asks ffmpeg for 0.15 s after it, because with B-frames ffmpeg seeks 3/23 s
  before the time it is given and landed on the keyframe before. In a test the
  part's first packet was the keyframe itself, and the sound started 0.17 s in
  instead of being stretched.

### If a part does not finish

A part is written as a **fragmented MP4**: a piece every keyframe (every 2-5 s
on YouTube), each one playable as soon as it is written. When the download
stops for any reason, whatever came down plays. When it finishes, adding the
title and cover turns it into an ordinary MP4.

- **Killed** (Termux closed, the phone off, Force stop, a crash): the next run
  of the same command finds the `.part`, cuts it back to its last whole piece,
  and renames it to what it holds: `TITLE [id] (2h24m55s-2h38m45s).mp4`. Then
  it fetches only the rest, starting 2 s before the end of that file (a little
  overlap, never a gap), into a new file. It never writes over the old file.
  In a test, a 15-minute part killed at 13:50 was kept as a 13:50 file with no
  decode errors (picture and sound both end at 830.0 s). The rerun fetched
  only the remaining 1:20, and a third run said `already here, nothing to
  download`.
- **The disk fills up:** it stops with `!! the disk is full. What came down
  plays; free some space and run the same command again -- it carries on from
  where the files end.` Before it starts, it compares the part's estimated
  size with the free space and says roughly where it will stop. If the part
  came down whole but there is no room to add the title and cover (that needs
  a second copy), the file is kept as it is (it plays) and the half-made
  `.temp.mp4` is removed. All of this was tested on a 250 MB disk.
- **A FAT32 memory card** holds no file of 4 GB or more. When a part reaches
  that, the file is closed (it plays) and the rest goes into a new file, by
  itself. musictool warns before it starts when the card is FAT32 and the part
  is bigger than 4 GB. On Android it looks behind `/storage/XXXX-XXXX` for the
  card's real file system.
- **Dropped connection, or 403 because YouTube's links ran out** (they last
  about 6 hours): it gets fresh links and carries on from where the file ends.
- A `.part` from an older musictool (an ordinary MP4, whose index is written
  only at the end) cannot be played or repaired by this. It is left as it is
  and the part is fetched again.
- With `--subs`, the chat (it covers the whole video) is fetched with the
  first piece only, and not again on a rerun once it is there. It is named
  after the part, `TITLE [id] (1h00m00s-4h00m00s).live_chat.json`, and
  `musictool chat` on that file finds the part's video and writes the chat
  from where the part starts.

**The chat replay as subtitles.** `chat` downloads a finished stream's chat
replay (text only: about 8 MB for a busy 3½-hour stream) and turns it into a
subtitle file with the video's name, so the player loads it by itself:

```bash
musictool chat URL                               # into your download folder
musictool chat URL -o "D:\Videos" --lines 6 --hold 0
musictool chat URL --format srt --side right --pad 50
musictool chat URL --embed                       # also as a track, copied
musictool chat "D:\Videos\T [id].live_chat.json"  # one you already have
musictool chat "D:\Videos\T [id].ass" --format srt  # a chat file it made, as SRT
musictool chat "D:\Videos\T [id].mkv" --lines 3     # the chat track in a video
```

- **How many messages at once: `--lines` (default 5).** A message stays on
  screen until `--lines` newer messages are below it, then the next one
  pushes it off the top. `--lines 1` behaves like ordinary subtitles, and 6
  or more looks like the chat scrolling. **`--hold`** (default 15 s) is the
  longest one message stays, so a quiet chat does not leave a line up for
  minutes; `--hold 0` means a message stays until it is pushed off.
- **Where: `--side right|left|center`, `--place bottom|middle|top`.** With
  ASS (the default) the chat is a column (`--width`, % of the picture) on
  that side. This works in mpv, VLC, MPC-HC, PotPlayer and anything else
  that uses libass. SRT uses `{\an3}`-style tags, which most players honour.
  For one that does not, `--pad 50` pushes every line right with no-break
  spaces: that gives a centred column on the right half (a subtitle file
  cannot line up a left edge, because it does not know the font). VTT uses
  cue positions (browsers, VLC).
- **Colour, no box, a little space.** Each name is bold and in colour, and
  the message in a lighter tint of the same colour, so you can tell at a
  glance whose line is whose. The owner, moderators and members get
  YouTube's colours (yellow, blue, green), a Super Chat the colour YouTube
  gave it, and everyone else a colour of their own, the same one every time.
  `--colours role` colours only those, `--colours off` none. There is no box
  behind the text: it is outlined in black with a soft shadow, so it reads
  on a bright picture too (`--box` puts the see-through dark box back).
  Between two messages there is a gap of 0.4 of a line (`--gap`, in lines;
  `--gap 0` for none). The colours are in the file itself, in all three
  subtitle formats. SRT and VTT cannot make a part-line gap, so there `--gap
  1` or more puts an empty-looking line between messages; VTT's colours and
  "no box" come from a STYLE block that browsers honour and some players
  ignore.
- **`--format ass|srt|vtt|txt`.** `txt` is a readable log, `[1:02:03.450]
  name: message`, not a subtitle.
- **Remaking a chat file.** Give `chat` a file it made before instead of the
  link: an `.ass`, `.srt`, `.vtt` or `.txt`, or a video with the *Live chat*
  track that `--embed` added. It is made again with whatever you change
  (`--format`, `--lines`, `--side`, `--colours`, `--box`, `--offset`, ...).
  Anything you leave out stays as that file had it, and nothing is
  downloaded. The result goes beside the source (or beside the video with
  the same name), and a file in the same format is replaced in place. ASS,
  VTT and the text log keep every message and the settings inside them: the
  ASS in a `[MusicTool Chat]` section that players skip, the VTT in a
  comment, the log in its `#` lines. They read back exactly, in the
  stream's time, so `--range` and a later `--embed` still line up. SRT has
  no room for that, so its messages are rebuilt from what is on screen when,
  with roles taken from the colours. Messages that never showed at all (more
  than `--lines` arriving in the same instant) are not in an SRT. Files from
  older versions are rebuilt the same way. To edit the chat by hand, edit the
  text log (delete a line, fix a word) or the ASS `Message:` lines, then
  remake it in the format you want. A subtitle musictool did not make is
  read as well as it can be, and never overwritten (`NAME.chat.srt`).
- **Parts.** A video named `(from 1h00m00s)` gets the chat from 1:00:00 on,
  starting at 0:00 of the file. Whatever was on screen at that moment stays
  up as if it had been there all along. Every part of the stream in the
  folder gets its own piece of the chat, timed from its own name.
  `--range 0:00:00-6:40:00` picks parts: only the ones inside that range get
  a subtitle, still each from its own name, and the others are left alone.
  A video without a part in its name (or one given with `--video`) is taken
  to hold the range. `--offset 2.5` moves the chat later.
- **Nothing is downloaded twice.** A `.live_chat.json` already in the folder
  is used. The video is found by `[id]` in its name or in musictool's index.
  A subtitle file musictool did not write is never overwritten (`NAME.chat.srt`
  is used instead). One it wrote is replaced on the next run, so trying other
  settings is just running it again.
- **`--embed`** adds it to the video as a subtitle track named *Live chat*,
  copied, not re-encoded, with a check for free space first. An `.mkv` is
  updated in place, and a chat track from an earlier run is replaced rather
  than doubled. An `.mp4` gets an `.mkv` copy beside it, because MP4 cannot
  hold an ASS track and its own subtitle type loses the placement. The
  `.mp4` is kept. The MP4's cover picture is left out of the `.mkv`: it would
  become a second video track. In the MKV, the few seconds of picture before
  a part's start become visible, and the chat and the sound both start after
  them, together. A subtitle next to an `.mkv` like that is timed to the
  sound as well.

In the window this is all in the Live stream section: **Only this part**,
**Download this part** and the **Live chat replay as subtitles** box (see
[Live stream & chat](#live-stream--chat-a-section-of-download)).

## Live download progress (why you used to see nothing)

`musictool` previously captured yt-dlp's output and only printed it when the
process *finished* -- on a long playlist that's minutes of silence. Now the
download is streamed while it runs (yt-dlp redraws its percentage with carriage
returns, so the output is split on `\r` as well as `\n`), and:

- the **log** gets readable progress lines as they happen: `Downloading item 3 of
  186`, the current `Destination: ...`, and errors;
- the **GUI** (Windows/macOS/Linux/Termux with tkinter) shows a dedicated panel
  above the log with two progress bars -- overall playlist position and the current
  file's percentage -- plus the current filename, download speed, the per-file
  `ETA`, and an **estimated time left for the whole run** (computed from elapsed
  time and fraction done).

The bars update from parsed yt-dlp events, so they work for single videos and for
whole playlists. In a plain terminal you get the streamed lines too (the bar is a
GUI-only addition, never doubled output).

### What the panel shows

Two bars, each with its own numbers beside it:

```
████████░░░░░░░░   playlist  64.0%   item 286 of 447   ·   18m 04s elapsed, est. 10m 09s left
██████░░░░░░░░░░   Elevation Worship - Praise.m4a    42.5%   1.20MiB/s   ETA 00:09
```

- **percentages on both bars**, not just a moving rectangle;
- **elapsed time in minutes** (`18m 04s`, `1h 02m`) and the estimated time left for
  the whole run, computed from how long it has taken to get this far -- never a
  bare number of seconds like `1084s`;
- the current file, its own percentage, the download speed and yt-dlp's ETA.

**The split is draggable.** The bars and their text sit in a horizontal paned
window, so you can drag the divider to make the bars narrower and the text wider
(or the other way round). The width you leave it at is saved and restored next
time.

### Two traps that left the bar completely dead

Streaming alone was not enough -- both of these had to be fixed for anything to move:

1. **`--print` implies `--quiet`.** `get` asks yt-dlp for
   `--print after_move:%(id)s<TAB>%(filepath)s`, which is how it learns which file just
   finished and what its videoId is (that is what feeds the per-file lyrics pass). But
   `--print` also switches yt-dlp into quiet mode, and in quiet mode it prints *nothing
   else*: no `Downloading item 3 of 186`, no percentage, no speed, no ETA. The only
   output was the `id<TAB>filename` line per file -- exactly the "just the names of the
   files" symptom. `get` now passes **`--no-quiet`** next to `--print`, which cancels the
   implied quiet; the template still prints, and the progress lines come back.
2. **Tkinter is not thread-safe.** The download runs on a worker thread and the bars were
   being updated by calling `root.after(...)` *from that thread*, which on Windows can
   silently do nothing at all. Progress events now go into a `queue.Queue` and are applied
   by the same main-thread pump that already drains the log, coalesced to the newest
   redraw so hundreds of `\r` updates a second cannot flood the UI. Completion is
   signalled through the same queue (`FINISH`), so no widget is ever touched off-thread.

The parser is written against the strings yt-dlp actually emits (verified against
2026.08.19; live percentage events confirmed again with 2026.10.26), including the awkward ones: `at Unknown B/s ETA Unknown` before the first
sample (the `Unknown` tokens are ignored rather than shown as a speed), the fragment
estimate `[download] 4.2% of ~ 12.34MiB at ...`, and the final
`[download] 100% of 7.63MiB in 00:00:03 at 2.00MiB/s` (a completion, not a percentage).

### If you saw `ERROR: [generic] '%(artist,uploader)s:%(meta_artist)s' is not a valid URL`

`--replace-in-metadata` takes **three** values -- `FIELDS`, `REGEX` and `REPLACE`. Only
two were being passed, so yt-dlp swallowed the *next* option (`--parse-metadata`) as the
replacement text and the template after it was left over as a "URL". That broke the
` - Topic` artist cleanup as well. The replacement is now the empty string it always
should have been, which is what deletes the suffix.

## Lyrics are included as it downloads

`get` does not wait for the batch to finish before doing lyrics. yt-dlp is run with
`--print after_move:%(id)s\t%(filepath)s`, and the moment a finished file is reported,
`get`:

1. records that file's **videoId** in the download archive index, so the `ytmusic`
   provider can look the song up by its exact identity instead of by title search;
2. reads the file's tags, fetches its lyrics (synced preferred, per the usual
   provider order), and **embeds them into the file right away**;
3. moves on to the next download.

So on the 186-track playlist, track 1 already has its synced lyrics in the file while
track 2 is still downloading -- you can play and see them immediately, and a crash or
WiFi drop half-way through leaves the finished files complete rather than lyrics-less.
The `==> lyrics for <file>` lines appear interleaved with the download progress, and a
single combined summary + `lyrics-report.txt` is written at the end for the whole batch.

All lyrics HTTP work shares **one session**, so the local response cache and rate
limits apply across the batch instead of restarting per file. `--no-lyrics` still skips
it entirely; `--lyrics-only` (or a run where nothing downloaded) falls back to scanning
the output folder as before.

## Finding wrongly-embedded lyrics

If a previous tool grabbed lyrics for the wrong file, several unrelated songs end
up carrying the same lyrics. `duplicates` finds them by **content**, not filename:

```bash
python musictool.py duplicates ~/Music/Gospel            # report
python musictool.py duplicates ~/Music/Gospel --json     # for scripting
python musictool.py duplicates ~/Music/Gospel --fix      # re-fetch each one
```

Each file's lyrics are reduced to a fingerprint of their *words* — timestamps and
LRC header tags (`[ti:]`, `[ar:]`, `[by:]`, `[offset:]`) are stripped first, so the
same lyrics pasted in with slightly different timings still group together. Text
shorter than `--min-chars` (default 40) is ignored, so near-empty tags don't all
collide into one group.

Groups are labelled:

- **SUSPICIOUS** — members have *different* titles. This is the bug.
- **same title** — usually a genuine duplicate (two uploads of one song), left alone
  unless you pass `--fix`.

`--fix` re-fetches each file using **its own** tags (artist, title, album, duration,
and its videoId if it was downloaded with `get`) — never folder order — then
re-reports. Genuinely duplicated songs will still match afterwards, which is correct.

## Lyrics sources

Tried in this order by default (`--providers` to change it):

### 1. `ytmusic` — YouTube Music's own lyrics, by videoId
The most exact source **for a YouTube-sourced library**: it is the lyrics page
YouTube Music shows for that very video (licensed from Musixmatch), so it
cannot be the wrong song.

It needs the **videoId**, which `get` records in `.musictool-index.json` beside
your downloads. Two unauthenticated calls: `next` as `WEB_REMIX` (find the
lyrics tab) then `browse` as `ANDROID_MUSIC` (read the lines). `WEB_REMIX`
returns one block of plain text; `ANDROID_MUSIC` returns one row per line,
each with its start time — when YouTube sends the times.

> **Live, 2026-11-18: YouTube mostly sends no times.** Every lookup tested that
> day (several songs, `ANDROID_MUSIC` 7.03 to 8.47 and `IOS_MUSIC`, with and
> without a visitor id) returned the lines without their `cueRange`; one early
> request did include them. musictool now keeps such a result as an
> **unsynced exact match**: a synced hit from `lrclib`/`netease` still wins,
> and with `--allow-unsynced` it beats any fuzzy plain-text match. When YouTube
> does send times, it is a synced hit and nothing else is tried.

Results are cached compactly per videoId (the raw lyrics page is ~650 KB).

Deliberately does **not** search YouTube by artist/title. YouTube's search never
reports "no match": give it a nonsense title and it returns confident, fully-timed
lyrics for a different song. Looking up by the exact videoId you downloaded avoids
that failure mode entirely.

### 2. `lrclib` — free, open, keyless
`https://lrclib.net`. No API key, no registration, documented API. Synced and
plain. Its only requirements are a `User-Agent` identifying the client, honouring
`429`/`Retry-After`, and 200–500 ms between requests. musictool sends
`musictool/<version> (https://github.com/JohnOlowe/MusicTool)`, waits 0.35 s
between requests, and on a 429 waits what `Retry-After` says (seconds or an HTTP
date; 5 s if it is missing or unreadable, at most 120 s) plus up to 30 % random
jitter, then retries. In the live test it supplied the synced lyrics for both
real songs (Ed Sheeran's *Shape of You*, Sinach's *Way Maker*).

### 3. `netease` — NetEase Cloud Music
Public, keyless, very large synced corpus (strongest for CJK, but covers plenty of
Western tracks too). Uses `/api/search/get` then `/api/song/lyric?lv=-1`,
with a `Referer` header. The credit lines NetEase puts at the top of most
lyrics (`[00:00.000] 作词 : …`, `作曲`, `制作人`, …) are removed before
anything is embedded.

(Until 2026-11-18 it used `/api/search/get/web`, which now answers with an
encrypted string instead of results — the provider crashed with
`'str' object has no attribute 'get'` on every search. Found by the first
live run; an unexpected answer is now "no results", not a crash.)

### Not implemented, and why

| source | why not |
|---|---|
| MusixMatch direct | needs an API key, and the free tier returns only ~30% of each lyric |
| Genius, AZLyrics, Megalobiz | plain text only, and aggressive bot detection — this is what kept blocking `ytmdl` and `syncedlyrics` |

If LRCLIB, NetEase and YouTube Music all miss a track, there is a good chance
nobody has synced lyrics for it. `lyrics-report.txt` lists every miss with the
reason.

### Matching safety

Fuzzy searches return other songs with the same title, so every candidate from
`lrclib`/`netease` must clear a title + artist + duration similarity threshold
before it is accepted. A `TRIBL - Well, Well, Well` file will not be tagged with
Maverick City's lyrics. `ytmusic` is exempt because it is an exact-videoId lookup.

Filenames are normalised first: `&amp;` → `&`, `⧸` → `/`, `｜` → `|`, fullwidth
`？`/`：` → `?`/`:`, `(feat. …)`/`(Live)`/`(Ao Vivo)`/`(Radio Version)`/
`(Extended Version)`/`(Spontaneous)`/`[ … ]` stripped, medleys split into parts
and searched separately, and yt-dlp credit lists (`"A, B, C, D"`) retried with the
lead credit.

## What it writes

| file | contents |
|---|---|
| `Lyrics/<song>.lrc` | sidecar for players that read external lyrics |
| `©lyr` / `LYRICS` tag | embedded, via mutagen — no re-mux, so cover art and other tags survive |
| `Lyrics/_backup/<song>.old.lrc` | previous lyrics, only when something is overwritten |
| `lyrics-report.txt` | every fix, every miss with its reason |
| `.musictool-index.json` | file → videoId/url, so `ytmusic` works later |
| `~/.config/musictool/http-cache.json` | API responses; re-runs make no calls (about 120 KB per song — trimmed to the fields musictool reads; it was ~4× that) |

`scan` classifies each file as `synced` (fine), `partial` (a few timestamps),
`plain` (no timestamps) or `empty`.

The sidecar and the embedded tag are tracked **separately**. If your `.lrc` is good
but the tag inside the file is stale or garbage, `lyrics` repairs the tag from the
sidecar without a network call, instead of skipping the file as "already ok".

`lyrics_dir` is a folder *name* (`Lyrics`), resolved against the folder being
scanned or downloaded to — never the current working directory. A lyrics folder
unrelated to the scan is ignored, so stale sidecars elsewhere cannot silently
override the tags in your files.

## GUI

```bash
python musictool.py gui
```

Four tabs — **Download**, **Library**, **Report**, **Settings** — over the
same code paths as the CLI.

**Download** is where nearly everything happens, in three steps from the top:

1. **Paste links.** Paste anything — a link, several, or a whole chat message
   with links in it. Each link is recognised the moment it lands, from the
   link alone: a YouTube Music song or album, a video, *a video in a
   playlist — just this one*, a Short, a live stream, a playlist, a channel,
   a SoundCloud or Bandcamp track or album, a video on another site. Several
   links are summed up (`3 links: 2 songs, 1 YouTube video`). A Spotify link
   is explained instead of attempted (its streams are encrypted), and lines
   without a link are counted and skipped. **Identify online** asks yt-dlp
   about the first video (one small request) and finds what the link hides:
   live right now, scheduled, a finished stream, a music video, a vertical
   Short, a long talk. Settings → Smart can do that look-up for every paste.
2. **How to download it.** **✦ Smart (auto)** plus nine modes, one per kind
   of thing:

   | mode | what you get |
   | --- | --- |
   | Song | m4a, tags + cover, synced lyrics embedded, this song only |
   | Album / music playlist | m4a with lyrics, numbered `001 - …` in a folder named after the album |
   | Music video | mp4 up to 1080p, tags + cover, no lyrics |
   | Video | mkv up to 1080p with chapters and subtitles |
   | Short | mp4 at the best quality there is |
   | Podcast / talk (audio) | m4a with chapters, no lyrics |
   | Video playlist | mkv up to 1080p, numbered in a folder of its own |
   | Whole channel | mkv up to 1080p, a folder per channel, dated names |
   | Live stream | recorded from its first minute, watchable while it records |

   **Smart** picks the mode from what you pasted, and **learns**: every
   download you start is noted (which mode for which kind of link, and how
   you set that mode up — quality, size cap, container, audio language,
   names, folder). A choice becomes the default once it is the majority of
   your last ten runs for that kind *and* you made it at least twice; the
   line under the modes then says so (`you usually choose Music video for a
   YouTube Music song · learned from you: up to 720p`). A mode you pick by
   hand is obeyed; only one-or-many still follows the link, so *Song* with
   an album link in the box becomes *Album*. Change an option by hand and the
   tab stops adjusting things and says *changed by hand*. Settings → Smart
   shows what was learned, turns learning off, or forgets it all.
   **Save to** is right under the modes, with **Browse…** and **Open**.

Below that: the **fine-tune** strip (the toggles you reach for most), then
foldable sections — **Quality & size**, **Audio tracks**, **Live stream &
chat**, **More options** (every other `get` flag) and **Fix language**. Open
the one you need; the window remembers which were open. A live link opens
the Live section by itself. The bottom bar stays put while the tab scrolls:
**Check access (no cookies)**, **List qualities + sizes** (+ **audio
formats**) and the big button, which says what it will do — *Download song +
lyrics*, *Download album + lyrics*, *Download Short*, *Record live stream*,
*Download channel* … — and *Add to queue* while something is running.

**Library** holds the jobs on music you already have — **Fix lyrics**,
**Scan & duplicates** and **Lyrics files (.lrc)** — one at a time, over one
folder field and one file list. **Report** reads `lyrics-report.txt`.
**Settings** holds Smart, the window, the lyrics sources, saved defaults and
`doctor`.

**The window fits your display.** It opens at 1080 x 720 (90% of the old
800-pixel height), centred; on a smaller display it shrinks to fit with the
taskbar kept clear, and it can never be made larger than the screen
(Settings → Window shows the numbers and has **Reset size and layout**). The
log starts low, a quarter of the window, already at the first frame, and its
buttons sit beside the progress bars so it gets the room. Rows of buttons
and long hints wrap on a narrow window instead of pushing the tab sideways.

### Quality & size (a section of Download)

It works on the first link in the box (or one you type into it). Press
**Load qualities**, and it shows the collapsed
list — not the 127 rows yt-dlp reports. From there:

- **Filters** on one line: max height, container, codec, a size cap, and the
  sort order, plus *Merge variant splits*, *Hide same-quality doubles* and
  *Show audio-only tracks*. Every change re-renders the table immediately.
- **Auto** — pick a preset (*Best quality*, *Most compatible (H.264/mp4)*,
  *Best that fits my size limit*, *Smallest file*, *Audio only*) and it fills in
  the `-f` and tells you why it chose it, including the total size with audio.
- **Manual** — click a row and it becomes the choice instead; the row's audio
  partner and total size are shown underneath.
- **Audio** — a video-only row is paired with the best audio track in your
  audio language (`--audio-lang`, in More options), or with the track you
  chose in the **Audio tracks** section. The line next to **Choose audio
  track…** says which one.
- The `-f` box at the bottom holds the result. **Copy command** puts the whole
  `musictool get ...` line on your clipboard, **Download this quality** runs it
  with the settings from the Download tab. Double-clicking a row downloads it.

If YouTube refuses the request the section says why and repeats the fix from
`check` — it does not leave you staring at an empty table. The same goes for
an answer with no audio or video in it (the JS challenge failed): both this
section and Audio tracks say so and name the fix. The lookup uses the
cookies and the `--js-runtimes` / `--remote-components` set in More options.

### Audio tracks (a section of Download)

A dubbed video offers one audio track per spoken language, often in two codecs
(Opus/webm and AAC/m4a) and at several bitrates. This tab lists them so you can
see what is really there and take a specific one. It shares the URL with
Quality & size, so one **Load** fills both.

- **Language** — starts at your audio language. The list shows every language
  the video has (`es (original), en, fr`), plus **all**. Picking a language
  also makes it the Download tab's `--audio-lang`, so every download in the
  window follows it; **all** only widens the list. A language the video does
  not have gives an empty table and a line saying which ones it does have.
  Tracks that carry no language tag (a normal single-language upload) are all
  shown, with a note.
- The table — id, language (`*` = original), codec, container, bitrate, size
  and YouTube's own name for the track (`English, medium`).
- **Download this audio** — downloads exactly that track as music (`get URL
  --format-selector <id>`, converted to the audio format set on the Download
  tab), even if the Download tab is in video mode. Double-click does the same.
  **Copy command** puts that command on the clipboard.
- **Use with a video quality** — keeps the track for Quality & size, which
  then pairs every video row with it (`-f 248+251-1`). **Automatic audio**
  goes back to the best track in your language. The choice is per video:
  loading a different URL forgets it.

On the command line, `musictool formats URL --show-audio` lists the audio
tracks in your `--audio-lang` (`--audio-lang any` for all of them) and names
the spoken languages; `musictool get URL -f <id>` takes one. In the window,
that is the **+ audio formats** tick beside **List qualities + sizes**.

### Live stream & chat (a section of Download)

The stream's link (the first link in the box, or one typed here), the folder
— the tab's **Save to** — an optional **Copy to watch goes to** folder
(another drive when this one is full) and an **Audio only** tick, then:

- **Is it live? How big?** — `live --status`: live since when, how big so
  far, how fast it grows, how much room the folder has, and every stream with
  its id. It fills the **Video stream** / **Audio stream** pickers; left on
  automatic the best is taken, a picked stream is taken exactly (`--exact`).
- **Record from the start** — `get` with the picture kept (or audio only),
  no lyrics, this video only, into the folder; the live handling above does
  the rest.
- **What's in the folder?** — what a stopped recording holds and what is
  missing, including one made by plain yt-dlp.
- **Watch what's there so far** — after a stop it joins the loose pieces and
  makes `TITLE (so far).mp4`. During a recording it stays clickable and only
  reads the pieces into a copy, alongside the recording rather than queued
  behind it.
- **Download the rest** — `live --resume`.
- **Download this part** — `get --range` with the time range typed in **Only
  this part** (e.g. `1:00:00-` for everything after the first hour). It uses
  the picked streams if there are any.
- **Open the copy** — plays the newest copy with the system's player.
- **Live chat replay as subtitles** — Format, Messages at once, Longest on
  screen, Side, Height, User names, SRT spaces, Colours, Space between
  messages, *Dark box behind the text*, and *Also add it to the video as a
  subtitle track*. **Get the chat** runs `chat` for the link into the
  folder. **Remake a chat file…** asks for a chat file musictool made (or a
  video with the chat track) and makes it again with these settings, for
  example as another format. A part downloaded with *Download this part* gets the chat of that
  part from its name. With no video of it in the folder, the *Only this
  part* field is used.

With a live link in the box the big button is **Record live stream**, which is
**Record from the start**. Cookies, proxy and the JavaScript settings come
from More options, and the section's fields come back next time.


**Every CLI option is in the GUI, and it stays that way automatically.** The
widgets are generated from the argparse parser itself, and running a job builds the
exact argv the CLI would receive and calls `cli.main()` with output redirected into
the log. There is no second copy of the option handling to drift out of sync, so
adding a flag to `cli.py` adds a widget with no GUI work. The test suite asserts
this for every flag of every subcommand.

Every run echoes the command it used:

```
$ musictool duplicates "/home/you/Music" --min-chars 40 --fix --allow-unsynced
```

so you can copy it into a shell or a cron job.

- **Browse buttons** — every field that takes a path (output folder, lyrics
  folder, cookies.txt, download archive) has a **Browse...** button beside it, so
  you never have to type or paste a Windows path, and a short grey line under the
  field says what it is for.
- **The output folder is remembered.** Downloading somewhere saves that folder,
  and it is the default the next time you open the window (or run the CLI). The
  URLs and folders you use are kept as history too.
- **Stop** and **Force stop** — bottom right, never greyed out; see "Stopping a
  run".
- **Check access (no cookies)** and **List qualities + sizes** — next to the
  download button: the first tells you whether YouTube is blocking you before you
  change anything, the second prints the quality/size table into the log so you
  can copy an id into the `-f` field. Tick **+ audio formats** beside it to
  list the audio-only tracks too, in the spoken language set on the tab
  (`formats --show-audio`); unticked, a video-mode list shows the video
  qualities only. The tick is remembered. "This video only, not its playlist" is a
  quick option at the top of the tab, since most of the time you want one video.
- **Quick options** — the toggles you reach for constantly (`--allow-unsynced`,
  "redo files that already have lyrics", subfolders, dry run, .lrc-only) sit in a
  strip that is the first block of each tab, laid out in a wrapping grid so it
  stays short on small screens. They are bound to the *same* variables as the
  generated widgets below, so ticking one ticks the other; nothing to keep in sync.
- **Pick the files** — the Library's **Fix lyrics** and **Scan & duplicates** jobs list every
  audio file with its current state, so you can run the whole folder, a subset, or
  everything except a few. Files that share lyrics with another file are flagged in
  the list. See below.
- **Remembered paths and URLs** — the last 12 folders and URLs are kept in the
  config and offered in the dropdowns, so you are not re-typing
  `/storage/67FE-19FE/Music/...` each time.
- **Scan & duplicates** — scan, scan-as-JSON, find duplicate groups, render the
  groups as a tree, and **FIX duplicate lyrics**, all from one Library job.
- **Report** — loads `lyrics-report.txt` and shows a summary line
  (`2 fixed (40%) · 1 already ok · 1 unresolved · 0 unreadable · of 5 scanned`),
  a collapsible tree of fixed / unresolved / unreadable with the reason for each
  miss, and a per-provider tally. Export to JSON or CSV. It refreshes itself after
  a run, and duplicate scans render straight into the same tree.
- **Settings** — Smart (learning on/off, what was learned, forget it), the
  window (display size, reset), probe the lyrics sources, read/set/reset
  saved defaults, and run
  `doctor`. "Save these options as defaults" writes the current widgets to the
  config so the next launch starts where you left off.
- **Everything scrolls** — each tab's body sits in one scrolling area, so a short
  or narrow window cannot push a control out of reach. The run buttons (and the
  Library's folder field) are pinned outside it and stay
  on screen while everything else, including the quick options and the file list,
  scrolls. The mouse wheel scrolls whichever tab the pointer is over, and
  stays out of the way of the file list, the report tree and the log, which scroll
  themselves.
- **Resizable log** — the tabs and the log are two halves of one draggable split.
  Drag the bar between them, or use **log taller** / **log shorter** / **reset
  split** beside the progress bars. It starts at a quarter of the window and
  follows the window's size until you move it yourself. Neither half can be
  pushed off screen, and **Clear** empties the pane.

### Running a subset of your files

Press **Load file list** in **Library → Fix lyrics** and every audio file in the
folder appears with a tick box, its state (`synced` / `plain` / `empty` /
`unreadable`), its tags and its length. Everything starts ticked.

| you want to | do this |
| --- | --- |
| redo every file, even the ones that look fine | **Redo ALL files (force)** |
| run only the broken ones | **Only needing lyrics** |
| run everything except a few | tick **All**, then click the ones to skip (or highlight them and press **Skip highlighted**) |
| run just two or three files | **None**, then click the ones you want (or highlight them and press **Only highlighted**) |
| re-do only the wrong-lyrics damage | **Only shared lyrics** |

Click a row to toggle its tick. Highlight several rows (shift/ctrl-click) and use
**Skip highlighted** / **Only highlighted** to move them in bulk. The line under
the list always says how many files will be processed.

When every file is ticked the tool is given the folder; when you narrow it down,
only the ticked files are passed — so an unticked file is not touched, and the
report is still written next to them.

Needs tkinter (`pkg install python-tkinter` on Termux, `python3-tk` on Debian).
If it is missing the CLI prints the install command for your platform and keeps
working.

## Config

```bash
python musictool.py config                    # show everything
python musictool.py config output_dir ~/Music
python musictool.py config provider_order lrclib,netease,ytmusic
python musictool.py config allow_unsynced true
python musictool.py config --reset
```

Stored at `~/.config/musictool/config.json` (`%APPDATA%\musictool\config.json` on
Windows). Environment overrides: `MUSICTOOL_COOKIES`, `MUSICTOOL_OUT`,
`MUSICTOOL_PROVIDERS`, `MUSICTOOL_UA` (the User-Agent; default
`musictool/<version> (https://github.com/JohnOlowe/MusicTool)`), plus
`MUSICTOOL_LRCLIB_BASE` / `MUSICTOOL_NETEASE_BASE` / `MUSICTOOL_YTM_BASE` for
mirrors and testing. CLI flags always win.

## Platform notes

- **Termux:** `yt-dlp` may only be importable, not on `PATH` — the tool falls back
  to `python -m yt_dlp`. `doctor` tells you which it found. For the JS runtime,
  `pkg update && pkg upgrade` before `pkg install deno` (an outdated system
  gives `CANNOT LINK EXECUTABLE "deno"`), or `pkg install nodejs`, which
  musictool then enables by itself.
- **Windows:** paths with spaces and `&` are quoted when the command is echoed.
- **Both:** `--ascii` avoids filenames some car stereos and older players mangle.

## Tests

```bash
python3 tests/test_musictool.py
```

No network needed: the three lyrics APIs are stubbed with the response shapes they
were seen to return live (including the ones that broke the providers) and `yt-dlp` is replaced by a fake on `PATH`. Needs `mutagen`, and either
`ffmpeg` on `PATH` or `pip install imageio-ffmpeg`.

`tests/tkstub.py` is a small headless stand-in for tkinter. It renders nothing, but
it lets the suite *execute* `gui.run()` on a machine with no display and no tkinter:
build the real widget tree, invoke real buttons, tick real boxes, and capture the
argv each button sends to the CLI. Pixels are out of scope; behaviour is not.

## Verified / not verified

**Verified by running the code — 1217 assertions, 0 failures**, with or without
tkinter installed (`tests/test_musictool.py`): text normalisation against real messy
filenames, LRC parse/shift/classify, tag read/write/backup on real `.m4a` files,
all three providers against stub APIs that replay the response shapes seen live
(including `WEB_REMIX` returning no timings, `ANDROID_MUSIC` with and without
cue times, and NetEase's encrypted `/web` answer), the candidate-rejection
guard, synced-over-unsynced precedence, duplicate-lyrics
detection and repair, GUI/CLI option parity and argv round-tripping for every flag,
path history, report parsing, `build_args` for every download option, the videoId index,
and the CLI end-to-end as a subprocess with a fake `yt-dlp` on `PATH` — `get`,
`scan`, `scan --json`, `lyrics`, `--dry-run`, `duplicates`, `duplicates --fix`,
`--providers`, `config`, `doctor`, `providers --test`, and `--help` for every
subcommand.

**Verified live, 2026-11-18** (yt-dlp 2026.10.26, ffmpeg 9.0, Python 3.13, Linux):

- **The three lyrics sources, for real.** `providers --test`: lrclib and NetEase
  reachable with synced results. `lyrics` on three real tagged `.m4a` files:
  *Ed Sheeran – Shape of You* (with its videoId, so `ytmusic` was asked first)
  and *Sinach – Way Maker* got synced lyrics from lrclib (93 and 45 timed
  lines, embedded and written as `.lrc`); an invented title was refused by the
  matching guard rather than given someone else's lyrics; a second run with
  `--force` made **0** network calls.
- **What that live run found and fixed:** NetEase's search endpoint had
  changed (every search crashed); YouTube Music now mostly sends lyrics
  without times (they were all being thrown away); and the HTTP cache keyed
  every POST to a URL as the same request, so on a playlist **every song
  after the first would have been given the first song's YouTube Music
  lyrics**, trusted and unchecked. All three are fixed and pinned by tests.
- **`check` against real YouTube** — from this cloud machine it answers
  `BLOCKED (bot)` with the Firefox/cookies.txt advice, which is the right
  verdict for a server address (see [Am I blocked?](#am-i-blocked----check)).
- **A real `yt-dlp` download** through `get` and through `download()`, from a
  local HTTP server: the file lands, the index is written, `on_file` fires with
  the id, and the progress bar receives live percentage events (12 for a
  2 MiB file at 1 MiB/s) — the `--print` + `--no-quiet` fix still holds.
  yt-dlp's own help still says `--print` implies `--quiet`, that only deno
  is enabled by default, and that `--remote-components` is unneeded when
  `yt-dlp-ejs` is installed — which plain `pip install yt-dlp` does not do.
- **Live streams, with the real yt-dlp downloader** (2026-11-19): a local
  server fed yt-dlp a stream whose pieces arrive over time, the way its
  YouTube extractor does. With `-N 4` all 78 pieces piled up loose and the
  file was still 0 bytes halfway; with one worker per stream there was never
  a loose piece and the file was growing (2 MiB halfway), in the same time.
  A recording killed outright (`kill -9`) after 17 of 40 s: the copy to watch
  played 17.4 s, and carrying on under the name `live` reuses fetched exactly
  the 46 missing pieces of 80; the result was 40.02 s, 1200 frames, no decode
  errors. `musictool live --join` on a folder that `-N 4` left behind joined
  all 18 pieces and made an 18 s copy. The argv `get` builds for a live
  stream was parsed by yt-dlp's own option parser.
- **Real YouTube, with the user's cookies** (2026-11-19, R0GdEkPZSvM, a
  finished 3h39m stream): `get --range 1:00:00-1:00:30 --keep-video --quality
  360` fetched exactly 30.02 s (vp9 + AAC from YouTube's HLS streams) as
  `TGG - … [R0GdEkPZSvM] (1h00m00s-1h00m30s).mp4`. `chat URL --embed` then
  downloaded the replay (6295 messages, 8.6 MB). It wrote the ASS from
  1:00:00 and made the `.mkv` with the chat track, and its first cue starts
  on the same millisecond as the sound. A frame rendered with the track shows
  the chat as a column on the right of the real picture. Before the fix, the
  `.mkv` had shown the chat 3.8 s early, because the picture starts at the
  keyframe before the cut.
- **Real YouTube, unfinished parts** (2026-11-22, c88810jEKMw, 1080p60
  `299+140`, `--exact`). A part started at 2:25:00 begins on the keyframe at
  2:24:55, and its first video packet is that keyframe. A 15-minute part was
  killed with `kill -9` at 446 MB; the rerun kept 13:50 of it, playable, and
  fetched only 2:38:43 on, into a new file. On a 250 MB disk, the part
  stopped at 8:10 with the disk-full message and a playable file, and a rerun
  on the still-full disk left nothing behind. With room for the rest but not
  for a second copy, the rest was kept untagged, and the next run said
  `already here`. With a 60 MB file-size limit standing in for FAT32's 4 GB,
  a 5-minute part came down as three playable files that overlap by a few
  seconds.
- **Real YouTube, `--just-download` and the chat** (2026-11-23, eRUHPTKNgkI,
  `--range 1:00:00-1:00:30 --subs --exact -f 299+140`): the chat came down
  beside the part (1.7 MB, all 11:21:25 of it), no step ran after the
  download, and the part stayed a fragmented MP4 with its `mfra` index (it
  seeks). A rerun fetched neither again. `musictool chat` on the chat file
  found the part and wrote the chat from 1:00:00. Before this, the same
  command without `--just-download` got no chat, because `--exact` had turned
  `--subs` off.
- **Real chat, coloured and remade** (2026-11-23, R0GdEkPZSvM's 8 MB replay,
  6,175 messages over 3:39:12): drawn onto a picture with ffmpeg's libass
  filter, the new ASS shows coloured names and text, outlined with no box,
  with a small gap between messages, and it reads over both a white and a
  black background. Before, it was grey names on dark boxes. The ASS and the
  text log made from it read back all 6,175 messages; the SRT read back
  6,131. The other 44 were never on screen, because more than 5 arrived in
  the same instant. Each step took about half a second.
- **The GUI, drawn.** Under a virtual display (Xvfb) the real Tk window was
  opened and screenshotted, including the Audio tracks, Quality and Live
  streams tabs; that
  caught two layout faults no stub could (log lines running together, a
  clipped label), both fixed.

Earlier, against yt-dlp 2026.08.19: the stop path (a real download sent
`SIGINT` three seconds in came back with `stopping after the current file...`
(the wording at the time),
then `==> stopped by user`, exit 0, no orphan process), and `--cookies`
accepted by a real `yt-dlp`. Browser-cookie detection is unit-tested against a
fake Windows profile tree (two browsers, different cookie timestamps -> picks
the newer one).

**Not verified:**
- **Real downloads from YouTube without cookies, and the Quality / Audio
  tracks tabs on real YouTube data.** YouTube refuses this cloud machine's
  address unless a login is sent (see above). With the user's cookies, one
  short part and one chat replay were fetched (above), and nothing else. The format
  logic is tested against the shapes yt-dlp reports for YouTube, including a
  dubbed video, but a real dubbed video has not been loaded into the tab.
- **A real YouTube live stream** -- for the same reason. yt-dlp's live
  handling was read in its source and driven with a live-style stream (above);
  the Windows "Access is denied" locks were simulated, not produced by
  Defender.
- **Timed lyrics from `ytmusic`.** The code reads them when YouTube sends
  them; on 2026-11-18 it almost never did. `python musictool.py providers
  --test` asks for a sample video's lyrics and tells you which it is today
  (`timed lyrics`, or `sent the lines WITHOUT times`) — run it before a big
  batch.
- **Windows and Termux specifics** — the App-Bound-Encryption cookie failure,
  `winget`/`pkg` installs, paths with `&`, how the theme looks — come from the
  code and from yt-dlp's issue tracker, not from a run on those systems.
