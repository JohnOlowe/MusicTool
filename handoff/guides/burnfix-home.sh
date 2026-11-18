#!/data/data/com.termux/files/usr/bin/bash
set -e
# Big Little Lies 2x05 - 3-core fix for 15:30 sync drift + wrong font
# Workdir: $HOME where burn_00.mp4 lives. No bc, awk only. Verified 2026-10-24.
MOVIE="/storage/67FE-19FE/Download/MovieBox/Big_Little_Lies_1080P_S02_E05.mp4"
SUBS="/storage/emulated/0/Fonts/subs.srt"
OUT="/storage/67FE-19FE/Download/MovieBox/Big_Little_Lies_1080P_S02_E05.subbed.mp4"

FONTDIR="$HOME/fonts"
FONTNAME="Caveat"
FONTSIZE=28
MARGINV=28
HEIGHT=1080
BITRATE="1350k"
BUFSIZE="2700k"

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

# FONT CHECK - 8 second test before 12 min run
echo "--- FONT CHECK (should say Caveat, not Arial) ---"
ffmpeg -y -ss "$OFFSET1" -t 2 -i "$MOVIE" -vf "subtitles=$SUBS:fontsdir=$FONTDIR:force_style='FontName=$FONTNAME,Fontsize=$FONTSIZE,OutlineColour=&H000000,Outline=2,MarginV=$MARGINV'" -c:v h264_mediacodec -profile:v main -b:v $BITRATE -maxrate $BITRATE -bufsize $BUFSIZE -c:a aac -b:a 128k /data/data/com.termux/files/usr/tmp/fonttest.mp4 2>&1 | grep -i "fontselect\|Using font\|fallback" || echo "No fontselect line - check ffmpeg -filters | grep subtitles"

rm -f fix_0*.mp4 fix_concat.txt

for i in 0 1 2; do
  OFFSET=$(awk -v o="$OFFSET1" -v c="$CHUNK" -v n="$i" 'BEGIN{printf "%.3f", o + n*c}')
  DUR="$CHUNK"
  [ "$i" = "2" ] && DUR=$(awk -v r="$REMAIN" -v c="$CHUNK" 'BEGIN{printf "%.3f", r - 2*c}')
  echo "fix_$i OFFSET=$OFFSET DUR=$DUR"
  (
    ffmpeg -y -ss "$OFFSET" -t "$DUR" -i "$MOVIE" \
      -vf "setpts=PTS+$OFFSET/TB,scale=-2:$HEIGHT,subtitles=$SUBS:fontsdir=$FONTDIR:force_style='FontName=$FONTNAME,Fontsize=$FONTSIZE,OutlineColour=&H000000,Outline=2,Shadow=1,MarginV=$MARGINV,PrimaryColour=&H00FFFFFF,BorderStyle=1',setpts=PTS-$OFFSET/TB" \
      -c:v h264_mediacodec -profile:v main -level 4.0 -b:v $BITRATE -maxrate $BITRATE -bufsize $BUFSIZE -g 50 \
      -af "aresample=async=1:first_pts=0" -c:a aac -b:a 128k -ar 48000 -ac 2 \
      "fix_0${i}.mp4"
  ) &
done
wait

ls -lh burn_00.mp4 fix_0*.mp4
ls burn_00.mp4 fix_0*.mp4 | sort | sed "s/^/file '/;s/$/'/" > fix_concat.txt
cat fix_concat.txt
ffmpeg -y -f concat -safe 0 -i fix_concat.txt -c copy "$OUT"

echo "Done -> $OUT"
ls -lh "$OUT"
