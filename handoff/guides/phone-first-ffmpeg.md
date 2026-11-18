# Phone-First FFmpeg Guide — Tecno Spark 30C (Helio G81)

Rewritten after getting your actual hardware picture right:
**yours = phone (Helio G81) + Celeron N3350 laptop (+ friend's 8th Gen i5 sometimes).**
The Ryzen, the Xeon and the Tiger Lake were not available to you. Everything below
runs on the phone unless noted.

## Your phone's actual capabilities (Helio G81)

| Task | Supported? |
|---|---|
| HEVC / H.265 decode (8-bit & Main10) | ✅ up to 4K |
| H.264 decode | ✅ up to 4K |
| H.264 **encode** | ✅ up to 1080p |
| HEVC **encode** | ✅ up to 1080p |
| Dolby Vision tone mapping | ❌ — MediaCodec ignores DV metadata |

**Consequence: any 1080p job can be a 100% hardware pipeline on your phone.**
The 4K iPhone files couldn't be, because the encoder caps at 1080p.

Your Termux FFmpeg 8.1.2 is well built — it has `mediacodec`, `libx264`, `libx265`,
`libass`, `vulkan` and `libplacebo`. You are not short of options.

---

# 1. Your new file, read for you

```
IMG_3095.MOV
Duration   00:00:39.15          (39 seconds)
Video      hevc (Main 10), hvc1
           yuv420p10le (tv, bt2020nc / bt2020 / arib-std-b67)
           1080x1920  — PORTRAIT, not landscape
           30 fps, 11,266 kb/s
           DOVI configuration: profile 8, level 4
Audio      aac LC, 44100 Hz, stereo, 125 kb/s
```

**What this is:** a 1080×1920 portrait clip, 10-bit HEVC, in Dolby Vision Profile 8,
BT.2020 colour. 39 seconds, so it's a couple of minutes of work on the phone at most.

**Three things worth knowing:**

1. **It's 1080p, not 4K.** Different from the iPhone files — this one your phone's
   encoder *can* handle in hardware, all the way through.
2. **Dolby Vision Profile 8** means HDR. If you play it on an SDR screen without tone
   mapping it will look **washed out and grey**, or dark and lifeless. This is the
   single biggest quality issue with this file.
3. `te_is_reencode: 1` + `DreaminaMetaInfo` = it was already re-encoded by a CapCut/
   Dreamina-style app before reaching WhatsApp. Minor, but it means it's a second
   generation encode.

Your `Unknown cover type: 0x1` warning is harmless — a stray cover-art atom.

---

# 2. Bitrate control on the phone

## The fix

MediaCodec ignores `-b:v` unless you pin the profile to force CBR:

```bash
ffmpeg -hwaccel mediacodec -i "input.mp4" \
  -c:v h264_mediacodec -profile:v main -level 4.0 \
  -b:v 2500k -maxrate 2500k -bufsize 5000k \
  -c:a copy "out.mp4"
```

| Flag | What it does |
|---|---|
| `-profile:v main` | **The one that matters.** Forces MediaCodec out of its quality-driven mode into CBR. Without it `-b:v` is advisory. |
| `-maxrate 2500k` | Hard ceiling regardless of what the encoder wants |
| `-bufsize 5000k` | 2-second buffer — stops it overfilling on action then starving |

## Verify on 30 seconds before you believe it

```bash
ffmpeg -y -i "IMG_3095.MOV" -t 30 -c copy /sdcard/t30.mp4
ffmpeg -y -hwaccel mediacodec -i /sdcard/t30.mp4 \
  -c:v h264_mediacodec -profile:v main -level 4.0 \
  -b:v 2500k -maxrate 2500k -bufsize 5000k -c:a copy /sdcard/t30_out.mp4

# what did we ACTUALLY get?
ls -l /sdcard/t30_out.mp4
ffprobe -v error -select_streams v:0 -show_entries stream=bit_rate,profile -of default=nw=1 /sdcard/t30_out.mp4
```

30 s at a true 2500 kbps ≈ **9 MB** plus 0.5 MB audio. If you get ~18 MB, the
bitrate flag is still being ignored and you need the fallback below.

## Fallback: skip MediaCodec entirely when you need an exact size

Your phone has `libx264` and `libx265` built in, and 8 cores. For short clips this is
perfectly fast and gives you **deterministic** results:

```bash
# quality-targeted (size follows quality, varies a little)
ffmpeg -i "input.mp4" -vf "scale=-2:1080,format=yuv420p" \
  -c:v libx264 -crf 23 -preset veryfast -pix_fmt yuv420p -c:a copy "out.mp4"

# exact-size, two-pass (true CBR, costs double the time)
ffmpeg -y -i "input.mp4" -vf "scale=-2:1080,format=yuv420p" -c:v libx264 -b:v 2500k \
  -pass 1 -passlogfile /sdcard/plog -an -f null /dev/null
ffmpeg -y -i "input.mp4" -vf "scale=-2:1080,format=yuv420p" -c:v libx264 -b:v 2500k \
  -pass 2 -passlogfile /sdcard/plog -pix_fmt yuv420p -c:a copy "out.mp4"

# smallest option — x265, roughly 30% smaller than x264 at equal quality
ffmpeg -i "input.mp4" -vf "scale=-2:1080,format=yuv420p" \
  -c:v libx265 -crf 26 -preset medium -tag:v hvc1 -c:a copy "out.mp4"
```

Note `-tag:v hvc1` on the x265 line — without it Android players often refuse to
play HEVC in an MP4.

**Rule of thumb:** hardware encode for *speed*, software encode for *predictable size*.
Most of your clips are 39 seconds. Just use `libx264 -crf 23 -preset veryfast` and
stop fighting MediaCodec.

---

# 3. Subtitle hard-burn on the phone — the real analysis

## Why it's slow, and why lowering resolution is NOT the fix you might expect

Your movie transcode with **zero software filters** ran at **2.2–2.4×**. That's the
ceiling, and here's the thing: most of that cost is **not** pixel work. It's the
fixed per-frame overhead of FFmpeg handing frames across the MediaCodec boundary
(JNI + gralloc buffer copy) on both the decode and encode side.

That fixed cost is the same at 1080p as at 480p. So shrinking the picture only
attacks the smaller half of the bill:

| Resolution | Pixel work vs 1080p | Realistic speed | 112-min movie |
|---|---|---|---|
| 1080p | 100% | ~1.5× | ~75 min |
| 720p | 44% | ~2.0× | ~56 min |
| 480p | 25% | ~2.2× | ~51 min |

It helps, but it plateaus. Dropping all the way to 480p only buys you ~25 minutes.
**That is not the lever you want.**

## The lever: run several encodes at once

The fixed MediaCodec overhead is per-process. Split the movie into parts, run several
FFmpeg processes in parallel, then glue them back together with a stream copy.

Your Helio G81 has **8 cores**. Running 4 processes should scale well because the
hardware encoder handles them concurrently and the CPU filters spread across cores.

**Expected: 4 processes → roughly 3–4× the throughput → the 112-minute movie in
~20–30 minutes instead of 100.** *(Test this — it's an estimate, not a measurement.)*

Save this as `burn.sh` in Termux:

```bash
#!/data/data/com.termux/files/usr/bin/bash
# burn.sh — parallel subtitle hard-burn for Termux
# usage: ./burn.sh  (edit the variables below first)

MOVIE="The_Love_Hypothesis_1080P.mp4"
SUBS="subtitles.srt"
OUT="Love_Subbed.mp4"
TOTAL=6724          # seconds — get from: ffprobe -v error -show_entries format=duration -of csv=p=0 "$MOVIE"
PARTS=4             # try 2 first, then 4, then 8
HEIGHT=720
BITRATE="1800k"

CHUNK=$(( (TOTAL + PARTS - 1) / PARTS ))

echo "Splitting into $PARTS parts of ~$CHUNK s..."
rm -f reset_*.mp4 burn_*.mp4 concat.txt
ffmpeg -y -i "$MOVIE" -c copy -f segment -segment_time "$CHUNK" \
  -segment_time_delta 1 reset_%02d.mp4

echo "Burning subtitles in parallel..."
for f in reset_*.mp4; do
  idx="${f:6:2}"
  OFFSET=$(( 10#$idx * CHUNK ))
  (
    # setpts shifts timestamps forward so libass looks up the right subtitle
    # cues, then back down again so the output part starts at zero.
    ffmpeg -y -i "$f" \
      -vf "setpts=PTS+$OFFSET/TB,scale=-2:$HEIGHT,subtitles=$SUBS,setpts=PTS-$OFFSET/TB" \
      -c:v h264_mediacodec -profile:v main -level 4.0 \
      -b:v $BITRATE -maxrate $BITRATE -bufsize 5000k \
      -af "aresample=async=1:first_pts=0" -ac 2 -c:a aac -b:a 128k \
      "burn_${idx}.mp4"
  ) &
done
wait

echo "Joining parts..."
ls burn_*.mp4 | sort | sed "s/^/file '/;s/$/'/" > concat.txt
ffmpeg -y -f concat -safe 0 -i concat.txt -c copy "$OUT"

rm -f reset_*.mp4 burn_*.mp4 concat.txt
echo "Done -> $OUT"
ls -lh "$OUT"
```

Then: `chmod +x burn.sh && ./burn.sh`

### Things to be aware of

- **Test with `PARTS=2` first.** Multiple concurrent MediaCodec encoder sessions can
  fail on some drivers. If a part errors, drop back.
- **`-af "aresample=async=1:first_pts=0"`** fixes the small audio clicks you get at
  part boundaries from AAC priming samples. Costs almost nothing.
- **Video joins cleanly** because each part starts with a fresh IDR from the encoder.
- **Test on a short movie first** to confirm subtitles land on the right lines in the
  later parts — that's what the `setpts` shift is doing, and it's the thing most
  likely to need a tweak.
- **Drop `subtitles=` entirely** if you're not burning, and you get the 2.2× ceiling.

### If you'd rather not run a script

Same effect, one part at a time — slower, but far easier to debug:

```bash
CHUNK=1681   # 6724 / 4
OFFSET=0
ffmpeg -y -hwaccel mediacodec -ss $OFFSET -i "$MOVIE" -t $CHUNK -i "$SUBS" \
  -vf "scale=-2:720,subtitles='$SUBS'" \
  -c:v h264_mediacodec -profile:v main -b:v 1800k -maxrate 1800k -bufsize 3600k \
  -c:a aac -b:a 128k part_0.mp4
```

---

# 4. HDR → SDR on the phone (the tone-mapping problem)

`IMG_3095.MOV` is Dolby Vision. Straight to 8-bit H.264 without tone mapping, it will
look **washed out / grey**. MediaCodec will not do this for you — it ignores the
Dolby Vision RPU.

First check your build has `zscale`:

```bash
ffmpeg -filters 2>/dev/null | grep -E "zscale|tonemap|libplacebo"
```

If `zscale` is there, this is the correct conversion:

```bash
ffmpeg -y -hwaccel mediacodec -i "IMG_3095.MOV" \
  -vf "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,\
tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p" \
  -c:v h264_mediacodec -profile:v main -level 4.0 \
  -b:v 2500k -maxrate 2500k -bufsize 5000k -c:a copy "IMG_3095_sdr.mp4"
```

**This breaks the pure-hardware pipeline** — the tone map is CPU work on every frame,
because your MediaCodec can't tone map. On a 39-second clip that's irrelevant. On the
112-minute movie it would hurt, so for that one, burn the HDR to SDR *first* without
subtitles, then add subtitles in a second pass at 720p where the filter work is cheap.

If `zscale` is missing, tell me and I'll give you an alternative.

---

# 5. Your Celeron laptop, for when it's convenient

The Celeron N3350 has Quick Sync that **decodes HEVC Main10** and **encodes H.264** —
so for `IMG_3095.MOV` it can also do a full hardware pass:

```cmd
ffmpeg -hwaccel qsv -hwaccel_output_format qsv -i "IMG_3095.MOV" ^
 -vf "vpp_qsv=format=nv12:out_color_matrix=bt709:out_color_range=limited" ^
 -c:v h264_qsv -b:v 2500k -maxrate 2500k -bufsize 5000k -c:a copy "out.mp4"
```

`vpp_qsv` does the 10-bit → 8-bit conversion **inside the GPU**, which the phone
command above can't do without a CPU round trip. It does **not** tone map HDR — for
that, drop the `-vf` and accept washed-out colour, or accept a CPU step.

---

# Summary

| Job | Do this |
|---|---|
| Exact file size, phone | `-profile:v main -maxrate -bufsize` → if still wrong, use `libx264 -crf` |
| Short clips (like this 39 s one) | `libx264 -crf 23 -preset veryfast` — simpler and predictable |
| Smallest possible file | `libx265 -crf 26 -preset medium -tag:v hvc1` |
| Subtitle burn, phone | Split into 4 parts, run in parallel, concat → ~20–30 min instead of 100 |
| Dolby Vision → SDR | `zscale` + `tonemap` chain, accepts a CPU step |
| 1080p hardware pipeline | Phone handles it fully; only 4K jobs exceed the encoder |

**Tell me which job you're actually doing next** and I'll tighten it to one command
with your real filenames. If you want, paste the output of
`ffmpeg -filters | grep -E "zscale|tonemap"` so I can confirm the tone-map chain
will work before you spend time on it.