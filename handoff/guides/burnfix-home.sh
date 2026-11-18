#!/data/data/com.termux/files/usr/bin/bash
set -e
# Big Little Lies 2x05 - 3-core fix for 15:30 sync drift + wrong font
# Workdir: $HOME where burn_00.mp4 lives. No bc, awk only. Verified 2026-10-24.
# Corrected 2026-11-18: -bitrate_mode cbr instead of the ignored -maxrate/-bufsize,
# -level 4 (4.0 meant level 1.1), a font check that reads the font actually used,
# and a per-part wait so a failed part stops the join.
MOVIE="/storage/67FE-19FE/Download/MovieBox/Big_Little_Lies_1080P_S02_E05.mp4"
SUBS="/storage/emulated/0/Fonts/subs.srt"
OUT="/storage/67FE-19FE/Download/MovieBox/Big_Little_Lies_1080P_S02_E05.subbed.mp4"

FONTDIR="$HOME/fonts"
FONTNAME="Caveat"
FONTSIZE=28
MARGINV=28
HEIGHT=1080
BITRATE="1350k"
# h264_mediacodec: -bitrate_mode cbr asks for CBR; -maxrate/-bufsize are ignored
# by this encoder. "-level 4.0" is read as the raw number 4 = level 1.1, so "4".
VENC="-c:v h264_mediacodec -profile:v main -level 4 -bitrate_mode cbr -b:v $BITRATE -g 50"
TMPBASE="${TMPDIR:-${PREFIX:-/data/data/com.termux/files/usr}/tmp}"

cd "$HOME"
pwd
ls -lh burn_00.mp4 2>&1 || echo "WARN: burn_00.mp4 missing - will fallback to 930.018"

TOTAL=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$MOVIE")
D0=$(ffprobe -v error -show_entries format=duration -of csv=p=0 burn_00.mp4 2>/dev/null || echo "930.018")

echo "TOTAL=$TOTAL D0=$D0"
if [ -z "$TOTAL" ]; then echo "ffprobe TOTAL failed"; exit 1; fi

OFFSET1=$(printf "%.3f" "$D0")
REMAIN=$(awk -v t="$TOTAL" -v d="$D0" 'BEGIN{printf "%.3f", t-d}')
CHUNK=$(awk -v r="$REMAIN" 'BEGIN{printf "%.3f", r/3}')

echo "Remaining $REMAIN sec = $CHUNK sec x3"
echo "Offsets: $OFFSET1 , $(awk -v o="$OFFSET1" -v c="$CHUNK" 'BEGIN{printf "%.3f", o+c}') , $(awk -v o="$OFFSET1" -v c="$CHUNK" 'BEGIN{printf "%.3f", o+2*c}')"

# FONT CHECK - 8 second test before 12 min run.
# libass prints "fontselect: (Caveat, 400, 0) -> <font actually used>". The part
# in brackets always says Caveat; only the part after "->" tells you the truth.
echo "--- FONT CHECK ---"
FONTLINE=$(ffmpeg -hide_banner -y -ss "$OFFSET1" -t 2 -i "$MOVIE" -vf "subtitles=$SUBS:fontsdir=$FONTDIR:force_style='FontName=$FONTNAME,Fontsize=$FONTSIZE,OutlineColour=&H000000,Outline=2,MarginV=$MARGINV'" -c:v h264_mediacodec -b:v $BITRATE -an "$TMPBASE/fonttest.mp4" 2>&1 | grep -i "fontselect" | head -1 || true)
if [ -z "$FONTLINE" ]; then
  echo "No fontselect line (no subtitle in those 2 s?) - check the result by eye"
else
  echo "$FONTLINE"
  case "$(echo "${FONTLINE#*-> }" | tr 'A-Z' 'a-z')" in
    *caveat*) echo "Font OK";;
    *) echo "WRONG FONT: libass fell back to: ${FONTLINE#*-> }"; exit 1;;
  esac
fi

rm -f fix_0*.mp4 fix_concat.txt

PIDS=""
for i in 0 1 2; do
  OFFSET=$(awk -v o="$OFFSET1" -v c="$CHUNK" -v n="$i" 'BEGIN{printf "%.3f", o + n*c}')
  DUR="$CHUNK"
  [ "$i" = "2" ] && DUR=$(awk -v r="$REMAIN" -v c="$CHUNK" 'BEGIN{printf "%.3f", r - 2*c}')
  echo "fix_$i OFFSET=$OFFSET DUR=$DUR"
  (
    ffmpeg -y -ss "$OFFSET" -t "$DUR" -i "$MOVIE" \
      -vf "setpts=PTS+$OFFSET/TB,scale=-2:$HEIGHT,subtitles=$SUBS:fontsdir=$FONTDIR:force_style='FontName=$FONTNAME,Fontsize=$FONTSIZE,OutlineColour=&H000000,Outline=2,Shadow=1,MarginV=$MARGINV,PrimaryColour=&H00FFFFFF,BorderStyle=1',setpts=PTS-$OFFSET/TB" \
      $VENC \
      -af "aresample=async=1:first_pts=0" -c:a aac -b:a 128k -ar 48000 -ac 2 \
      "fix_0${i}.mp4"
  ) &
  PIDS="$PIDS $!"
done
# a bare `wait` returns 0 even when a part failed; check each one
FAILED=0
for p in $PIDS; do wait "$p" || FAILED=$((FAILED+1)); done
if [ "$FAILED" -gt 0 ]; then
  echo "Error: $FAILED part(s) failed - not joining"; exit 1
fi

ls -lh burn_00.mp4 fix_0*.mp4
ls burn_00.mp4 fix_0*.mp4 | sort | sed "s/^/file '/;s/$/'/" > fix_concat.txt
cat fix_concat.txt
ffmpeg -y -f concat -safe 0 -i fix_concat.txt -c copy "$OUT"

echo "Done -> $OUT"
ls -lh "$OUT"
