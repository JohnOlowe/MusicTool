#!/data/data/com.termux/files/usr/bin/bash
set -e
# (On a PC run it as: bash burn_parallel_flexible.sh ... --encoder libx264)
# burn_parallel_flexible.sh - flexible parallel subtitle burn for phone (Termux)
# Usage:
#   ./burn_parallel_flexible.sh -c 3 -i input.mp4 -s subs.srt -o out.mp4
#   ./burn_parallel_flexible.sh -c 2 -i "/storage/.../Movie.mp4" -s "/storage/.../subs.srt"
#   ./burn_parallel_flexible.sh --help
#
# -c / --cores : 1 or more, number of parallel ffmpeg jobs
# -i / --input : input video file
# -s / --subs  : subtitle file (srt/ass) - copied to $TMPDIR (Termux: $PREFIX/tmp) to handle spaces
# -o / --output: output file (optional, default: inputname.subbed.mp4 next to input)
# --height     : output height (default 1080, use 720 for TV/speed)
# --bitrate    : video bitrate (default 1350k)
# --encoder    : h264_mediacodec (default, phone hardware) or libx264 (any PC)
# --font       : FontName as shown by fc-list (default Caveat)
# --fontsize   : Fontsize (default 28)
# --fontdir    : directory containing fonts (default $HOME/fonts)

show_help() {
  cat << 'EOF'
Usage: ./burn_parallel_flexible.sh -c CORES -i INPUT -s SUBS [-o OUTPUT] [options]

Required:
  -c, --cores N      Number of parallel jobs (1,2,3,4,6,8...)
  -i, --input FILE   Input video
  -s, --subs FILE    Subtitle file (.srt/.ass)

Optional:
  -o, --output FILE  Output file (default: <input>.subbed.mp4)
  --height N         Output height, e.g. 720, 1080 (default: 1080)
  --bitrate RATE     Video bitrate, e.g. 1500k, 2M (default: 1350k)
  --bufsize RATE     VBV bufsize, libx264 only (default: 2x bitrate).
                     h264_mediacodec ignores -maxrate/-bufsize; it gets
                     -bitrate_mode cbr instead.
  --encoder NAME     h264_mediacodec (default) or libx264
  --font NAME        FontName exactly as fc-list shows (default: Caveat)
  --fontsize N       Fontsize (default: 28)
  --fontdir DIR      Font directory (default: $HOME/fonts)
  --margInv N        Bottom margin (default: 28)
  --keep-tmp         Don't delete work dir on exit
  -h, --help         Show this

Examples:
  ./burn_parallel_flexible.sh -c 3 -i movie.mp4 -s subs.srt
  ./burn_parallel_flexible.sh -c 1 -i "/storage/67FE-19FE/Movie.mp4" -s "/sdcard/Subs/My Subs.srt" -o "/storage/emulated/0/Movies/out.mp4" --height 720
  ./burn_parallel_flexible.sh -c 3 -i input.mkv -s sub.ass --font "Dancing Script" --fontsize 32

The script copies subs to a temp dir so spaces/colons in the original path never break the filter.
It also verifies the font in 2 seconds before starting the long encode, and
stops if libass fell back to another font. Any failed part stops the join.
EOF
  exit 0
}

# defaults
CORES=""
INPUT=""
SUBS=""
OUTPUT=""
HEIGHT=1080
BITRATE="1350k"
BUFSIZE=""
FONTNAME="Caveat"
FONTSIZE=28
FONTDIR="$HOME/fonts"
MARGINV=28
KEEP_TMP=0
ENCODER="h264_mediacodec"

while [ $# -gt 0 ]; do
  case "$1" in
    -c|--cores) CORES="$2"; shift 2;;
    -i|--input) INPUT="$2"; shift 2;;
    -s|--subs) SUBS="$2"; shift 2;;
    -o|--output) OUTPUT="$2"; shift 2;;
    --height) HEIGHT="$2"; shift 2;;
    --bitrate) BITRATE="$2"; shift 2;;
    --bufsize) BUFSIZE="$2"; shift 2;;
    --font) FONTNAME="$2"; shift 2;;
    --fontsize) FONTSIZE="$2"; shift 2;;
    --fontdir) FONTDIR="$2"; shift 2;;
    --margInv|--margV|--margin) MARGINV="$2"; shift 2;;
    --encoder) ENCODER="$2"; shift 2;;
    --keep-tmp) KEEP_TMP=1; shift 1;;
    -h|--help) show_help;;
    *) echo "Unknown arg: $1 (see --help)"; exit 1;;
  esac
