# Full Context — QSV / FFmpeg Video Optimization

Source: Gemini chat export, 2026-10-14 (two PDFs, same conversation, 59 exchanges).
The PDFs and the full-text dumps made from them (`full_a.txt`, `full_b.txt`) were
**not** included in this repository; this summary is all that survives of them.

---

## The Machines (all of them)

> Every machine **mentioned in the Gemini chat**. Ownership is settled in
> `../CONTEXT_MASTER.md`: the owner has only the **Tecno Spark 30C** and the
> **Celeron N3350** laptop. The EliteBook 840 G6 (i5-8365U) and x360 1040 G5
> (i7-8650U) are borrowed; the rest were discussed, not owned.

| Device | Chip | Media Engine | OS |
|---|---|---|---|
| "HomePC" | **i5-8365U** (Whiskey Lake, 8th gen, 4C/8T, 15W) | Intel UHD 620, QSV | Windows 10 |
| EliteBook 845 G7 | **Ryzen 5 PRO 4650U** (Zen 2, 6C/12T, 15W) | AMD VCN 2.1 | Win 10 |
| ZBook 17 G3 | **Xeon E3-1535M v5** (Skylake-H mobile, 4C/8T, 45W) | QSV + HEVC 8-bit enc | Win 10 |
| ZBook Firefly 14 G8 | **i5-1145G7** (Tiger Lake, 4C/8T, 28W) | Iris Xe, QSV + AV1 dec | Win 10 |
| EliteBook x360 1040 G5 | i7-8650U (8th gen, 4C/8T) | UHD 620 | Win 10 |
| "Potato" | **Celeron N3350** (Apollo Lake, 2C/2T, 6W) | HD 500, QSV | Win 10, battery 35,000 → 21,660 mWh |
| Old relic | **AMD A4-4300M** (Trinity, 2012) | Radeon HD 7420G | Win 10 |
| Tecno Spark 30C | **Helio G81** (8C, 12nm) | Mali-G52, MediaCodec | Android 14, Termux |
| Old phone ("TV box" in the chat) | **Snapdragon 410** (4× A53, 2013) | Adreno 306 | Android 4.4 |

> OS column is as stated in the Gemini chat. "HomePC" has the same i5-8365U as the
> borrowed EliteBook 840 G6, which `android-emulator-setup.md` records on **Windows 11
> build 26200**; the notes never say whether HomePC *is* that EliteBook, so treat the
> OS of each machine as unconfirmed until checked (`winver`).

FFmpeg on PC: **9.0.1 full build (gyan.dev)**, Windows. FFmpeg on phone: v6.x in
Termux at the time of this chat; the later BTSP notes (2026-11-11) record 8.1.2.

