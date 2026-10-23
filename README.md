# musictool

Download music from YouTube/YouTube Music and fetch **synced** lyrics for it.
Replaces a `get_music.sh` shell script with something that handles playlists,
multiple lyrics sources, and both a CLI and a GUI.

- **Cross-platform:** Windows, macOS, Linux, Termux/Android
- **Python 3.8+, stdlib only** plus two dependencies: `yt-dlp` and `mutagen`
- Works offline for scanning; the only network calls are to yt-dlp and lyrics APIs

```
./
├── musictool.py          entry point
└── musictool/
    ├── cli.py            command line
    ├── gui.py            Tkinter interface
    ├── download.py       yt-dlp wrapper + videoId index
    ├── lyrics.py         scan / fetch / embed pipeline
    ├── providers.py      ytmusic, lrclib, netease
    ├── tags.py           mutagen tag + .lrc handling
    ├── config.py         saved defaults
    └── util.py           normalisation, LRC, HTTP
```

## Install

```bash
pip install yt-dlp mutagen

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

## Quick start

```bash
# one video
python musictool.py get "https://music.youtube.com/watch?v=XXXX"

# a whole playlist
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

## The window: two questions instead of nine checkboxes

`musictool gui` opens the **Download** tab as a single choice, because that is
all the downloader actually needs to know before everything else falls out of
it:

| | **One item** | **A whole playlist** |
|---|---|---|
| **Music** (audio + lyrics) | `music_single` | `music_playlist` |
| **Video** (keep the picture) | `video_single` | `video_playlist` |

Pick one and the checkboxes set themselves — no reading, no ritual:

- **Music · one song** — audio extracted to m4a, tags + cover embedded, lyrics
  embedded as each file lands, `--no-playlist` so a `watch?v=…&list=…` link
  cannot turn one song into ninety files.
- **Music · playlist / album** — same, walking the whole playlist in order.
- **Video · one video** — the picture kept in mkv, `1080` by default (or pick a
  quality by its size under *More options*), chapters and subtitles on, lyrics
  off, `--no-playlist`.
- **Video · playlist** — same, files numbered inside a folder named after the
  playlist.

Three details make it behave:

1. **The URL is read as you paste it.** A `/playlist?list=…` link moves the
   window to a playlist run by itself. `watch?v=…&list=…` deliberately stays a
   **single** — the `list=` there is the "up next" queue, not the thing you
   asked for. The moment you tick anything by hand the window stops guessing
   and leaves the checkboxes to you.
2. **The rest of the options are one click away.** Cookies, proxy, speed
   limit, retries, subtitle languages and the filename template sit behind
   *More options*; the six toggles worth seeing stay on the tab. A mode has
   already set all of them to something sensible.
3. **The button says what will happen.** It reads *Download + lyrics* for
   music and *Download video* for video, so the mode you are in is never a
   surprise at the moment you press it.

The same four modes are exactly what the CLI spells out longhand:

```
music_single     get URL --format m4a --audio-quality 4 --no-playlist
music_playlist   get URL --format m4a --audio-quality 4 \
                     --template '%(playlist_title)s/%(playlist_index)03d - %(title)s.%(ext)s'
video_single     get URL --keep-video --quality 1080 --merge-format mkv \
                     --chapters --subs --no-lyrics --no-playlist
video_playlist   get URL --keep-video --quality 1080 --merge-format mkv \
                     --chapters --subs --no-lyrics \
                     --template '%(playlist_title)s/%(playlist_index)03d - %(title)s.%(ext)s'
```

### One scroll, folded options, and nothing to retype

Three things about the window that used to cost clicks:

**The whole tab scrolls as one.** The URL box, the mode, the fine-tune strip
and the full option list all live inside a single scrolling surface, so the
wheel moves the tab wherever the pointer is — you do not have to aim at the
options first, and the part above *More options* moves with the part below it.
Only the run buttons stay pinned. (A text box that genuinely cannot scroll —
the three-line URL field — passes the wheel on; one with thirty URLs in it
keeps it.)

