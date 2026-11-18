# Full Context — QSV / FFmpeg Video Optimization

Source: Gemini chat export, 2026-10-14 (two PDFs, same conversation, 59 exchanges).
Full text saved at `context/full_a.txt` (styled, 79pp) and `context/full_b.txt` (flat, 26pp).

---

## The Machines (all of them)

| Device | Chip | Media Engine | OS |
|---|---|---|---|
| "HomePC" | **i5-8365U** (Whiskey Lake, 8th gen, 4C/8T, 15W) | Intel UHD 620, QSV | Windows 10 |
| EliteBook 845 G7 | **Ryzen 5 PRO 4650U** (Zen 2, 6C/12T, 15W) | AMD VCN 2.1 | Win 10 |
| ZBook 17 G3 | **Xeon E3-1535M v5** (Skylake-SP, 4C/8T, 45W) | QSV + HEVC enc | Win 10 |
| ZBook Firefly 14 G8 | **i5-1145G7** (Tiger Lake, 4C/8T, 28W) | Iris Xe, QSV + AV1 dec | Win 10 |
| EliteBook x360 1040 G5 | i7-8650U (8th gen, 4C/8T) | UHD 620 | Win 10 |
| "Potato" | **Celeron N3350** (Apollo Lake, 2C/2T, 6W) | HD 500, QSV | Win 10, battery 35,000 → 21,660 mWh |
| Old relic | **AMD A4-4300M** (Trinity, 2012) | Radeon HD 7420G | Win 10 |
| Tecno Spark 30C | **Helio G81** (8C, 12nm) | Mali-G52, MediaCodec | Android 14, Termux |
| Old TV box | **Snapdragon 410** (4× A53, 2013) | Adreno 306 | Android 4.4 |

FFmpeg on PC: **9.0.1 full build (gyan.dev)**, Windows. FFmpeg on phone: v6.x in Termux.

---

## The Source Files

**A. iPhone 12 Pro Max, 4K HDR (the original problem)**
```
IMG_7774.MOV / IMG_7789.MOV
Duration 03:28.99 / 03:36.35
Video: hevc (Main 10), yuv420p10le(tv, bt2020nc/arib-std-b67), 3840x2160, 30fps, 36–40 Mbps
Side data: DOVI config (Dolby Vision Profile 8), Display Matrix rotation -90°
Audio: aac LC 44100 stereo 187 kb/s
```
iOS 18.7.8.

**B. WhatsApp "P.judah (1).mp4" / "Thanks (1).mp4" — the cursed format**
```
h264 (High 10), yuv420p10le(tv, bt709), 1440x2560, 60fps, ~5.1 Mbps, 74s
Audio: aac LC 44100 stereo 129 kb/s
Encoded by Lavc63.1.101 libx264  ← someone forgot -pix_fmt yuv420p
```

**C. "tenure.mp4"** — h264 High, yuv420p, 720x1280, 30fps, 2:51. Image sideways, no rotation metadata.

**D. "The_Love_Hypothesis_1080P.mp4" — the movie**
```
hevc (Main), yuv420p(tv, bt709), 1920x1040 [SAR 481:480 DAR 37:20], 24fps, 756 kb/s
Audio: aac LC 48000 stereo 192 kb/s
Duration 01:52:04.80  →  765 MB original
```

---

## What Actually Happened

### 1. The iPhone command crashed — twice, identically
```
Incompatible pixel format 'yuv420p' for codec 'h264_qsv', auto-selecting format 'nv12'
Stream #0:0 -> #0:0 (hevc (hevc_qsv) -> h264 (h264_qsv))   ← decode worked fine
Impossible to convert between the formats supported by the filter
  'graph -1 input from stream 0:0' and the filter 'auto_scale_0'
[vf#0:0] Error reinitializing filters!
[vf#0:0] Task finished with error code: -40 (Function not implemented)
Conversion failed!
```
**Cause:** `-hwaccel qsv` emits frames in `qsv` pixel format. A software `-vf scale`
filter cannot consume them — FFmpeg needs an explicit `hwdownload`. Plus `-pix_fmt
yuv420p` is not a valid input format for `h264_qsv`.

Side effect: 0-byte `_android.mp4` files were created, then the `for %f in (*.mov *.mp4)`
loop tried to re-ingest them → `moov atom not found`.

**Working fallback used:** drop `-hwaccel qsv`, add `format=nv12`, loop `*.MOV` only.
~6 min per 3.5-min 4K clip (software 4K Main10 decode was the bottleneck, not QSV encode).

**Never-actually-run "proper" fix:** `-vf "scale_qsv=w=-2:h=1080:format=nv12"`.

### 2. The 10-bit H.264 problem
Files B won't play on Android. Root cause: **H.264 High 10 profile has essentially no
hardware decoder anywhere** — Intel, AMD, Qualcomm, MediaTek all skipped it. Correct.

### 3. The movie conversions (actual measured results)
- Phone (`h264_mediacodec`, with `-vf scale=-2:720`, `-profile:v baseline -b:v 1500k`): **2.03x → 1.96x**, output 1.96 MB/frame-set... output ~17.5 MB for 66s
- Phone (`h264_mediacodec`, no scaling, `-b:v 2500k`): **2.4x → ~2.2x**
- Celeron (`-hwaccel qsv -hwaccel_output_format qsv -c:v h264_qsv -b:v 2500k`): **1.89 GB output**
- Phone same job: **3.66 GB output** ← MediaCodec ignored `-b:v 2500k`

