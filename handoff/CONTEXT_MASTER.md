# CONTEXT_MASTER — Devices, Constraints & Best Configs (2026-10-14)

> Single file for handoff agent. Everything here is enforced by user corrections. Do NOT re-infer devices or cite Reddit/fabricated sources.

## 1) Devices — Owned vs Discussed

**Owned (act on these):**
- **Tecno Spark 30C — Helio G81** — 12nm 2x Cortex-A75 2.0GHz + 6x A55 1.7GHz, Mali-G52 MC2 820-950MHz, Android 14, daily driver. `encode H.264/H.265 up to 2K@30 FHD@60 HD@120, decode H.264/H.265/VP9 up to 2K@30, audio AIFF/CAF/MP3/MP4/WAV`. TDP 5W. This is the *phone-first* target (user: “I don’t always have laptops”).
- **Intel Celeron N3350 laptop** — owned, lowest spec. Used for Premiere proxies, burn jobs JOBS=2. Software decode only (`no -hwaccel qsv` for preview concat; probe `h264_qsv` via `testsrc` before use). No IdeaPad/HDD/8GB claims.
- **HP EliteBook 840 G6 — i5-8365U / UHD 620** — borrowed/friend laptop reference. Gives 9.21x vs Celeron in benchmarks. i5-8365U WHiskey Lake 8th Gen.
- **Generic 8th Gen friend laptop** — optional, same class.

**Discussed but NOT owned (never assume ownership):**
- **Tecno KM7 Spark 40 Pro+ — Helio G200 (MT6789)** — MT6789 6nm 2x A76 2.2GHz + 6x A55 2.0GHz Mali-G57 MC2 1.1GHz Android 15 HiOS 15 8GB/128-256GB UFS 2.2. Encode same caps as G81 (2K30/FHD60/HD120 H.264/H.265), decode QHD30, no VP9/AV1 encode. Geekbench 1944/582 vs G81 1322/417, Antutu 417944 vs 254779 (~64% faster). User explicitly corrected: comparison only, not owned.

**Other mentions:** Old Android 4.4 KitKat separate phone for legacy testing; non-smart TV via flash drive only (`encode → flash → TV`, FAT32 4GB limit, Baseline 3.1).

## 2) Standing Constraints & Corrections

- Keep `android-emulator-setup.md` current: WHPX NOT Hyper-V, `C:\Android` NOT `%LOCALAPPDATA%\Android`, `MyPixel API34`, `webcam0 HP HD Camera` (16:9→4:3 crop), UDP 59100 via `10.0.2.2` + `adb reverse`, program firewall allow rules (never disable Firewall), no `swiftshader_indirect`, no one webcam to both front/rear, qcow2→raw, `adb shell` file access, Chrome white-page fix.
- Phone workflow default. Compress/screen-record → hardware accel + small + any-Android → H.264 NOT HEVC. WhatsApp Documents not transcoded; 39s CapCut source; judge quality by detail not size.
- VLC: `Automatic/Direct3D11/DXVA2.0/Disable` only; `Decided format: DX11` is *output* not decode proof; Task Manager CPU 80-100% = software, 20-40% = hardware.
- Premiere Pro **2023 (23.6)** menus only (`Edit→Preferences`).
- Android 15 MediaCodec-from-Termux claim is **false** — actually HIDL→AIDL HAL breakage, fixed FFmpeg 8.0. Cite trac 11363/11508 + AOSP git, not Reddit.
- Font always `~/fonts` + `fc-cache -f` + `fc-list` + 2s `fontselect`. User correction: `FONTDIR=/storage/emulated/0/Fonts` caused Arial fallback, never use.
- Subtitle hard-burn: keep `Lato` → `Caveat/Dancing` handwritten, pretty, parallel 3 jobs where possible, 120ms debounce hover, no `-hwaccel qsv` decode with `scale` (black screen → use software scale).
- Metadata preservation: user correction **“not dropping any information, the logo and every metadata must be kept”** → for any podcast/BTSP fix use `-map 0 -map_metadata 0 -map_chapters 0 -c:t copy` (keep `cover.webp` + chapters + COMMENT/PURL). Do NOT strip with `-map 0:v:0`.
- Devices are exactly Tecno G81 + Celeron + EliteBook 840 G6; do NOT invent Ryzen/ZBook/Tiger Lake/AMD A4 etc.
- `This PC/Videos/Editing/VID_20260930_075352_edit.mp4 + VID20260930_075755_edit.mp4` per-folder .bat workflows.

## 3) Verified Best Configurations

### A) Phone-first FFmpeg (Tecno G81)
- **Transcode/screen-record:** `h264_mediacodec` (or `hevc_mediacodec` if size) — `MediaCodec CBR` main, safe_kbps formula for TV 4GB FAT32 limit. See `phone-first-ffmpeg.md` + `bitrate-and-subspeed-fixes.md`.
- **TV file:** `H.264 Baseline 3.1 720p MP4 faststart 4GB` via `subtitle-hard-burn.md`.

