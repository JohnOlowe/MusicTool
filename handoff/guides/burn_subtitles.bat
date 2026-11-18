@echo off
REM ============================================================
REM  Subtitle hard-burn benchmark + full run  (Intel Quick Sync)
REM  Works on: Celeron N3350, EliteBook 840 G6, any 8th/10th gen i5/i7
REM  Does NOT work on AMD - this uses h264_qsv, not h264_amf.
REM
REM  Run from Command Prompt in the folder holding the movie and .srt
REM  (the subtitles filter chokes on complex paths, so cd into the folder)
REM  The quotes in subtitles='%SUBS%' cover spaces and commas in the name;
REM  a ':' or '\' (i.e. a full C:\ path) still breaks it - use the bare name.
REM ============================================================

set MOVIE=My_Life_with_the_Walter_Boys - 1x1 - Welcome to Colorado.TXT.IMDB.en.mp4
set SUBS=My_Life_with_the_Walter_Boys - 1x1 - Welcome to Colorado.TXT.IMDB.en.srt
set OUT=My_Life_with_the_Walter_Boys - 1x1 - Welcome to Colorado.TXT.IMDB.mp4

REM 1080 = full quality. 720 = 2.25x less libass work, use this for the TV box.
set HEIGHT=1080
set FONT=Lato

echo.
echo ============================================================
echo  STEP 1 - Duration and 2-minute test clip
echo ============================================================
REM Length in seconds, from ffprobe. (The old line used %%~tA, which is the
REM file's date and time, not its duration.)
set TOTAL=
for /f "delims=" %%D in ('ffprobe -v error -show_entries format^=duration -of csv^=p^=0 "%MOVIE%"') do set TOTAL=%%D
echo Duration: %TOTAL% seconds
if exist bench_in.mp4 del bench_in.mp4
ffmpeg -y -i "%MOVIE%" -t 120 -c copy bench_in.mp4

echo.
echo ============================================================
echo  STEP 2 - Hardware encode + burn subs (2 min test)
echo    Note: "start "" /B /High /Wait" keeps progress visible in THIS
echo    window without freezing the machine. Never use /Realtime.
echo ============================================================
start "" /B /High /Wait ffmpeg -y -i "bench_in.mp4" ^
 -vf "scale=-2:%HEIGHT%,subtitles='%SUBS%':force_style='FontName=%FONT%'" ^
 -c:v h264_qsv -global_quality 22 -preset medium -g 50 -c:a copy bench_out.mp4

echo.
echo ============================================================
echo  STEP 3 - What did we ACTUALLY get?
echo ============================================================
ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,profile,width,height,bit_rate -of default=nw=1 bench_out.mp4

for %%A in (bench_out.mp4) do set SZ=%%~zA
echo File size: %SZ% bytes for 120s
echo Read the speed= figure printed in STEP 2, then work out the full run:
echo    full_seconds = %TOTAL% / speed_multiplier
echo Subtitle placement is the thing to check here, not just speed.

echo.
echo ============================================================
echo  STEP 4 - Full encode
echo ============================================================
set /p GO=Type YES to encode the full file now:
if /I "%GO%"=="YES" (
  start "" /B /High /Wait ffmpeg -y -i "%MOVIE%" ^
   -vf "scale=-2:%HEIGHT%,subtitles='%SUBS%':force_style='FontName=%FONT%'" ^
   -c:v h264_qsv -global_quality 22 -preset medium -g 50 -c:a copy "%OUT%"
  echo.
  echo Checking the result...
  ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,profile,width,height,bit_rate -of default=nw=1 "%OUT%"
  for %%A in ("%OUT%") do echo File size: %%~zA bytes
) else (
  echo Cancelled.
)

echo.
echo Done.
pause