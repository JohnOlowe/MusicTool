# Handoff — Devices & Best Configurations
First generated 2026-10-14 for the next agent; covers work up to 2026-11-11 and
was corrected on 2026-11-18. This folder is the source of truth for the
ffmpeg / hardware-acceleration work. It is **not part of MusicTool** — nothing in
`musictool/` or `tests/` uses it.

## Quick Map
- `CONTEXT_MASTER.md` — full device + constraint + workflow summary (read first)
- `guides/` — the Markdown guides plus their `.bat`/`.sh`/`.py` helpers and preview images
- `context/pdfs-and-state/` — job-state notes (Big Little Lies 2x05 burn, BTSP podcast fix) and `subs_lato.txt`, a text export of the Gemini chat about burning subtitles in Lato. The source PDFs mentioned in the notes are **not** included.

Paths in these files are relative to `handoff/` unless they are device paths
(`/storage/...`, `C:\...`, `$HOME` in Termux). References to `/home/user/...` in
older notes point at the original sandbox, which no longer exists.
