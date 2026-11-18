# Fixes — Bitrate Mismatch & Subtitle Hard-Burn Speed

> ⚠️ **PARTLY SUPERSEDED — read this first.**
> The `-profile:v main` MediaCodec CBR finding below is still correct and important.
> But the machine recommendations are **obsolete**: this guide suggests a Ryzen 5 PRO
> 4650U and a Tiger Lake i5-1145G7, and **you have neither**. All `-hwaccel amf` /
> `h264_amf` commands here are dead — you have no AMD machine.
> For subtitle burning, use **`subtitle-hard-burn.md`**, which is QSV/MediaCodec
> only and carries the measured 9.21× benchmark.

Follow-up to the QSV/FFmpeg thread. Two separate issues, dealt with separately.

---

# PART 1 — The Phone Produced 3.66 GB, the Celeron Produced 1.89 GB

Same target (`-b:v 2500k`), same 6,724-second movie, wildly different results.

## The numbers

| Machine | Command | Actual video bitrate |
|---|---|---|
| Celeron N3350 | `-c:v h264_qsv -b:v 2500k -c:a copy` | **~2,400 kbps** ✅ hit target |
| Tecno (Helio G81) | `-c:v h264_mediacodec -b:v 2500k -c:a copy` | **~4,680 kbps** ❌ 1.87× over |

3.66 GB ÷ 6,724 s = 4.68 Mbps. You asked for 2.5. It ignored you.

## The evidence points at one specific cause

Look back at your earlier run that *did* honour the bitrate:

```
-vf "scale=-2:720"  -c:v h264_mediacodec  -profile:v baseline  -b:v 1500k
→ 17,504 KiB for 66 s ≈ 1,980 kbps   ✅
```

That one had **`-profile:v baseline`** in it. The 3.66 GB run did not.

This is the pattern: MediaTek's MediaCodec H.264 encoder will **only lock into hard CBR when constrained to Baseline or Main profile.** Hand it High profile (the default when you don't specify) and it switches to its own quality-driven rate control, where `-b:v` is treated as advisory and the encoder spends whatever it thinks the picture deserves.

That's why the same phone, same chip, same ffmpeg build, gave you two different behaviours — and why the Celeron's Quick Sync hit 2500k exactly. QSV has deterministic rate control; MediaCodec does not, by default.

## The fix — pin the profile and cap the rate

```bash
# Termux, on the Tecno
ffmpeg -hwaccel mediacodec -i "/storage/67FE-19FE/Download/MovieBox/The_Love_Hypothesis_1080P.mp4" \
  -c:v h264_mediacodec -profile:v main -level 4.0 \
  -b:v 2500k -maxrate 2500k -bufsize 5000k \
  -c:a copy \
  "/storage/67FE-19FE/Download/MovieBox/Love_Fast.mp4"
```

Three changes, all load-bearing:

| Flag | Why |
|---|---|
| `-profile:v main` | Forces MediaCodec into CBR mode. This is the one that actually did it last time. |
| `-maxrate 2500k` | Hard ceiling. Even if the encoder wants more, it can't exceed this. |
| `-bufsize 5000k` | Two seconds of buffer. Stops it overfilling on action shots and then starving. |

Drop `-profile:v main` and it goes back to ignoring you.

## Verify before you trust it

Don't encode a 2-hour movie to find out. Encode 60 seconds and check:

```bash
# 1. Make a 60-second clip (copy, no re-encode — instant)
ffmpeg -i "The_Love_Hypothesis_1080P.mp4" -t 60 -c copy "/sdcard/test60.mp4"

# 2. Encode it with your target settings
ffmpeg -hwaccel mediacodec -i "/sdcard/test60.mp4" \
  -c:v h264_mediacodec -profile:v main -level 4.0 \
  -b:v 2500k -maxrate 2500k -bufsize 5000k -c:a copy "/sdcard/test60_out.mp4"

# 3. Measure what you ACTUALLY got
ffprobe -v error -select_streams v:0 -show_entries stream=bit_rate,codec_name,profile \
  -of default=nw=1 "/sdcard/test60_out.mp4"
```

Then do the maths: `size_MB × 8 ÷ 60 = bitrate in kbps`. For 60 s at a true 2500 kbps you want **≈ 17–19 MB** plus audio.

Run that before touching the full movie. Thirty seconds of work saves two hours.

## The honest recommendation: don't do this on the phone at all

MediaCodec is built for **real-time capture** — recording a video call where the priority is "never drop a frame," not "hit 2,500 kbps." Trying to make it an archiver is fighting the design. You'll keep chasing it.

You have three laptops that will do this deterministically:

**AMD Ryzen 5 PRO 4650U (EliteBook 845 G7)** — best choice, VCN 2.1 has genuinely excellent rate control:
```cmd
ffmpeg -hwaccel amf -i "The_Love_Hypothesis_1080P.mp4" -c:v h264_amf -quality speed ^
  -rc cbr -b:v 2500k -maxrate 2500k -bufsize 5000k -c:a copy "Love_Ryzen.mp4"
```

**Intel i5-1145G7 (ZBook Firefly 14 G8)** — Iris Xe, use QSV:
```cmd
ffmpeg -hwaccel qsv -hwaccel_output_format qsv -i "The_Love_Hypothesis_1080P.mp4" ^
  -c:v h264_qsv -b:v 2500k -maxrate 2500k -bufsize 5000k -c:a copy "Love_TigerLake.mp4"
```