> **Termux and `-hwaccel mediacodec`:** in a Termux build there is no Java VM, so
> `-hwaccel mediacodec` fails with `No Java virtual machine has been registered`
> (FFmpeg trac #11508, still open). The `h264_mediacodec` / `hevc_mediacodec`
> *codecs* fall back to the NDK API automatically and work. The phone commands
> below therefore decode in software (no `-hwaccel`); to try hardware decode,
> put `-c:v hevc_mediacodec` (or `h264_mediacodec`) **before** `-i` — untested
> on the Spark 30C.

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
ffmpeg -i "/storage/67FE-19FE/Download/MovieBox/The_Love_Hypothesis_1080P.mp4" -c:v h264_mediacodec -b:v 2500k -c:a copy "/storage/67FE-19FE/Download/MovieBox/Love_1080p_Fast.mp4"
```

**Phone (Termux), 10-bit H.264 fix → 1080p 8-bit, hardware encode:**
```cmd
ffmpeg -i "input.mp4" -vf "scale=-2:1080,format=yuv420p" -c:v h264_mediacodec -b:v 15000k -c:a copy "out.mp4"
```

**Phone (Termux), KitKat-safe downscale:**
```cmd
ffmpeg -i "input.mp4" -vf "fps=30,scale=-2:720,format=yuv420p" -c:v h264_mediacodec -profile:v baseline -b:v 2000k -c:a aac -b:a 128k "out.mp4"
```

**Phone (Termux), hard-burn subs:**
```cmd
ffmpeg -i "The_Love_Hypothesis_1080P.mp4" -vf "subtitles=subtitles.srt" -c:v h264_mediacodec -b:v 2500k -c:a copy "Love_Hardsubbed.mp4"
```

---

## Where The Chat Ended

User: *"What about this laptop? Compare it with the Ryzen and the ZBook — HP ZBook
Firefly 14 G8, Intel Core i5-1145G7."*

Gemini answered (Firefly vs Ryzen vs Xeon). Thread ended there.

---

## ⚠️ Gemini Answers Checked — What Holds and What Does Not

Re-checked 2026-11-18 against Intel's Quick Sync Video codec tables (as compiled
on Wikipedia), AMD AMF/VCE documentation and MediaTek Helio G81 spec sheets. An
earlier version of this list "corrected" items 1, 2, 3, 5 and 8 wrongly.

1. **"6th Gen Skylake can't hardware-decode 10-bit HEVC."** Essentially right.
   Skylake has fixed-function HEVC **8-bit** decode *and* encode; its Main10
   decode is only hybrid (partly shaders/CPU). Full fixed-function HEVC 10-bit
   decode + encode (and VP9) arrived with Kaby Lake.

2. **"Celeron N3350 QSV encodes H.265."** Right. Apollo Lake (Gen9 LP) does HEVC
   8-bit encode and decode, HEVC Main10 decode, VP9 8-bit decode, and H.264
   encode/decode. No VP9 encode, no AV1.

3. **"Xeon E3-1535M v5 is blind to 10-bit HEVC in hardware."** Essentially right.
   It is a Skylake-H mobile chip (not Skylake-SP): HEVC 8-bit encode/decode,
   Main10 decode hybrid only, no Main10 encode. (AV1: correctly absent.)

4. **"Use `-hwaccel amf` on the AMD A4-4300M."** Wrong. The A4-4300M is a 2012
   Trinity APU with a TeraScale 3 Radeon HD 7420G; AMF supports only GCN-or-newer
   GPUs (VCE/VCN, current drivers). No AMF encode on that machine. At most,
   H.264 *decode* via `-hwaccel dxva2`/`d3d11va` on its UVD block.

5. **"Helio G81 cannot process 4K in hardware."** Right per spec sheets: video
   decode tops out at 2K@30 (1080p60) and encode at 2K@30. File B (1440×2560,
   60 fps, H.264 High 10) fails on two counts — 10-bit H.264 has no hardware
   decoder, and QHD at 60 fps exceeds the 2K@30 decode limit.

6. **`-preset veryslow` blamed for the 6-minute runtime.** Wrong. The bottleneck was
   software decode of 4K HEVC Main10 + software `scale`. QSV encode was never the
   constraint.

7. **"MediaCodec panics and dumps data when starved."** Unverified framing. The
   1.89 GB vs 3.66 GB gap is more likely MediaCodec ignoring/poorly honouring `-b:v`,
   not adaptive bit-dumping.

8. **HDR claim.** Gemini said "Dolby Vision/HLG", and that is right: the log
   shows Dolby Vision Profile 8 with transfer `arib-std-b67`, which *is* HLG —
   iPhones record Dolby Vision 8.4 on an HLG base layer.

9. **Never addressed: tone mapping.** The 4K10 bt2020nc Dolby Vision source downscaled
   to 8-bit nv12 will look washed out / wrong. `scale_qsv` does not tone-map. This is
   the biggest unresolved technical issue in the whole thread.

10. **Chromium/Netflix-grade advice missing:** `zscale=t=linear:npl=100,tonemap=hable,
    format=yuv420p` for a correct HDR→SDR conversion.