done

if [ -z "$CORES" ] || [ -z "$INPUT" ] || [ -z "$SUBS" ]; then
  echo "Missing required args."
  echo "Need: -c CORES -i INPUT -s SUBS"
  show_help
fi

if ! echo "$CORES" | grep -qE '^[1-9][0-9]*$'; then
  echo "Error: -c must be 1 or more"; exit 1
fi
if [ ! -f "$INPUT" ]; then
  echo "Error: input not found: $INPUT"; exit 1
fi
if [ ! -f "$SUBS" ]; then
  echo "Error: subs not found: $SUBS"; exit 1
fi
if [ ! -d "$FONTDIR" ]; then
  echo "Warning: fontdir not found: $FONTDIR (font may fallback to Arial)"
fi
case "$ENCODER" in
  h264_mediacodec|libx264) ;;
  *) echo "Error: --encoder must be h264_mediacodec or libx264"; exit 1;;
esac

if [ -z "$BUFSIZE" ]; then
  # default 2x bitrate
  if echo "$BITRATE" | grep -q "k$"; then
    KNUM=$(echo "$BITRATE" | tr -d 'kK')
    BUFSIZE="$(( KNUM * 2 ))k"
  else
    BUFSIZE="$BITRATE"
  fi
fi

if [ -z "$OUTPUT" ]; then
  # default output next to input
  ODIR=$(dirname "$INPUT")
  OBASE=$(basename "$INPUT")
  OBASE="${OBASE%.*}.subbed.mp4"
  OUTPUT="$ODIR/$OBASE"
fi

# Video encoder flags.
# h264_mediacodec: -bitrate_mode cbr is what asks the phone's encoder for CBR
#   (FFmpeg 6.0+); -maxrate/-bufsize are ignored by this encoder. Level is
#   given as "4", not "4.0": "4.0" is not one of its named levels, is read as
#   the raw number 4, and that is MediaCodec's constant for level 1.1.
# libx264: real VBV, so -maxrate/-bufsize do apply.
if [ "$ENCODER" = "h264_mediacodec" ]; then
  VENC="-c:v h264_mediacodec -profile:v main -level 4 -bitrate_mode cbr -b:v $BITRATE -g 50"
  VTEST="-c:v h264_mediacodec -b:v $BITRATE"
else
  VENC="-c:v libx264 -preset veryfast -profile:v main -pix_fmt yuv420p -b:v $BITRATE -maxrate $BITRATE -bufsize $BUFSIZE -g 50"
  VTEST="-c:v libx264 -preset ultrafast -pix_fmt yuv420p"
fi

# work dir for this run (isolated). Termux sets TMPDIR to $PREFIX/tmp.
TMPBASE="${TMPDIR:-${PREFIX:-/data/data/com.termux/files/usr}/tmp}"
WORKDIR="$TMPBASE/burn_$$"
mkdir -p "$WORKDIR"
# keep the real extension: an .ass file renamed .srt loses nothing today, but
# there is no reason to make libass guess
SUBS_TMP="$WORKDIR/subs.tmp.${SUBS##*.}"
cp "$SUBS" "$SUBS_TMP"

cleanup() {
  if [ "$KEEP_TMP" = 0 ]; then
    rm -rf "$WORKDIR"
  else
    echo "Keeping tmp: $WORKDIR"
  fi
}
trap cleanup EXIT

echo "Input : $INPUT"
echo "Subs  : $SUBS -> $SUBS_TMP"
echo "Output: $OUTPUT"
echo "Cores : $CORES, Height: $HEIGHT, Bitrate: $BITRATE, Encoder: $ENCODER"
echo "Font  : $FONTNAME $FONTSIZE in $FONTDIR, MarginV $MARGINV"
echo ""

TOTAL=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$INPUT")
if [ -z "$TOTAL" ]; then echo "ffprobe failed on $INPUT"; exit 1; fi
echo "Duration: $TOTAL sec"

# font verify (2 sec)
echo ""
echo "--- FONT CHECK ---"
fc-cache -f >/dev/null 2>&1 || true
if ! fc-list | grep -qi "$(echo "$FONTNAME" | cut -d' ' -f1)"; then
  echo "(fc-list has no match for $FONTNAME - spelling may be off)"
