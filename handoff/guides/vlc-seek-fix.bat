@echo off
REM VLC external-audio SEEK FIX for YouTube DASH iso6mp41
REM Fixes the .m4a fragmentation (why VLC stutters) without making a giant muxed copy
REM Needs ~1.2GB free for the 20h audio only, NOT video+audio
setlocal

set "VIDEO=GTA V - FULL GAME (4K 60FPS) Walkthrough Gameplay No Commentary.mp4"
set "AUDIO=GTA V - FULL GAME (4K 60FPS) Walkthrough Gameplay No Commentary.m4a"
set "AUDIOFIX=_audio_fixed.m4a"

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

if not exist "%AUDIO%" (
  echo Put this .bat in the same folder as video + m4a
  pause
  exit /b
)

echo [1/3] De-fragmenting DASH audio for seeking...
ffmpeg -v error -fflags +genpts -i "%AUDIO%" -c:a copy -movflags +faststart "%AUDIOFIX%"
if errorlevel 1 (
  echo ffmpeg failed - trying re-encode to rebuild index...
  ffmpeg -v error -i "%AUDIO%" -c:a aac -ar 48000 -b:a 128k -movflags +faststart "%AUDIOFIX%"
)

echo [2/3] Verifying index...
ffprobe -v error -show_entries stream=codec_name,sample_rate -of default=noprint_wrappers=1 "%AUDIOFIX%"

echo [3/3] Launching VLC with seekable slave...
REM delete old fragmented audio and replace in-place to save space (optional)
REM Uncomment next 2 lines if you want to keep only fixed version:
REM del "%AUDIO%"
REM ren "%AUDIOFIX%" "%AUDIO%" & set "AUDIOFIX=%AUDIO%"

"%VLC%" --file-caching 5000 --network-caching 5000 "%VIDEO%" :input-slave="%AUDIOFIX%" --input-slave-caching 5000
