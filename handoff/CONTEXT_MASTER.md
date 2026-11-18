# CONTEXT_MASTER — Devices, Constraints & Best Configs

> Single file for the handoff agent. First written 2026-10-14; it also covers jobs
> up to 2026-11-11 (Big Little Lies 2x05, BTSP podcasts). Re-checked and corrected
> 2026-11-18 — corrections are marked. Everything under §1–2 comes from user
> corrections: do NOT re-infer devices or cite Reddit/fabricated sources.

## 1) Devices — Owned vs Borrowed vs Discussed

**Owned (act on these — only these two):**
- **Tecno Spark 30C — Helio G81** (MT6769J) — 12nm 2x Cortex-A75 2.0GHz + 6x A55 1.7GHz, Mali-G52 MC2 820-950MHz, Android 14, daily driver. `encode H.264/H.265 up to 2K@30 FHD@60 HD@120, decode H.264/H.265/VP9 up to 2K@30, audio AIFF/CAF/MP3/MP4/WAV`. TDP 5W. **No 4K decode** (some guides said 4K; that was wrong). This is the *phone-first* target (user: "I don't always have laptops").
- **Intel Celeron N3350 laptop** — Apollo Lake, 2C/2T, HD Graphics 500 (Gen9 LP). Lowest spec. Used for Premiere proxies and burn jobs (JOBS=2). Quick Sync: H.264 encode/decode, HEVC 8-bit encode/decode, HEVC Main10 decode, VP9 8-bit decode; no AV1. The hardware *decoder* exists, but the rule is **no `-hwaccel qsv` decode in a pipeline with CPU filters** (`scale`, `subtitles`, previews) — black screen / filter errors. Probe `h264_qsv` with a `testsrc` encode before use. No IdeaPad/HDD/8GB claims.

**Borrowed (never plan around these):**
- **HP EliteBook 840 G6 — i5-8365U / UHD 620** (Whiskey Lake, 4C/8T, 15W) — the emulator guide was validated on it. *(Corrected: an earlier version listed it under "Owned".)*
- **HP EliteBook x360 1040 G5 — i7-8650U / UHD 620** — source of the **9.21×** subtitle-burn figure (`h264_qsv`, 5:18 for ~49 min of video; see `guides/subtitle-hard-burn.md` §2). *(Corrected: the old text credited 9.21× to the 840 G6 "vs Celeron". It was measured on this machine and is a speed relative to real time, not a ratio to the Celeron.)* Same chip class as the 840 G6, so expect similar QSV speed there; the Celeron is far slower.
- The old "generic 8th Gen friend laptop — optional, same class" entry is most likely this x360; either way it is borrowed.

**Discussed but NOT owned (never assume ownership):**
- **Tecno KM7 Spark 40 Pro+ — Helio G200 (MT6789)** — 6nm 2x A76 2.2GHz + 6x A55 2.0GHz, Mali-G57 MC2 1.1GHz, Android 15 HiOS 15, 8GB/128-256GB UFS 2.2. Encode same caps as G81 (2K30/FHD60/HD120 H.264/H.265), decode QHD30, no VP9/AV1 encode. Geekbench 1944/582 vs G81 1322/417, AnTuTu 417944 vs 254779 (~64% faster). User explicitly corrected: comparison only, not owned.
- Machines from the Gemini chat (Ryzen 5 PRO 4650U, ZBook Xeon E3-1535M v5, ZBook Firefly i5-1145G7, AMD A4-4300M) — not the user's. See `guides/qsv-ffmpeg-context.md`.

**Playback targets:** old Android 4.4 KitKat phone (separate, legacy testing); non-smart TV played via flash drive only (`encode → flash → TV`, FAT32 4 GB limit, H.264 Baseline 3.1, 720p, hard-burned subs).

## 2) Standing Constraints & Corrections

