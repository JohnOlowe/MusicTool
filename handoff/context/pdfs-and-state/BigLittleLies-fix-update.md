# Fix Update 2026-10-24 19:40 - Flexible script

User: make original 3 core script more flexible
- select cores 1+
- free with input/subs paths (should copy subs itself to temp)

Created: /home/user/burn_parallel_flexible.sh
- args: -c CORES -i INPUT -s SUBS [-o OUTPUT] [--height] [--bitrate] [--font] [--fontsize] [--fontdir]
- copies subs to /usr/tmp/burn_$$/subs.tmp.srt to handle spaces/colons
- workdir per-run isolated, concat safe 0
- uses awk not bc, ffmpeg -ss before -i + setpts OFFSET/TB trick for correct subs per chunk
- font verify 2 sec before long encode (fontselect line)
- defaults: HEIGHT 1080 BITRATE 1350k BUFSIZE 2x FONTSIZE 28 FONT Caveat FONTDIR $HOME/fonts
- no bc, TERMUX compatible, 1 or more cores

User's earlier path correction: font always in ~/fonts, not /storage - fixed in state doc.

