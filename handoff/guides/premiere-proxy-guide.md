# Premiere Pro 2023 (23.6) — proxies without Media Encoder

**Fixed for:** Tecno Spark 30C (Helio G81) + Celeron N3350 laptop are the only devices you own. Every other machine (EliteBook 840 G6, x360 1040 G5 / i7-8650U, the old KitKat phone, the non-smart TV) is borrowed or a playback target — never planned around for this job.

**Your footage:** `This PC > Videos > Editing`
```
VID_20260930_075352_edit.mp4
VID_20260930_075755_edit.mp4
```
Filenames like `VID_` are phone-camera exports. In practice that means: h264, progressive, **stereo (2.0)**, and very likely **variable frame rate (VFR)**. That matters for proxies because Premiere is strict about the match:

> For Premiere to attach a proxy, **frame rate, duration, resolution aside, and audio-channel count must match the original.** If they don't, Attach fails with `must have matching audio channels` or `must have matching framerate and overlapping timecode`. This is real Premiere behaviour; the `[7][10][18]` numbered citations in the paste you sent are fabricated and should be ignored.

Good news: for stereo phone edits, H.264 proxies are the correct, smallest, and fastest choice. You do **not** need ProRes.

---

## 0. What the paste got wrong

| Paste claim | Correction |
|---|---|
| Machine is an "IdeaPad" with 8 GB RAM and a 5400 rpm HDD | You do not own an IdeaPad. Your owned laptop is the **Celeron N3350**. Do not tune for RAM/disk you haven't confirmed. Keep this guide storage-agnostic. |
| "Premiere can't *create* proxies without Media Encoder [10]" with fake citations | Roughly true in spirit: the built-in **Create Proxies** button queues Media Encoder. But you don't need it — **Project panel → Proxy → Attach Proxies** links proxies made anywhere, including FFmpeg. That's the workflow below. |
| Numbered references `[1][7][10][16][18][19][20]` | All fabricated. Discarded. |
| ProRes is the default recommendation | For 2-channel phone footage ProRes is 5–15× larger for no benefit. Use H.264. ProRes / Cineform only matter if the source has 4/6/8 channels or critical timecode you must preserve. |
| `-c:v libx264 -preset fast` presented as the only option on the Celeron | Works, but on your Celeron `h264_qsv` (Quick Sync) encodes faster and barely touches the CPU. Both are given below. |
| Implies ProRes needs `prores_ks` check, `gyan.dev` is the only build | `gyan.dev` builds are fine, but any recent full build works. You already have FFmpeg on the Celeron and in Termux. |

---

## 1. Install / verify FFmpeg (once, Celeron)

You already have it for the subtitle work. Just verify:

```cmd
ffmpeg -version
ffmpeg -encoders | findstr qsv
```

- If `h264_qsv` appears → you have Quick Sync. Use it.
- If not → the `libx264` commands still work.

