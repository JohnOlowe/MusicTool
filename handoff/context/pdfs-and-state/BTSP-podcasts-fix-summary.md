# BTSP Podcasts - XPlayer fixes that WORKED - 2026-11-11

Two files, both originally `VP9 1080p 29.97fps + AAC 44.1k` in `Matroska/DASH` with no `Cues` -> `0:00` frozen [15] + `Missing CodecPrivate` silence [23][24]

## WORKED 1: Our Villain Origin Story #1 - 52:10
```
ffmpeg -y -i "...Our Villain Origin Story - Beautiful Twin Sisters Podcast #1.mkv" \
 -map 0:v:0 -map 0:a:0 -map_metadata 0 -map_chapters 0 \
 -c:v copy -c:a aac -b:a 128k -ar 48000 -ac 2 \
 -movflags +faststart \
 "...Our Villain Origin Story - MP4FIX.mp4"
```
Result: `mkv (Tracks after Clusters) -> mp4` remux, video copy (VP9 stays, no quality loss), audio `AAC 44.1k -> AAC 48k` rebuilds AudioSpecificConfig (extradata_size 5 -> now valid per [1]), `movflags+faststart` puts `moov` at front so ExoPlayer can get duration without scanning. XPlayer: time runs + sound.

Why: AAC in `3GPP/MP4/ADTS` is on Android's official list [12][19], AAC in `mkv` is not. Moving to mp4 fixes parser.

## WORKED 2: we moved - BTSP #96 - 41:11
```
ffmpeg -y -i "...we moved - BTSP #96.mkv" \
 -map 0:v:0 -map 0:a:0 -map_metadata 0 -map_chapters 0 \
 -c:v copy -tag:v vp09 \
 -c:a aac -profile:a aac_low -b:a 128k -ar 48000 -ac 2 \
 + faststart -> .mp4
```
Same as above + `-tag:v vp09` explicitly. Critical for VP9-in-mp4 - Android's Mp4Extractor looks for `vp09`/`vpcC` [6][21]. Without tag, some Tecno ROMs show black with audio. With tag, XPlayer sees `VP9` correctly and uses software `libgav1` fallback.

Both: NO video re-encode = same size/quality, 10-15 sec, keeps cover.webp chapters? No chapters on #96 so none to keep, but PURL/ARTIST/DESCRIPTION preserved via map_metadata.

## Pattern for all BTSP podcasts from YouTube/DASH dash/iso6mp41 .mkv
All have same `Lavf62.12/63.1 + 1k tbn + DASH` mux -> all need same `mkv -> mp4 + AAC 48k + faststart (+ vp09 tag if VP9)`.

Batch template:
ffmpeg -y -i "$IN" -map 0:v:0 -map 0:a:0 -map_metadata 0 -map_chapters 0 -c:v copy -tag:v vp09 -c:a aac -profile:a aac_low -b:a 128k -ar 48000 -ac 2 -movflags +faststart "$OUT.mp4"

Do NOT use -c copy alone (keeps bad AAC private [24]) or -c:t copy to mp4 (mp4 doesn't take webp attachment same way).

Files audited for laptop efficiency:
- preview-generation-guide.md now JOBS=2 for Celeron N3350, QSV probe only for encode, hover priority queue
- burn_parallel_flexible.sh is phone-only (h264_mediacodec), not for laptop previews
- subtitle-hard-burn.md correctly has no -hwaccel for filter burns
