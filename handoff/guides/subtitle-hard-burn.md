# Subtitle Hard-Burn — the complete guide

How to burn subtitles into a video permanently, on whichever machine you actually
have this week. Covers the My Life with the Walter Boys job, the speed
benchmarks behind it, and the phone-first workflow.

## Your devices

| Device | Status | Encoder |
|---|---|---|
| **Tecno Spark 30C** (Helio G81) | ✅ **Owned** — default | `h264_mediacodec` |
| **Celeron N3350 laptop** | ✅ **Owned** | `h264_qsv` |
| EliteBook 840 G6 (i5-8365U) | 🔄 Borrowed | `h264_qsv` |
| EliteBook x360 1040 G5 (i7-8650U) | 🔄 Borrowed | `h264_qsv` |
| Android 4.4 KitKat | Separate older **phone** | a playback target, not a workstation |
| Non-smart TV | Plays from an **old flash drive** | the playback target |

**No AMD machine exists in this picture.** `-hwaccel amf` / `h264_amf` are dead.

**Related:** `phone-first-ffmpeg.md` (phone as default), `bitrate-and-subspeed-fixes.md`
(the older two-fix guide — note it recommends machines you don't own).

---

# 1. The reference job

This is the file the guide was built around, so you have exact working paths.

```
Video:  My_Life_with_the_Walter_Boys - 1x1 - Welcome to Colorado.TXT.IMDB.en.mp4
Subs:   My_Life_with_the_Walter_Boys - 1x1 - Welcome to Colorado.TXT.IMDB.en.srt
Output: My_Life_with_the_Walter_Boys - 1x1 - Welcome to Colorado.TXT.IMDB.mp4
Font:   Lato
```

| Machine | Location |
|---|---|
| Tecno Spark 30C | `/storage/emulated/0/Movies/` |
| Laptop | `C:\Users\hp\Videos\` |

Episode is ~49 minutes. Output was ~432 MB.

---

# 2. Measured results — don't guess, these are real numbers

Software encode, `libx264 -preset medium -threads 0`, 1080p, with subtitles:

| Device | Speed | Notes |
|---|---|---|
| Tecno (Helio G81) | **0.7×** | Worse than useless |
| Celeron N3350 | **0.55×** | Slower still |

Hardware encode, same job:

| Device | Speed | Notes |
|---|---|---|
| **i7-8650U (borrowed x360 1040 G5)** | **9.21×** | `h264_qsv` — not a libx264 figure |

The 9.21× run: **5 min 18 s** for ~49 minutes of video, output ~432 MB.
(2940 s ÷ 9.21 ≈ 319 s = 5:19 — consistent.)

Software encoding is simply not viable. **Always use a hardware encoder.**

### Reference points from other jobs

- Tecno pure transcode, **no filters**, 1080p: **2.2–2.4×** (the ceiling)
- Any `subtitles=` filter drops it below that ceiling
- The borrowed i7-8650U and the borrowed EliteBook 840 G6's i5-8365U are the
  same class of chip (4C/8T, 15 W, UHD 620 with the same Quick Sync engine), so
  expect a similar `h264_qsv` figure on either. The Celeron N3350 is far slower.

---

# 3. Pick your machine first

## You own exactly two devices

| Machine | Encoder | Verdict |
|---|---|---|
| **Tecno Spark 30C** (Helio G81) | `h264_mediacodec` | **Default.** Always available, nothing to borrow, no asking |
| **Celeron N3350 laptop** | `h264_qsv` | Your only laptop. Usable, but slow |

## Borrowed — never plan around these

| Machine | Encoder | Verdict |
|---|---|---|
| HP EliteBook 840 G6 (i5-8365U/UHD 620) | `h264_qsv` | On loan — used for the emulator setup, not permanently yours |
| EliteBook x360 1040 G5 (i7-8650U) | `h264_qsv` | **Fastest measured at 9.21×** — but you must have it on loan that day |

> ⚠️ **The 9.21× figure came from the borrowed i7-8650U and is not representative of
> your Celeron N3350**, which is a much weaker chip. Always benchmark on the
> hardware you actually have that day before promising a runtime.

> **No AMD machine is available to you.** `-hwaccel amf` / `h264_amf` are AMD-only
> and will fail on every machine listed.

---

# 4. The commands

## Termux, on the Tecno

```bash
ffmpeg -i "/storage/emulated/0/Movies/My_Life_with_the_Walter_Boys - 1x1 - Welcome to Colorado.TXT.IMDB.en.mp4" \
 -vf "subtitles='/storage/emulated/0/Movies/My_Life_with_the_Walter_Boys - 1x1 - Welcome to Colorado.TXT.IMDB.en.srt':force_style='FontName=Lato'" \
 -c:v h264_mediacodec -profile:v main -bitrate_mode cbr -b:v 1800k -g 50 \
 -c:a copy \
 "/storage/emulated/0/Movies/My_Life_with_the_Walter_Boys - 1x1 - Welcome to Colorado.TXT.IMDB.mp4"
```

- `-bitrate_mode cbr` is what asks MediaCodec for constant bitrate. Without it
  `-b:v` is only a loose target and the output can land ~1.9× over it.
  (`-profile:v main` only sets the profile; `-maxrate`/`-bufsize` are not passed
  to MediaCodec by FFmpeg at all.) Whether the G81 honours CBR: check the size of
  a 30-second test.
- `-g 50` — a keyframe every 50 frames (~2 s at 24–25 fps), keeps scrubbing smooth
- Decode is in software on purpose: `-hwaccel mediacodec` does not work in Termux
  (FFmpeg trac #11508)

## Windows, any Quick Sync machine

```cmd
cd /d C:\Users\hp\Videos
start "" /B /High /Wait ffmpeg -i "My_Life_with_the_Walter_Boys - 1x1 - Welcome to Colorado.TXT.IMDB.en.mp4" ^
 -vf "subtitles=My_Life_with_the_Walter_Boys - 1x1 - Welcome to Colorado.TXT.IMDB.en.srt:force_style='FontName=Lato'" ^
 -c:v h264_qsv -global_quality 22 -preset medium -g 50 -c:a copy ^
 "My_Life_with_the_Walter_Boys - 1x1 - Welcome to Colorado.TXT.IMDB.mp4"
```

**`cd` into the folder first.** This is not optional advice — the `subtitles`
filter chokes on paths containing spaces and commas unless you `cd` and use bare
filenames. It saves an hour of escaping.

**`-hwaccel qsv -hwaccel_output_format qsv` is intentionally absent** here. Adding
it with a CPU filter like `subtitles` fails — the filter needs frames in RAM. Decode
in software; only the *encode* is hardware.

---

# 5. The Windows priority wrapper

Use it when you want FFmpeg to have the machine to itself but still see progress.

```cmd
start "" /B /High /Wait ffmpeg <your command>
```

| Piece | Why |
|---|---|
| `start ""` | Empty quotes give the window a blank title. Without them Windows misparses the quotes in your subtitle filter and **spawns a new window anyway**, ignoring `/B`. |
| `/B` | No new window — binds FFmpeg to your current console so progress updates scroll. |
| `/High` | Dominates background apps but leaves the console enough CPU to repaint. |
| `/Wait` | Prompt doesn't return until the encode finishes. |

> **Do not use `/Realtime`.** It starves the console process that draws the progress
> text — you get updates every few minutes instead of every second, and your mouse
> and keyboard stutter. `/High` is the correct choice.

### QuickEdit mode — the silent killer

**Clicking inside the Command Prompt pauses the entire encode.** QuickEdit tries to
select text, which suspends the process. If the output freezes, press **Enter** or
**Esc** and it resumes. If you hit Ctrl+C or click deliberately, you may kill it.

---

# 6. Why the burn is slow

`subtitles` is a **CPU filter** (libass). Adding it breaks the pure-hardware pipeline:

```
VPU decodes ──► GPU memory
                      │
                      ▼  ← BREAKS HERE: frames drop to system RAM
      CPU runs libass, rasterises glyphs, blends onto every pixel
                      │
                      ▼  ← and go back up to the GPU
VPU encodes
```

libass rasterises and blends on **every single frame**. For a 49-minute episode at
24 fps that's ~70,000 blending operations. That's the bottleneck — never the encoder.

**Consequence:** at high hardware-encode speeds (9× and up), libass becomes the wall.
The GPU finishes frames faster than the CPU can draw the font onto them.

---

# 7. Three ways to speed it up

## Fix 1 — Scale down before burning (biggest win, free)

```bash
-vf "scale=-2:720,subtitles=$SUBS:force_style='Fontsize=26,MarginV=28'"
```

- libass rasterises at 720p instead of 1080p → **2.25× less work**
- The encode is cheaper too
- Output drops dramatically

**Scale the font to match.** `Fontsize` and `MarginV` must grow with the canvas or
the text looks tiny. This is a 10-second adjustment loop, not a 2-hour commit.

Go further for a small TV: `scale=-2:480` is another 2.25× cheaper again.

## Fix 2 — Move it off the phone

The Helio G81 was doing hardware decode, libass rasterisation, hardware encode and
Android all at once. Its pure-transcode ceiling is 2.2–2.4×; any CPU filter goes
below that. A laptop with 4+ real cores gives the filter far more to work with.

## Fix 3 — Benchmark a 2-minute clip first

```cmd
ffmpeg -i "movie.mp4" -t 120 -c copy bench_in.mp4
ffmpeg -i bench_in.mp4 -vf "scale=-2:720,subtitles=subs.srt" ^
 -c:v h264_qsv -global_quality 22 -preset medium -c:a copy bench_out.mp4
```

Read `speed=` at the end, then:

```
full_runtime_seconds = total_seconds ÷ speed_multiplier
```

Example: `speed=4.5x` on a 6724 s movie → `6724 / 4.5` ≈ **25 minutes**.

---

# 8. Long files — split and burn in parallel

For anything over an hour, splitting into parts and burning them concurrently beats
any single-flag optimisation, because libass is per-frame CPU work and idle cores
are free capacity.

```bash
# burn.sh — parallel subtitle hard-burn for Termux
# chmod +x burn.sh && ./burn.sh   (edit the variables first)

MOVIE="/storage/emulated/0/Movies/My_Life_with_the_Walter_Boys - 1x1 - Welcome to Colorado.TXT.IMDB.en.mp4"
SUBS="/storage/emulated/0/Movies/My_Life_with_the_Walter_Boys - 1x1 - Welcome to Colorado.TXT.IMDB.en.srt"
OUT="/storage/emulated/0/Movies/WalterBoys_subbed.mp4"
TOTAL=2940          # ffprobe -v error -show_entries format=duration -of csv=p=0 "$MOVIE"
PARTS=4             # try 2, then 4, then 8
HEIGHT=720
BITRATE="1800k"

CHUNK=$(( (TOTAL + PARTS - 1) / PARTS ))

echo "Splitting into $PARTS parts of ~$CHUNK s..."
rm -f reset_*.mp4 burn_*.mp4 concat.txt
# -reset_timestamps 1 makes every part start at 0 (without it each part keeps
# its original timestamps and the setpts shift below is applied twice).
ffmpeg -y -i "$MOVIE" -c copy -f segment -segment_time "$CHUNK" \
  -segment_time_delta 1 -reset_timestamps 1 reset_%02d.mp4

echo "Burning subtitles in parallel..."
OFFSET=0
PIDS=""
for f in reset_*.mp4; do
  idx="${f:6:2}"
  # -c copy cuts land on keyframes, so parts are not exactly CHUNK long.
  # OFFSET is the sum of the real durations before this part; idx*CHUNK drifts
  # (that was the 8-second Big Little Lies bug).
  DUR=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$f")
  (
    # setpts shifts timestamps up so libass looks up the RIGHT subtitle cues,
    # then back down so each output part starts at zero.
    ffmpeg -y -i "$f" \
      -vf "setpts=PTS+$OFFSET/TB,scale=-2:$HEIGHT,subtitles=$SUBS,setpts=PTS-$OFFSET/TB" \
      -c:v h264_mediacodec -profile:v main -bitrate_mode cbr -b:v $BITRATE -g 50 \
      -af "aresample=async=1:first_pts=0" -ac 2 -c:a aac -b:a 128k \
      "burn_${idx}.mp4"
  ) &
  PIDS="$PIDS $!"
  OFFSET=$(awk -v o="$OFFSET" -v d="$DUR" 'BEGIN{printf "%.3f", o+d}')
done
# A bare `wait` returns 0 even if a part failed, and the join would then
# stitch together whatever parts exist. Wait for each part and stop instead.
FAILED=0
for p in $PIDS; do wait "$p" || FAILED=$((FAILED+1)); done
if [ "$FAILED" -gt 0 ]; then
  echo "$FAILED part(s) failed - not joining. Try fewer PARTS."; exit 1
fi

echo "Joining parts..."
ls burn_*.mp4 | sort | sed "s/^/file '/;s/\$/'/" > concat.txt
ffmpeg -y -f concat -safe 0 -i concat.txt -c copy "$OUT"
```

### The one thing that will bite you

The `setpts` shift. Without it, **part 2 onward gets the wrong subtitles** — libass
reads cues by timestamp, and each segment restarts at zero (with
`-reset_timestamps 1`). The shift must be the part's **real** start time — the
running total of the measured part durations — not `part number × CHUNK`,
because stream-copy cuts land on keyframes.

**Always test on a short file first** and confirm subtitles land on the correct lines
in the later parts.

### Simpler alternative, one part at a time

```bash
CHUNK=735    # 2940 / 4
OFFSET=0
ffmpeg -y -i "$MOVIE" -ss $OFFSET -t $CHUNK \
  -vf "scale=-2:720,subtitles=$SUBS" \
  -c:v h264_mediacodec -profile:v main -bitrate_mode cbr -b:v 1800k \
  -c:a aac -b:a 128k part_0.mp4
```

Slower but far easier to debug. Here `-ss`/`-t` sit *after* `-i` (output
seeking): ffmpeg decodes from the start and throws frames away until `OFFSET`,
so the subtitles filter still sees the film's real timestamps and needs no
`setpts` shift — the price is decoding everything before each part.

---

# 9. Targeting the non-smart TV

## The playback chain

```
You encode the file  →  copy to an old flash drive  →  plug into the TV  →  TV plays it
```

**The phone is not in this chain.** The file goes to the TV on a flash drive and the
TV decodes it with its own hardware. The TV's decoder is therefore the *only*
constraint — your phone's capabilities are irrelevant to this decision.

The Android 4.4 KitKat is a **separate phone**, and it supports *more* than the TV.
So it imposes no extra limit: one file that satisfies the TV satisfies both.

## What an old non-smart TV can usually do

| Capability | Typical | Assume |
|---|---|---|
| H.264 (AVC) | ✅ Yes | ✅ |
| H.265 (HEVC) | ⚠️ Sometimes | ❌ No |
| AV1 | ❌ No | ❌ No |
| H.264 Baseline / Main | ✅ Yes | ✅ |
| H.264 High profile | ⚠️ Often | ❌ No |
| 1080p | ⚠️ Sometimes | **720p is safer** |
| Soft / external subtitles | ❌ No | **Must burn them in** |
| MP4 container | ✅ Usually | ✅ Use MP4 |
| MKV container | ❌ Often | ❌ Use MP4 |

## The command

```bash
ffmpeg -i "input.mp4" -vf "scale=-2:720,subtitles=$SUBS:force_style='Fontsize=26,MarginV=28'" \
  -c:v h264_mediacodec -profile:v baseline -level 3.1 \
  -bitrate_mode cbr -b:v 1500k -g 50 \
  -c:a aac -b:a 128k -ac 2 -movflags +faststart "for_the_tv.mp4"
```

| Flag | Why |
|---|---|
| `-profile:v baseline -level 3.1` | Old TVs predate High profile. Baseline + 3.1 is the safest common denominator. |
| `scale=-2:720` | Many old TV panels are 720p. Scaling down also cuts libass work by 2.25×. |
| `Fontsize` / `MarginV` bump | Compensates for the smaller canvas — otherwise the text looks tiny. |
| Hard-burned subtitles | It cannot display soft subs. Ever. |
| `-movflags +faststart` | Index at the front so the TV can start playing without seeking to the end. |

> **Never use HEVC or AV1 for the TV.** Both are beyond it. H.264 is the only safe choice.

## The flash drive is a hard limit too

**FAT32 cannot hold a file larger than 4 GB.** Old flash drives are almost always
FAT32, and many old TVs won't read exFAT or NTFS at all. If the drive is FAT32, that
4 GB ceiling is unavoidable — FFmpeg cannot write a bigger file to it.

Maximum safe video bitrate for a given runtime:

```
safe_kbps = (4,000,000,000 ÷ runtime_seconds × 8 ÷ 1000) − 150
```

The 150 covers audio and container overhead.

| Runtime | Max video bitrate |
|---|---|
| 49 min (2940 s) | ~10,700 kbps |
| 96 min (5782 s) | ~5,400 kbps |
| 112 min (6724 s) | ~4,600 kbps |

At 1500k you'll never come close. This only becomes a problem above roughly 4000k on
a long film.

**Check the drive's format before you encode.** If it's NTFS or exFAT, some TVs show
nothing at all — not an error message, just a black screen.

---

# 10. Subtitle quality notes

| Point | Detail |
|---|---|
| **`force_style`** | Essential when downscaling. Bump `Fontsize` and `MarginV` to match. |
| **`.ass` beats `.srt`** | If you have an ASS file, use the `ass` filter instead — it skips SRT→ASS style guessing and parses faster. |
| **`cd` into the folder** | The `subtitles` filter chokes on complex paths. Bare filenames only. |
| **UTF-8 with BOM** | Non-ASCII characters render as boxes without it. |
| **Check your font exists** | If Lato isn't installed on the machine doing the burn, libass silently substitutes and your styling is wrong. |

Verify the font is present before a long job — it's a 5-second check that can save
an hour of wrongly-styled output.

---

# 11. Font install - verify in 8 seconds (don't waste 12 min)
For Termux the font **must** be in `$HOME/fonts` and `fontsdir` must point there:
```bash
mkdir -p ~/fonts
cp "/storage/emulated/0/Fonts/Caveat.ttf" ~/fonts/  # or curl from github
fc-cache -f
fc-list | grep -i caveat  # must show Caveat, not empty
# 2-second test - look for fontselect line:
ffmpeg -y -ss 930 -t 2 -i movie.mp4 -vf "subtitles=subs.srt:fontsdir=$HOME/fonts:force_style='FontName=Caveat,Fontsize=28'" -c:v h264_mediacodec -c:a aac "$PREFIX/tmp/fonttest.mp4" 2>&1 | grep -i fontselect
# should be: fontselect: (Caveat, 400, 0) -> /data/.../Caveat.ttf
# Read the RIGHT side of the arrow. The left side is only what was asked for and
# always says Caveat; a fallback looks like
#   fontselect: (Caveat, 400, 0) -> /.../DejaVuSans.ttf, 0, DejaVuSans
# -> FontName spelling wrong, wrong fontsdir, or fc-cache not run
```
(Termux has no writable `/tmp`; `$PREFIX/tmp` is
`/data/data/com.termux/files/usr/tmp`.)

# 12. Gotchas

| Symptom | Cause | Fix |
|---|---|---|
| New window spawns, no progress | `start` confused by quotes | Add `""` after `start` |
| Progress updates every few minutes | `/Realtime` starving the console | Use `/High` |
| Output freezes completely | Clicked in the Command Prompt (QuickEdit) | Press Enter or Esc |
| `Encoder configure failed, -10000` | Wrong pixel format | Add `-pix_fmt nv12` |
| Bitrate ~1.9× over target | MediaCodec treating `-b:v` as a loose VBR target | Add `-bitrate_mode cbr`; if the size is still wrong, use two-pass `libx264` |
| Wrong subtitles after the first part | Missing `setpts` shift | See section 8 |
| `-threads 0` present with hardware encode | Leftover from the software command | Delete it — encoders ignore CPU threads |
| `Unknown encoder 'h264_mediacodec'` | Old FFmpeg | `pkg upgrade ffmpeg` |

---

# 13. Don't propagate these Gemini errors

Corrections from the original conversation, verified since:

- ❌ **"Termux's FFmpeg usually doesn't have MediaCodec support."** False. Your Tecno
  runs the `h264_mediacodec` *encoder* without issue. (What does not work in
  Termux is `-hwaccel mediacodec` decoding — no Java VM, trac #11508.)
- ❌ **"The phone only beat the Celeron because libx264 is x86-optimised and the
  Helio G81 throttles in a glass sandwich."** Backwards. The Celeron N3350 is simply
  the weaker chip. The phone won on raw silicon, not architecture or thermals.
- ❌ **Recommendations to use a Ryzen 5 PRO 4650U and a Tiger Lake i5-1145G7.**
  You don't have either. All `-hwaccel amf` advice is dead — no AMD machine.
- ⚠️ **Speed estimates were guesses.** The 9.21× figure was real; everything else
  in that thread was speculation. Benchmark a 2-minute clip.

---

# TL;DR

**Always hardware-encode.** Software tops out at 0.7× — unusable.

**Phone (default):** `h264_mediacodec` with `-bitrate_mode cbr -b:v …`. Expect well
under 2× with a subtitle filter (the real 1080p burn ran at ~0.7×).

**Your Celeron:** `h264_qsv`. The 9.21× figure belongs to a *borrowed* i7-8650U and
will not transfer to it. Benchmark before you commit to a runtime.

**Wrap Windows jobs in `start "" /B /High /Wait`** so progress stays visible without
freezing the machine.

**Speed it up by scaling down first** — 720p cuts libass work by 2.25× — and by
splitting long files to burn in parallel with a `setpts` shift per part.

**For the TV:** H.264 **Baseline**, hard-burned, 720p, **MP4**, under **4 GB**.
The TV plays from a flash drive, so the drive's filesystem is a hard ceiling —
FAT32 stops at 4 GB, and many old TVs cannot read exFAT or NTFS at all.