**Options you have not touched are folded away.** *More options* used to dump
all forty-one settings on the screen at once. Now the grid shows the rows that
have something in them — folder, filename template, audio format, language,
speed limit if you set one — plus anything you have changed, and folds the
blank rest behind one button:

```
   [ show all 41 options ]   31 more folded away
   ┌ files, folders and access ────────────────┐
   │ Folder: [...]      Filename: [...]         │
   │ Audio format: m4a  Audio quality: 4        │
   └────────────────────────────────────────────┘
```

Sections are named for a reader (*files, folders and access*, *quality and
format*, *lyrics*), and a setting that is already on the fine-tune strip is not
repeated in the grid.

**Your values come back.** Every field on every tab, the mode you were in, the
URLs you had open and whether you left the options expanded are written to the
config file as you change them and again when you close the window or start a
run — so a restart is not a form to fill in. `800M` is still `800M`.

```
~/.config/musictool/config.json     "gui_opts": { "get": { "max_size": "800M", … } }
```

Choosing a mode no longer wipes what you typed: a blank in a mode's settings
means "no opinion", so your size ceiling, cookies and speed limit survive a
switch from Music to Video. The single exception is a raw format selector,
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
* In the GUI it is the **Fix language in existing files** button on the
  Download tab — same folder field, same language setting.


## Commands

| command | what it does |
|---|---|
| `get URL...` | download, embedding each file's lyrics as it lands |
| `check URL...` | is this reachable, or does YouTube want a login? (no cookies used) |
| `formats URL...` | the qualities on offer (variant splits merged), with sizes, plus the command to take one |
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

`-o/--output-dir` · `-t/--template` · `--format m4a|mp3|opus|flac|ogg|wav` ·
`-q/--audio-quality 0-10` · `--keep-video` · `--no-thumbnail` · `--no-metadata` ·
`--chapters` · `--ascii` (ASCII-only filenames, helps picky players) ·
`--cookies FILE` · `--cookies-from-browser auto|off|edge|chrome|firefox|brave|...` ·
`--proxy` · `--limit-rate 2M` · `--concurrent N` ·
`--archive FILE` (skip already-downloaded videos) · `--items 1-50` ·
`--no-playlist` · `--yes-playlist` · `--retries N` (attempts across a network
drop) · `--net-max-wait SECONDS` (how long to keep retrying, default 240)

### Quality options (video downloads)

`--quality 720` (or `1080`, `best`, `smallest`) · `--max-size 250M` (best quality
that fits under it) · `-f 137+140` (a raw yt-dlp selector, overrides both) ·
`--merge-format mkv|mp4|webm|mov` · `--subs` · `--sub-langs en,es` ·
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

When it *is* blocked it says which kind of block and what would fix it --
`bot` (needs a login), `age`, `private`, `members`, `unavailable`, or `network`
(a dead connection, which is not a block at all) -- and exits non-zero so you can
script around it. `--with-cookies` runs the same check *with* your login, for
comparing the two.

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
      musictool get "https://youtube.com/watch?v=XXXX" --keep-video -f 625/625-2+251/251-17
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
`--show-audio` puts the audio tracks back in the table.

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
      musictool get "https://youtube.com/watch?v=XXXX" --keep-video -f 137/137-0+251/251-17