### 4. Old dumb LED TV → hard-burn subtitles needed (no .srt support)

---

## The Working Commands (accumulated)

**Intel, iPhone 4K10 Dolby Vision → 1080p Android (the one that never got tested):**
```cmd
for %f in (*.MOV) do ffmpeg -hwaccel qsv -i "%f" -vf "scale_qsv=w=-2:h=1080:format=nv12" -c:v h264_qsv -global_quality 24 -preset veryslow -c:a aac -b:a 128k "%~nf_android.mp4"
```

**Intel, iPhone 4K10 → software decode path (what actually ran, ~6 min/clip):**
```cmd
for %f in (*.MOV) do ffmpeg -i "%f" -vf "scale=-2:1080,format=nv12" -c:v h264_qsv -global_quality 24 -preset veryslow -c:a aac -b:a 128k "%~nf_android.mp4"
```

**Celeron N3350, pure HEVC→H.264 hardware (movie):**
```cmd
ffmpeg -hwaccel qsv -hwaccel_output_format qsv -i "%USERPROFILE%\Downloads\The_Love_Hypothesis_1080P.mp4" -c:v h264_qsv -b:v 2500k -c:a copy "%USERPROFILE%\Downloads\Love_QSV.mp4"
```

**Phone (Termux), movie → KitKat-safe, no CPU bottleneck:**
```cmd
ffmpeg -hwaccel mediacodec -i "/storage/67FE-19FE/Download/MovieBox/The_Love_Hypothesis_1080P.mp4" -c:v h264_mediacodec -b:v 2500k -c:a copy "/storage/67FE-19FE/Download/MovieBox/Love_1080p_Fast.mp4"
```

**Phone (Termux), 10-bit H.264 fix → 1080p 8-bit, hardware encode:**
```cmd
ffmpeg -i "input.mp4" -vf "scale=-2:1080,format=yuv420p" -c:v h264_mediacodec -b:v 15000k -c:a copy "out.mp4"
```

**Phone (Termux), KitKat-safe downscale:**
```cmd
ffmpeg -hwaccel mediacodec -i "input.mp4" -vf "fps=30,scale=-2:720,format=yuv420p" -c:v h264_mediacodec -profile:v baseline -b:v 2000k -c:a aac -b:a 128k "out.mp4"
```

**Phone (Termux), hard-burn subs:**
```cmd
ffmpeg -hwaccel mediacodec -i "The_Love_Hypothesis_1080P.mp4" -vf "subtitles=subtitles.srt" -c:v h264_mediacodec -b:v 2500k -c:a copy "Love_Hardsubbed.mp4"
```

---

## Where The Chat Ended

User: *"What about this laptop? Compare it with the Ryzen and the ZBook — HP ZBook
Firefly 14 G8, Intel Core i5-1145G7."*

Gemini answered (Firefly vs Ryzen vs Xeon). Thread ended there.

---

## ⚠️ Known Errors in the Gemini Answers — Do Not Propagate

1. **"6th Gen Skylake can't hardware-decode 10-bit HEVC."** Wrong. Skylake introduced
   HEVC Main/Main10 *decode*. What Kaby Lake added was HEVC *encode*.

2. **"Celeron N3350 QSV encodes H.265."** Wrong. Apollo Lake QSV = HEVC decode only,
   H.264 encode/decode. No HEVC encoder.

3. **"Xeon E3-1535M v5 is blind to 10-bit HEVC in hardware."** Wrong. Skylake-SP Xeon
   Scalable has HEVC Main10 decode *and* encode. (AV1: correctly absent.)

4. **"Use `-hwaccel amf` on the AMD A4-4300M."** Wrong. AMF requires RDNA (Vega+).
   The A4-4300M is 2012 Trinity/Radeon 7420G. No AMF, no usable FFmpeg hwaccel.

5. **"Helio G81 cannot process 4K in hardware."** Half wrong. It decodes 4K; its
   hardware **encoder** caps at 1080p. The real blocker for file B was 10-bit H.264,
   not resolution.

6. **`-preset veryslow` blamed for the 6-minute runtime.** Wrong. The bottleneck was
   software decode of 4K HEVC Main10 + software `scale`. QSV encode was never the
   constraint.

7. **"MediaCodec panics and dumps data when starved."** Unverified framing. The
   1.89 GB vs 3.66 GB gap is more likely MediaCodec ignoring/poorly honouring `-b:v`,
   not adaptive bit-dumping.

8. **HDR claim.** Log shows **Dolby Vision Profile 8**, bt2020nc — Gemini said
   "Dolby Vision/HLG". HLG is wrong.

9. **Never addressed: tone mapping.** The 4K10 bt2020nc Dolby Vision source downscaled
   to 8-bit nv12 will look washed out / wrong. `scale_qsv` does not tone-map. This is
   the biggest unresolved technical issue in the whole thread.

10. **Chromium/Netflix-grade advice missing:** `zscale=t=linear:npl=100,tonemap=hable,
    format=yuv420p` for a correct HDR→SDR conversion.