No need to hunt for `prores_ks` unless you end up needing multichannel ProRes (you don't, for these two files).

---

## 2. Check what you actually have

Before making proxies, confirm the match requirements so you don't encode twice.

In `Videos\Editing`:

```cmd
cd /d "%USERPROFILE%\Videos\Editing"
ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,width,height,r_frame_rate,avg_frame_rate,duration -of default=nw=1 "VID_20260930_075352_edit.mp4"
ffprobe -v error -select_streams a:0 -show_entries stream=codec_name,channels -of default=nw=1 "VID_20260930_075352_edit.mp4"
ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,width,height,r_frame_rate,avg_frame_rate,duration -of default=nw=1 "VID20260930_075755_edit.mp4"
ffprobe -v error -select_streams a:0 -show_entries stream=codec_name,channels -of default=nw=1 "VID20260930_075755_edit.mp4"
```

What you're looking for:
- **Audio channels = 2** → H.264 proxies are fine.
- **Frame rate** — if `r_frame_rate` and `avg_frame_rate` differ (e.g. `30/1` vs `30k/1001`) or MediaInfo says "Variable", the file is VFR. Fix it in the proxy command or the attach will fail with `must have matching framerate`.

If either file shows **4 or more channels**, stop and use the ProRes/Cineform variant in §4. For these phone edits you almost certainly won't.

---

## 3. Make the proxies — H.264 720p (correct for these files)

General rules for Premiere 23.6 proxies made with FFmpeg:
1. Never overwrite originals. Put proxies in a `Proxies` subfolder.
2. Scale to 720p (`scale=-2:720` keeps aspect, even width). For 4K source you can go `scale=-2:540` to save more.
3. **Force constant frame rate (CFR)** if the source is VFR — phone files usually are.
4. Keep **all** streams with `-map 0` so channel count stays identical.
5. Copy the creation time / metadata if you want, but Premiere matches on **filename + Media Start**, not metadata alone.

### Option A — fastest on your Celeron (Quick Sync)

```cmd
cd /d "%USERPROFILE%\Videos\Editing"
mkdir Proxies 2>nul

ffmpeg -y -i "VID_20260930_075352_edit.mp4" -vf "scale=-2:720,fps=30" -c:v h264_qsv -global_quality 23 -preset medium -c:a aac -b:a 128k -map 0 "Proxies\VID_20260930_075352_edit_proxy.mp4"
ffmpeg -y -i "VID20260930_075755_edit.mp4" -vf "scale=-2:720,fps=30" -c:v h264_qsv -global_quality 23 -preset medium -c:a aac -b:a 128k -map 0 "Proxies\VID20260930_075755_edit_proxy.mp4"
```

- `fps=30` — replace `30` with the source's rate (`25` for PAL, `30` for NTSC/phone, `23.976` or `24` for cinema). Use the `r_frame_rate` you saw above. If unsure, `fps=30` is the safest default for phone 1080p30.
- `-global_quality 23` is QSV's quality knob (lower = bigger/better, like CRF but for QSV). `23` is the sweet spot for proxies.
- If `h264_qsv` errors with `Unknown encoder`, use Option B.

### Option B — software fallback (any machine, including the Tecno if needed)

```cmd
ffmpeg -y -i "VID_20260930_075352_edit.mp4" -vf "scale=-2:720,fps=30" -c:v libx264 -crf 23 -preset fast -c:a aac -b:a 128k -map 0 "Proxies\VID_20260930_075352_edit_proxy.mp4"
ffmpeg -y -i "VID20260930_075755_edit.mp4" -vf "scale=-2:720,fps=30" -c:v libx264 -crf 23 -preset fast -c:a aac -b:a 128k -map 0 "Proxies\VID20260930_075755_edit_proxy.mp4"
```

- `-crf 23 -preset fast` is tuned for the N3350 — `fast` keeps CPU reasonable, `medium` would be slower for little proxy benefit.
- Do **not** use `-preset veryslow` — pointlessly slow for proxies.

> **Why `fps` filter, not `-vsync cfr -r 30`?** `fps=30` inside `-vf` normalises VFR to CFR before encoding and is what Frame.io / Adobe field notes actually recommend for phone sources. `-r` alone can duplicate/drop at the wrong stage and still fail the match.

---

## 4. When you *do* need ProRes / Cineform (not these files)

Only if a source has **>2 audio channels or timecode you must keep** — e.g. `.mxf` from a camera, `.mov` with 4-channel. MP4/H.264 cannot carry >2 channels through Premiere's proxy path or preserve timecode reliably.

```cmd
ffmpeg -y -i "input.mxf" -vf "scale=-2:720,fps=25" -c:v prores_ks -profile:v 0 -vendor ap10 -pix_fmt yuv422p10le -c:a pcm_s16le -map 0 "Proxies\input_proxy.mov"
```

- Profile `0` = ProRes Proxy (lightest). `vendor ap10` writes the Apple tag Premiere expects.
- These files are **much larger** than H.264 — only use when H.264 would fail the channel check.

A lighter alternative on Windows is GoPro Cineform (also accepted by Premiere, smaller than ProRes):

```cmd
ffmpeg -y -i "input.mxf" -vf "scale=-2:720,fps=25" -c:v cfhd -quality 8 -c:a pcm_s16le -map 0 "Proxies\input_proxy.mov"
```

For your two `VID_` edits, **skip this section**.

---

## 5. Batch the whole folder (Windows)

If you add more edits to `Videos\Editing`, run one loop instead of typing each file. This handles names with spaces and underscores correctly:

**Command Prompt (.bat):**
```bat
@echo off
cd /d "%USERPROFILE%\Videos\Editing"
if not exist "Proxies" mkdir "Proxies"
for %%F in (*.mp4 *.mov *.mxf) do (
  echo Processing %%F
  ffmpeg -y -i "%%F" -vf "scale=-2:720,fps=30" -c:v h264_qsv -global_quality 23 -preset medium -c:a aac -b:a 128k -map 0 "Proxies\%%~nF_proxy.mp4"
)
echo Done. Check Proxies\ for results.
pause
```

If `h264_qsv` is unavailable, replace ` -c:v h264_qsv -global_quality 23 -preset medium` with ` -c:v libx264 -crf 23 -preset fast`.

**PowerShell (same folder):**
```powershell
Set-Location "$env:USERPROFILE\Videos\Editing"
New-Item -ItemType Directory -Force -Path Proxies | Out-Null
foreach ($f in Get-ChildItem *.mp4,*.mov,*.mxf) {
  ffmpeg -y -i $f.Name -vf scale=-2:720,fps=30 -c:v h264_qsv -global_quality 23 -preset medium -c:a aac -b:a 128k -map 0 "Proxies/$($f.BaseName)_proxy.mp4"
}
```

> For 4K phone source on the N3350, change `720` to `540` — halves the decode load in Premiere's preview.

---

## 6. Attach in Premiere Pro 2023 (23.6)

No Media Encoder needed for this path:

1. **Import originals as normal** (Media Browser or drag from `Videos\Editing`).
2. In the **Project panel**, select both originals → **Right-click → Proxy → Attach Proxies…**
   - Premiere 2023 path is exactly that: `Proxy > Attach Proxies`. Older guides say `Proxy > Create Proxies` — that's the Media Encoder queue, not this.
3. In the dialog:
   - Check **Relink Others Automatically**
   - Set **Match File Properties** to **File Name + Media Start** (the only reliable match for FFmpeg-made proxies; timecode alone fails for phone files that have no timecode track)
   - Point to **one** proxy file in `Proxies\` → Premiere links the rest automatically if names match `*_proxy.mp4`
4. Enable the proxy toggle:
   - **Program Monitor wrench icon → Show Proxy Badge**
   - Drag **Toggle Proxies** to the button bar if missing: **Program Monitor wrench → Button Editor**, drag it in. Blue = proxies active.

**Test with one clip first.** Drop it to the timeline, toggle proxies on/off — you should see resolution switch but audio stay identical.

---

## 7. If Attach fails

| Error | Cause | Fix |
|---|---|---|
| `proxy media and full resolution media must have matching audio channels` | Source has 4/6/8 ch, proxy has 2 | Remake that proxy with the ProRes/Cineform command in §4 keeping `-map 0` and `pcm_s16le` |
| `must have matching framerate and overlapping timecode` | Source is VFR, proxy is CFR at wrong rate — or vice versa | Remake with `-vf "scale=-2:720,fps=RATE"` where `RATE` equals the source's `r_frame_rate` (e.g. `fps=25`, `fps=29.97`, `fps=30`) |
| `duration mismatch` | Trimmed proxy or `-t` used | Never trim when making proxies — duration must be sample-identical |
| Proxy attaches but audio is silent / wrong language | Source had multiple audio streams, `-map 0` was omitted | Remake with `-map 0` so all streams are present |
| `Unknown encoder 'h264_qsv'` | QSV not available (driver or FFmpeg build) | Use the `libx264` variant |

---

## 8. For your hardware specifically

- Stay with **H.264 720p** proxies on the Celeron. They are small, decode with Quick Sync, and scrub well.
- Set Premiere **Playback Resolution to 1/4** while editing, **Toggle Proxies ON**. That previews the 720p proxy at quarter res — the lightest possible path for the N3350.
- Close other apps while encoding proxies — the N3350 has 2 cores, and the `subtitles`/proxy encode will use both.
- Do the proxy encodes **on the Celeron itself** (QSV). The Tecno can do it (`h264_mediacodec`) but moving files back and forth costs more time than the Celeron saves.

---

## 9. One-click .bat for your folder

Save as `make_proxies.bat` **inside** `Videos\Editing` and double-click it:

```bat
@echo off
REM make_proxies.bat — Premiere 23.6 proxies for VID_*.mp4
REM Put this file in %USERPROFILE%\Videos\Editing and double-click.

cd /d "%~dp0"
if not exist "Proxies" mkdir "Proxies"

echo Checking encoder...
ffmpeg -encoders 2>nul | findstr /i "h264_qsv" >nul
if %errorlevel%==0 (
  echo Using h264_qsv (Quick Sync)
  set "VCODEC=-c:v h264_qsv -global_quality 23 -preset medium"
) else (
  echo QSV not found, using libx264
  set "VCODEC=-c:v libx264 -crf 23 -preset fast"
)

REM --- your two files ---
if exist "VID_20260930_075352_edit.mp4" (
  ffmpeg -y -i "VID_20260930_075352_edit.mp4" -vf "scale=-2:720,fps=30" %VCODEC% -c:a aac -b:a 128k -map 0 "Proxies\VID_20260930_075352_edit_proxy.mp4"
)
if exist "VID20260930_075755_edit.mp4" (
  ffmpeg -y -i "VID20260930_075755_edit.mp4" -vf "scale=-2:720,fps=30" %VCODEC% -c:a aac -b:a 128k -map 0 "Proxies\VID20260930_075755_edit_proxy.mp4"
)

REM --- any future files you add to this folder ---
for %%F in (*.mp4 *.mov *.mxf) do (
  if not "%%~nF"=="VID_20260930_075352_edit" if not "%%~nF"=="VID20260930_075755_edit" (
    if not exist "Proxies\%%~nF_proxy.mp4" (
      echo Processing %%F
      ffmpeg -y -i "%%F" -vf "scale=-2:720,fps=30" %VCODEC% -c:a aac -b:a 128k -map 0 "Proxies\%%~nF_proxy.mp4"
    )
  )
)

echo.
echo Done. Proxies are in Proxies\
echo Attach them in Premiere: Project panel ^> Right-click ^> Proxy ^> Attach Proxies
pause
```

> Change `fps=30` to match your source if `ffprobe` shows `25` or `29.97`. Everything else can stay as-is.
