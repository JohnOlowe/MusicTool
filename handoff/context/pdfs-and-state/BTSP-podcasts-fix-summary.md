# BTSP Podcasts - XPlayer fixes that WORKED - 2026-11-11

> **Corrected 2026-11-18.** The two commands below are what fixed playback on the
> phone, but both use `-map 0:v:0 -map 0:a:0`, which **drops the `cover.webp`
> attachment** — against the standing rule "the logo and every metadata must be
> kept" (`../../CONTEXT_MASTER.md`). Use the **Recommended** command at the end
> instead. Bracketed numbers like `[15]` refer to sources in the original chat;
> that list was not preserved.

Two files, both originally `VP9 1080p 29.97fps + AAC 44.1k` in `Matroska/DASH` with no `Cues` -> `0:00` frozen [15] + `Missing CodecPrivate` silence [23][24]

## WORKED 1: Our Villain Origin Story #1 - 52:10
```
ffmpeg -y -i "...Our Villain Origin Story - Beautiful Twin Sisters Podcast #1.mkv" \
 -map 0:v:0 -map 0:a:0 -map_metadata 0 -map_chapters 0 \
 -c:v copy -c:a aac -b:a 128k -ar 48000 -ac 2 \
 -movflags +faststart \
 "...Our Villain Origin Story - MP4FIX.mp4"
```
Result: `mkv (Tracks after Clusters) -> mp4` remux, video copy (VP9 stays, no quality loss), audio `AAC 44.1k -> AAC 48k` re-encoded, so the AAC encoder writes a fresh, valid AudioSpecificConfig (the `extradata_size` figures in the original notes were not re-checked), `movflags+faststart` puts `moov` at front so ExoPlayer can get duration without scanning. XPlayer: time runs + sound. Cover art: dropped (see note at top).

Why: AAC in `3GPP/MP4/ADTS` is on Android's official list [12][19], AAC in `mkv` is not. Moving to mp4 fixes parser.

## WORKED 2: we moved - BTSP #96 - 41:11
```
ffmpeg -y -i "...we moved - BTSP #96.mkv" \
 -map 0:v:0 -map 0:a:0 -map_metadata 0 -map_chapters 0 \
 -c:v copy -tag:v vp09 \
 -c:a aac -profile:a aac_low -b:a 128k -ar 48000 -ac 2 \
 -movflags +faststart "...we moved - BTSP #96.mp4"
```
Same as above + `-tag:v vp09` explicitly. Critical for VP9-in-mp4 - Android's Mp4Extractor looks for `vp09`/`vpcC` [6][21]. Without tag, some Tecno ROMs show black with audio. With tag, XPlayer recognises the stream as VP9. (An earlier note said it then "uses software `libgav1`" — wrong: libgav1 is an **AV1** decoder. VP9 goes to the phone's VP9 decoder — the G81 has hardware VP9 decode up to 2K@30 — or Android's software libvpx decoder.)

Both: NO video re-encode = same size/quality, 10-15 sec. #96 has no chapters. What `-map_metadata 0` carries into mp4 is the standard tags (title, artist, date, comment, description); a non-standard key such as `PURL` is **dropped** unless `-movflags +use_metadata_tags` is added — and in ffmpeg 9.0.2 that flag drops the cover art instead (tested). If the URL matters, copy it into the comment with `-metadata comment="..."`.

## Pattern for all BTSP podcasts from YouTube/DASH dash/iso6mp41 .mkv
All have same `Lavf62.12/63.1 + 1k tbn + DASH` mux -> all need same `mkv -> mp4 + AAC 48k + faststart (+ vp09 tag if VP9)`.

## Recommended (keeps the cover, chapters and standard tags)

Tested 2026-11-18 on ffmpeg 9.0.2 against a VP9 + AAC 44.1k mkv with a
`cover.webp` attachment, chapters and tags: output is VP9 (`vp09`) + AAC-LC
48 kHz stereo + the cover as an mp4 `attached pic` + chapters + title/artist/
date/comment/description. Not yet run on the phone's 8.1.2 build.

```bash
# 1) extract the cover. ffmpeg then prints "At least one output file must be
#    specified" - expected; cover.webp has been written.
ffmpeg -y -dump_attachment:t:0 cover.webp -i "$IN"

# 2) remux: video copied, audio rebuilt, cover re-added as mp4 cover art
ffmpeg -y -i "$IN" -i cover.webp \
 -map 0:v:0 -map 0:a:0 -map 1:v:0 -map_metadata 0 -map_chapters 0 \
 -c:v:0 copy -tag:v:0 vp09 -c:v:1 mjpeg -disposition:v:1 attached_pic \
 -c:a aac -profile:a aac_low -b:a 128k -ar 48000 -ac 2 \
 -movflags +faststart "$OUT.mp4"
```

- `-tag:v:0 vp09` only when the video is VP9; drop it for H.264 sources.
- If the mkv has no attachment, skip step 1 and drop `-i cover.webp`,
  `-map 1:v:0`, `-c:v:1 mjpeg -disposition:v:1 attached_pic`.

Do NOT use -c copy alone (keeps bad AAC private [24]) or `-map 0 ... -c:t copy` to
mp4 — verified: mp4 cannot hold the webp attachment stream and ffmpeg aborts with
`Could not find tag for codec none ... codec not currently supported in container`.

Files audited for laptop efficiency:
- preview-generation-guide.md now JOBS=2 for Celeron N3350, QSV probe only for encode, hover priority queue
- burn_parallel_flexible.sh is a subtitle burner (default `h264_mediacodec`; `--encoder libx264` on a PC), not for laptop previews
- subtitle-hard-burn.md correctly has no -hwaccel for filter burns
