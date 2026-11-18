# Fix Update 2026-10-24 19:40 - Flexible script

User: make original 3 core script more flexible
- select cores 1+
- free with input/subs paths (should copy subs itself to temp)

Created: `burn_parallel_flexible.sh` (now in `handoff/guides/`)
- args: -c CORES -i INPUT -s SUBS [-o OUTPUT] [--height] [--bitrate] [--font] [--fontsize] [--fontdir]
- copies subs to `$PREFIX/tmp/burn_$$/` (`/data/data/com.termux/files/usr/tmp`) to handle spaces/colons
- workdir per-run isolated, concat safe 0
- uses awk not bc, ffmpeg -ss before -i + setpts OFFSET/TB trick for correct subs per chunk (each chunk is decoded from the original, so fixed `n×CHUNK` offsets are exact here — unlike the stream-copy split that caused the drift)
- font verify 2 sec before long encode (fontselect line — check the font path after `->`, not the requested name)
- defaults: HEIGHT 1080 BITRATE 1350k BUFSIZE 2x FONTSIZE 28 FONT Caveat FONTDIR $HOME/fonts
- no bc, TERMUX compatible, 1 or more cores

User's earlier path correction: font always in ~/fonts, not /storage - fixed in state doc.

