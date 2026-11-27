"""Stopping long jobs -- deliberately tkinter-free.

Two ways, both from the GUI (Stop / Force stop) and from a terminal (Ctrl+C
once / twice):

* **Stop** sets a flag. The download and lyrics loops check it between units
  of work: yt-dlp is ended at its next line of output (with the ffmpeg it
  started -- that used to keep downloading on its own), files that already
  finished keep their lyrics, and an ffmpeg step that is writing a file is
  allowed to finish it.
* **Force stop** sets the flag *and* ends, at once, every program musictool
  started (yt-dlp, ffmpeg, ...) together with everything those started, cuts
  any wait short, and kills anything that is started after it. Unfinished
  downloads stay as .part files; running the same command again carries on
  (a --range part is fetched by `ffmpeg -y` and starts over).

Every long-running program is started through `popen` / `run` here so Force
stop can find it.

Nothing here imports tkinter, so the CLI and the tests can use it too.
"""

import os
import signal
import subprocess
import sys
import threading
import time

_lock = threading.Lock()
_flag = False
_forced = False
_procs = set()          # every Popen started through popen()/run(), until it ends

_WIN = sys.platform.startswith("win")
_NO_WINDOW = 0x08000000           # CREATE_NO_WINDOW: no console flashing up

# The string cmd_get recognises as "the user stopped this", so it can report a
# partial run as a partial run rather than as a failure.
CANCELLED = "stopped by user"


def request():
    """Ask the running job to stop. Safe to call from any thread, any time."""
    global _flag
    with _lock:
        _flag = True


def reset():
    global _flag, _forced
    with _lock:
        _flag = False
        _forced = False
        for p in [p for p in _procs if p.poll() is not None]:
            _procs.discard(p)


def requested():
    return _flag


def forced():
    return _forced


def force():
    """Force stop: set the flag and end every program started through
    popen()/run(), with all of their children, right now. Anything started
    after this is ended as soon as it starts (until reset()).
    -> how many were still running."""
    global _flag, _forced
    with _lock:
        _flag = True
        _forced = True
        live = [p for p in _procs if p.poll() is None]
    for p in live:
        kill_tree(p)
    return len(live)


def sleep(secs):
    """time.sleep that a Stop cuts short. -> False when it was cut short."""
    left = float(secs or 0)
    while left > 0:
        if _flag:
            return False
        step = min(0.25, left)
        time.sleep(step)
        left -= step
    return not _flag


def _no_console():
    """Windows, and this process has no console of its own: started with
    pythonw (the .vbs launcher, a shortcut). Every console program started
    from here -- yt-dlp, ffmpeg, deno -- would then get a new console window
    of its own: empty (its output comes to us), black, on top."""
    if not _WIN:
        return False
    try:
        import ctypes
        return not ctypes.windll.kernel32.GetConsoleWindow()
    except Exception:                                        # noqa: BLE001
        return False


def quiet(kw):
    """Popen keywords that start a console program hidden when we have no
    console (see _no_console). With a console (the CLI in a terminal) they
    are left alone: the program shares it, and Ctrl+C reaches it as before.
    The program's own children (yt-dlp's ffmpeg) share its hidden console,
    so they stay hidden too. -> kw"""
    flags = kw.get("creationflags", 0) or 0
    if _no_console() and not flags & 0x18:   # NEW_CONSOLE / DETACHED asked
        kw["creationflags"] = flags | _NO_WINDOW
    return kw


def popen(args, **kw):
    """subprocess.Popen, remembered so Force stop can end it (and started
    without a window of its own: quiet)."""
    p = subprocess.Popen(args, **quiet(kw))
    with _lock:
        _procs.add(p)
        late = _forced
    if late:                        # started after a Force stop: end it now
        kill_tree(p)
    return p


def done(p):
    """Forget a process started with popen() once it has ended."""
    with _lock:
        _procs.discard(p)


def run(args, *, input=None, capture_output=False, timeout=None, check=False,
        **kw):
    """subprocess.run, but Force stop can end it. Same arguments and result;
    a timeout ends the program and everything it started, then raises
    subprocess.TimeoutExpired like subprocess.run."""
    if capture_output:
        kw["stdout"] = subprocess.PIPE
        kw["stderr"] = subprocess.PIPE
    if input is not None:
        kw.setdefault("stdin", subprocess.PIPE)
    p = popen(args, **kw)
    try:
        try:
            out, err = p.communicate(input, timeout=timeout)
        except subprocess.TimeoutExpired:
            kill_tree(p)
            p.communicate()
            raise
        except BaseException:
            kill_tree(p)
            raise
    finally:
        done(p)
    r = subprocess.CompletedProcess(args, p.returncode, out, err)
    if check:
        r.check_returncode()
    return r


