# Android Emulator from Scratch — Verified Working Guide (Windows, no Android Studio)

**Tested on:** HP EliteBook 840 G6 · Intel Core i5-8365U (Whiskey Lake, 4C/8T, 15W) ·
Intel UHD 620 · Windows 11 build 26200 · JDK 17 · SDK at `C:\Android`

> **Ownership note:** This guide was validated on a **borrowed** EliteBook 840 G6.
> Your owned devices are the **Celeron N3350 laptop** and the **Tecno Spark 30C (Helio G81)**.
> Adjust `hw.ramSize` and `hw.cpu.ncore` to the machine you actually have that day.

This version is built from a real setup session. Every command and config value here
has been run, and the mistakes that cost time are documented so you can avoid them.

---

## Read this first — the three things that waste the most time

**1. `avdmanager` prints alarming `devices.xml` errors. They are harmless.**

You'll see something like:
```
Error: Could not load devices from C:\Android\system-images\android-34\...\devices.xml
Error: cvc-enumeration-valid: Value '420' is not facet-valid...
Error: Android Virtual Device 'MyPixel' already exists.
```
**The AVD gets created anyway.** `avdmanager` falls back to default hardware profiles.
Ignore it, add `--force` if it complains the device exists, and move on.

Do **not** start hand-editing `devices.xml` to silence those errors. It's a rabbit
hole with no benefit — the emulator never reads that file.

**2. The AVD `config.ini` that `avdmanager` generates is wrong for a laptop.**
It ships with `hw.gpu.enabled=no`, which software-renders the entire Android UI.
You must edit it. See Step 7 — this is mandatory, not optional.

**3. BIOS virtualization is the #1 silent blocker.** On HP business laptops VT-x is
often disabled at the factory. Check it *before* anything else.

---

## Step 1 — Enable virtualization in BIOS

1. Restart → press **F10** at boot (HP; F2 for Dell/Lenovo/ASUS/Acer)
2. Navigate to **Advanced → Power Options** (or **Advanced → System Options → Enabling Virtualization**)
3. Set **Intel Virtualization Technology (VT-x)** to **Enabled**
4. Save & exit

Check afterwards: **Settings → System → About → "Virtualization: Enabled"**

## Step 2 — Enable the Windows hypervisor (Admin Command Prompt)

```cmd
dism /online /enable-feature /featurename:HypervisorPlatform /all
dism /online /enable-feature /featurename:VirtualMachinePlatform /all
```

**Reboot.** These load at boot.

> Do **not** install the "Android Emulator Hypervisor Driver" (AEHD). WHPX,
> enabled above, is what the modern emulator uses. If a guide tells you to run
> `sdkmanager "extras;google;Android_Emulator_Hypervisor_Driver"`, skip it — the
> step is unnecessary. AEHD is being retired: emulator 37.1.11 (July 2026)
> migrates Windows users from AEHD to WHPX, and Google ends AEHD support on
> 31 December 2026. (Intel HAXM is already gone — removed in emulator 36.2.11,
> October 2025.)

## Step 3 — Install Java

```cmd
winget install EclipseAdoptium.Temurin.17.JDK
```

Close and reopen Command Prompt.

## Step 4 — Command-line SDK tools

1. Download **Command line tools only** (Windows) from
   <https://developer.android.com/studio#command-line-tools-only>
2. Build this exact structure — the `latest` folder name is mandatory:

```
C:\Android\cmdline-tools\latest\bin\sdkmanager.bat
C:\Android\cmdline-tools\latest\lib\
```

