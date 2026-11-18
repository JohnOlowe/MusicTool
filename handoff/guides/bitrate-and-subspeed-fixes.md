# Fixes — Bitrate Mismatch & Subtitle Hard-Burn Speed

> ⚠️ **SUPERSEDED IN PART — read this first.**
> - **Bitrate:** the theory below that `-profile:v main` forces MediaCodec into CBR
>   is **not supported**. FFmpeg's `h264_mediacodec` sends the profile and the
>   bitrate to the encoder but never changes its rate-control mode because of the
>   profile, and it does not pass `-maxrate`/`-bufsize` at all. The explicit switch
>   is `-bitrate_mode cbr` (FFmpeg 6.0+). The "evidence" run was itself ~32 % over
>   its target (see Part 1). The commands below have been corrected accordingly.
> - **Machines:** this guide suggests a Ryzen 5 PRO 4650U and a Tiger Lake
>   i5-1145G7, and **you have neither** (you own only the Tecno and the Celeron
>   N3350). The AMF/`h264_amf` commands would only apply to that Ryzen (AMF needs
>   an AMD GCN-or-newer GPU); on your machines they fail.
> - For subtitle burning, use **`subtitle-hard-burn.md`**, which is QSV/MediaCodec
>   only and carries the measured 9.21× benchmark.

Follow-up to the QSV/FFmpeg thread. Two separate issues, dealt with separately.

---

# PART 1 — The Phone Produced 3.66 GB, the Celeron Produced 1.89 GB

Same target (`-b:v 2500k`), same 6,724-second movie, wildly different results.

## The numbers

| Machine | Command | Actual overall bitrate (video + 192k copied audio) |
|---|---|---|
| Celeron N3350 | `-c:v h264_qsv -b:v 2500k -c:a copy` | **~2,400 kbps** ✅ on target |
| Tecno (Helio G81) | `-c:v h264_mediacodec -b:v 2500k -c:a copy` | **~4,680 kbps** ❌ ~1.8× over |

3.66 GiB × 8 ÷ 6,724 s ≈ 4.68 Mbps (1.89 GiB → ≈ 2.41 Mbps). You asked for 2.5.
It ignored you.

## The evidence points at one specific cause

Look back at your earlier run that *did* honour the bitrate:

```
-vf "scale=-2:720"  -c:v h264_mediacodec  -profile:v baseline  -b:v 1500k
→ 17,504 KiB for 66 s ≈ 2,170 kbps overall, ≈ 2,000 kbps video   ⚠️ ~32 % over 1500k
```

That one had **`-profile:v baseline`** in it and came out *less* over target
than the 3.66 GB run — but it still missed by about a third, so it does not
show that the profile locks the encoder into CBR. (It was also 720p, a much
easier picture to hold at a bitrate.)

What FFmpeg actually does: `h264_mediacodec` hands MediaCodec a `bitrate` and,
only if you ask, a `bitrate-mode` (`-bitrate_mode cq|vbr|cbr|cbr_fd`). Without
that the device picks its own mode — typically VBR, where `-b:v` is a loose
target. The profile is passed separately and does not change the mode. Quick
Sync, by contrast, has deterministic rate control, which is why the Celeron hit
2500k.

## The fix — ask for CBR explicitly

```bash
# Termux, on the Tecno
ffmpeg -i "/storage/67FE-19FE/Download/MovieBox/The_Love_Hypothesis_1080P.mp4" \
  -c:v h264_mediacodec -profile:v main -bitrate_mode cbr -b:v 2500k \
  -c:a copy \
  "/storage/67FE-19FE/Download/MovieBox/Love_Fast.mp4"
```

| Flag | Why |
|---|---|
| `-bitrate_mode cbr` | Asks MediaCodec for constant bitrate. Whether the G81 encoder honours it is untested — verify below. |
| `-b:v 2500k` | The target. |
| `-profile:v main` | Profile only (Main plays more widely than High on old devices). Not a rate-control switch. |