```

A selector like `137/137-0` means "the plain itag, or that exact copy of it" --
whichever yt-dlp has, it downloads the whole stream, never a slice of one.

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
no `--cookies` at all. Add `--remote-components ejs:github` only if yt-dlp says it
needs the JS component; recent versions usually do not.

## Cookies: "Sign in to confirm you're not a bot"

YouTube refuses playlist downloads from an ordinary IP address unless the request
carries a logged-in session. That is the wall you hit part-way through a big
playlist, and it is not something the tool can talk its way around -- it needs your
login. Two ways to give it one:

**1. Let it read your browser (the default, and the easy one on Windows).**

```bash
python musictool.py get "https://music.youtube.com/playlist?list=..." -o "~/Music/Gospel Songs"
```

`--cookies-from-browser` defaults to **`auto`**: musictool looks at the browsers
installed on your machine, finds the ones with a cookie database, and uses the one
whose database was touched most recently -- i.e. the browser you actually watch
YouTube in. On Windows that is normally Edge or Chrome, in
`%LOCALAPPDATA%\Microsoft\Edge\User Data` or `...\Google\Chrome\User Data`.
Nothing is exported, nothing is uploaded; yt-dlp decrypts the cookie store locally
the same way it always has. The log tells you what it picked:

```
cookies: reading your chrome login (C:\Users\you\AppData\Local\Google\Chrome\...\Cookies)
```

Pick one yourself with `--cookies-from-browser chrome` (or `edge`, `firefox`,
`brave`, `vivaldi`, `opera`, `chromium`, `whale`, `safari` -- the browsers yt-dlp
can read), or switch it off with `--cookies-from-browser off`. `musictool doctor`
prints the browser it would use.

If no browser is found the download still runs, and says so instead of failing
silently. And if YouTube still refuses, the error you get now says what to do
rather than leaving you with a bare `ERROR:`.

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
| `No supported JavaScript runtime` / `n challenge solving failed` | YouTube's n-challenge needs a JS engine; yt-dlp only enables deno by default | install deno or node, add `--js-runtimes node` and `--remote-components ejs:github` |
| `The page needs to be reloaded` | the bot wall, usually because the JS challenge went unsolved | same as above |
| `[pot:bgutil:http] ... 127.0.0.1:4416` | the **bgutil PO-token plugin** is installed but its server is not running. It is not yt-dlp's own solver. | start the plugin's server, or uninstall the plugin |
| `Requested format is not available` | that itag is not offered to your account/client -- nothing to do with Premium | `musictool formats URL --show-audio --all-formats` to see what is really there |

`musictool doctor` reports the two things that cause the middle of that table:
whether a JS runtime is on your PATH, and whether a PO-token server is answering on
port 4416.

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
- yt-dlp is terminated rather than abandoned, so no orphan process keeps writing;
- a summary and `lyrics-report.txt` are still written for the part that completed,
  so you can see exactly what is done and what is left.

Ctrl+C in a terminal asks once ("stopping after the current file..."); a second
Ctrl+C quits immediately.

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
2026.08.19), including the awkward ones: `at Unknown B/s ETA Unknown` before the first
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

### 1. `ytmusic` — YouTube Music's own timed lyrics
The best source **for a YouTube-sourced library**, because it is the same
MusixMatch-licensed timing YouTube Music shows you, so it lines up with your file.

It needs the **videoId**, which `get` records in `.musictool-index.json` beside
your downloads. Three unauthenticated calls: `next` (find the lyrics tab) then
`browse` (read the cues). Only the `ANDROID_MUSIC` client returns timings —
`WEB_REMIX` returns plain text, so the client version matters.

Deliberately does **not** search YouTube by artist/title. YouTube's search never
reports "no match": give it a nonsense title and it returns confident, fully-timed
lyrics for a different song. Looking up by the exact videoId you downloaded avoids
that failure mode entirely.

### 2. `lrclib` — free, open, keyless
`https://lrclib.net`. No API key, no registration, documented API. Synced and
plain. Its only requirements are a `User-Agent` identifying the client, honouring
`429`/`Retry-After`, and 200–500 ms between requests — all implemented.

### 3. `netease` — NetEase Cloud Music
Public, keyless, very large synced corpus (strongest for CJK, but covers plenty of
Western tracks too). Uses `/api/search/get/web` then `/api/song/lyric?lv=-1`,
with a `Referer` header.

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
| `~/.config/musictool/http-cache.json` | API responses; re-runs make no calls |

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

Seven tabs — **Download**, **Fix lyrics**, **Scan & duplicates**, **Report**,
**Tools**, **Lyrics files**, **Quality** — over the same code paths as the CLI.

### The Quality tab

Paste a video URL, press **Load qualities**, and the tab shows the collapsed
list — not the 127 rows yt-dlp reports. From there:

- **Filters** on one line: max height, container, codec, a size cap, and the
  sort order, plus *Merge variant splits*, *Hide same-quality doubles* and
  *Show audio-only tracks*. Every change re-renders the table immediately.