### B) Subtitle Hard-Burn (MovieBox / Big Little Lies)
- **Drift fix:** `CHUNK*idx` 8s drift → `awk START+=DUR cumsum` + `setpts` shift. Source `918.92s vs 927s SRT`.
- **3-core:** `burn_parallel_flexible.sh -c 1` + `-c/-i/-s/-o/--height/--bitrate/--font`, temp subs copy, awk, font verify.
- **State:** `context/BigLittleLies-2x05-burn-state.md` + `burnfix-home.sh`/`burn_3core-home.sh` (awk-only, no `bc`, `HOME` vs MovieBox wd). Previews `preview-caveat.png`.

### C) Premiere Pro 23.6 Proxies (Celeron)
- `720p CFR` `fps` filter `-map 0` `QSV23 / libx264 crf23` Per-folder `.bat` for owned `VID_...mp4` files. No IdeaPad/HDD cites. See `premiere-proxy-guide.md`.

### D) Hover Preview Generation
- `preview-generation-guide.md` v2: `ffprobe duration` → `8/24/40/58/76/90%` timestamps `scale=-2:360 fps=24 -an libx264 ultrafast crf30` (opt `h264_qsv global_quality 30` if `testsrc` probe), `JOBS=2` Celeron /3 i5, `concat -c copy` ~1.3s Celeron, `requestPreview()` unshift+120ms debounce, **no `-hwaccel qsv` decode**.

### E) Emulator
- `android-emulator-setup.md`: WHPX `C:\Android` `MyPixel` webcam0 `10.0.2.2` `adb reverse` UDP 59100.

### F) BTSP Podcasts #1/#95/#96 Fix (Authoritative)
```
Podcast #1: .../Podcasts/Our Villain Origin Story - Beautiful Twin Sisters Podcast #1.mkv 52:10 VP9+AAC44100+cover.webp 18 chapters
Issue: XPlayer 0:00 frozen (missing Cues / Tracks-after-Clusters isSeekable=false) + silent AAC (malformed A_AAC CodecPrivate extradata_size 0/1)
Fix: re-mux Cues + rebuild private:
  ffmpeg 8.1.2 -i in.mkv -map 0 -map_metadata 0 -map_chapters 0 -c:v copy -c:a aac -profile:a aac_low -b:a 128k -ar 48000 -ac 2 -c:t copy -movflags +faststart out.mp4
Proof: AUDIOFIX.mkv 93915 frames 358966KiB extradata_size=5 Qavg537 confirmed on Tecno Spark 30C (time+audio)
#96: same but mkv→mp4 c:v copy -tag:v vp09 c:a aac_low 128k 48k faststart (VP9 stays, no quality loss, intermediate mkvmerge fixed Cues only, ffmpeg copy alone failed due DASH iso6mp41 no PTS)
Summary: context/BTSP-podcasts-fix-summary.md
```

### G) GTA V 4K60 YouTube DASH (Current Thread)
```
Video: matroska,webm google/video-file VP9 Profile0 yuv420p bt709 1920x1080 60fps 3546kb/s 20:28:02.33 ~32.6GB
Audio: mp4 dash iso6mp41 Google aac LC 44100 stereo 128k 20:28:02.37 ~1.1GB
WebM+AAC mixed DASH: pipe | vlc not seekable (live stream), VLC x86 input-slave never seeks well.
Zero-space seek: vlc_http_seek_v3.py fragmented MP4 frag_keyframe+empty_moov copy, handling Range (probe 5MB rule, probesize 32M)
Phone-conventional (copy whenever possible, large file):
  ffmpeg -y -i video.webm -i audio.m4a -map 0:v:0 -map 1:a:0 -map_metadata 1 -map_chapters 1 -c:v copy -tag:v vp09 -c:a aac -profile:a aac_low -b:a 128k -ar 48000 -ac 2 -movflags +faststart+use_metadata_tags out.mp4
  (video copy instant, audio rebuild 48k; -c:a copy keeps 44.1k but silent risk)
VLC x86 path: C:\Program Files (x86)\VideoLAN\VLC\vlc.exe
```

### H) VLCFlix
- `1.1.13 39a03f7` software-only, no `-hwaccel` (auto_scale_0 black screen risk), verified at `/tmp/vlcflix/repo` (gone after reboot, doc persists).

## 4) Key Results to Preserve
- Tecno G81 0.7x burn → G200 ~1.1-1.3x, no new codecs, AV1 via libdav1d CPU.
- Premiere QSV vs libx264 tradeoffs, fps filter for CFR.
- All guides are in `/home/user/*.md` — pack them verbatim.

## 5) Errors & Dead Ends (Do Not Retry)
QSV PDF 2pp garbled, Gemini moov loop, -hwaccel qsv+subs fails, HEVC decode -10000→software, WhatsApp not transcoded, Hypervisor obsolete, swiftshader, mount userdata.img running, netsh separately, Premiere IdeaPad cites, CHUNK*idx drift, bc missing, FONTDIR /storage fallback, podcast copy-only retains bad private, etc.

## 6) External Sources
GitHub Gemini PDF 57pp, termux #21264 trac 11363/11508 AOSP, devicespec/nanoreview, salivity mkv Cues, androidx isSeekable, developer.android AAC/Opus containers, matroska codec specs, ExoPlayer 1229/1511.

---
*This master + guides/ + context/ = complete handoff.*