No `-hwaccel mediacodec`: it fails in Termux (no Java VM, FFmpeg trac #11508);
decode runs on the CPU and the encode is still hardware. `-maxrate`/`-bufsize`
are left out because `h264_mediacodec` ignores them.

## Verify before you trust it

Don't encode a 2-hour movie to find out. Encode 60 seconds and check:

```bash
# 1. Make a 60-second clip (copy, no re-encode — instant)
ffmpeg -i "The_Love_Hypothesis_1080P.mp4" -t 60 -c copy "/sdcard/test60.mp4"

# 2. Encode it with your target settings
ffmpeg -i "/sdcard/test60.mp4" \
  -c:v h264_mediacodec -profile:v main -bitrate_mode cbr -b:v 2500k \
  -c:a copy "/sdcard/test60_out.mp4"

# 3. Measure what you ACTUALLY got
ffprobe -v error -select_streams v:0 -show_entries stream=bit_rate,codec_name,profile \
  -of default=nw=1 "/sdcard/test60_out.mp4"
```

Then do the maths: `size_MB × 8 ÷ 60 = bitrate in kbps`. For 60 s at a true 2500 kbps you want **≈ 17–19 MB** plus audio.

Run that before touching the full movie. Thirty seconds of work saves two hours.
If it still lands far over target, use two-pass `libx264` (see
`phone-first-ffmpeg.md`) — slower, but the size is deterministic.

## The honest recommendation: don't do this on the phone at all

MediaCodec is built for **real-time capture** — recording a video call where the priority is "never drop a frame," not "hit 2,500 kbps." Trying to make it an archiver is fighting the design. You'll keep chasing it.

*(Obsolete — you do not have these laptops; kept for the record. On your own
Celeron N3350, the `h264_qsv` line further down applies.)* The original advice
was:

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
ffmpeg -i "movie.mp4" -vf "subtitles=subtitles.srt" \
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
- Your output drops from ~3.6 GB to **~1.5 GB** (1,800 kbps × 6,724 s ≈ 1.51 GB of
  video, plus ~0.16 GB of copied 192 kbps audio)

The subtitle font must be scaled up to match (`force_style`), or it'll be tiny against the smaller canvas. Nudge it until it looks right on the TV — that's a 10-second loop, not a 2-hour one.

**Go further if the TV is small:** `scale=-2:480` is another 2.25× cheaper again. For burned subtitles viewed from a couch, 480p is genuinely fine.

### Fix 2 — Move it to a laptop. Don't burn on the phone.

The Helio G81 was doing three jobs at once: HEVC decode, libass rasterisation, and hardware encode — while running Android. Its measured pure-transcode ceiling was 2.2–2.4×, and *any* CPU filter pushes it below that.

*(Obsolete — not your machine:)* the Ryzen 5 PRO 4650U has 6 Zen 2 cores for libass. On your own laptop the option is the Celeron N3350 with `h264_qsv` — benchmark it with Fix 3, swapping the encoder for `-c:v h264_qsv -b:v 1800k` and dropping `-hwaccel amf`.

Rough expectations for the 112-minute movie, **at 720p with burned subs**:

| Machine | Expected speed | Wall clock |
|---|---|---|
| Tecno phone, 1080p (current) | ~1.0–1.2× (a real 1080p burn later measured ~0.7×) | ~95–110 min |
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

Read the `speed=` figure at the end. `speed=4.5x` → the full 6,724 s movie takes
`6724 ÷ 4.5` ≈ 1,494 s ≈ **25 minutes**. (The clip length does not enter into it;
an earlier version divided by `4.5 × 120` and got a wrong 12.4 minutes.)

In general: `full_runtime_minutes = 6724 ÷ speed_multiplier ÷ 60`.

---

## Subtitle quality notes

- **`force_style`** is your friend on a small screen: bump `Fontsize` and `MarginV` when downscaling so text stays readable.
- **Use `.ass` not `.srt` if you can.** The `ass` filter skips SRT→ASS style guessing and parses faster. Rename the filter: `-vf "scale=-2:720,ass=subs.ass"`.
- **`cd` into the folder first.** The `subtitles` filter chokes on complex paths. `cd /d C:\path\to\movie` in Command Prompt, then use bare filenames.
- **Check the .srt is UTF-8** and has a BOM if it has any non-ASCII characters, or accented characters will render as boxes.

---

## TL;DR

**Bitrate:** Add `-bitrate_mode cbr` and verify on a 60-second clip. If MediaCodec still misses, use two-pass `libx264`, or encode on the Celeron with `h264_qsv`, whose rate control is deterministic.

**Subtitles:** Burn at 720p, not 1080p. Benchmark a 2-minute clip on the machine you actually have (phone or Celeron) to get a real number; the Ryzen figures in this guide do not apply to you.