Both will land on your target. And the Ryzen has 6 cores, so it can do the subtitle burn fast too — which brings us to Part 2.

---

# PART 2 — The Subtitle Hard-Burn

## Why it's slow right now

```bash
ffmpeg -hwaccel mediacodec -i "movie.mp4" -vf "subtitles=subtitles.srt" \
  -c:v h264_mediacodec -b:v 2500k -c:a copy "out.mp4"
```

`subtitles` is a **CPU filter** (libass). The moment it's in the chain, your "pure hardware pipeline" isn't pure anymore:

```
VPU decodes ──► GPU memory
                      │
                      ▼  ← BREAKS HERE: frames must come down to system RAM
      CPU runs libass, rasterises glyphs, blends onto every pixel
                      │
                      ▼  ← and go back up to the GPU
VPU encodes
```

libass has to rasterise text and blend it across **161,376 frames** (6,724 s × 24 fps). That's the bottleneck, not the encoder.

## The three fixes, in order of impact

### Fix 1 — Scale down before burning. Biggest win, and it's free.

Your target is a **non-smart LED TV**. It is almost certainly 720p or lower, and its own scaler is weak. You are burning 1080p subtitles into a picture that the TV will immediately shrink to 720p anyway.

```cmd
ffmpeg -hwaccel amf -i "The_Love_Hypothesis_1080P.mp4" ^
  -vf "scale=-2:720,subtitles=subtitles.srt:force_style='Fontsize=26,MarginV=28'" ^
  -c:v h264_amf -quality speed -rc cbr -b:v 1800k -maxrate 1800k -bufsize 3600k ^
  -c:a copy "Love_720p_Subbed.mp4"
```

Why this is the big one:

- libass rasterises and blends at **720p instead of 1080p** → 2.25× less work
- The encode is also at 720p → noticeably cheaper
- Your output drops from ~3.6 GB to **~150 MB**

The subtitle font must be scaled up to match (`force_style`), or it'll be tiny against the smaller canvas. Nudge it until it looks right on the TV — that's a 10-second loop, not a 2-hour one.

**Go further if the TV is small:** `scale=-2:480` is another 2.25× cheaper again. For burned subtitles viewed from a couch, 480p is genuinely fine.

### Fix 2 — Move it to a laptop. Don't burn on the phone.

The Helio G81 was doing three jobs at once: hardware HEVC decode, libass rasterisation, and hardware encode — while running Android. Its measured pure-transcode ceiling was 2.2–2.4×, and *any* CPU filter pushes it below that.

The Ryzen 5 PRO 4650U has 6 Zen 2 cores doing nothing but libass. That's a 4–8× jump in filter throughput.

Rough expectations for the 112-minute movie, **at 720p with burned subs**:

| Machine | Expected speed | Wall clock |
|---|---|---|
| Tecno phone, 1080p (current) | ~1.0–1.2× | ~95–110 min |
| Tecno phone, 720p | ~1.8–2.8× | ~40–62 min |
| **Ryzen 5 4650U, 720p** | **~4–7×** | **~16–28 min** |
| Ryzen 5 4650U, 1080p | ~3–5× | ~22–37 min |
| Tiger Lake i5-1145G7, 720p | ~4–6× | ~19–28 min (throttles if sustained) |

**These are estimates — measure before you commit.** Benchmark below.

### Fix 3 — Benchmark a real clip first

Two minutes of work, and it tells you the actual number instead of my guess:

```cmd
REM make a 2-minute clip
ffmpeg -i "The_Love_Hypothesis_1080P.mp4" -t 120 -c copy "bench_in.mp4"

REM run the real burn on it
ffmpeg -hwaccel amf -i "bench_in.mp4" ^
  -vf "scale=-2:720,subtitles=subtitles.srt" ^
  -c:v h264_amf -quality speed -rc cbr -b:v 1800k -c:a copy "bench_out.mp4"
```

Read the `speed=` figure at the end. `speed=4.5x` on 120 s of video → the full 6,724 s movie takes `6724 / (4.5 × 120)` ≈ **12.4 minutes**.

Multiply out: `full_runtime_seconds = 6724 ÷ speed_multiplier ÷ 60`.

---

## Subtitle quality notes

- **`force_style`** is your friend on a small screen: bump `Fontsize` and `MarginV` when downscaling so text stays readable.
- **Use `.ass` not `.srt` if you can.** The `ass` filter skips SRT→ASS style guessing and parses faster. Rename the filter: `-vf "scale=-2:720,ass=subs.ass"`.
- **`cd` into the folder first.** The `subtitles` filter chokes on complex paths. `cd /d C:\path\to\movie` in Command Prompt, then use bare filenames.
- **Check the .srt is UTF-8** and has a BOM if it has any non-ASCII characters, or accented characters will render as boxes.

---

## TL;DR

**Bitrate:** Add `-profile:v main -maxrate 2500k -bufsize 5000k`. MediaCodec only obeys CBR when pinned to Baseline/Main — without the profile flag it silently reverts to its own rate control. Better still, do the encode on the Ryzen or Tiger Lake laptop where the result is deterministic.

**Subtitles:** Burn at 720p, not 1080p, and do it on the Ryzen. Expect ~20 minutes instead of ~100. Benchmark a 2-minute clip first to get a real number instead of an estimate.