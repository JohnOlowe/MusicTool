#!/data/data/com.termux/files/usr/bin/bash
# Original 3-way split for Big Little Lies 2x05 - archived 2026-10-24
# This version had sync bug: OFFSET=idx*CHUNK (927*idx) not cumulative, caused 8s drift at 15:30
# Kept for reference, use burnfix-home.sh instead
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
TOTAL=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$MOVIE")
PARTS=3
CHUNK=$(( ( ${TOTAL%.*} + PARTS -1) / PARTS ))
echo "BUGGY CHUNK $CHUNK - do not reuse without fix"