def _children_map():
    """{parent pid: [child pids]} for every process we can see (Linux,
    Android, macOS, BSD). Empty when it cannot be read."""
    kids = {}
    try:                                    # Linux / Android: no ps needed
        for d in os.listdir("/proc"):
            if not d.isdigit():
                continue
            try:
                with open(f"/proc/{d}/stat", "rb") as f:
                    st = f.read().decode("ascii", "replace")
                ppid = int(st[st.rindex(")") + 2:].split()[1])
            except (OSError, ValueError, IndexError):
                continue
            kids.setdefault(ppid, []).append(int(d))
        if kids:
            return kids
    except OSError:
        pass
    try:                                    # macOS / BSD
        out = subprocess.run(["ps", "-A", "-o", "pid=", "-o", "ppid="],
                             capture_output=True, text=True, timeout=10).stdout
        for line in out.splitlines():
            a = line.split()
            if len(a) == 2 and a[0].isdigit() and a[1].isdigit():
                kids.setdefault(int(a[1]), []).append(int(a[0]))
    except Exception:                       # noqa: BLE001
        pass
    return kids


def descendants(pid):
    """Every process started by `pid`, and by those, ... (not on Windows).
    Deepest last."""
    kids, out, todo = _children_map(), [], [pid]
    while todo:
        for c in kids.get(todo.pop(0), []):
            if c not in out and c != pid:
                out.append(c)
                todo.append(c)
    return out


def _signal(pids, sig):
    for pid in pids:
        try:
            os.kill(pid, sig)
        except (OSError, ProcessLookupError):
            pass


def _taskkill(proc):
    """Windows: end `proc` and its whole tree. -> True when taskkill ran."""
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True, timeout=20,
                       creationflags=_NO_WINDOW)
        return True
    except Exception:                       # noqa: BLE001
        return False


def kill_tree(proc):
    """End `proc` and everything it started, at once. yt-dlp starts ffmpeg
    (and on Windows yt-dlp.exe may itself be a launcher with yt-dlp inside
    it): killing only the top one left the real download running."""
    if proc is None:
        return
    try:
        if proc.poll() is not None:
            return
    except Exception:                       # noqa: BLE001
        return
    if _WIN:
        _taskkill(proc)
    else:
        _signal(list(reversed(descendants(proc.pid))), signal.SIGKILL)
    try:
        proc.kill()
    except Exception:                       # noqa: BLE001
        pass
    try:
        proc.wait(timeout=5)
    except Exception:                       # noqa: BLE001
        pass


def stop_process(proc, wait=3.0):
    """Stop a subprocess and what it started: ask first (ffmpeg then closes
    the file it is writing properly), kill whatever ignores it."""
    if proc is None:
        return
    try:
        if proc.poll() is not None:
            return
    except Exception:
        return
    if _WIN:
        # terminate() is TerminateProcess anyway, which takes only the top
        # process: yt-dlp's ffmpeg went on downloading in the background
        kill_tree(proc)
        return
    kids = descendants(proc.pid)      # read first: they lose their parent
    _signal(list(reversed(kids)), signal.SIGTERM)
    try:
        proc.terminate()
    except Exception:
        pass
    try:
        proc.wait(timeout=wait)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    deadline = time.time() + wait
    while kids and time.time() < deadline:
        kids = [k for k in kids if _alive(k)]
        if kids:
            time.sleep(0.1)
    _signal(kids, signal.SIGKILL)


def _alive(pid):
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            st = f.read().decode("ascii", "replace")
        return st[st.rindex(")") + 2:].split()[0] != "Z"
    except OSError:
        pass
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def install_sigint(announce=None):
    """First Ctrl+C asks for a clean stop; a second one is a Force stop: every
    program musictool started is ended with its children, then it quits."""
    hits = {"n": 0}

    def handler(signum, frame):
        hits["n"] += 1
        if hits["n"] == 1:
            request()
            if announce:
                announce()
            else:
                print("\nstopping... (press Ctrl+C again to force stop: "
                      "everything ends now)")
        else:
            n = force()
            print(f"\nforce stop: ended {n} running program(s) and everything "
                  f"they started; unfinished downloads stay as .part files "
                  f"(run the same command again to carry on; a --range part "
                  f"starts over)")
            raise KeyboardInterrupt

    try:
        signal.signal(signal.SIGINT, handler)
        return True
    except Exception:
        return False      # not the main thread, or SIGINT is unavailable