- **Auto** — pick a preset (*Best quality*, *Most compatible (H.264/mp4)*,
  *Best that fits my size limit*, *Smallest file*, *Audio only*) and it fills in
  the `-f` and tells you why it chose it, including the total size with audio.
- **Manual** — click a row and it becomes the choice instead; the row's audio
  partner and total size are shown underneath.
- The `-f` box at the bottom holds the result. **Copy command** puts the whole
  `musictool get ...` line on your clipboard, **Download this quality** runs it
  with the settings from the Download tab. Double-clicking a row downloads it.

If YouTube refuses the request the tab says why and repeats the fix from
`check` — it does not leave you staring at an empty table.


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
- **Stop** — bottom right, live only while a job runs; see "Stopping a run".
- **Check access (no cookies)** and **List qualities + sizes** — next to the
  download button: the first tells you whether YouTube is blocking you before you
  change anything, the second prints the quality/size table into the log so you
  can copy an id into the `-f` field. "This video only, not its playlist" is a
  quick option at the top of the tab, since most of the time you want one video.
- **Quick options** — the toggles you reach for constantly (`--allow-unsynced`,
  "redo files that already have lyrics", subfolders, dry run, .lrc-only) sit in a
  strip that is the first block of each tab, laid out in a wrapping grid so it
  stays short on small screens. They are bound to the *same* variables as the
  generated widgets below, so ticking one ticks the other; nothing to keep in sync.
- **Pick the files** — the **Fix lyrics** and **Scan & duplicates** tabs list every
  audio file with its current state, so you can run the whole folder, a subset, or
  everything except a few. Files that share lyrics with another file are flagged in
  the list. See below.
- **Remembered paths and URLs** — the last 12 folders and URLs are kept in the
  config and offered in a dropdown on every tab, so you are not re-typing
  `/storage/67FE-19FE/Music/...` each time.
- **Scan & duplicates** — scan, scan-as-JSON, find duplicate groups, render the
  groups as a tree, and **FIX duplicate lyrics**, all from one tab.
- **Report** — loads `lyrics-report.txt` and shows a summary line
  (`2 fixed (40%) · 1 already ok · 1 unresolved · 0 unreadable · of 5 scanned`),
  a collapsible tree of fixed / unresolved / unreadable with the reason for each
  miss, and a per-provider tally. Export to JSON or CSV. It refreshes itself after
  a run, and duplicate scans render straight into the same tree.
- **Tools** — probe the lyrics sources, read/set/reset saved defaults, and run
  `doctor`. "Save these options as defaults" writes the current widgets to the
  config so the next launch starts where you left off.
- **Everything scrolls** — each tab's body sits in one scrolling area, so a short
  or narrow window cannot push a control out of reach (your 1600x720 window is
  exactly why). The path field and the run buttons are pinned outside it and stay
  on screen while everything else, including the quick options and the file list,
  scrolls. The mouse wheel scrolls whichever tab the pointer is over, and
  stays out of the way of the file list, the report tree and the log, which scroll
  themselves.
- **Resizable log** — the tabs and the log are two halves of one draggable split.
  Drag the bar between them, or use **log taller** / **log shorter** / **reset
  split** just above the log. Neither half can be pushed off screen, and
  **Clear** empties the pane.

### Running a subset of your files

Press **Load file list** on the **Fix lyrics** tab and every audio file in the
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
`MUSICTOOL_PROVIDERS`, `MUSICTOOL_GENIUS`, `MUSICTOOL_MUSIXMATCH`, plus
`MUSICTOOL_LRCLIB_BASE` / `MUSICTOOL_NETEASE_BASE` / `MUSICTOOL_YTM_BASE` for
mirrors and testing. CLI flags always win.

## Platform notes

- **Termux:** `yt-dlp` may only be importable, not on `PATH` — the tool falls back
  to `python -m yt_dlp`. `doctor` tells you which it found.
- **Windows:** paths with spaces and `&` are quoted when the command is echoed.
- **Both:** `--ascii` avoids filenames some car stereos and older players mangle.

## Tests