- `android-emulator-setup.md` rules: WHPX NOT Hyper-V, `C:\Android` NOT `%LOCALAPPDATA%\Android`, `MyPixel` API 34, `webcam0` = HP HD Camera (16:9 → 4:3 centre crop), never map one webcam to both front and rear, no `swiftshader_indirect`, AEHD not needed (being retired 31 Dec 2026; HAXM already removed).
  - *Not yet written into that guide* (from the original sessions, unverified here): UDP to a host listener on port 59100 via `10.0.2.2`, program-specific Windows Firewall allow rules (never disable the firewall), qcow2→raw, `adb shell` file access, Chrome white-page fix. Note: the emulator reaches the host's UDP port directly at `10.0.2.2`; `adb reverse` forwards TCP/local sockets only, **not UDP**, so it is not part of that path.
- Phone workflow default. Compress/screen-record → hardware encode + small + any-Android → H.264 NOT HEVC. WhatsApp Documents not transcoded; 39s CapCut source; judge quality by detail not size.
- VLC: `Automatic/Direct3D11/DXVA2.0/Disable` only; `Decided format: DX11` is *output* not decode proof; Task Manager CPU 80-100% = software, 20-40% = hardware.
- Premiere Pro **2023 (23.6)** menus only (`Edit→Preferences`).
- MediaCodec from Termux — two separate issues, cite trac 11363/11508 + the FFmpeg commit, not Reddit:
  - The Android 15 MediaCodec-from-Termux breakage is not a Termux ban: it came from the HIDL→AIDL HAL switch, and FFmpeg fixed it by starting a binder thread pool (commit `eccbbd5`), first released in **FFmpeg 8.1** *(corrected: not 8.0)*. The phone's Termux FFmpeg 8.1.2 has it.
  - `-hwaccel mediacodec` still fails in Termux: `No Java virtual machine has been registered` (trac #11508, open). The `h264_mediacodec`/`hevc_mediacodec` codecs fall back to the NDK API and work — so decode in software (or `-c:v hevc_mediacodec` before `-i`), encode with `h264_mediacodec`.
- MediaCodec bitrate: `-profile:v main` does **not** switch MediaCodec to CBR, and `h264_mediacodec` ignores `-maxrate`/`-bufsize`. Use `-bitrate_mode cbr -b:v …` and verify on a 30–60 s clip; if it still misses, two-pass `libx264`. *(Corrected: older notes called `-profile:v main` the CBR fix.)*
- Font always `~/fonts` + `fc-cache -f` + `fc-list` + 2s `fontselect` test — read the font **path after `->`** in the `fontselect:` line, not the requested name. User correction: `FONTDIR=/storage/emulated/0/Fonts` caused Arial fallback, never use.
- Subtitle hard-burn: keep `Lato` → `Caveat/Dancing` handwritten, pretty, parallel 3 jobs where possible, 120ms debounce hover, no `-hwaccel qsv` decode with `scale` (black screen → use software scale).
- Metadata preservation: user correction **"not dropping any information, the logo and every metadata must be kept"** → for any podcast/BTSP fix keep `cover.webp` + chapters + tags. `-map 0 … -c:t copy` to mp4 **fails** (mp4 cannot hold the webp attachment) and `-map 0:v:0 -map 0:a:0` alone drops the cover — use the two-step command in §3 F. *(Corrected: the earlier `-c:t copy` advice did not work.)*
- Devices are exactly: owned Tecno G81 + Celeron N3350; borrowed EliteBook 840 G6 + x360 1040 G5; do NOT invent Ryzen/ZBook/Tiger Lake/AMD A4 etc. as the user's.
- `This PC/Videos/Editing/VID_20260930_075352_edit.mp4 + VID20260930_075755_edit.mp4` per-folder .bat workflows.

## 3) Verified Best Configurations

### A) Phone-first FFmpeg (Tecno G81)
- **Transcode/screen-record:** `h264_mediacodec` (or `hevc_mediacodec` if size) with `-profile:v main -bitrate_mode cbr -b:v …`; safe_kbps formula for the TV's 4 GB FAT32 limit. See `phone-first-ffmpeg.md`, `bitrate-and-subspeed-fixes.md` and `subtitle-hard-burn.md` §9.
- **TV file:** `H.264 Baseline 3.1 720p MP4 faststart <4GB` via `subtitle-hard-burn.md`.

### B) Subtitle Hard-Burn (MovieBox / Big Little Lies)
- **Drift fix:** stream-copy split + `OFFSET = idx*CHUNK` drifted 8 s → offsets = running sum of the real part durations (`awk`), plus the `setpts` shift. Source: first part really `918.92s` (later log `930.018s`) vs the assumed `927s`.
- **3-core:** `burn_parallel_flexible.sh -c N` + `-i/-s/-o/--height/--bitrate/--encoder h264_mediacodec|libx264/--font/--fontsize/--fontdir`, temp subs copy (in `$TMPDIR`, else `$PREFIX/tmp`), awk, font verify. It cuts with `-ss` before `-i` and re-encodes, so its fixed offsets are exact. *(Corrected 2026-11-18: MediaCodec now gets `-level 4 -bitrate_mode cbr` instead of `-level 4.0` and the ignored `-maxrate`/`-bufsize`; `--bufsize` applies to libx264 only; the font check reads the font after `->` and stops on a fallback; a failed part stops the join. Same fixes in `burnfix-home.sh`. Tested end to end with `--encoder libx264` on Linux; MediaCodec path not run here.)*
- **State:** `context/pdfs-and-state/BigLittleLies-2x05-burn-state.md` + `guides/burnfix-home.sh` (the fix) / `guides/burn_3core-home.sh` (the buggy original) — awk-only, no `bc`, work in `$HOME` not MovieBox. Previews `guides/preview-caveat.png`, `guides/preview-dancing.png`.

### C) Premiere Pro 23.6 Proxies (Celeron)
- `720p CFR` via the `fps` filter at the source's **exact** `r_frame_rate` (e.g. `30000/1001`, not 30), `-map 0`, `QSV global_quality 23 / libx264 crf 23`. Per-folder `.bat` for the owned `VID_...mp4` files. No IdeaPad/HDD cites. See `premiere-proxy-guide.md`.

### D) Hover Preview Generation
- `preview-generation-guide.md` v2: `ffprobe duration` → `8/24/40/58/76/90%` timestamps, `scale=-2:360 fps=24 -an libx264 ultrafast crf30` (optional `h264_qsv global_quality 30` if the `testsrc` probe passes), `JOBS=2` Celeron / 3 i5. Estimated ~1.3 s of extraction per preview on the Celeron with JOBS=2, plus ~0.05 s for `concat -c copy` *(corrected: the 1.3 s is extraction, not the concat)*. `requestPreview()` unshift + 120ms debounce, **no `-hwaccel qsv` decode**.

### E) Emulator
- `android-emulator-setup.md`: WHPX, `C:\Android`, `MyPixel`, `webcam0` on the back slot. (UDP 59100 via `10.0.2.2`: see §2 — not in the guide yet.)

### F) BTSP Podcasts #1/#95/#96 Fix (Authoritative)
```
Podcast #1: .../Podcasts/Our Villain Origin Story - Beautiful Twin Sisters Podcast #1.mkv 52:10 VP9+AAC44100+cover.webp 18 chapters
Issue: XPlayer 0:00 frozen (missing Cues / Tracks-after-Clusters isSeekable=false) + silent AAC (malformed A_AAC CodecPrivate)
What fixed playback on the phone (ffmpeg 8.1.2, confirmed on Tecno Spark 30C: time + audio):
  ffmpeg -y -i in.mkv -map 0:v:0 -map 0:a:0 -map_metadata 0 -map_chapters 0 -c:v copy -tag:v vp09 -c:a aac -profile:a aac_low -b:a 128k -ar 48000 -ac 2 -movflags +faststart out.mp4
  -> plays, but DROPS cover.webp (breaks the keep-the-logo rule)
Recommended (keeps cover + chapters + standard tags; tested on ffmpeg 9.0.2, not yet on the phone):
  ffmpeg -y -dump_attachment:t:0 cover.webp -i in.mkv      (the "At least one output file" message is expected)
  ffmpeg -y -i in.mkv -i cover.webp -map 0:v:0 -map 0:a:0 -map 1:v:0 -map_metadata 0 -map_chapters 0 -c:v:0 copy -tag:v:0 vp09 -c:v:1 mjpeg -disposition:v:1 attached_pic -c:a aac -profile:a aac_low -b:a 128k -ar 48000 -ac 2 -movflags +faststart out.mp4
  (-tag:v:0 vp09 only for VP9; PURL and other non-standard tags are dropped by mp4 — +use_metadata_tags keeps them but loses the cover)
#96: same, no chapters. An mkvmerge intermediate fixed Cues only; ffmpeg -c copy alone failed (per the summary: the bad AAC private is kept as-is).
Summary: context/pdfs-and-state/BTSP-podcasts-fix-summary.md
```
*(Corrected: the earlier "authoritative" line used `-map 0 … -c:t copy` into mp4, which aborts with `Could not find tag for codec none`; its "proof" cited an `AUDIOFIX.mkv`, i.e. an mkv, not the mp4 command.)*

### G) GTA V 4K60 YouTube DASH (Current Thread)
```
Video: matroska,webm google/video-file VP9 Profile0 yuv420p bt709 1920x1080 60fps 3546kb/s 20:28:02.33 ~32.6GB
Audio: mp4 dash iso6mp41 Google aac LC 44100 stereo 128k 20:28:02.37 ~1.1GB
WebM+AAC mixed DASH: pipe | vlc not seekable (live stream), VLC x86 input-slave never seeks well.
Zero-space seek: guides/vlc_http_seek_v3.py fragmented MP4 frag_keyframe+empty_moov copy, handling Range (probe 5MB rule, probesize 32M)
Phone-conventional (copy whenever possible, large file):
  ffmpeg -y -i video.webm -i audio.m4a -map 0:v:0 -map 1:a:0 -map_metadata 1 -map_chapters 1 -c:v copy -tag:v vp09 -c:a aac -profile:a aac_low -b:a 128k -ar 48000 -ac 2 -movflags +faststart+use_metadata_tags out.mp4
  (video copy instant, audio rebuild 48k; -c:a copy keeps 44.1k but silent risk)
VLC x86 path: C:\Program Files (x86)\VideoLAN\VLC\vlc.exe
```
Note: `guides/vlc-seek-fix.bat` and `vlc-seek-fix-webm.bat` now find VLC themselves: `%ProgramFiles%`, then `%ProgramFiles(x86)%`, then `where vlc`, and stop with a message if none is found. *(Corrected 2026-11-18: they used to hard-code one path each, 64-bit and x86 respectively.)*

### H) VLCFlix
- `1.1.13 39a03f7` software-only, no `-hwaccel` (auto_scale_0 black screen risk), verified at `/tmp/vlcflix/repo` in the original sandbox (gone after reboot; not in this repository).

## 4) Key Results to Preserve
- Tecno G81 1080p subtitle burn measured ~0.7× → G200 estimated ~1.1-1.3× (an estimate, not measured), no new codecs, AV1 via libdav1d CPU.
- Premiere QSV vs libx264 tradeoffs, fps filter for CFR.
- All guides are in `handoff/guides/` of this repository *(the original note said `/home/user/*.md` in the old sandbox)*.

## 5) Errors & Dead Ends (Do Not Retry)
QSV PDF 2pp garbled, Gemini moov loop, -hwaccel qsv+subs fails, HEVC decode -10000→software, WhatsApp not transcoded, Hypervisor driver (AEHD/HAXM) obsolete, swiftshader, mount userdata.img while running, netsh separately, Premiere IdeaPad cites, CHUNK*idx drift, bc missing, FONTDIR /storage fallback, podcast copy-only retains bad private, `-c:t copy` webp attachment into mp4, `-profile:v main` as a CBR switch, `-hwaccel mediacodec` in Termux.

## 6) External Sources
Gemini chat PDF 57pp and a garbled 2pp QSV PDF (**not included in this repository**; the `full_a.txt`/`full_b.txt` dumps are missing too), termux #21264, FFmpeg trac 11363/11508, FFmpeg commit eccbbd5, AOSP, devicespec/nanoreview, salivity mkv Cues, androidx isSeekable, developer.android AAC/Opus containers, matroska codec specs, ExoPlayer 1229/1511.

---
*This master + guides/ + context/ = complete handoff.*
