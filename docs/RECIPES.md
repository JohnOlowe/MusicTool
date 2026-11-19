# Recipes

Commands that were measured, not guessed. Each was run end to end and the
output read back. `docs/DECISIONS.md` explains *why* each one is written this
way.

These are hand-run commands, not (yet) `musictool` subcommands. Turning the
metadata recipe into `musictool meta URL FILE` is a known piece of future work.
The one exception is the first recipe, which is a `musictool live` command.

---

## A live recording that stopped (disk full) -- watch it, then get the rest

The case it was written for: a live stream recorded with plain
`yt-dlp --live-from-start -N 4` stopped when the disk filled, leaving
thousands of `TITLE.f299.mp4.part-FragN` pieces and a `.part` with little in
it. Windows, cookies in Downloads:

```bat
musictool live "C:\Users\Beloved\Videos\GTA V\New Stream"
musictool live "C:\Users\Beloved\Videos\GTA V\New Stream" --join
musictool live "C:\Users\Beloved\Videos\GTA V\New Stream" --watch --out E:\
musictool live "C:\Users\Beloved\Videos\GTA V\New Stream" --resume https://www.youtube.com/live/xMXp80Cntxw --cookies "%USERPROFILE%\Downloads\cookies.txt"
```

1. The first line only reads: how many pieces of each stream, which can be
   joined, free space.
2. `--join` appends the pieces to the `.part` files in place (it needs room
   for about two pieces), frees their space, and makes `TITLE (so far).mp4`.
   If it is stopped or Windows locks a file, run it again -- it carries on.
3. `--watch --out E:\` puts the copy on another drive; it needs about as
   much room as the recording. Delete it when done -- the pieces stay.
4. `--resume`, after making room: while the stream is live or just ended, the
   same pieces continue (nothing fetched twice); once it is a normal video,
   only the part after yours, as `TITLE (rest).mp4`, plus the ffmpeg line
   that makes one file of the two.

In the window: the **Live streams** tab, **What's in the folder?**, **Watch
what's there so far**, **Download the rest**.

---

## Embed metadata and cover art into a file you already have

`yt-dlp --skip-download --embed-metadata` does **not** work here — with
`skip_download` yt-dlp returns before `post_process` runs, so nothing touches
the existing file. Three steps instead:

**1. Fetch the metadata and thumbnail only** (no video, no audio):

```bat
yt-dlp --skip-download --write-info-json --write-thumbnail -o "ID.%(ext)s" "URL"
```

**2. Convert the JSON to ffmpeg's ffmetadata format.** The line is deliberately
free of `%`, because `cmd.exe` would eat `%d` inside a `.bat` file:

```bat
python -c "import json;d=json.load(open('ID.info.json',encoding='utf-8'));e=lambda s:str(s).replace('\\','\\\\').replace(';','\\;').replace('=','\\=').replace('\n',' ').replace('\r',' ');o=[';FFMETADATA1','title='+e(d.get('title') or ''),'artist='+e(d.get('uploader') or d.get('channel') or ''),'date='+str(d.get('upload_date') or ''),'comment='+e(d.get('description') or '')];[o.extend(['[CHAPTER]','TIMEBASE=1/1000','START='+str(round(c['start_time']*1000)),'END='+str(round(c['end_time']*1000)),'title='+e(c.get('title') or '')]) for c in (d.get('chapters') or [])];open('meta.txt','w',encoding='utf-8').write('\n'.join(o)+'\n')"
```

The `.replace('\n',' ')` is load-bearing: ffmpeg's ffmetadata parser reads `\n`
back as the letter `n`. The `\;` and `\=` escapes matter too — an unescaped `;`
or `=` corrupts the parse silently.

**3. Fold it into the existing file** — `-c copy`, so video and audio are
untouched and it takes seconds even on a 20-hour file:

```bat
ffmpeg -i "movie.mp4" -i meta.txt -i ID.webp -map 0 -map 2 -map_metadata 1 ^
       -c copy -c:v:1 mjpeg -disposition:v:1 attached_pic "movie-tagged.mp4"