(Extract the zip → it yields a `cmdline-tools` folder → rename that to `latest` →
move it into `C:\Android\cmdline-tools\`)

## Step 5 — Environment variables

**System variables → New:**

| Variable | Value |
|---|---|
| `ANDROID_HOME` | `C:\Android` |
| `ANDROID_SDK_ROOT` | `C:\Android` |

**Path → New**, three entries:
```
%ANDROID_HOME%\cmdline-tools\latest\bin
%ANDROID_HOME%\platform-tools
%ANDROID_HOME%\emulator
```

Close and reopen Command Prompt.

## Step 6 — Download packages

```cmd
sdkmanager --licenses
```
`y` + Enter for each.

```cmd
sdkmanager "platform-tools" "emulator" "platforms;android-34" "system-images;android-34;google_apis_playstore;x86_64"
```

> The `WARNING: sdkmanager is deprecated` message is Google migrating to a new CLI.
> Noise — everything here still works.

## Step 7 — Create the AVD, then fix its config

```cmd
cd /d C:\Android
echo no | avdmanager create avd -n MyPixel -k "system-images;android-34;google_apis_playstore;x86_64" -d pixel_7 --force
```

*Expect `devices.xml` errors. Ignore them. Continue.*

Now edit the generated config — **this is the step that decides whether it's usable**:

```cmd
notepad "%USERPROFILE%\.android\avd\MyPixel.avd\config.ini"
```

Change these lines:

| Key | Default | Set to |
|---|---|---|
| `hw.gpu.enabled` | `no` | **`yes`** |
| `hw.gpu.mode` | `auto` | **`host`** |
| `hw.cpu.ncore` | `4` | **`2`** |
| `hw.ramSize` | `2G` | **`3072`** (16 GB machine) or `2048` (8 GB) |
| `hw.keyboard` | `no` | `yes` |
| `fastboot.forceColdBoot` | `no` | `yes` |

> **`hw.cpu.ncore=2` matters.** On a 4-core/8-thread laptop, giving the guest 4 cores
> starves Windows and the whole desktop crawls. Two cores + host GPU is the right split.
>
> **`hw.gpu.enabled=yes` matters most.** Without it the emulator software-renders every
> pixel and feels unusable.

## Step 8 — Verify, then first launch

```cmd
emulator -accel-check > accel.txt 2>&1
type accel.txt
```
Look for `WHPX(...) is installed and usable.` (The `accel:` number can look garbled
in the console — the WHPX line is the one that matters.)

```cmd
emulator -avd MyPixel -no-snapshot-load -no-boot-anim
```

- `-no-snapshot-load` — cold boot; a stale snapshot can hide config changes
- `-no-boot-anim` — skips the boot animation

First boot takes 3–5 minutes (dexopt + snapshot download). Watch from a second prompt:

```cmd
adb devices
adb shell getprop sys.boot_completed     → 1 when ready
```

## Step 9 — Day-to-day

```cmd
emulator -avd MyPixel
```

No flags needed. Config persists in `config.ini`.

---

## Camera

### The reality

**The emulator exposes only ONE camera to Android.** Check it:

```cmd
adb shell cmd media.camera get-number-of-cameras
```

Returns `1`. The stock Camera app therefore has no working front/back switch — this
is a virtual-HAL limitation, not a misconfiguration.

### The fix: map your webcam to the BACK slot

Don't try to use "front." Point the back camera at your laptop webcam and the app
will use it as its only camera.

**1.** Check what cameras your laptop has:
```powershell
Get-PnpDevice -Class Camera | Select-Object FriendlyName, Status
```
On the EliteBook 840 G6 this lists **HP HD Camera** and **HP IR Camera**.
**`webcam0` = HD colour camera.** (The IR one renders dark and grainy — that's how
you tell them apart if you guess wrong.)

**2.** Edit the config:
```cmd
notepad "%USERPROFILE%\.android\avd\MyPixel.avd\config.ini"
```
```
hw.camera.back=webcam0
hw.camera.front=none
```

**3.** Cold boot (the camera initialises at boot):
```cmd
emulator -avd MyPixel -no-snapshot-load -no-boot-anim
```

### ⚠️ Never do this

**Do not map `hw.camera.front` and `hw.camera.back` to the same physical webcam.**
That combination hard-froze an EliteBook and required a forced power-off.
Change one camera at a time; keep the other `emulated` or `none`.

### If the image is rotated

```
hw.camera.back.orientation=90     → try 0, 180 or 270, cold boot each time
```

### If you can't fit in frame

Your webcam is 16:9; the emulator's virtual HAL presents 4:3 and **centre-crops**,
so you lose about a quarter of the horizontal view. There is no FOV setting.

- **Sit closer** — the only real variable
- **Or** use **Extended Controls → Camera → source: Virtual Scene** (the `⋮` panel on
  the emulator's right toolbar) for a proper wide-angle synthetic room

For testing app camera *behaviour* — permissions, lifecycle, rotation — FOV rarely
matters. Real framing needs a physical phone.

---

## Troubleshooting — real errors hit in practice

| Symptom | Cause | Fix |
|---|---|---|
| `ERROR: x86_64 emulation currently requires hardware acceleration` | BIOS VT-x off, or no reboot after `dism` | Step 1, then Step 2 + reboot |
| Emulator runs but UI is a slideshow | `hw.gpu.enabled=no` (the default!) | Step 7 → set to `yes` + `host` |
| Whole machine crawls | `hw.cpu.ncore=4` on a 4-core laptop | Step 7 → `2` |
| Window invisible / off-screen | Window coords outside display | Task Manager → expand `qemu-system-x86_64` → Maximize. (Do not switch to `-gpu swiftshader_indirect` — software rendering is what Step 7 exists to avoid.) |
| Console froze mid-download | You clicked in the window (QuickEdit) | Press **Enter** or **Esc** |
| `devices.xml` / `cvc-*` errors from avdmanager | **Harmless.** AVD is created anyway | Ignore |
| "already exists" | AVD already made | Add `--force` |
| `emulator not recognized` | PATH not refreshed | Close and reopen Command Prompt |
| Terminal looks frozen at 99% during download | Console rendering, not a stall | Let it finish; redirect to file if worried |

---

## Getting files in and out

**Drag and drop** onto the emulator window → lands in `/sdcard/Download/`.

**adb** (scriptable, most reliable):
```cmd
adb push "C:\path\to\file.mp4" /sdcard/Download/
adb pull /sdcard/Download/file.mp4 "C:\Users\you\Videos\"
```

**Clipboard** is shared between host and guest automatically.

### Reaching a physical phone over the network — this works

The emulator is **NAT'd through the host, not isolated**. It can reach any device on
your physical LAN **by IP address**:

```cmd
adb shell ping -c 3 192.168.1.104      ← your phone's IP
```

Anything HTTP/TCP-based will work. What will **not** work is Wi-Fi Direct and hotspot
broadcast — those need Layer 2 radio access the emulator doesn't have. That is the
actual boundary, and it's narrower than "impossible."

---

## Notes for this specific machine (EliteBook 840 G6)

- **i5-8365U is a 15-watt chip.** Plug it in for sustained emulator use; keep it on a
  hard surface, not a bed or lap.
- **UHD 620 handles `-gpu host` fine.**
- **4C/8T** → `hw.cpu.ncore=2` is correct; 4 will starve Windows.
- RAM: `hw.ramSize=3072` on 16 GB, `2048` on 8 GB.
- Disk: allow ~15 GB free for the system image plus AVD data partition.

---

## Condensed checklist

```
□ BIOS: VT-x → Enabled
□ Admin CMD: dism HypervisorPlatform + VirtualMachinePlatform
□ Reboot
□ winget install EclipseAdoptium.Temurin.17.JDK
□ cmdline-tools → C:\Android\cmdline-tools\latest\bin
□ ANDROID_HOME, ANDROID_SDK_ROOT, 3× PATH
□ New CMD: sdkmanager --licenses
□ sdkmanager "platform-tools" "emulator" "platforms;android-34" "system-images;android-34;google_apis_playstore;x86_64"
□ avdmanager create avd ... --force        (ignore devices.xml errors)
□ Edit config.ini:  gpu.enabled=yes, gpu.mode=host, cpu.ncore=2,
                    ramSize=3072, keyboard=yes, forceColdBoot=yes
□ emulator -accel-check > accel.txt 2>&1
□ emulator -avd MyPixel -no-snapshot-load -no-boot-anim
□ adb shell getprop sys.boot_completed    → 1
□ (optional) hw.camera.back=webcam0
```

~30 minutes, most of it downloading a ~1.5 GB system image.