fi
# libass prints "fontselect: (Requested, 400, 0) -> /path/to/UsedFont.ttf ...".
# The part in brackets is only what was asked for and always shows your
# FontName; the path after "->" is the font actually used.
FONTLINE=$(ffmpeg -hide_banner -y -ss 2 -t 1 -i "$INPUT" -vf "subtitles=$SUBS_TMP:fontsdir=$FONTDIR:force_style='FontName=$FONTNAME,Fontsize=$FONTSIZE'" $VTEST -an "$WORKDIR/fonttest.mp4" 2>&1 | grep -i "fontselect" | head -1 || true)
if [ -z "$FONTLINE" ]; then
  echo "(no fontselect line - no subtitle in the first seconds? check the result by eye)"
else
  echo "$FONTLINE"
  USED="${FONTLINE#*-> }"
  WANT=$(echo "$FONTNAME" | tr -d ' ' | tr 'A-Z' 'a-z')
  if echo "$USED" | tr -d ' ' | tr 'A-Z' 'a-z' | grep -q "$WANT"; then
    echo "Font OK: $USED"
  else
    echo "WRONG FONT: libass fell back to: $USED"
    echo "Fix FontName (exactly as fc-list shows it) or --fontdir, then run again."
    exit 1
  fi
fi
echo ""

# chunk math with awk (no bc)
CHUNK=$(awk -v t="$TOTAL" -v c="$CORES" 'BEGIN{printf "%.3f", t/c}')
echo "Splitting $TOTAL sec into $CORES x $CHUNK sec..."

# build concat list as we go
CONCAT="$WORKDIR/concat.txt"
> "$CONCAT"

# launch parallel jobs using -ss before -i (fast) + setpts trick for correct subs.
# Each part is decoded from the original and re-encoded, so it starts exactly
# at OFFSET and fixed n*CHUNK offsets are right (unlike a -c copy split, which
# cuts at keyframes).
PIDS=""
for i in $(seq 0 $((CORES-1))); do
  OFFSET=$(awk -v ch="$CHUNK" -v n="$i" 'BEGIN{printf "%.3f", n*ch}')
  DUR="$CHUNK"
  if [ "$i" = "$((CORES-1))" ]; then
    DUR=$(awk -v t="$TOTAL" -v c="$CHUNK" -v n="$i" 'BEGIN{printf "%.3f", t - n*c}')
  fi
  PART="$WORKDIR/part_$(printf "%02d" "$i").mp4"
  echo "part $i: OFFSET=$OFFSET DUR=$DUR -> $PART"
  (
    ffmpeg -y -ss "$OFFSET" -t "$DUR" -i "$INPUT" \
      -vf "setpts=PTS+$OFFSET/TB,scale=-2:$HEIGHT,subtitles=$SUBS_TMP:fontsdir=$FONTDIR:force_style='FontName=$FONTNAME,Fontsize=$FONTSIZE,OutlineColour=&H000000,Outline=2,Shadow=1,MarginV=$MARGINV,PrimaryColour=&H00FFFFFF,BorderStyle=1',setpts=PTS-$OFFSET/TB" \
      $VENC \
      -af "aresample=async=1:first_pts=0" -c:a aac -b:a 128k -ar 48000 -ac 2 \
      -loglevel error -stats "$PART"
  ) &
  PIDS="$PIDS $!"
done
# A bare `wait` always returns 0, so a part that failed used to go unnoticed
# and the join stitched together whatever was left. Wait for each one.
FAILED=0
for p in $PIDS; do
  wait "$p" || FAILED=$((FAILED+1))
done
if [ "$FAILED" -gt 0 ]; then
  echo "Error: $FAILED part(s) failed - not joining. Work dir kept: $WORKDIR"
  KEEP_TMP=1
  exit 1
fi

for i in $(seq 0 $((CORES-1))); do
  PART="$WORKDIR/part_$(printf "%02d" "$i").mp4"
  echo "file '$PART'" >> "$CONCAT"
done
cat "$CONCAT"

echo ""
echo "Joining to $OUTPUT ..."
ffmpeg -y -f concat -safe 0 -i "$CONCAT" -c copy "$OUTPUT"

echo ""
echo "Done -> $OUTPUT"
ls -lh "$OUTPUT"
ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,width,height,bit_rate -of default=nw=1 "$OUTPUT" || true
ffprobe -v error -show_entries format=duration,bit_rate -of default=nw=1 "$OUTPUT" || true
