# Big Little Lies 2x05 - Burn State - 2026-10-24 19:30 IST

## Source you own
- Movie (external SD): `/storage/67FE-19FE/Download/MovieBox/Big_Little_Lies_1080P_S02_E05.mp4`
  - `1920x1080 h264 High 1336 kb/s 25fps yuv420p 00:46:19.03 2779.03s 1501 kb/s total`
  - `aac 44100Hz stereo 157 kb/s`
- Subs (internal): `~/fonts/subs.srt` in real phone is also `~/fonts`? User copy to check - actually SUBS path in scripts: `/storage/emulated/0/Fonts/subs.srt` per user message, but user clarifies font dir is `~/fonts`. Keeping both as reported: MOVIE on SD, SUBS as given in script, FONTDIR is `~/fonts`.

## Working directory on phone
- All intermediate files live directly in `$HOME` on the Tecno Spark 30C (Helio G81), NOT in MovieBox:
  - `~/burn_00.mp4` = first ~15:18 (930.018s in last log, 918.92s in earlier log - varies with keyframe) - GOOD, keep this
  - `~/burn_01.mp4` `~/burn_02.mp4` = broken due to OFFSET bug (8.08s early)
  - `~/burnfix.sh` = current fix script (was cd to MovieBox, now fixed to cd $HOME)
  - `~/burn_3core.sh` = original 3-way split that caused the 8s drift

## The two bugs hit so far
1. **Sync drift at 15:30**: `OFFSET = idx * CHUNK (927)` assumed exact cuts. Segment with `-c copy` cuts on keyframe (~918-930s), so part 2 subtitles 8.08s too early. Fix = measure the real duration of the first part (`burnfix-home.sh` measures the finished `burn_00.mp4`, falling back to 930.018) and use `OFFSET = cumulative DUR`.
2. **bc: command not found**: Termux on your phone does NOT have `bc`. All `bc` math failed -> `ss: -t` empty -> 3 ffmpeg fails -> only old `burn_00.mp4` was concatenated. Fix = `awk` only.
3. **ls cannot access reset_00.mp4**: `burnfix.sh` did `cd MovieBox` but files are in `$HOME`. Fix = `cd "$HOME"` or absolute `$HOME/burn_00.mp4`.

## Font failure 2026-10-24 ~19:22 - CORRECTED
- Requested: handwritten pretty font, correctly installed by user to `~/fonts/Caveat.ttf` (NOT /storage)
- Used: my bad script set `FONTDIR="/storage/emulated/0/Fonts"` - wrong. Your font was always correctly in `~/fonts` where fontconfig scans.
- Result: wrong font rendered (Arial fallback) - because `fontsdir` pointed to empty/wrong folder, libass never found Caveat despite it being correctly installed. Cost: 12 min wasted.
- Fix: `FONTDIR="$HOME/fonts"` and `fc-cache -f` was already correct in your setup. My `burnfix-home.sh` in workspace is now fixed to `$HOME/fonts`. Also verify `FontName` matches `fc-list` exactly: `fc-list | grep -i caveat` must show `Caveat` family.

## Verified working config for next run (3 cores, same size)
- `HEIGHT=1080` keep same size, `BITRATE=1350k BUFSIZE=2700k` -> total ~1478k, file ~500MB same as source 520MB
- `h264_mediacodec -profile:v main -level 4.0 -g 50`, no `bc`, `awk` for math. **CBR is not actually enforced by these flags**: `-profile:v main` only sets the profile and `h264_mediacodec` ignores `-maxrate`/`-bufsize`. Add `-bitrate_mode cbr` to ask for CBR (see `guides/phone-first-ffmpeg.md`). Also change `-level 4.0` to `-level 4`: `4.0` is not one of `h264_mediacodec`'s named levels (`4` is), so it is read as the raw number 4 — MediaCodec's constant for **level 1.1** (checked in FFmpeg's `mediacodecenc.c`). Named values such as `3.1` work as expected.
- Remaining 1849s split 3 ways = 616.3s each, offsets: 930.018, 1546.35, 2162.69
- Must verify font in 8 seconds BEFORE long run: `ffmpeg -ss 930 -t 5 ...` + check the stderr `fontselect:` line. Read the path **after** the `->`: it must be `.../Caveat.ttf`. The part in brackets, `(Caveat, 400, 0)`, is only the requested name and appears even when libass falls back to another font.

## Devices (owned vs borrowed) - user correction 2026-10-24
- Owned: Celeron N3350 laptop (h264_qsv), Tecno Spark 30C Helio G81 (h264_mediacodec) - ONLY these
- Borrowed: EliteBook 840 G6 i5-8365U, EliteBook x360 1040 G5 i7-8650U (9.21x reference), KitKat phone (separate old phone, not TV), non-smart TV (plays via old FAT32 flash drive, max 4GB, needs Baseline 3.1 720p hard-burned)
- No AMD AMF, no 4K HEVC to TV, SD card is playback chain for TV

## To resume next session
1. `cd $HOME && ls -lh burn_00.mp4 fix_0*.mp4 redo_0*.mp4` - keep burn_00.mp4
2. Check font: `fc-list | grep -i caveat` and 5s test encode
3. Run `~/burnfix.sh` (now fixed) - 10 min
4. Verify at 15:30, 30:45, concat -> `/storage/67FE-19FE/Download/MovieBox/Big_Little_Lies_1080P_S02_E05.subbed.mp4`

## Where these files are now (in this repository)
- `handoff/guides/`: `android-emulator-setup.md` (borrowed EliteBook note fixed),
  `subtitle-hard-burn.md` (ownership + TV FAT32 fix), `premiere-proxy-guide.md`
  (Celeron-only, no IdeaPad), `phone-first-ffmpeg.md`, `qsv-ffmpeg-context.md`,
  `bitrate-and-subspeed-fixes.md`
- This state file: `handoff/context/pdfs-and-state/BigLittleLies-2x05-burn-state.md`
- Script copies: `handoff/guides/burn_3core-home.sh` (the buggy original, kept for
  reference) and `handoff/guides/burnfix-home.sh` (the fix). On the phone they
  live as `~/burn_3core.sh` and `~/burnfix.sh`.
