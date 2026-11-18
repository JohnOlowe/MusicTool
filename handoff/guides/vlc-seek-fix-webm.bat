@echo off
REM Fix for YouTube DASH: webm video + m4a audio with VLC 32-bit
REM Video is webm (VP9/AV1), Audio is dash iso6mp41 AAC - VLC x86 stutters on mixed containers
setlocal

set "VIDEO=GTA V - FULL GAME (4K 60FPS) Walkthrough Gameplay No Commentary.webm"
set "AUDIO=GTA V - FULL GAME (4K 60FPS) Walkthrough Gameplay No Commentary.m4a"

REM Find VLC: 64-bit install, 32-bit (x86) install, or vlc on PATH.
set "VLC=%ProgramFiles%\VideoLAN\VLC\vlc.exe"
if not exist "%VLC%" set "VLC=%ProgramFiles(x86)%\VideoLAN\VLC\vlc.exe"
if not exist "%VLC%" for /f "delims=" %%V in ('where vlc 2^>nul') do set "VLC=%%V"
if not exist "%VLC%" (
  echo VLC not found in Program Files, Program Files ^(x86^) or on PATH.
  echo Edit the VLC line in this .bat to point at your vlc.exe.
  pause
  exit /b 1
)

if not exist "%VIDEO%" (
  echo Put this .bat NEXT TO both files. Looking for:
  echo  "%VIDEO%"
  echo  "%AUDIO%"
  dir /b
  pause
  exit /b
)

echo [1/3] Checking video...
ffprobe -v error -show_entries stream=codec_name,width,height,r_frame_rate -of default=noprint_wrappers=1 "%VIDEO%"

echo.
echo [2/3] Re-wrapping audio to match webm timeline (AAC -^> Opus in webm, rebuilds index)...
REM No temp 100GB mux - just re-encodes 1.1GB audio to webm container VLC x86 handles better
ffmpeg -v error -i "%AUDIO%" -c:a libopus -ar 48000 -b:a 128k -vn audio_fixed.webm
if errorlevel 1 (
  echo libopus not found, trying aac in mka...
  ffmpeg -v error -i "%AUDIO%" -c:a aac -ar 48000 -b:a 128k audio_fixed.mka
  set "AUDIOFIX=audio_fixed.mka"
) else (
  set "AUDIOFIX=audio_fixed.webm"
)

echo [3/3] Launching VLC with seekable slave...
"%VLC%" --file-caching 5000 --input-slave-caching 5000 "%VIDEO%" :input-slave="%AUDIOFIX%"

echo.
echo If seeking still jumps, close VLC and try mpv (0-byte mux, handles webm+m4a perfectly):
echo mpv "%VIDEO%" --audio-file="%AUDIO%" --cache=yes
pause