```bash
python3 tests/test_musictool.py
```

No network needed: the three lyrics APIs are stubbed with their documented response
shapes and `yt-dlp` is replaced by a fake on `PATH`. Needs `mutagen`, and either
`ffmpeg` on `PATH` or `pip install imageio-ffmpeg`.

`tests/tkstub.py` is a small headless stand-in for tkinter. It renders nothing, but
it lets the suite *execute* `gui.run()` on a machine with no display and no tkinter:
build the real widget tree, invoke real buttons, tick real boxes, and capture the
argv each button sends to the CLI. Pixels are out of scope; behaviour is not.

## Verified / not verified

**Verified by running the code — 773 assertions, 0 failures**
(`tests/test_musictool.py`): text normalisation against real messy
filenames, LRC parse/shift/classify, tag read/write/backup on real `.m4a` files,
all three providers against stub APIs that replay the documented response shapes
(including `WEB_REMIX` returning no timings and `ANDROID_MUSIC` returning cues),
the candidate-rejection guard, synced-over-unsynced precedence, duplicate-lyrics
detection and repair, GUI/CLI option parity and argv round-tripping for every flag,
path history, report parsing, `build_args` for every download option, the videoId index,
and the CLI end-to-end as a subprocess with a fake `yt-dlp` on `PATH` — `get`,
`scan`, `scan --json`, `lyrics`, `--dry-run`, `duplicates`, `duplicates --fix`,
`--providers`, `config`, `doctor`, `providers --test`, and `--help` for every
subcommand.

**Also verified against the real thing** (not just fakes): a real `yt-dlp`
(2026.08.19) downloading from a local HTTP server with a real `ffmpeg` on `PATH`, run
through `download()` and through the `get` CLI. That is what exposed both progress bugs
-- with the flags we were passing, yt-dlp emitted *only* the `after_move` line, so the
bar had zero events to consume -- and it is how the fix was confirmed: 17 live
percentage events plus `item`/`file`/`done`, the log showing `[download] ...  ETA ...`
lines, `on_file` firing with the real videoId, and the finished file landing in the
output folder with the index written. The fake `yt-dlp` in the test suite models the
`--print`-implies-`--quiet` behaviour, with a negative control proving that without
`--no-quiet` there is genuinely nothing to parse.

The stop path was also driven for real: a genuine `yt-dlp` download was started
from the CLI, sent `SIGINT` three seconds in, and came back with
`stopping after the current file...`, then `==> stopped by user`, exit code 0, no
orphan process. Browser-cookie detection is unit-tested against a fake Windows
profile tree (two browsers, different cookie timestamps -> picks the newer one),
and `--cookies` was passed to a real `yt-dlp` to confirm the flag combination is
accepted.

**Not verified here:**
- **Live calls to YouTube Music, LRCLIB and NetEase.** This sandbox's egress
  allowlist blocks them (`api.github.com` and `pypi.org` return 200; `lrclib.net`
  and `google.com` return 000). Run `python musictool.py providers --test` on your
  machine to check reachability before a big batch. The `ytmusic` provider follows
  the documented reverse-engineered InnerTube flow; if YouTube changes the client
  version that returns timings, that provider will simply return nothing and fall
  through to `lrclib`.
- **The GUI's pixels.** tkinter is not installed in this sandbox, so the window was
  never *drawn*. Everything except the drawing is now executed for real against
  `tests/tkstub.py`: `gui.run()` builds all seven tabs, every button is invoked, and
  the argv each one produces is captured and asserted — including ticking the
  quick-strip `--allow-unsynced` box, loading the file list, skipping a highlighted
  file, redoing everything with `--force`, refusing to run with nothing ticked, and
  resizing the log pane, and wheel events routed to the right scrolling area.
  Widget generation still covers every CLI flag of every
  subcommand, and each value round-trips through `build_argv` and back through the
  real argparse parser. What no stub can prove is font metrics, real sash dragging
  and how the theme looks on your platform.
- **Real `yt-dlp` downloads**, for the same egress reason; a fake `yt-dlp`
  exercises the argument building, output parsing and index writing.
