# Fix Update 2026-10-24 19:40 - Flexible script

User: make original 3 core script more flexible
- select cores 1+
- free with input/subs paths (should copy subs itself to temp)

Created: `burn_parallel_flexible.sh` (now in `handoff/guides/`)
- args: -c CORES -i INPUT -s SUBS [-o OUTPUT] [--height] [--bitrate] [--encoder] [--font] [--fontsize] [--fontdir]
- copies subs to `$TMPDIR/burn_$$/` (Termux: `$PREFIX/tmp`, i.e. `/data/data/com.termux/files/usr/tmp`) to handle spaces/colons
- workdir per-run isolated, concat safe 0
- uses awk not bc, ffmpeg -ss before -i + setpts OFFSET/TB trick for correct subs per chunk (each chunk is decoded from the original, so fixed `n×CHUNK` offsets are exact here — unlike the stream-copy split that caused the drift)
- font verify 2 sec before long encode (fontselect line — the script reads the font after `->`, not the requested name, and exits if libass fell back)
- defaults: HEIGHT 1080 BITRATE 1350k ENCODER h264_mediacodec (`-level 4 -bitrate_mode cbr`) FONTSIZE 28 FONT Caveat FONTDIR $HOME/fonts; BUFSIZE 2x bitrate is used only with `--encoder libx264`
- 2026-11-18: each part is waited for separately; if one fails the parts are not joined and the work dir is kept
- no bc, TERMUX compatible, 1 or more cores

User's earlier path correction: font always in ~/fonts, not /storage - fixed in state doc.