```

This writes a **new** file rather than editing in place. Check the output
before deleting the original. If the thumbnail lands as `.jpg`, change that one
filename.

Verified: a title containing `:` and `[HD]`, a description containing `;`, `=`
and newlines, and two chapters all read back intact, with
`Stream #0:N … (attached pic)` alongside the untouched video and audio.

> **Do not "test" step 2 under bash or WSL.** Bash processes the double-quoted
> string and collapses `\\` to `\`, so Python raises
> `SyntaxError: unexpected character after line continuation character`.
> `cmd.exe` does not touch backslashes, so the line is correct for Windows. To
> verify it on Linux, strip the `python -c "` prefix and the trailing quote,
> write the source to a `.py` file, and run that.

---

## Cut a section without re-encoding

```bat
ffmpeg -ss 02:25:00 -to 04:30:00 -i "in.mp4" -map 0 -c copy "out.mp4"
```

Both `-ss` and `-to` **before** `-i`. Moving `-to` after `-i` measures it from
the seek point, not from the start of the file — a 2h05m request produced
4h30m of output in testing. `-t 02:05:00` (a duration) is equivalent and
harder to get wrong.

Two caveats, both measured:

- A `-c copy` cut snaps to the **previous keyframe**, so the start can be early
  by up to one GOP. Only a re-encode is frame-exact.
- Chapters and the title survive, and chapters are re-timed to the new origin.
  Cover art survives but gets flagged `(timed thumbnails)` instead of
  `(attached pic)`.

Seeking is index-based, so source length does not matter: a 10-minute cut out
of a 2h05m file took 734 ms.

---

## Mux a separate audio track into VP9 webm, zero re-encode

```bat
ffmpeg -y -i "video.webm" -i "audio.webm" -map 0:v:0 -map 1:a:0 -c copy "out.webm"
```

WebM accepts **only** VP8/VP9/AV1 video with Vorbis/Opus audio and WebVTT
subtitles. AAC will not go in it at all.

To put VP9 in an mp4 instead: `-tag:v vp09 -c copy` muxes cleanly, whereas
VP9 + Opus in mp4 muxes but warns `track 1: codec frame size is not set` —
fine for a desktop, not something to ship to a phone.

---

## Re-encode only the audio, keeping the video

```bat
ffmpeg -i "in.webm" -c:v copy -c:a aac -b:a 128k -ar 48000 "out.mp4"
```

Software AAC runs 66–250x realtime, so even 20 hours of audio is 5–20 minutes.
There is no hardware audio encoder on any platform, and none is needed.

If the file has cover art, `-map 0 -c:v <codec>` fails with
`Could not find tag for codec h264 in stream #3` — the cover is a video
stream. Use `-c:v:0 <codec> -c:v:1 copy -disposition:v:1 attached_pic`, or
`-map -0:v:1` to drop it.

---

## Calibrate a transcode estimate on the actual machine

Cut one minute and read the reported speed:

```bat
ffmpeg -hwaccel qsv -ss 00:10:00 -t 60 -i "file.webm" -c:v h264_qsv -preset veryfast -global_quality 23 -c:a aac -b:a 128k -ar 48000 test.mp4
```

`-hwaccel` is an **input** option and must come before `-i`; after it, ffmpeg
refuses with `Option hwaccel ... cannot be applied to output url`. If the
source codec has no hardware decoder on that chip (e.g. AV1 or 10-bit VP9 on
an Apollo Lake Celeron), ffmpeg decodes in software and still encodes with
`h264_qsv`. With no usable Intel GPU/driver, `h264_qsv` fails to open
(`Error creating a MFX session: -9`); drop `-hwaccel qsv` and use
`-c:v libx264 -preset veryfast -crf 23` to calibrate the software path instead.

Read `speed=X.XXx` from the output and divide the file's hours by it. Handing
someone a measured multiple of *their* machine beats quoting a benchmark from
someone else's.
