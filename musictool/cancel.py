"""Cooperative cancellation for long jobs -- deliberately tkinter-free.

The GUI's Stop button and Ctrl+C in a terminal both just set a flag. The
download and lyrics loops check it between units of work, so a 447-item
playlist stops after the file it is on instead of in the middle of a
half-written .part -- and whatever already finished keeps its lyrics, because
those are embedded as each file lands.

Nothing here imports tkinter, so the CLI and the tests can use it too.
"""

import signal
import threading

_lock = threading.Lock()
_flag = False

# The string cmd_get recognises as "the user stopped this", so it can report a
# partial run as a partial run rather than as a failure.
CANCELLED = "stopped by user"


def request():
    """Ask the running job to stop. Safe to call from any thread, any time."""
    global _flag
    with _lock:
        _flag = True


def reset():
    global _flag
    with _lock:
        _flag = False


def requested():
    return _flag


def stop_process(proc, wait=3.0):
    """Terminate a subprocess, escalating to kill if it ignores the request."""
    if proc is None:
        return
    try:
        if proc.poll() is not None:
            return
    except Exception:
        return
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


def install_sigint(announce=None):
    """First Ctrl+C asks for a clean stop; a second one quits immediately."""
    hits = {"n": 0}

    def handler(signum, frame):
        hits["n"] += 1
        if hits["n"] == 1:
            request()
            if announce:
                announce()
            else:
                print("\nstopping after the current file... "
                      "(press Ctrl+C again to quit now)")
        else:
            raise KeyboardInterrupt

    try:
        signal.signal(signal.SIGINT, handler)
        return True
    except Exception:
        return False      # not the main thread, or SIGINT is unavailable
