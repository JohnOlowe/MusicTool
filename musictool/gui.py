"""Tkinter GUI.

Design notes:
  * Option widgets are generated from the argparse parser, so every CLI flag is
    present here automatically -- adding a flag to cli.py adds a widget.
  * The most-used toggles are shown a second time in a "quick options" strip
    bound to the SAME variables as the generated widgets. It is another view of
    them, not a copy, so the two cannot disagree.
  * Running a job builds the exact argv the CLI would receive and calls
    cli.main(argv), with stdout/stderr redirected into the log pane. There is no
    second implementation of the options to drift out of sync.
  * Tabs and log live in a vertical PanedWindow, so the log can be dragged to
    any height (or resized with the buttons above it).
  * Each tab's body is a ScrollArea: one scrollbar pair for everything on the
    tab, so a short or narrow window can never push a control out of reach.
    The path field, the quick options and the run buttons are pinned outside
    it, so those stay on screen while the rest scrolls.
  * tkinter is imported at module level on purpose: if it is missing (common on
    Termux) the ImportError surfaces when cli.py imports this module, so the CLI
    can print install instructions instead of a traceback.
"""

import contextlib
import json
import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import cancel
from . import chat
from . import config as configmod
from . import download
from . import formats
from . import live
from . import progress
from . import uicmd
from .cli import build_parser, main as cli_main
from .util import parse_range

BOX_ON, BOX_OFF = "[x]", "[ ]"        # ASCII: renders in every Tk font
WHEEL = ("<MouseWheel>", "<Button-4>", "<Button-5>")   # Windows/mac, then X11


FINISH = "__finish__"     # sentinel: worker done -> bars to 100%


def open_path(path):
    """Open a file with whatever the system plays it with."""
    import subprocess
    import sys
    if hasattr(os, "startfile"):                      # Windows
        os.startfile(path)                            # noqa: S606
        return
    from .util import is_termux
    cmd = ("termux-open" if is_termux() else
           "open" if sys.platform == "darwin" else "xdg-open")
    subprocess.Popen([cmd, path], stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)


class QueueStream:
    """File-like object that forwards writes to the log queue."""

    def __init__(self, q):
        self.q = q

    def write(self, s):
        if s:
            self.q.put(str(s))

    def flush(self):
        pass


class ScrollArea(ttk.Frame):
    """A frame whose contents scroll when they outgrow the space available.

    The whole tab body goes inside one of these, so nothing can be pushed off
    screen by a short window. The canvas tracks the width it is given, so wide
    option grids are not clipped either; a horizontal scrollbar is the fallback
    when the window itself is narrower than the grid.

    The mouse wheel is bound application-wide and filtered by ancestry: a wheel
    event scrolls whichever ScrollArea the pointer is actually over, and is left
    alone when the pointer is over a widget that scrolls itself (the file list,
    the report tree, the log).
    """

    def __init__(self, parent, height=180):
        super().__init__(parent)
        # explicit small request: a default tk.Canvas asks for 378x289 and would
        # otherwise dictate the size of the whole tab
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0,
                                height=height, width=240)
        self.vsb = ttk.Scrollbar(self, orient="vertical",
                                 command=self.canvas.yview)
        self.hsb = ttk.Scrollbar(self, orient="horizontal",
                                 command=self.canvas.xview)
        self.frame = ttk.Frame(self.canvas)
        self._win = self.canvas.create_window((0, 0), window=self.frame,
                                              anchor="nw")
        self.canvas.bind("<Configure>", self._fit)
        self.frame.bind("<Configure>", lambda e: self._region())
        self.canvas.configure(yscrollcommand=self.vsb.set,
                              xscrollcommand=self.hsb.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vsb.grid(row=0, column=1, sticky="ns")
        self.hsb.grid(row=1, column=0, sticky="ew")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        for seq in WHEEL:
            self.canvas.bind_all(seq, self._wheel, add="+")

    def _region(self):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _fit(self, ev):
        self.canvas.itemconfigure(
            self._win, width=max(ev.width, self.frame.winfo_reqwidth()))
        self._region()

    def _over_me(self, w):
        """Is w inside this area? Stops at any widget that scrolls itself."""
        while w is not None:
            if w is self.canvas or w is self.frame:
                return True
            if isinstance(w, (ttk.Treeview, tk.Listbox)):
                return False
            if isinstance(w, tk.Text):
                # A short box that cannot scroll itself should not swallow the
                # wheel: the three-line URL field is the first thing on the
                # tab, and the tab is what you meant to scroll.
                try:
                    top, bottom = w.yview()
                    if float(bottom) - float(top) >= 0.999:
                        w = getattr(w, "master", None)
                        continue
                except Exception:                            # noqa: BLE001
                    pass
                return False
            w = getattr(w, "master", None)
        return False

    def _wheel(self, ev):
        if not self._over_me(getattr(ev, "widget", None)):
            return
        delta = getattr(ev, "delta", 0) or 0
        step = (-1 if delta > 0 else 1) if delta else \
            (-1 if getattr(ev, "num", 0) == 4 else 1)
        self.canvas.yview_scroll(step, "units")

    def to_top(self):
        self.canvas.yview_moveto(0.0)


# The parser's own group titles are internal names; these are what a person
# reading the window expects to see.
GROUP_TITLES = {
    "download": "files, folders and access",
    "quality": "quality and format",
    "lyrics": "lyrics",
    "listing": "listing",
    "paths": "paths",
    "options": "options",
}


class OptionsPane(ttk.Frame):
    """Auto-generated widgets for every option of one subcommand."""

    COLUMNS = 2

    def __init__(self, parent, sub, cfg, on_change=None, saved=None):
        super().__init__(parent)
        self.sub = sub
        self.vars = {}
        self.on_change = on_change
        self.saved = dict(saved or {})
        self.parser = build_parser()
        self.show_all = tk.BooleanVar(value=False)
        self._groups = []      # [(labelframe, [(dest, cell, spec, grid opts)])]
        self._count = tk.StringVar(value="")
        self._build(cfg)

    def touched(self):
        """The user moved something by hand -- modes stop overriding it."""
        self._compact()        # a value you just changed is worth showing
        if self.on_change:
            self.on_change()

    def apply(self, values):
        """Set the widgets a mode has an opinion about; leave the rest alone.

        Writes the variables directly, so it does not look like a user click
        and does not re-arm the "the user is driving" flag.
        """
        for dest, val in values.items():
            v = self.vars.get(dest)
            if v is None:
                continue
            if val in ("", None) and dest not in uicmd.MODE_RESET:
                # A blank is "the mode has no opinion", not "empty this out":
                # your size ceiling, cookies and speed limit survive a mode
                # switch instead of having to be typed again.
                continue
            if isinstance(v, tk.BooleanVar):
                v.set(bool(val))
            else:
                v.set("" if val is None else str(val))
        self._compact()

    def _build(self, cfg):
        """Widgets come from uicmd.widget_specs, i.e. from the parser itself."""
        specs = uicmd.widget_specs(self.sub, cfg, self.parser)
        groups = {}
        for spec in specs:
            if spec["dest"] in self.saved:
                # Start where you left off, not at the CLI default: a restart
                # must not be a form to fill in all over again.
                spec = dict(spec, default=self.saved[spec["dest"]])
            groups.setdefault(spec["group"], []).append(spec)
        # The fine-tune strip above already carries these, so the grid does
        # not repeat them -- one place to look for each setting.
        self._quick_dests = {q["dest"] for q in
                             uicmd.quick_specs(self.sub, parser=self.parser)}
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=8, pady=(2, 0))
        self._all_btn = ttk.Button(bar, command=self._toggle_all)
        self._all_btn.pack(side="left")
        ttk.Label(bar, textvariable=self._count, foreground="#888").pack(
            side="left", padx=8)
        for title, items in groups.items():
            box = ttk.LabelFrame(self, text=GROUP_TITLES.get(title, title))
            box.pack(fill="x", padx=6, pady=4)
            self._groups.append((box, [self._widget(box, sp) for sp in items]))
        self._compact()

    # ---------------------------------------------------------- folding
    def _toggle_all(self):
        self.show_all.set(not self.show_all.get())
        self._compact()

    def _worth_showing(self, dest, spec):
        """Does this row earn its place on the screen?

        A toggle earns it by being on when it starts off (or the reverse); a
        field earns it by having something in it, or by differing from where
        it started. Blank, untouched fields are the bulk of the forty-odd
        options and are what made the old grid a wall.
        """
        v = self.vars.get(dest)
        if v is None:
            return False
        if dest in getattr(self, "_quick_dests", ()):
            return False            # already on the fine-tune strip
        want = spec["default"]
        if isinstance(v, tk.BooleanVar):
            return bool(v.get()) != bool(want)
        have = str(v.get() or "").strip()
        want = "" if want in (None, "") else str(want).strip()
        return bool(have) or have != want

    def _compact(self):
        """Show the rows that have something in them; fold the rest away.

        A mode, or the values restored from last session, decide most of these
        forty-odd options, and most of them are blank. What stays on screen is
        the handful with a value in them plus anything you have changed; the
        rest sit behind one button -- reachable, but not filling the window.
        """
        shown = hidden = 0
        for _box, rows in self._groups:
            n = 0
            for dest, cell, spec, gopts in rows:
                if self.show_all.get() or self._worth_showing(dest, spec):
                    row, col = divmod(n, self.COLUMNS)
                    cell.grid(row=row, column=col, **gopts)
                    n += 1
                else:
                    cell.grid_remove()
                    hidden += 1
            shown += n
        self._all_btn.configure(text=f"show all {shown + hidden} options")
        self._count.set("" if not hidden
                        else f"{hidden} more folded away")
        return shown

    def _widget(self, parent, spec):
        """One option in its own cell, so a row can be folded or re-flowed.

        -> (dest, cell, spec, grid options)
        """
        dest, kind, label = spec["dest"], spec["kind"], spec["label"]
        start = spec["default"]
        outer = ttk.Frame(parent)
        if kind in ("check", "check_off"):
            self._toggle(outer, spec)
            return (dest, outer, spec, {"sticky": "w", "padx": 6, "pady": 1})
        v = tk.StringVar(value="" if start in (None, "") else str(start))
        f = ttk.Frame(outer)
        f.pack(fill="x")
        ttk.Label(f, text=label + ":").pack(side="left")
        if kind == "choice":
            c = ttk.Combobox(f, textvariable=v, width=10, state="readonly",
                             values=spec["choices"])
            c.pack(side="left", padx=4)
            c.bind("<<ComboboxSelected>>", lambda e: self.touched())
        else:
            e = ttk.Entry(f, textvariable=v, width=18)
            e.pack(side="left", padx=4, fill="x", expand=True)
            e.bind("<KeyRelease>", lambda ev: self.touched())
        if spec.get("browse"):
            ttk.Button(f, text="Browse...", width=9,
                       command=lambda vv=v, w=spec["browse"]: self.browse(vv, w)
                       ).pack(side="left", padx=(0, 2))
        if spec.get("hint"):
            ttk.Label(outer, text=spec["hint"], foreground="#888",
                      wraplength=340, justify="left").pack(anchor="w")
        self.vars[dest] = v
        return (dest, outer, spec, {"sticky": "we", "padx": 6, "pady": 2})

    def browse(self, var, what):
        """Fill a path field from a real dialog instead of by typing it."""
        cur = (var.get() or "").strip()
        if what == "dir":
            pick = filedialog.askdirectory(
                initialdir=cur if os.path.isdir(cur) else os.getcwd())
        else:
            pick = filedialog.askopenfilename(
                initialdir=os.path.dirname(cur) or os.getcwd(),
                filetypes=[("all files", "*.*")])
        if pick:
            var.set(pick)

    def _toggle(self, parent, spec, row=None, column=None):
        """A checkbutton on the pane's own variable for spec['dest'].

        check_off semantics: the variable means "feature on"; unticking emits
        the --no-* flag.
        """
        dest = spec["dest"]
        v = self.vars.get(dest)
        if v is None:
            v = tk.BooleanVar(value=bool(spec["default"]))
            self.vars[dest] = v
        text = spec["label"]
        if spec["kind"] == "check_off":
            text = f"{text}  (untick = {spec['flag']})"
        cb = ttk.Checkbutton(parent, text=text, variable=v,
                             command=self.touched)
        if row is None:
            cb.pack(anchor="w", padx=6, pady=1)
        else:
            cb.grid(row=row, column=column, sticky="w", padx=6, pady=2)
        return cb

    def quick_row(self, parent, text="quick options", per_row=3):
        """Strip with the common toggles, same variables.

        Laid out on a wrapping grid (per_row across) so on a short window it
        takes two or three rows instead of five.
        """
        bar = ttk.LabelFrame(parent, text=text)
        bar.pack(fill="x", padx=8, pady=(2, 2))
        for i, spec in enumerate(uicmd.quick_specs(self.sub, parser=self.parser)):
            row, col = divmod(i, per_row)
            self._toggle(bar, spec, row=row, column=col)
        return bar

    def values(self):
        return {d: v.get() for d, v in self.vars.items()}


class FilePicker(ttk.Frame):
    """Tick which files a run should touch.

    Every file starts ticked, so "run" means "the whole folder" until you
    narrow it: untick a few to skip them, or use None / Only highlighted to
    build a subset. Nothing is fetched for an unticked file.
    """

    COLUMNS = ("state", "title", "artist", "dur", "note")
    PER_ROW = 5

    def __init__(self, parent, path_getter, options_getter=None):
        super().__init__(parent)
        self.path_getter = path_getter
        self.options_getter = options_getter or (lambda: {})
        self.rows = []
        self.root = ""
        self.loaded_for = ""
        self._build()

    def _build(self):
        # A grid, not a single packed row: nine buttons in one line overflow a
        # narrow window and the last ones become unreachable.
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=8, pady=(4, 0))
        ttk.Label(bar, text="Files:").grid(row=0, column=0, sticky="w",
                                           padx=(0, 4))
        buttons = [("Load file list", "load"), ("All", "all"), ("None", "none"),
                   ("Invert", "invert"), ("Only needing lyrics", "needs"),
                   ("Only already OK", "ok"), ("Only shared lyrics", "shared"),
                   ("Skip highlighted", "skip"), ("Only highlighted", "only")]
        for i, (label, mode) in enumerate(buttons):
            row, col = divmod(i, self.PER_ROW)
            ttk.Button(bar, text=label, command=lambda m=mode: self.apply(m)
                       ).grid(row=row, column=col + 1, sticky="w", padx=2,
                              pady=1)

        wrap = ttk.Frame(self)
        wrap.pack(fill="both", expand=True, padx=8, pady=4)
        self.tree = ttk.Treeview(wrap, columns=self.COLUMNS, height=6,
                                 selectmode="extended")
        self.tree.heading("#0", text="run?  file")
        for cid, text, w in (("state", "state", 70), ("title", "title", 200),
                             ("artist", "artist", 140), ("dur", "dur", 42),
                             ("note", "note", 220)):
            self.tree.heading(cid, text=text)
            self.tree.column(cid, width=w, anchor="w")
        self.tree.column("#0", width=290)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<Button-1>", self._click)
        self.tree.bind("<space>", self._space)

        self.summary = tk.StringVar(
            value="no file list loaded — every file in the folder will be used")
        ttk.Label(self, textvariable=self.summary, foreground="#555").pack(
            anchor="w", padx=8, pady=(0, 4))

    # ---------------------------------------------------------------- loading
    def load(self, quiet=False):
        p = (self.path_getter() or "").strip()
        if not p:
            if not quiet:
                messagebox.showwarning("musictool", "Choose a folder or file first.")
            return False
        opts = self.options_getter() or {}
        inv = uicmd.inventory([p], recursive=bool(opts.get("recursive")),
                              lyrics_dir=opts.get("lyrics_dir") or None)
        self.rows = inv["rows"]
        self.root = inv["root"]
        self.loaded_for = os.path.abspath(os.path.expanduser(p))
        self._refresh()
        if not self.rows and not quiet:
            if not os.path.exists(p):
                messagebox.showwarning("musictool",
                                       f"That path doesn't exist:\n{p}")
            else:
                messagebox.showinfo("musictool", "No audio files found there.")
        return bool(self.rows)

    def _refresh(self):
        self.tree.delete(*self.tree.get_children())
        for r in self.rows:
            self.tree.insert("", "end", iid=str(r["id"]), text=self._label(r),
                             values=(r["state"], r["title"], r["artist"],
                                     r["duration"], r["note"]))
        self._summarise()

    @staticmethod
    def _label(r):
        return f'{BOX_ON if r["sel"] else BOX_OFF}  {r["name"]}'

    def _paint(self, row):
        self.tree.item(str(row["id"]), text=self._label(row))

    def _summarise(self):
        if not self.rows:
            self.summary.set("no file list loaded — every file in the folder "
                             "will be used")
            return
        sel = sum(1 for r in self.rows if r["sel"])
        need = sum(1 for r in self.rows if r["needs"])
        self.summary.set(f"{len(self.rows)} file(s) listed · {sel} will be "
                         f"processed · {need} need fixing")

    # ------------------------------------------------------------- interaction
    def _click(self, event):
        row = self.tree.identify_row(event.y)
        if not row:
            return
        self._flip([row])

    def _space(self, _event=None):
        self._flip(list(self.tree.selection()))
        return "break"

    def _flip(self, iids):
        for iid in iids:
            r = self.rows[int(iid)]
            r["sel"] = not r["sel"]
            self._paint(r)
        self._summarise()

    def apply(self, mode):
        if mode == "load":
            return self.load()
        if not self.rows and not self.load(quiet=True):
            return False
        if mode in ("skip", "only"):
            hl = {int(i) for i in self.tree.selection()}
            for r in self.rows:
                r["sel"] = (r["id"] in hl) if mode == "only" else (
                    False if r["id"] in hl else r["sel"])
        else:
            uicmd.select(self.rows, mode)
        self._refresh()
        return True

    # ------------------------------------------------------------- what to run
    def positionals(self, default):
        """Paths to hand the CLI: the folder when everything is ticked.

        A list loaded for a different folder is ignored rather than applied to
        the wrong files.
        """
        want = os.path.abspath(os.path.expanduser(default or ""))
        if not self.rows or self.loaded_for != want:
            return [default], "whole folder"
        return uicmd.pick_paths(self.rows, self.root)


def run():
    cfg = configmod.load()
    root = tk.Tk()
    root.title("musictool — download & lyrics")
    root.geometry("1080x800")
    root.minsize(780, 520)
    q = queue.Queue()
    # Progress events come from the worker thread. Tkinter is not thread-safe,
    # so the hook only ever does pq.put(...) -- the same main-thread pump that
    # drains the log also applies them. (Calling root.after() from the worker
    # is what silently did nothing on Windows: the bar never moved.)
    pq = queue.Queue()
    # ...and so must every other callback into the widgets: a worker never
    # touches a tkinter object, it queues a closure that pump() runs.
    uq = queue.Queue()
    status = tk.StringVar(value="idle")
    report_headline = tk.StringVar(value="no report loaded")

    # ------------------------------------------------------------ log plumbing
    def log(msg):
        # One call, one line. Command output arrives through QueueStream with
        # its own newlines; these are the GUI's own notes, and without this
        # they ran into each other ("...for 'X'copied: musictool get ...").
        msg = str(msg)
        q.put(msg if msg.endswith("\n") else msg + "\n")

    def pump():
        # Reschedule before doing any work: one bad event must not be able to
        # stop the loop, or the log and the progress bar freeze for the rest
        # of the session (that is exactly what an exception here used to do).
        root.after(120, pump)
        try:
            sync_mode()          # the mode follows the URL you just pasted
        except Exception as e:                                  # noqa: BLE001
            log(f"!! {type(e).__name__}: {e}")
        # callbacks queued by worker threads -- they must run here, on the
        # main thread, not in the thread that produced them.
        try:
            while True:
                cb = uq.get_nowait()
                try:
                    cb()
                except Exception as e:                        # noqa: BLE001
                    log(f"!! {type(e).__name__}: {e}")
        except queue.Empty:
            pass
        try:
            while True:
                outbox.configure(state="normal")
                outbox.insert("end", q.get_nowait())
                outbox.see("end")
                outbox.configure(state="disabled")
        except queue.Empty:
            pass
        newest_pct, others, finished = {}, [], False
        try:
            while True:
                ev = pq.get_nowait()
                if ev == FINISH:
                    finished = True
                elif (ev or {}).get("type") == "pct":
                    # redraws: only the last one matters -- per download and
                    # per stream (video and audio side by side report apart)
                    newest_pct[(ev.get("tag"), ev.get("stream"))] = ev
                else:
                    others.append(ev)    # item/file/done: none may be dropped,
        except queue.Empty:              # or the bar loses the playlist position
            pass
        for ev in others:
            _apply_progress(ev)
        for ev in newest_pct.values():
            _apply_progress(ev)
        if finished:
            _apply_progress(FINISH)

    def busy(on):
        state = "disabled" if on else "normal"
        for w in run_buttons:
            if w in queue_ok:
                continue        # stays live: clicking it adds to the queue
            try:
                w.configure(state=state)
            except Exception:
                pass
        try:                    # bget may not exist yet on the first pass
            bget.configure(text=get_label())
        except Exception:
            pass
        # Stop deliberately keeps its state: it stays clickable while every
        # other button is greyed out, and while idle it can clear a request
        # you made by mistake.
        if cancel.requested():
            status.set(("force stopping..." if cancel.forced() else
                        "stopping...") if on else "stopped")
        else:
            status.set("working..." if on else "idle")

    def ui(fn):
        """Queue a widget touch for the main thread (pump runs it)."""
        uq.put(fn)

    # How many jobs are in flight. This is data, not a widget, so the worker
    # updates it directly: Stop must know the truth the moment a job starts
    # and the moment it ends, not a pump tick later.
    running = {"n": 0}

    def stop_now():
        """Ask the running job to stop. Whatever finished keeps its lyrics.

        Always available. With nothing running it says so instead of quietly
        arming a stop that the next run would inherit.
        """
        if not running["n"]:
            if cancel.requested():
                cancel.reset()
                log("cleared a stop request left over from the last run")
            else:
                log("nothing is running -- there is nothing to stop")
            status.set("idle")
            return
        cancel.request()
        status.set("stopping...")
        log("\nstopping -- yt-dlp (and the ffmpeg it started) is stopped at "
            "its next line, a file being written is finished, files already "
            "downloaded keep their lyrics. Force stop ends everything now.")

    def force_now():
        """Force stop: end every program musictool started, with whatever
        they started, at once. Nothing more is done (no lyrics, no merging);
        unfinished downloads stay as .part files for next time."""
        if not running["n"]:
            n = cancel.force()          # strays from an earlier run, if any
            cancel.reset()
            log(f"force stop: ended {n} leftover program(s)" if n else
                "nothing is running -- there is nothing to stop")
            status.set("idle")
            return
        status.set("force stopping...")
        log("\nforce stop -- ending yt-dlp, ffmpeg and everything they "
            "started, now")

        def kill():
            n = cancel.force()
            log(f"force stop: ended {n} running program(s). Unfinished "
                f"downloads stay as .part files -- run the same thing again "
                f"to carry on from there (a --range part starts over).")
        threading.Thread(target=kill, daemon=True).start()

    def worker(fn, after=None):
        # Called from a button, i.e. the main thread, so grey the other
        # buttons out here rather than a pump tick later: there should be no
        # window in which a second run can be started.
        running["n"] += 1
        busy(True)

        def go():
            rc = None
            try:
                rc = fn()
            except SystemExit as e:            # cli.main can raise via argparse
                log(f"!! {e}")
            except Exception as e:
                log(f"!! {type(e).__name__}: {e}")
            finally:
                running["n"] = max(0, running["n"] - 1)
                ui(lambda: busy(False))
                pq.put(FINISH)
                if after:
                    ui(lambda: after(rc))
        threading.Thread(target=go, daemon=True).start()

    # Jobs waiting their turn. Pasting more links mid-download used to be
    # impossible -- the button was greyed out -- so now it queues.
    pending = []

    def _start(argv, after=None):
        cancel.reset()
        reset_progress()
        log("\n$ " + uicmd.quote_cmd(argv) + "\n")

        def job():
            with contextlib.redirect_stdout(QueueStream(q)), \
                    contextlib.redirect_stderr(QueueStream(q)):
                return cli_main(argv)

        def finished(rc):
            if after:
                after(rc)
            if not pending:
                return
            if cancel.requested():
                log(f"\n==> stopped -- dropping the {len(pending)} queued "
                    f"job(s) rather than starting them into a stop")
                pending.clear()
                return
            nxt, cb = pending.pop(0)
            log(f"\n==> next in the queue"
                f"{f' ({len(pending)} more behind it)' if pending else ''}")
            _start(nxt, cb)

        worker(job, after=finished)

    def do_argv(argv, after=None):
        """Run a CLI command on the worker thread, echoing it first.

        With a job already running it goes on the queue instead: you can keep
        adding links while the first batch is still downloading.
        """
        remember_all()          # what you ran is what comes back next time
        if running["n"]:
            pending.append((list(argv), after))
            log(f"\n+ queued: {uicmd.quote_cmd(argv)}")
            log(f"  {len(pending)} job(s) waiting -- this starts the moment "
                f"the current one finishes")
            status.set(f"working... {len(pending)} queued")
            return
        _start(argv, after)

    def remember(**paths):
        """Persist any paths/URLs the user just used."""
        changed = False
        for kind, val in paths.items():
            if not val:
                continue
            if kind == "url":
                for u in str(val).splitlines():
                    if u.strip():
                        uicmd.add_url(cfg, u.strip())
                        changed = True
            else:
                uicmd.add_path(cfg, val, key="recent_paths")
                changed = True
        if changed:
            configmod.save(cfg)
            refresh_history()

    # ------------------------------------------------------------ shared bits
    bar = ttk.Frame(root)                      # status bar, pinned at the bottom
    bar.pack(side="bottom", fill="x", padx=10, pady=(0, 6))
    ttk.Label(bar, textvariable=status).pack(side="left")
    ttk.Label(bar, textvariable=report_headline, foreground="#0a6").pack(side="right")
    # Stop is the one control that must never be greyed out: it is what you
    # reach for precisely when a run is going wrong.
    # Force stop sits next to it, for when "at the next safe point" is not
    # soon enough: everything started is ended at once.
    force_btn = ttk.Button(bar, text="Force stop", width=11, command=force_now)
    force_btn.pack(side="right", padx=(0, 6))
    stop_btn = ttk.Button(bar, text="Stop", width=8, command=stop_now)
    stop_btn.pack(side="right", padx=6)

    split = tk.PanedWindow(root, orient="vertical", sashwidth=9,
                           sashrelief="raised", bg="#b8b8b8", opaqueresize=True,
                           borderwidth=0)
    split.pack(fill="both", expand=True, padx=8, pady=8)

    nb = ttk.Notebook(root)
    split.add(nb, minsize=170, stretch="always")

    history_values = uicmd.recent_paths(cfg)
    url_history_values = uicmd.recent_paths(cfg, key="recent_urls")
    panes = {}
    areas = {}
    # Everything you set in the window last time, keyed by tab. Restored below
    # into the widgets themselves, and rewritten whenever anything moves, so a
    # restart is never a form to fill in again.
    SAVED = dict(cfg.get("gui_opts") or {})
    EXTRA = dict(cfg.get("gui_extra") or {})
    live_state = {}         # the Live streams tab's fields, once it exists
    run_buttons = []
    # Buttons that must stay clickable mid-run, because clicking one now means
    # "do this next" rather than "start a second download".
    queue_ok = []
    path_combos = []
    url_combos = []

    _save_pending = {"t": False}

    def remember_all():
        """Persist every widget in the window, on every tab.

        Written straight to the config file rather than on exit, so a crash or
        a killed process loses nothing either.
        """
        try:
            opts = {sub: dict(pane.values()) for sub, pane in panes.items()}
            extra = {"urls": urls.get("1.0", "end").strip(),
                     "download_mode": current_mode(),
                     "more_options": bool(adv_open.get()),
                     "list_audio": bool(list_audio.get()),
                     "show_all_options": bool(panes["get"].show_all.get())}
            extra.update({k: v.get() for k, v in live_state.items()})
            configmod.remember(gui_opts=opts, gui_extra=extra)
        except Exception as e:                                 # noqa: BLE001
            log(f"!! could not save your settings: {type(e).__name__}: {e}")

    def remember_soon():
        """Coalesce a burst of edits into one write."""
        if _save_pending["t"]:
            return
        _save_pending["t"] = True

        def _go():
            _save_pending["t"] = False
            remember_all()
        root.after(400, _go)

    def refresh_history():
        vals = uicmd.recent_paths(cfg)
        history_values.clear()
        history_values.extend(vals)
        for cb in path_combos:
            cb.configure(values=vals)
        for cb in url_combos:
            cb.configure(values=uicmd.recent_paths(cfg, key="recent_urls"))

    def path_field(parent, initial, label="Folder / file:"):
        f = ttk.Frame(parent)
        f.pack(fill="x", padx=8, pady=(8, 2))
        ttk.Label(f, text=label).pack(side="left")
        var = tk.StringVar(value=initial)
        cb = ttk.Combobox(f, textvariable=var, values=history_values, width=52)
        cb.pack(side="left", padx=6, fill="x", expand=True)
        path_combos.append(cb)

        def browse():
            d = filedialog.askdirectory()
            if d:
                var.set(d)
        ttk.Button(f, text="Browse...", command=browse).pack(side="left")
        return var

    def after_run(default_dir_getter):
        def _after(rc):
            log(f"\n(exit {rc})")
            d = default_dir_getter()
            if d:
                rpt = os.path.join(d, "lyrics-report.txt")
                if os.path.exists(rpt):
                    report_path.set(rpt)
                    load_report_tab()
        return _after

    # ================================================== Download tab
    dl = ttk.Frame(nb)
    nb.add(dl, text="  Download  ")
    # ONE scrolling surface for the whole tab. The URL box, the mode, the
    # fine-tune strip and the full option list all live inside it, so the wheel
    # scrolls the tab wherever the pointer is -- you do not have to aim at the
    # options first -- and the part above "More options" moves with the part
    # below it instead of staying stuck. Only the run buttons are pinned.
    areas["get"] = ScrollArea(dl)
    body = areas["get"].frame
    ttk.Label(body, text="URL(s), one per line — video or playlist:").pack(
        anchor="w", padx=8, pady=(8, 2))
    urls = tk.Text(body, height=3)
    urls.pack(fill="x", padx=8)
    ucb = ttk.Combobox(body, values=url_history_values, state="readonly", width=70)
    ucb.pack(fill="x", padx=8, pady=2)
    ucb.bind("<<ComboboxSelected>>", lambda e: urls.insert("end", ucb.get() + "\n"))
    url_combos.append(ucb)
    ttk.Label(body, text="↑ recently used URLs (select to insert)",
              foreground="#888").pack(anchor="w", padx=8)

    # ================================================= the mode
    # Two questions decide nine checkboxes, so ask those two and set the rest.
    # "Music or video" and "one item or a playlist" are shown as a grid because
    # they are independent: every combination is a mode that already exists.
    user_touched = {"v": False}

    def on_options_changed():
        user_touched["v"] = True
        describe_mode()
        remember_soon()

    whatvar = tk.StringVar(value="music")
    scopevar = tk.StringVar(value="single")
    mode_desc = tk.StringVar(value="")

    mbox = ttk.LabelFrame(body, text="what are you downloading?")
    mbox.pack(fill="x", padx=8, pady=(4, 2))
    _wr = ttk.Frame(mbox)
    _wr.pack(fill="x", padx=6, pady=(2, 0))
    for val, txt in (("music", "Music  (audio + lyrics)"),
                     ("video", "Video  (keep the picture)")):
        ttk.Radiobutton(_wr, text=txt, value=val, variable=whatvar,
                        command=lambda: mode_picked()).pack(side="left", padx=6)
    _sr = ttk.Frame(mbox)
    _sr.pack(fill="x", padx=6, pady=(0, 2))
    for val, txt in (("single", "One item"),
                     ("playlist", "A whole playlist")):
        ttk.Radiobutton(_sr, text=txt, value=val, variable=scopevar,
                        command=lambda: mode_picked()).pack(side="left", padx=6)
    ttk.Label(body, textvariable=mode_desc, foreground="#06a",
              wraplength=900, justify="left").pack(anchor="w", padx=10)

    panes["get"] = OptionsPane(body, "get", cfg, on_change=on_options_changed,
                               saved=SAVED.get("get"))
    # The handful of toggles worth seeing stay on the tab; the long tail of
    # options (cookies, proxy, speed, retries, subtitle languages) hides behind
    # one checkbox, because a mode has already set all of it sensibly.
    quickbar = ttk.Frame(body)
    quickbar.pack(fill="x", padx=8)
    panes["get"].quick_row(quickbar, text="fine-tune")
    adv_open = tk.BooleanVar(value=bool(EXTRA.get("more_options")))
    ttk.Checkbutton(
        body, text="More options  (cookies, proxy, speed limit, subtitles, "
                   "retries, filename template\u2026)",
        variable=adv_open, command=lambda: toggle_adv()).pack(anchor="w", padx=8)

    def toggle_adv():
        if adv_open.get():
            panes["get"].pack(fill="x")
        else:
            panes["get"].pack_forget()

    def current_mode():
        return uicmd.make_mode(whatvar.get(), scopevar.get())

    def describe_mode():
        mode = current_mode()
        txt = uicmd.MODE_LABELS[mode] + ":  " + uicmd.MODE_SUMMARY[mode]
        off = uicmd.mode_drift(panes["get"].values(), mode)
        if off:
            # the summary describes the mode, not the checkboxes -- so once
            # they disagree it has to admit it rather than keep promising
            txt += "   \u00b7  changed by hand: " + ", ".join(sorted(off))
        mode_desc.set("\u2192  " + txt)

    def apply_mode(mode):
        """Set every checkbox the mode has an opinion about."""
        whatvar.set(uicmd.MODE_WHAT[mode])
        scopevar.set(uicmd.MODE_SCOPE[mode])
        panes["get"].apply(uicmd.MODE_VALUES[mode])
        describe_mode()
        try:
            # get_label() rather than the bare mode text: while a download is
            # running the button means "queue this", and the pump refreshes
            # this line every tick, which would otherwise talk it back.
            bget.configure(text=get_label())
        except NameError:
            pass            # first apply happens before the buttons exist

    def mode_picked():
        """The user chose a mode by hand: obey it, and stop guessing."""
        user_touched["v"] = False
        apply_mode(current_mode())
        remember_all()

    def sync_mode():
        """Follow the URL: a playlist link means a playlist run.

        Skipped once you have touched an option by hand -- at that point you
        are driving and the tab must not fight you.
        """
        u = [x.strip() for x in urls.get("1.0", "end").splitlines() if x.strip()]
        joined = "\n".join(u)
        if joined == url_seen["text"]:
            return
        url_seen["text"] = joined
        if user_touched["v"]:
            return
        want = uicmd.mode_for(u, whatvar.get())
        if uicmd.MODE_SCOPE[want] != scopevar.get():
            apply_mode(want)
        remember_soon()

    url_seen = {"text": ""}
    _mode0 = str(EXTRA.get("download_mode") or "")
    if _mode0 in uicmd.MODES:
        apply_mode(_mode0)
    _urls0 = str(EXTRA.get("urls") or "").strip()
    if _urls0:
        urls.insert("end", _urls0 + "\n")
        url_seen["text"] = "\n".join(
            [x.strip() for x in _urls0.splitlines() if x.strip()])
    # Switch the moment you paste, rather than on the next 120 ms tick -- and
    # keep the tick as a belt-and-braces copy for URLs that arrive some other
    # way (drag and drop, a script, a remembered session).
    urls.bind("<KeyRelease>", lambda e: sync_mode())
    urls.bind("<<Paste>>", lambda e: sync_mode())
    apply_mode("music_single")
    grow = ttk.Frame(dl)
    brow = ttk.Frame(grow)
    brow.pack(pady=6)
    bcheck = ttk.Button(brow, text="Check access (no cookies)",
                        command=lambda: go_check())
    bcheck.pack(side="left", padx=4)
    bfmt = ttk.Button(brow, text="List qualities + sizes",
                      command=lambda: go_formats())
    bfmt.pack(side="left", padx=(4, 0))
    # The video list normally hides the audio-only tracks (they are paired
    # with the video for you). Ticked, they are listed too -- only the ones in
    # the spoken language set above, as `formats --show-audio` does.
    list_audio = tk.BooleanVar(value=bool(EXTRA.get("list_audio")))
    ttk.Checkbutton(brow, text="+ audio formats", variable=list_audio,
                    command=lambda: remember_soon()).pack(side="left",
                                                          padx=(2, 4))
    bget = ttk.Button(brow, text="Download + lyrics",
                      command=lambda: go_get())
    bget.configure(text="Download video"
                   if uicmd.MODE_WHAT[current_mode()] == "video"
                   else "Download + lyrics")
    bget.pack(side="left", padx=4)
    blist = ttk.Button(brow, text="List wrong-language files",
                       command=lambda: go_relang(list_only=True))
    blist.pack(side="left", padx=4)
    blang = ttk.Button(brow, text="Fix language in existing files",
                       command=lambda: go_relang())
    blang.pack(side="left", padx=4)
    grow.pack(side="bottom", fill="x")
    areas["get"].pack(fill="both", expand=True)
    toggle_adv()          # closed to start with: the mode already set the rest

    def typed_urls():
        return [x.strip() for x in urls.get("1.0", "end").splitlines() if x.strip()]

    def go_check():
        """Is YouTube serving these to you? Deliberately cookie-free: a check
        that carries your login cannot tell you whether you are blocked."""
        u = typed_urls()
        if not u:
            messagebox.showwarning("musictool", "Paste at least one URL.")
            return
        do_argv(["check"] + u)

    def go_formats():
        """Qualities and their sizes for the first URL, into the log."""
        u = typed_urls()
        if not u:
            messagebox.showwarning("musictool", "Paste a video URL first.")
            return
        vals = dict(panes["get"].values())
        if list_audio.get():
            vals["show_audio"] = True      # filtered by --audio-lang
        do_argv(uicmd.build_argv("formats", vals, positionals=[u[0]],
                                 parser=build_parser()))

    def go_relang(list_only=False):
        """Put the right language in files that are already downloaded.

        Downloads only the audio track -- tens of MiB, not the hundreds the
        video cost -- and re-muxes it into the file you have, keeping the
        picture, the cover and the chapters.
        """
        vals = dict(panes["get"].values())
        d = (vals.get("output_dir") or "").strip()
        if not d or not os.path.isdir(d):
            messagebox.showwarning(
                "musictool",
                "Point the Folder field at the folder those files are in.")
            return
        argv = ["relang", d,
                "--audio-lang", (vals.get("audio_lang") or "en").strip() or "en"]
        if list_only:
            do_argv(argv + ["--list"])   # reads the files only -- no download
            return
        if vals.get("cookies"):
            argv += ["--cookies", vals["cookies"]]
        if vals.get("cookies_from_browser") not in (None, "", "off"):
            argv += ["--cookies-from-browser", vals["cookies_from_browser"]]
        if vals.get("proxy"):
            argv += ["--proxy", vals["proxy"]]
        do_argv(argv)

    def go_get():
        u = [x.strip() for x in urls.get("1.0", "end").splitlines() if x.strip()]
        if not u:
            messagebox.showwarning("musictool", "Paste at least one URL.")
            return
        vals = dict(panes["get"].values())
        out = vals.get("output_dir") or cfg.get("output_dir") or "."
        out = os.path.abspath(os.path.expanduser(out or "."))
        cfg["output_dir"] = out          # the field defaults to this next time
        remember(url="\n".join(u), path=out)
        argv = uicmd.build_argv("get", vals, positionals=u, parser=build_parser())
        do_argv(argv, after=after_run(lambda: os.path.abspath(
            os.path.expanduser(out or "."))))

    run_buttons += [bcheck, bfmt, bget]
    queue_ok.append(bget)

    def get_label():
        # While a download is going the button means "queue this", and the
        # pump's mode refresh must not talk it back into "Download".
        if running["n"]:
            return "Add to queue"
        return ("Download video" if uicmd.MODE_WHAT[current_mode()] == "video"
                else "Download + lyrics")

    # ================================================== Lyrics tab
    ly = ttk.Frame(nb)
    nb.add(ly, text="  Fix lyrics  ")
    lvar = path_field(ly, cfg.get("output_dir", "."))

    areas["lyrics"] = ScrollArea(ly)
    panes["lyrics"] = OptionsPane(areas["lyrics"].frame, "lyrics", cfg,
                                  saved=SAVED.get("lyrics"))
    panes["lyrics"].quick_row(areas["lyrics"].frame)

    def lyrics_options():
        v = panes["lyrics"].values()
        return {"recursive": v.get("recursive"), "lyrics_dir": v.get("lyrics_dir")}

    picker = FilePicker(areas["lyrics"].frame, lvar.get,
                        options_getter=lyrics_options)
    picker.pack(fill="x")
    panes["lyrics"].pack(fill="x")

    lrow = ttk.Frame(ly)
    b2 = ttk.Button(lrow, text="Fix lyrics", command=lambda: go_lyrics())
    b2.pack(side="left")
    ball = ttk.Button(lrow, text="Redo ALL files (force)",
                      command=lambda: go_lyrics(force=True))
    ball.pack(side="left", padx=6)
    ttk.Label(lrow, foreground="#888",
              text="tick files above to run a subset · untick to skip").pack(
        side="left", padx=8)
    run_buttons += [b2, ball]
    lrow.pack(side="bottom", fill="x", padx=8, pady=(0, 6))
    areas["lyrics"].pack(fill="both", expand=True)

    def go_lyrics(force=None):
        p = lvar.get().strip()
        if not p:
            messagebox.showwarning("musictool", "Choose a folder or file.")
            return
        remember(path=p)
        vals = dict(panes["lyrics"].values())
        if force:
            vals["force"] = True
            if not picker.rows:
                picker.load(quiet=True)
            picker.apply("all")
        pos, note = picker.positionals(p)
        if not pos:
            messagebox.showwarning("musictool", "No files are ticked.")
            return
        log(f"-- {note}")
        argv = uicmd.build_argv("lyrics", vals, positionals=pos,
                                parser=build_parser())
        do_argv(argv, after=after_run(lambda: uicmd.common_root(pos)))

    # ================================================== Scan + duplicates tab
    sc = ttk.Frame(nb)
    nb.add(sc, text="  Scan & duplicates  ")
    svar = path_field(sc, cfg.get("output_dir", "."))

    areas["duplicates"] = ScrollArea(sc)
    panes["duplicates"] = OptionsPane(areas["duplicates"].frame, "duplicates",
                                      cfg, saved=SAVED.get("duplicates"))
    panes["duplicates"].quick_row(areas["duplicates"].frame)

    def dupe_options():
        v = panes["duplicates"].values()
        return {"recursive": v.get("recursive"), "lyrics_dir": v.get("lyrics_dir")}

    dpicker = FilePicker(areas["duplicates"].frame, svar.get,
                         options_getter=dupe_options)
    dpicker.pack(fill="x")
    panes["duplicates"].pack(fill="x")

    scf = ttk.Frame(sc)

    def go_scan(json_out=False):
        p = svar.get().strip()
        if not p:
            return
        remember(path=p)
        vals = {"json": json_out,
                "lyrics_dir": panes["duplicates"].values().get("lyrics_dir", ""),
                "recursive": panes["duplicates"].values().get("recursive", False)}
        do_argv(uicmd.build_argv("scan", vals, positionals=[p],
                                 parser=build_parser()))

    def go_dupes(fix=None, json_out=None):
        """fix/json_out=None means 'whatever the user ticked in the panel'."""
        p = svar.get().strip()
        if not p:
            return
        remember(path=p)
        vals = dict(panes["duplicates"].values())
        if fix is not None:
            vals["fix"] = fix
        if json_out is not None:
            vals["json"] = json_out
        json_out = bool(vals.get("json"))
        pos, note = dpicker.positionals(p)
        if not pos:
            messagebox.showwarning("musictool", "No files are ticked.")
            return
        if dpicker.rows and len(pos) != len(dpicker.rows):
            log(f"-- {note}")
        argv = uicmd.build_argv("duplicates", vals, positionals=pos,
                                parser=build_parser())
        if json_out:
            do_argv(argv, after=load_dupe_tree_from_log)
        else:
            do_argv(argv, after=after_run(lambda: uicmd.common_root(pos)))

    ttk.Button(scf, text="Scan", command=go_scan).pack(side="left", padx=4)
    ttk.Button(scf, text="Scan (JSON)",
               command=lambda: go_scan(True)).pack(side="left", padx=4)
    ttk.Button(scf, text="Find duplicates",
               command=go_dupes).pack(side="left", padx=4)
    ttk.Button(scf, text="Duplicate groups (tree)",
               command=lambda: go_dupes(json_out=True)).pack(side="left", padx=4)
    b3 = ttk.Button(scf, text="FIX duplicate lyrics",
                    command=lambda: go_dupes(fix=True))
    b3.pack(side="left", padx=4)
    run_buttons += [b3]
    scf.pack(side="bottom", pady=6)
    areas["duplicates"].pack(fill="both", expand=True)

    # ================================================== Report tab
    # Not a ScrollArea: the tree below has its own scrollbars and must expand.
    rp = ttk.Frame(nb)
    nb.add(rp, text="  Report  ")
    rf = ttk.Frame(rp)
    rf.pack(fill="x", padx=8, pady=6)
    ttk.Label(rf, text="Report file:").pack(side="left")
    report_path = tk.StringVar()
    ttk.Entry(rf, textvariable=report_path, width=60).pack(side="left", padx=6,
                                                           fill="x", expand=True)

    def pick_report():
        f = filedialog.askopenfilename(
            filetypes=[("reports", "*.txt"), ("all", "*.*")])
        if f:
            report_path.set(f)
            load_report_tab()
    ttk.Button(rf, text="Open...", command=pick_report).pack(side="left")
    ttk.Button(rf, text="Export JSON",
               command=lambda: export_report("json")).pack(side="left", padx=4)
    ttk.Button(rf, text="Export CSV",
               command=lambda: export_report("csv")).pack(side="left")

    ttk.Label(rp, textvariable=report_headline, foreground="#0a6",
              font=("TkDefaultFont", 10, "bold")).pack(anchor="w", padx=10, pady=2)

    ttk.Button(rp, text="Reload", command=lambda: load_report_tab()).pack(
        anchor="w", padx=10)

    tree = ttk.Treeview(rp, columns=("detail", "extra"), height=16)
    tree.heading("#0", text="item")
    tree.heading("detail", text="detail")
    tree.heading("extra", text="note")
    tree.column("#0", width=380)
    tree.column("detail", width=200)
    tree.column("extra", width=300)
    tsb = ttk.Scrollbar(rp, orient="vertical", command=tree.yview)
    thb = ttk.Scrollbar(rp, orient="horizontal", command=tree.xview)
    tree.configure(yscrollcommand=tsb.set, xscrollcommand=thb.set)
    tree.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=4)
    tsb.pack(side="right", fill="y")
    thb.pack(side="bottom", fill="x")

    def load_report_tab():
        p = report_path.get().strip()
        tree.delete(*tree.get_children())
        if not p or not os.path.exists(p):
            report_headline.set("no report loaded")
            return
        rep = uicmd.load_report(p)
        report_headline.set(uicmd.headline(rep))
        for section, key in (("Fixed", "fixed"), ("Unresolved", "unresolved"),
                             ("Unreadable", "unreadable")):
            items = rep.get(key) or []
            if not items:
                continue
            node = tree.insert("", "end", text=f"{section} ({len(items)})",
                               open=(key == "unresolved"), values=("", ""))
            for it in items:
                if key == "fixed":
                    tree.insert(node, "end", text=it["name"],
                                values=(f'{it["provider"]} / {it["kind"]}',
                                        it.get("note", "")))
                else:
                    tree.insert(node, "end", text=it["name"],
                                values=("", it.get("why", "")))
        prov = rep.get("by_provider") or {}
        if prov:
            node = tree.insert("", "end", text="By provider", open=False)
            for k, v in sorted(prov.items(), key=lambda kv: -kv[1]):
                tree.insert(node, "end", text=k, values=(f"{v} file(s)", ""))

    def load_dupe_tree_from_log(_rc=None):
        """Parse the --json duplicate output out of the log pane into the tree."""
        text = outbox.get("1.0", "end")
        start = text.find("[")
        if start < 0:
            return
        try:
            data = json.loads(text[start:text.rfind("]") + 1])
        except Exception:
            log("(could not parse duplicate JSON for the tree view)")
            return
        tree.delete(*tree.get_children())
        susp = sum(1 for d in data if d.get("suspicious"))
        report_headline.set(
            f"{len(data)} duplicate group(s), {susp} suspicious · "
            f"{sum(len(d['files']) for d in data)} file(s) affected")
        for g in data:
            label = ("SUSPICIOUS" if g.get("suspicious") else "same title")
            node = tree.insert("", "end", open=True,
                               text=f'{label} — {len(g["files"])} files',
                               values=(f'{g["distinct_titles"]} title(s)',
                                       g.get("preview", "")))
            for f in g["files"]:
                tree.insert(node, "end", text=f, values=("", ""))

    def export_report(kind):
        p = report_path.get().strip()
        if not p or not os.path.exists(p):
            messagebox.showinfo("musictool", "Load a report first.")
            return
        rep = uicmd.load_report(p)
        dst = None
        if kind == "json":
            dst = filedialog.asksaveasfilename(defaultextension=".json",
                                               initialfile="lyrics-report.json")
            if dst:
                with open(dst, "w", encoding="utf-8") as fh:
                    fh.write(uicmd.report_to_json(rep))
        else:
            dst = filedialog.asksaveasfilename(defaultextension=".csv",
                                               initialfile="lyrics-report.csv")
            if dst:
                import csv
                with open(dst, "w", encoding="utf-8", newline="") as fh:
                    w = csv.writer(fh)
                    w.writerow(["status", "file", "detail", "note"])
                    for it in rep["fixed"]:
                        w.writerow(["fixed", it["name"],
                                    f'{it["provider"]}/{it["kind"]}',
                                    it.get("note", "")])
                    for key in ("unresolved", "unreadable"):
                        for it in rep[key]:
                            w.writerow([key, it["name"], "", it.get("why", "")])
        if dst:
            log(f"exported -> {dst}")

    # ================================================== Tools tab
    tl = ttk.Frame(nb)
    nb.add(tl, text="  Tools  ")
    areas["tools"] = ScrollArea(tl)
    tframe = areas["tools"].frame
    panes["providers"] = OptionsPane(tframe, "providers", cfg,
                                     saved=SAVED.get("providers"))
    panes["providers"].pack(fill="x")

    def go_providers():
        do_argv(uicmd.build_argv("providers", panes["providers"].values(),
                                 parser=build_parser()))
    ttk.Button(tframe, text="Check lyrics sources",
               command=go_providers).pack(pady=4)

    cfgf = ttk.LabelFrame(tframe, text="saved defaults (config)")
    cfgf.pack(fill="x", padx=8, pady=6)
    ck = tk.StringVar()
    cv = tk.StringVar()
    ttk.Label(cfgf, text="key:").pack(side="left", padx=4)
    ttk.Combobox(cfgf, textvariable=ck, width=18, state="readonly",
                 values=sorted(configmod.DEFAULTS)).pack(side="left")
    ttk.Label(cfgf, text="value:").pack(side="left", padx=4)
    ttk.Entry(cfgf, textvariable=cv, width=24).pack(side="left")

    def set_cfg():
        if not ck.get():
            return
        argv = ["config", ck.get()]
        if cv.get():
            argv.append(cv.get())
        do_argv(argv)
    ttk.Button(cfgf, text="Set", command=set_cfg).pack(side="left", padx=4)
    ttk.Button(cfgf, text="Show all",
               command=lambda: do_argv(["config"])).pack(side="left")
    ttk.Button(cfgf, text="Reset",
               command=lambda: do_argv(["config", "--reset"])).pack(side="left",
                                                                   padx=4)

    ttk.Button(tframe, text="Environment check (doctor)",
               command=lambda: do_argv(["doctor"])).pack(pady=4)

    def save_defaults():
        for pane in panes.values():
            for k, v in pane.values().items():
                if k in configmod.DEFAULTS:
                    cfg[k] = v
        configmod.save(cfg)
        log("saved current options as defaults -> " + configmod.PATH)
    ttk.Button(tframe, text="Save these options as defaults",
               command=save_defaults).pack(pady=4)
    areas["tools"].pack(fill="both", expand=True)

    # ================================================== Lyrics files tab
    lf = ttk.Frame(nb)
    nb.add(lf, text="  Lyrics files  ")
    lfvar = path_field(lf, cfg.get("output_dir", "."))

    areas["lyricsfiles"] = ScrollArea(lf)
    panes["extract"] = OptionsPane(areas["lyricsfiles"].frame, "extract", cfg,
                                   saved=SAVED.get("extract"))
    panes["extract"].pack(fill="x")
    panes["embed"] = OptionsPane(areas["lyricsfiles"].frame, "embed", cfg,
                                 saved=SAVED.get("embed"))
    panes["embed"].pack(fill="x")

    def go_files(sub):
        p = lfvar.get().strip()
        if not p:
            messagebox.showwarning("musictool", "Choose a folder or file.")
            return
        remember(path=p)
        vals = dict(panes[sub].values())
        argv = uicmd.build_argv(sub, vals, positionals=[p],
                                parser=build_parser())
        base = os.path.abspath(os.path.expanduser(p))
        do_argv(argv, after=after_run(
            lambda: base if os.path.isdir(base) else os.path.dirname(base)))

    lfr = ttk.Frame(lf)
    bex = ttk.Button(lfr, text="Extract lyrics to .lrc",
                     command=lambda: go_files("extract"))
    bex.pack(side="left")
    bem = ttk.Button(lfr, text="Embed .lrc into songs",
                     command=lambda: go_files("embed"))
    bem.pack(side="left", padx=6)
    ttk.Label(lfr, foreground="#888",
              text="extract -> edit the .lrc -> embed").pack(side="left", padx=8)
    run_buttons += [bex, bem]
    lfr.pack(side="bottom", fill="x", padx=8, pady=(0, 6))
    areas["lyricsfiles"].pack(fill="both", expand=True)

    # ==================================================== Quality tab
    # yt-dlp's raw format list is unusable (a 4K video reports 127 rows:
    # every stream split into 18 CDN variants, plus doubles of the same
    # resolution). This tab shows it collapsed, with filters, an automatic
    # pick and a manual click-to-choose.
    qtab = ttk.Frame(nb)
    nb.add(qtab, text="  Quality  ")
    ttk.Label(qtab, text="Pick a quality by its real file size:").pack(
        anchor="w", padx=8, pady=(8, 0))
    qrow = ttk.Frame(qtab)
    qrow.pack(fill="x", padx=8, pady=(0, 4))
    ttk.Label(qrow, text="Video URL:").pack(side="left")
    qurl = tk.StringVar(value="")
    ttk.Entry(qrow, textvariable=qurl, width=58).pack(
        side="left", padx=6, fill="x", expand=True)
    bql = ttk.Button(qrow, text="Load qualities", command=lambda: q_load())
    bql.pack(side="left", padx=4)
    run_buttons.append(bql)

    qf = ttk.LabelFrame(qtab, text="filters")
    qf.pack(fill="x", padx=8, pady=(0, 4))
    qres = tk.StringVar(value="any")
    qcont = tk.StringVar(value="any")
    qcodec = tk.StringVar(value="any")
    qsize = tk.StringVar(value="")
    qsort = tk.StringVar(value="size_desc")
    qaudio = tk.BooleanVar(value=False)
    qcollapse = tk.BooleanVar(value=True)
    qdedupe = tk.BooleanVar(value=True)
    _f1 = ttk.Frame(qf)
    _f1.pack(fill="x", padx=4, pady=2)
    ttk.Label(_f1, text="Max height:").pack(side="left")
    c = ttk.Combobox(_f1, textvariable=qres, width=7, state="readonly",
                     values=["any", "2160", "1440", "1080", "720", "480",
                             "360", "240", "144"])
    c.pack(side="left", padx=(2, 10))
    c.bind("<<ComboboxSelected>>", lambda e: q_draw())
    ttk.Label(_f1, text="Container:").pack(side="left")
    c = ttk.Combobox(_f1, textvariable=qcont, width=8, state="readonly",
                     values=["any", "mp4", "webm", "mkv"])
    c.pack(side="left", padx=(2, 10))
    c.bind("<<ComboboxSelected>>", lambda e: q_draw())
    ttk.Label(_f1, text="Codec:").pack(side="left")
    c = ttk.Combobox(_f1, textvariable=qcodec, width=8, state="readonly",
                     values=["any", "avc1", "vp9", "vp09", "av01", "opus",
                             "mp4a"])
    c.pack(side="left", padx=(2, 10))
    c.bind("<<ComboboxSelected>>", lambda e: q_draw())
    ttk.Label(_f1, text="Max size:").pack(side="left")
    e = ttk.Entry(_f1, textvariable=qsize, width=8)
    e.pack(side="left", padx=(2, 2))
    e.bind("<Return>", lambda ev: q_draw())
    ttk.Button(_f1, text="Apply", command=lambda: q_draw()).pack(
        side="left", padx=6)
    _f2 = ttk.Frame(qf)
    _f2.pack(fill="x", padx=4, pady=2)
    ttk.Checkbutton(_f2, text="Merge variant splits",
                    variable=qcollapse, command=lambda: q_draw()).pack(
        side="left", padx=4)
    ttk.Checkbutton(_f2, text="Hide same-quality doubles",
                    variable=qdedupe, command=lambda: q_draw()).pack(
        side="left", padx=4)
    ttk.Checkbutton(_f2, text="Show audio-only tracks",
                    variable=qaudio, command=lambda: q_draw()).pack(
        side="left", padx=4)
    ttk.Label(_f2, text="Sort:").pack(side="left", padx=(12, 0))
    c = ttk.Combobox(_f2, textvariable=qsort, width=16, state="readonly",
                     values=list(formats.SORTS))
    c.pack(side="left", padx=2)
    c.bind("<<ComboboxSelected>>", lambda ev: q_draw())

    qm = ttk.LabelFrame(qtab, text="choose")
    qm.pack(fill="x", padx=8, pady=(0, 4))
    qmode = tk.StringVar(value="auto")
    qpreset = tk.StringVar(value=formats.AUTO_LABELS["best"])
    _m1 = ttk.Frame(qm)
    _m1.pack(fill="x", padx=4, pady=2)
    ttk.Radiobutton(_m1, text="Auto:", variable=qmode, value="auto",
                    command=lambda: q_draw()).pack(side="left")
    c = ttk.Combobox(_m1, textvariable=qpreset, width=26, state="readonly",
                     values=[formats.AUTO_LABELS[m] for m in
                             formats.AUTO_MODES])
    c.pack(side="left", padx=4)
    c.bind("<<ComboboxSelected>>", lambda e: q_draw())
    ttk.Radiobutton(_m1, text="Manual: click a row below",
                    variable=qmode, value="manual",
                    command=lambda: q_draw()).pack(side="left", padx=(14, 0))
    # Which audio goes with the video: automatic (best track in your audio
    # language) or one you chose on the Audio tracks tab.
    ttk.Button(_m1, text="Choose audio track\u2026",
               command=lambda: a_show()).pack(side="right", padx=4)
    qaudio_txt = tk.StringVar(value="")
    ttk.Label(_m1, textvariable=qaudio_txt, foreground="#06a").pack(
        side="right", padx=4)

    qcols = ("id", "what", "container", "video", "codec", "lang", "size",
             "variants")
    qwrap = ttk.Frame(qtab)
    qwrap.pack(fill="both", expand=True, padx=8)
    qtree = ttk.Treeview(qwrap, columns=qcols, show="headings", height=10)
    qsb = ttk.Scrollbar(qwrap, orient="vertical", command=qtree.yview)
    qtree.configure(yscrollcommand=qsb.set)
    qsb.pack(side="right", fill="y")
    qtree.pack(side="left", fill="both", expand=True)
    for cid, txt, wd in (
            ("id", "id", 70), ("what", "what", 86), ("container", "container", 80),
            ("video", "video", 60), ("codec", "codec", 66), ("lang", "lang", 52),
            ("size", "size", 88), ("variants", "variants", 92)):
        qtree.heading(cid, text=txt)
        qtree.column(cid, width=wd, anchor="w")
    qtree.bind("<<TreeviewSelect>>", lambda ev: q_picked(ev))
    qtree.bind("<Double-1>", lambda ev: q_download())

    qinfo = tk.StringVar(value="Paste a video URL above and press "
                              "“Load qualities”.")
    ttk.Label(qtab, textvariable=qinfo, foreground="#06a",
              wraplength=980, justify="left").pack(anchor="w", padx=8,
                                                   pady=(4, 0))
    qcmd = ttk.Frame(qtab)
    qcmd.pack(fill="x", padx=8, pady=(2, 8))
    qfmt = tk.StringVar(value="")
    ttk.Entry(qcmd, textvariable=qfmt, state="readonly").pack(
        side="left", fill="x", expand=True, padx=(0, 6))
    ttk.Button(qcmd, text="Copy command",
               command=lambda: q_copy()).pack(side="left", padx=2)
    bqd = ttk.Button(qcmd, text="Download this quality",
                     command=lambda: q_download())
    bqd.pack(side="left", padx=2)
    run_buttons.append(bqd)

    q_rows = []
    q_iids = {}
    # The audio track chosen by hand on the Audio tracks tab, and the URL it
    # belongs to: a track id from one video means nothing for another.
    a_pin = {"row": None, "url": ""}

    def q_lang():
        """The spoken language you asked for (the Download tab's setting)."""
        return str(panes["get"].values().get("audio_lang") or "").strip()

    def q_pinned():
        if a_pin["row"] is not None and a_pin["url"] == qurl.get().strip():
            return a_pin["row"]
        return None

    def q_audio_note():
        pin = q_pinned()
        if pin is not None:
            qaudio_txt.set(f"audio: {pin['id']} [{formats._lang_tag(pin) or '?'}]"
                           f" {pin['acodec'] or '?'} {formats.track_bitrate(pin)}")
        else:
            lang = q_lang()
            qaudio_txt.set("audio: automatic, best in "
                           + ("any language" if formats.is_any_lang(lang)
                              else f"'{lang}'"))

    def q_cap():
        return formats.parse_size(qsize.get()) if qsize.get().strip() else None

    def q_visible():
        h = qres.get().strip()
        return formats.apply_filters(
            q_rows,
            max_height=int(h) if h.isdigit() else None,
            container=None if qcont.get() == "any" else qcont.get(),
            codec=None if qcodec.get() == "any" else qcodec.get(),
            max_bytes=q_cap(),
            include_audio=bool(qaudio.get()),
            collapse=bool(qcollapse.get()),
            dedupe=bool(qdedupe.get()),
            sort=qsort.get())

    def q_pool():
        """The visible rows plus the audio tracks the filters hid."""
        vis = q_visible()
        return formats.with_audio_pool(vis, q_rows)

    def q_picked(_ev=None):
        ids = list(qtree.selection() or [])
        if not ids:
            return
        r = q_iids.get(ids[-1])
        if not r:
            return
        qmode.set("manual")
        row, audio = formats.pair_with_audio(r, q_rows, q_cap(),
                                             lang=q_lang(), pinned=q_pinned())
        sel = formats.selector_for(row, audio)
        qfmt.set(sel)
        cap = q_cap()
        total = formats.total_bytes(row, audio)
        extra = ""
        if cap and total and total > cap:
            extra = (f"  (over your {formats.human_bytes(cap)} cap by "
                     f"{formats.human_bytes(total - cap)})")
        audio_txt = (f" + {audio['acodec']} audio {formats._spoken(audio)}"
                     .rstrip() if audio else " (audio included)")
        qinfo.set(f"manual: {row['height'] or '-'}p {row['vcodec'] or '-'} "
                  f"{row['ext']} ~ {formats.human_bytes(row['bytes'])}"
                  f"{audio_txt}{extra}  ->  -f {sel}")

    def q_draw(_ev=None):
        rows = q_visible()
        qtree.delete(*qtree.get_children())
        q_iids.clear()
        for r in rows:
            what = {"audio": "audio only", "both": "video+audio",
                    "file": "whole file"}.get(r["kind"], "video only")
            res = f"{r['height']}p" if r["height"] else "-"
            if r["fps"] and r["fps"] not in (25, 30):
                res += str(r["fps"])
            n = r.get("variants", 1)
            iid = qtree.insert("", "end", values=(
                r["id"], what, r["ext"], res, r["vcodec"] or r["acodec"] or "-",
                formats._lang_tag(r),
                formats.human_bytes(r["bytes"]),
                f"{n} merged" if n > 1 else ""))
            q_iids[iid] = r
        if qmode.get() == "auto":
            preset = next((m for m in formats.AUTO_MODES
                           if formats.AUTO_LABELS[m] == qpreset.get()), "best")
            sel, v, a, why = formats.auto_pick(q_pool(), mode=preset,
                                               max_bytes=q_cap(),
                                               lang=q_lang(),
                                               pinned=q_pinned())
            qfmt.set(sel or "")
            qinfo.set(("auto: " if sel else "auto: nothing matched — ") + why)
        else:
            q_picked()
        qinfo.set(qinfo.get() + f"     [{len(rows)} of {len(q_rows)} shown]")
        q_audio_note()

    def q_args(selector):
        vals = dict(panes["get"].values())
        vals["format_selector"] = selector
        vals["keep_video"] = True
        url = qurl.get().strip()
        return uicmd.build_argv("get", vals, positionals=[url] if url else [],
                                parser=build_parser())

    def q_download():
        if not qfmt.get():
            messagebox.showwarning("musictool",
                                   "No quality chosen yet — load the list first.")
            return
        if not qurl.get().strip():
            messagebox.showwarning("musictool", "Paste a video URL first.")
            return
        do_argv(q_args(qfmt.get()))

    def q_copy():
        if not qfmt.get():
            return
        cmd = uicmd.quote_cmd(q_args(qfmt.get()))   # already starts "musictool"
        root.clipboard_clear()
        root.clipboard_append(cmd)
        log("copied: " + cmd)
        qinfo.set("copied the full command to the clipboard")

    def q_load():
        url = qurl.get().strip()
        if not url:
            got = typed_urls()
            if got:
                url = got[0]
                qurl.set(url)
        if not url:
            messagebox.showwarning("musictool", "Paste a video URL first.")
            return
        g = panes["get"].values()
        opts = {"cookies": g.get("cookies"),
                "cookies_from_browser": g.get("cookies_from_browser"),
                # the Download tab's JS settings: without them a video whose
                # challenge needs node came back with no streams here
                "js_runtimes": g.get("js_runtimes"),
                "remote_components": g.get("remote_components")}

        def job():
            return download.probe(url, opts)

        def redraw():
            # one failing must not leave the other tab saying "reading ..."
            for fn in (q_draw, a_draw):
                try:
                    fn()
                except Exception as e:                      # noqa: BLE001
                    log(f"!! {fn.__name__}: {type(e).__name__}: {e}")

        def after(info):
            if not isinstance(info, dict) or info.get("error"):
                err = str((info or {}).get("error") or "no answer")
                q_rows.clear()
                qinfo.set("could not read the qualities: "
                          + err.splitlines()[0][:240])
                hint = download.ADVICE.get(download.classify_error(err))
                if hint:
                    qinfo.set(qinfo.get() + "\n-> " + hint)
                q_loaded["url"] = ""
                redraw()
                ainfo.set(qinfo.get())
                return
            q_rows[:] = formats.rows_from_info(info)
            q_loaded["url"] = url
            q_loaded["why"] = download.no_media_reason(info) or ""
            if a_pin["url"] != url:
                a_pin["row"], a_pin["url"] = None, ""
            title = info.get("title") or url
            log(f"{len(q_rows)} raw qualities for {title!r}")
            redraw()
            if q_loaded["why"]:
                log("!! " + q_loaded["why"])
                qinfo.set(q_loaded["why"])
                ainfo.set(q_loaded["why"])

        qinfo.set(f"reading the qualities of {url} …")
        ainfo.set(f"reading the audio tracks of {url} …")
        worker(job, after=after)

    q_loaded = {"url": "", "why": ""}

    # =============================================== Audio tracks tab
    # A dubbed video serves one audio track per spoken language, often in two
    # codecs and several bitrates. The automatic pick takes the best one in
    # your audio language; this tab lists them so you can see what is really
    # there and take a specific one -- on its own (music), or with the video
    # quality chosen on the Quality tab.
    atab = ttk.Frame(nb)
    nb.add(atab, text="  Audio tracks  ")
    ttk.Label(atab, text="See every audio track in a language and pick the "
                         "one to download:").pack(anchor="w", padx=8,
                                                  pady=(8, 0))
    arow = ttk.Frame(atab)
    arow.pack(fill="x", padx=8, pady=(0, 4))
    ttk.Label(arow, text="Video URL:").pack(side="left")
    ttk.Entry(arow, textvariable=qurl, width=58).pack(   # same URL as Quality
        side="left", padx=6, fill="x", expand=True)
    bal = ttk.Button(arow, text="Load audio tracks", command=lambda: q_load())
    bal.pack(side="left", padx=4)
    run_buttons.append(bal)

    alrow = ttk.Frame(atab)
    alrow.pack(fill="x", padx=8, pady=(0, 4))
    ttk.Label(alrow, text="Language:").pack(side="left")
    alang = tk.StringVar(value=q_lang() or "en")
    acombo = ttk.Combobox(alrow, textvariable=alang, width=12,
                          values=["all", alang.get()])
    acombo.pack(side="left", padx=(2, 6))
    acombo.bind("<<ComboboxSelected>>", lambda e: a_lang_picked())
    acombo.bind("<Return>", lambda e: a_lang_picked())
    aoffer = tk.StringVar(value="")
    ttk.Label(alrow, textvariable=aoffer, foreground="#888").pack(
        side="left", padx=4)

    acols = ("id", "lang", "codec", "container", "bitrate", "size", "note")
    awrap = ttk.Frame(atab)
    awrap.pack(fill="both", expand=True, padx=8)
    atree = ttk.Treeview(awrap, columns=acols, show="headings", height=8)
    asb = ttk.Scrollbar(awrap, orient="vertical", command=atree.yview)
    atree.configure(yscrollcommand=asb.set)
    asb.pack(side="right", fill="y")
    atree.pack(side="left", fill="both", expand=True)
    for cid, txt, wd in (
            ("id", "id", 80), ("lang", "language", 80), ("codec", "codec", 66),
            ("container", "container", 76), ("bitrate", "bitrate", 66),
            ("size", "size", 88), ("note", "what YouTube calls it", 260)):
        atree.heading(cid, text=txt)
        atree.column(cid, width=wd, anchor="w")
    atree.bind("<<TreeviewSelect>>", lambda ev: a_picked())
    atree.bind("<Double-1>", lambda ev: a_download())

    ainfo = tk.StringVar(value="Paste a video URL above and press "
                              "\u201cLoad audio tracks\u201d.")
    ttk.Label(atab, textvariable=ainfo, foreground="#06a",
              wraplength=980, justify="left").pack(anchor="w", padx=8,
                                                   pady=(4, 0))
    acmd = ttk.Frame(atab)
    acmd.pack(fill="x", padx=8, pady=(2, 8))
    afmt = tk.StringVar(value="")
    ttk.Entry(acmd, textvariable=afmt, state="readonly", width=14).pack(
        side="left", padx=(0, 6))
    ttk.Button(acmd, text="Copy command",
               command=lambda: a_copy()).pack(side="left", padx=2)
    bad = ttk.Button(acmd, text="Download this audio",
                     command=lambda: a_download())
    bad.pack(side="left", padx=2)
    run_buttons.append(bad)
    ttk.Button(acmd, text="Use with a video quality",
               command=lambda: a_use_with_video()).pack(side="left", padx=2)
    ttk.Button(acmd, text="Automatic audio",
               command=lambda: a_automatic()).pack(side="left", padx=2)

    a_iids = {}

    def a_selected():
        ids = list(atree.selection() or [])
        return a_iids.get(ids[-1]) if ids else None

    def a_lang_picked():
        """A language was chosen here. A real language also becomes the
        Download tab's --audio-lang, so every download in the window follows
        the same choice; 'all' only widens this list."""
        lang = alang.get().strip()
        if lang and not formats.is_any_lang(lang) and lang != q_lang():
            panes["get"].apply({"audio_lang": lang})
            remember_soon()
            log(f"audio language is now '{lang}' (Download tab's "
                f"--audio-lang follows it)")
        a_draw()
        q_draw()

    def a_draw():
        # follow the Download tab when its audio language was changed there
        # ('all' is a view of this list only, so it is left alone)
        _want = q_lang()
        if (not formats.is_any_lang(alang.get())
                and not formats.is_any_lang(_want) and _want != alang.get()):
            alang.set(_want)
        atree.delete(*atree.get_children())
        a_iids.clear()
        afmt.set("")
        spoken = formats.languages_in(
            [r for r in q_rows if r["kind"] == "audio"])
        codes = [c for c, _o, _n in spoken]
        acombo.configure(values=["all"] + codes + (
            [alang.get()] if alang.get() and alang.get() not in codes
            and alang.get() != "all" else []))
        aoffer.set(("this video: " + ", ".join(
            f"{c} (original)" if o else c for c, o, _n in spoken))
            if spoken else "")
        if not q_rows:
            if q_loaded["url"]:
                ainfo.set(q_loaded.get("why")
                          or "no formats reported for this video")
            return
        tracks, note = formats.audio_tracks(q_rows, alang.get())
        pin = q_pinned()
        for r in tracks:
            iid = atree.insert("", "end", values=(
                r["id"], formats._lang_tag(r) or "?", r["acodec"] or "-",
                r["ext"], formats.track_bitrate(r),
                formats.human_bytes(r["bytes"]), r.get("note") or ""))
            a_iids[iid] = r
            if pin is not None and r is pin:
                atree.selection_set(iid)
        lang = alang.get().strip()
        head = (f"{len(tracks)} audio track(s)"
                + ("" if formats.is_any_lang(lang) else f" in '{lang}'"))
        if not tracks:
            head = note
        elif note:
            head += " -- " + note
        if pin is not None:
            head += ("   \u00b7  used for video downloads: "
                     + formats.describe_track(pin))
        else:
            head += "   \u00b7  click one, then download it or use it with a video"
        ainfo.set(head + "   (* = original language)"
                  if any((r.get("lang_pref") or 0) > 0 for r in tracks)
                  else head)
        if pin is not None:
            afmt.set(str(pin["id"]))

    def a_picked():
        r = a_selected()
        if not r:
            return
        afmt.set(str(r["id"]))
        ainfo.set("chosen: " + formats.describe_track(r)
                  + "  ->  -f " + str(r["id"]))

    def a_args(track):
        vals = dict(panes["get"].values())
        vals["format_selector"] = str(track["id"])
        vals["keep_video"] = False        # audio only: extract to --format
        url = qurl.get().strip()
        return uicmd.build_argv("get", vals, positionals=[url] if url else [],
                                parser=build_parser())

    def a_need_track():
        r = a_selected()
        if r is None:
            messagebox.showwarning("musictool", "Load the list and click an "
                                   "audio track first.")
            return None
        if not qurl.get().strip():
            messagebox.showwarning("musictool", "Paste a video URL first.")
            return None
        return r

    def a_download():
        r = a_need_track()
        if r is not None:
            do_argv(a_args(r))

    def a_copy():
        r = a_selected()
        if r is None:
            return
        cmd = uicmd.quote_cmd(a_args(r))
        root.clipboard_clear()
        root.clipboard_append(cmd)
        log("copied: " + cmd)
        ainfo.set("copied the full command to the clipboard")

    def a_use_with_video():
        """Pin this track: the Quality tab pairs every video with it."""
        r = a_need_track()
        if r is None:
            return
        a_pin["row"], a_pin["url"] = r, qurl.get().strip()
        log("audio for video downloads: " + formats.describe_track(r))
        q_draw()
        a_draw()
        try:
            nb.select(qtab)
        except Exception:                                   # noqa: BLE001
            pass

    def a_automatic():
        a_pin["row"], a_pin["url"] = None, ""
        q_draw()
        a_draw()
        lang = q_lang()
        ainfo.set("video downloads pick the best audio in "
                  + ("any language" if formats.is_any_lang(lang)
                     else f"'{lang}'") + " again")

    def a_show():
        """From the Quality tab: open this tab, loading the list if needed."""
        try:
            nb.select(atab)
        except Exception:                                   # noqa: BLE001
            pass
        if qurl.get().strip() and q_loaded["url"] != qurl.get().strip():
            q_load()
        else:
            a_draw()

    # =============================================== Live streams tab
    # Record a stream that is live from its very start, watch what has come
    # down so far at any time, and carry on after a stop (a full disk, Stop,
    # a power cut). live.py explains why the pieces no longer pile up.
    ltab = ttk.Frame(nb)
    nb.add(ltab, text="  Live streams  ")
    areas["live"] = ScrollArea(ltab)
    lbody = areas["live"].frame
    ttk.Label(lbody, text="Record a live stream from its start, watch what you "
                          "have so far, and download the rest after a "
                          "stop:").pack(anchor="w", padx=8, pady=(8, 2))
    _lr = ttk.Frame(lbody)
    _lr.pack(fill="x", padx=8, pady=2)
    ttk.Label(_lr, text="Stream URL:").pack(side="left")
    lurl = tk.StringVar(value=str(EXTRA.get("live_url") or ""))
    ttk.Entry(_lr, textvariable=lurl, width=58).pack(
        side="left", padx=6, fill="x", expand=True)
    blstat = ttk.Button(_lr, text="Is it live? How big?",
                        command=lambda: l_status())
    blstat.pack(side="left", padx=4)

    def _l_dir_row(label, var, hint):
        row = ttk.Frame(lbody)
        row.pack(fill="x", padx=8, pady=2)
        ttk.Label(row, text=label).pack(side="left")
        ttk.Entry(row, textvariable=var, width=52).pack(
            side="left", padx=6, fill="x", expand=True)

        def browse():
            d = filedialog.askdirectory()
            if d:
                var.set(d)
                remember_soon()
        ttk.Button(row, text="Browse...", command=browse).pack(side="left")
        ttk.Label(lbody, text=hint, foreground="#888").pack(anchor="w",
                                                            padx=10)

    lfolder = tk.StringVar(value=str(EXTRA.get("live_folder")
                                     or cfg.get("output_dir") or "."))
    _l_dir_row("Folder:", lfolder, "where the recording goes -- and where a "
                                   "stopped one is (also one made by plain "
                                   "yt-dlp)")
    lout = tk.StringVar(value=str(EXTRA.get("live_out") or ""))
    _l_dir_row("Copy to watch goes to:", lout,
               "empty = the same folder. The copy needs as much free space "
               "as the recording: pick another drive when this one is full")
    laudio = tk.BooleanVar(value=bool(EXTRA.get("live_audio")))
    ttk.Checkbutton(lbody, text="Audio only (no picture)", variable=laudio,
                    command=lambda: remember_soon()).pack(anchor="w", padx=8,
                                                          pady=(2, 0))
    live_state.update(live_url=lurl, live_folder=lfolder, live_out=lout,
                      live_audio=laudio)

    # Which streams: filled by "Is it live? How big?". A chosen stream is
    # downloaded exactly (--exact) and copied as it is -- nothing converted.
    L_AUTO_V = "automatic (best video)"
    L_AUTO_A = "automatic (best audio in your language)"
    _ls = ttk.Frame(lbody)
    _ls.pack(fill="x", padx=8, pady=(4, 0))
    ttk.Label(_ls, text="Video stream:").pack(side="left")
    lvid = tk.StringVar(value=L_AUTO_V)
    lvcb = ttk.Combobox(_ls, textvariable=lvid, values=[L_AUTO_V],
                        state="readonly", width=40)
    lvcb.pack(side="left", padx=(4, 12))
    ttk.Label(_ls, text="Audio stream:").pack(side="left")
    laud = tk.StringVar(value=L_AUTO_A)
    lacb = ttk.Combobox(_ls, textvariable=laud, values=[L_AUTO_A],
                        state="readonly", width=40)
    lacb.pack(side="left", padx=4)
    lstreams_hint = tk.StringVar(value=(
        "press \u201cIs it live? How big?\u201d to list the streams. A stream "
        "you choose is downloaded exactly as it is: copied, nothing "
        "converted, no other stream instead."))
    ttk.Label(lbody, textvariable=lstreams_hint, foreground="#888",
              wraplength=980, justify="left").pack(anchor="w", padx=10)
    l_streams = {"url": "", "rows": [], "v": {}, "a": {}}

    def l_fill_streams(_rc=None):
        """After `live --status`: put the streams it read into the pickers."""
        u = lurl.get().strip()
        info = live.LAST_PROBE.get(u)
        if not isinstance(info, dict):
            return
        rows = formats.rows_from_info(info)
        vids = sorted((r for r in rows if r["kind"] in ("video", "both")),
                      key=lambda r: (-(r["height"] or 0), -(r["fps"] or 0),
                                     -(r["bytes"] or 0)))
        lang = str(panes["get"].values().get("audio_lang") or "en")
        auds = sorted((r for r in rows if r["kind"] == "audio"),
                      key=lambda r: (not formats._match_lang(r, lang)
                                     if not formats.is_any_lang(lang) else 0,
                                     -formats.track_bitrate_num(r)))
        vmap = {formats.describe_stream(r): r for r in vids}
        amap = {formats.describe_stream(r): r for r in auds}
        if l_streams["url"] != u:
            lvid.set(L_AUTO_V)
            laud.set(L_AUTO_A)
        l_streams.update(url=u, rows=rows, v=vmap, a=amap)
        lvcb["values"] = [L_AUTO_V] + list(vmap)
        lacb["values"] = [L_AUTO_A] + list(amap)
        lstreams_hint.set(
            f"{len(vmap)} video and {len(amap)} audio streams. Pick one (or "
            f"both) and it is downloaded exactly as it is -- copied, nothing "
            f"converted; video + audio go into mp4, webm or mkv, whichever "
            f"holds both unchanged.")

    def l_pick_video(_ev=None):
        """A video picked and the audio left on automatic: pair it with the
        audio that keeps the pair in one container (mp4+m4a, webm+webm)."""
        row = l_streams["v"].get(lvid.get())
        if row is None or row["kind"] == "both" or laud.get() != L_AUTO_A:
            return
        lang = str(panes["get"].values().get("audio_lang") or "en")
        a = formats.matching_audio(l_streams["rows"], row, lang)
        if a is not None:
            laud.set(formats.describe_stream(a))

    lvcb.bind("<<ComboboxSelected>>", l_pick_video)

    def l_selector():
        """The exact -f for the chosen streams, or "" for automatic."""
        if l_streams["url"] != lurl.get().strip():
            return ""
        v = None if laudio.get() else l_streams["v"].get(lvid.get())
        a = l_streams["a"].get(laud.get())
        if v is not None and v["kind"] == "both":
            return str(v["id"])            # one stream with picture and sound
        if v is not None and a is None and not laudio.get():
            a = formats.matching_audio(l_streams["rows"], v)
        ids = [str(r["id"]) for r in (v, a) if r is not None]
        return "+".join(ids)

    # Only part of a finished stream: e.g. the hours not watched yet
    _lp = ttk.Frame(lbody)
    _lp.pack(fill="x", padx=8, pady=(6, 0))
    ttk.Label(_lp, text="Only this part:").pack(side="left")
    lpart = tk.StringVar(value=str(EXTRA.get("live_part") or ""))
    ttk.Entry(_lp, textvariable=lpart, width=22).pack(side="left", padx=6)
    ttk.Label(_lp, foreground="#888", text=(
        "for a finished stream: 1:00:00- = from 1 hour to the end, "
        "1:00:00-2:30:00, -45:00 = the first 45 minutes")).pack(side="left")

    _lb = ttk.Frame(lbody)
    _lb.pack(fill="x", padx=8, pady=6)
    blrec = ttk.Button(_lb, text="Record from the start",
                       command=lambda: l_record())
    blrec.pack(side="left", padx=(0, 4))
    bllook = ttk.Button(_lb, text="What's in the folder?",
                        command=lambda: l_look())
    bllook.pack(side="left", padx=4)
    # stays clickable during a recording: it then only reads the pieces
    blwatch = ttk.Button(_lb, text="Watch what's there so far",
                         command=lambda: l_watch())
    blwatch.pack(side="left", padx=4)
    blrest = ttk.Button(_lb, text="Download the rest",
                        command=lambda: l_resume())
    blrest.pack(side="left", padx=4)
    blpart = ttk.Button(_lb, text="Download this part",
                        command=lambda: l_part())
    blpart.pack(side="left", padx=4)
    ttk.Button(_lb, text="Open the copy",
               command=lambda: l_open()).pack(side="left", padx=4)
    run_buttons.extend([blstat, blrec, bllook, blrest, blpart])

    linfo = tk.StringVar(value="Paste the stream's link, then press "
                               "\u201cRecord from the start\u201d.")
    ttk.Label(lbody, textvariable=linfo, foreground="#06a", wraplength=980,
              justify="left").pack(anchor="w", padx=8, pady=(2, 4))

    # The chat replay of a finished stream, as subtitles beside the video
    chf = ttk.LabelFrame(lbody, text="Live chat replay as subtitles")
    chf.pack(fill="x", padx=8, pady=(2, 6))
    _c1 = ttk.Frame(chf)
    _c1.pack(fill="x", padx=6, pady=(4, 2))
    cfmt = tk.StringVar(value=str(EXTRA.get("chat_format") or "ass"))
    clines = tk.StringVar(value=str(EXTRA.get("chat_lines") or "5"))
    chold = tk.StringVar(value=str(EXTRA.get("chat_hold") if EXTRA.get(
        "chat_hold") not in (None, "") else "15"))
    cside = tk.StringVar(value=str(EXTRA.get("chat_side") or "right"))
    cplace = tk.StringVar(value=str(EXTRA.get("chat_place") or "bottom"))
    cpad = tk.StringVar(value=str(EXTRA.get("chat_pad") or "0"))
    cnames = tk.BooleanVar(value=EXTRA.get("chat_names") is not False)
    cembed = tk.BooleanVar(value=bool(EXTRA.get("chat_embed")))
    for label, var, kind, vals, w in (
            ("Format:", cfmt, "combo", chat.FORMATS, 6),
            ("Messages at once:", clines, "spin", (1, 30), 4),
            ("Longest on screen (s, 0 = no limit):", chold, "entry", None, 5),
            ("Side:", cside, "combo", chat.SIDES, 7),
            ("Height:", cplace, "combo", chat.PLACES, 7)):
        ttk.Label(_c1, text=label).pack(side="left", padx=(0, 3))
        if kind == "combo":
            cb = ttk.Combobox(_c1, textvariable=var, values=list(vals),
                              state="readonly", width=w)
            cb.bind("<<ComboboxSelected>>", lambda _e: remember_soon())
            cb.pack(side="left", padx=(0, 10))
        elif kind == "spin":
            ttk.Spinbox(_c1, textvariable=var, from_=vals[0], to=vals[1],
                        width=w).pack(side="left", padx=(0, 10))
        else:
            ttk.Entry(_c1, textvariable=var, width=w).pack(side="left",
                                                           padx=(0, 10))
    _c2 = ttk.Frame(chf)
    _c2.pack(fill="x", padx=6, pady=2)
    ttk.Checkbutton(_c2, text="User names", variable=cnames,
                    command=lambda: remember_soon()).pack(side="left")
    ttk.Checkbutton(_c2, text="Also add it to the video as a subtitle track "
                              "(copied, nothing re-encoded)", variable=cembed,
                    command=lambda: remember_soon()).pack(side="left", padx=10)
    ttk.Label(_c2, text="SRT spaces:").pack(side="left", padx=(6, 3))
    ttk.Entry(_c2, textvariable=cpad, width=4).pack(side="left")
    bchat = ttk.Button(_c2, text="Get the chat", command=lambda: l_chat())
    bchat.pack(side="right")
    run_buttons.append(bchat)
    ttk.Label(chf, foreground="#555", wraplength=960, justify="left", text=(
        "Downloads only the chat (a few MB) and saves it next to the video "
        "with the same name, so the player shows it like subtitles. A "
        "message stays until \u201cMessages at once\u201d newer ones are "
        "below it: 1 is like normal subtitles, 6 looks like the chat "
        "scrolling. A part made with \u201cDownload this part\u201d gets "
        "the chat of that part. ASS keeps the side and colours in every "
        "player; SRT plays everywhere (SRT spaces push it right in players "
        "that ignore the side, try 40-60); TXT is a readable log. Adding it "
        "as a track: an .mkv is updated, an .mp4 gets an .mkv copy beside "
        "it.")).pack(anchor="w", padx=6, pady=(2, 6))
    live_state.update(live_part=lpart, chat_format=cfmt, chat_lines=clines,
                      chat_hold=chold, chat_side=cside, chat_place=cplace,
                      chat_pad=cpad, chat_names=cnames, chat_embed=cembed)
    ttk.Label(lbody, foreground="#555", wraplength=980, justify="left", text=(
        "\u2022 Record from the start: from the stream's first minute, even "
        "if it began hours ago. Video and audio are added to the file as they "
        "arrive, so it can be watched while it records, and a stop loses "
        "nothing.\n"
        "\u2022 Watch what's there so far: makes \u201cTITLE (so far).mp4\u201d "
        "without re-encoding. During a recording it only reads the pieces; "
        "after a stop it first joins any loose ones.\n"
        "\u2022 Download the rest: after a stop. While the stream is live or "
        "has just ended it carries on with the same pieces -- nothing already "
        "here is fetched again. Once it is a normal video it fetches only "
        "the part you are missing.\n"
        "\u2022 Download this part: only the time range above, of a stream "
        "that has finished -- e.g. the hours you have not watched yet. Saved "
        "as \u201cTITLE [id] (from 1h00m00s)\u201d.\n"
        "\u2022 Cookies, proxy and the JavaScript settings come from the "
        "Download tab (More options).")).pack(anchor="w", padx=8,
                                               pady=(0, 8))
    areas["live"].pack(fill="both", expand=True)
    l_made = {"path": ""}

    def l_net_args():
        vals = panes["get"].values()
        argv = []
        for dest, flag in (("cookies", "--cookies"),
                           ("cookies_from_browser", "--cookies-from-browser"),
                           ("proxy", "--proxy"),
                           ("js_runtimes", "--js-runtimes"),
                           ("remote_components", "--remote-components")):
            v = str(vals.get(dest) or "").strip()
            if v:
                argv += [flag, v]
        return argv

    def l_url():
        u = lurl.get().strip()
        if not u:
            messagebox.showwarning("musictool", "Paste the stream's link first.")
        return u

    def l_dir(must_exist=True):
        d = os.path.abspath(os.path.expanduser(lfolder.get().strip() or "."))
        if must_exist and not os.path.isdir(d):
            messagebox.showwarning("musictool", "Pick the folder the recording "
                                   "is in.")
            return ""
        return d

    def l_copies():
        """Copies made so far, newest first."""
        found = []
        for d in {lout.get().strip() or l_dir(False), l_dir(False)}:
            try:
                for n in os.listdir(d):
                    if " (so far)." in n and not n.endswith(".tmp"):
                        p = os.path.join(d, n)
                        found.append((os.path.getmtime(p), p))
            except OSError:
                pass
        return [p for _t, p in sorted(found, reverse=True)]

    def l_after(_rc=None):
        log(f"\n(exit {_rc})")
        made = l_copies()
        if made and made[0] != l_made["path"]:
            l_made["path"] = made[0]
            linfo.set(f"ready to watch: {made[0]}   -- \u201cOpen the copy\u201d "
                      f"plays it")

    def l_status():
        u = l_url()
        if u:
            remember(url=u)
            do_argv(["live", l_dir(False), "--status", u] + l_net_args(),
                    after=l_fill_streams)

    def l_record():
        u = l_url()
        if not u:
            return
        d = l_dir(False)
        vals = dict(panes["get"].values())
        vals.update(keep_video=not laudio.get(), no_lyrics=True,
                    no_playlist=True, yes_playlist=False, lyrics_only=False,
                    live_from_now=False, output_dir=d)
        sel = l_selector()
        if sel:
            # exactly the streams chosen above, copied as they are
            vals.update(format_selector=sel, exact=True, quality="",
                        max_size="")
        remember(url=u, path=d)
        linfo.set("recording -- \u201cWatch what's there so far\u201d works "
                  "while it runs")
        do_argv(uicmd.build_argv("get", vals, positionals=[u],
                                 parser=build_parser()), after=l_after)

    def l_look():
        d = l_dir()
        if d:
            do_argv(["live", d])

    def l_watch():
        d = l_dir()
        if not d:
            return
        out = lout.get().strip()
        remember_soon()
        if running["n"]:
            # Something is running -- most likely the recording itself. Its
            # pieces are not touched: they are only read into a copy, on a
            # thread of its own, so the recording carries on undisturbed.
            log("\nmaking a copy of what has been recorded so far (the "
                "recording carries on) ...")
            linfo.set("making a copy of what has been recorded so far ...")

            def job():
                rc, made = live.watch_copy(d, live.scan(d), out or None, log=log)
                ui(lambda: l_after(rc))
            threading.Thread(target=job, daemon=True).start()
            return
        do_argv(["live", d, "--join"] + (["--out", out] if out else []),
                after=l_after)

    def l_resume():
        u = l_url()
        d = l_dir() if u else ""
        if u and d:
            remember(url=u, path=d)
            do_argv(["live", d, "--resume", u] + l_net_args(), after=l_after)

    def l_part_value():
        """The part field, checked. -> "" (all of it), the text, or None."""
        t = lpart.get().strip()
        if not t:
            return ""
        try:
            parse_range(t)
        except ValueError as e:
            messagebox.showwarning("musictool", f"Only this part: {e}\n\n"
                                   "e.g. 1:00:00- (from 1 hour to the end) or "
                                   "1:00:00-2:30:00")
            return None
        return t

    def l_part():
        u = l_url()
        if not u:
            return
        t = l_part_value()
        if t is None:
            return
        if not t:
            messagebox.showinfo("musictool", "Type the part you want in "
                                "\u201cOnly this part\u201d first, e.g. "
                                "1:00:00- for everything after the first hour.")
            return
        d = l_dir(False)
        vals = dict(panes["get"].values())
        vals.update(keep_video=not laudio.get(), no_lyrics=True,
                    no_playlist=True, yes_playlist=False, lyrics_only=False,
                    output_dir=d, part=t)
        sel = l_selector()
        if sel:
            vals.update(format_selector=sel, exact=True, quality="",
                        max_size="")
        remember(url=u, path=d)
        remember_soon()
        linfo.set(f"downloading {t} of it")
        do_argv(uicmd.build_argv("get", vals, positionals=[u],
                                 parser=build_parser()), after=l_after)

    def l_chat():
        u = l_url()
        if not u:
            return
        d = l_dir(False)
        argv = ["chat", u, "-o", d, "--format", cfmt.get() or "ass"]
        for flag, var, cast in (("--lines", clines, int),
                                ("--hold", chold, float),
                                ("--pad", cpad, int)):
            t = var.get().strip()
            if not t:
                continue
            try:
                cast(t)
            except ValueError:
                messagebox.showwarning("musictool", f"{flag[2:]}: not a "
                                       f"number: {t}")
                return
            argv += [flag, t]
        argv += ["--side", cside.get() or "right",
                 "--place", cplace.get() or "bottom"]
        if not cnames.get():
            argv.append("--no-names")
        if cembed.get():
            argv.append("--embed")
        # The part comes from the video's name ("(from 1h00m00s)"). Only
        # with no video of it in the folder is the field used instead.
        t = l_part_value()
        if t is None:
            return
        vid = chat.video_id(u)
        if t and not (vid and chat.find_videos(d, vid)):
            argv += ["--range", t]
        remember(url=u, path=d)
        remember_soon()
        linfo.set("getting the chat replay ...")
        do_argv(argv + l_net_args(), after=lambda rc: log(f"\n(exit {rc})"))

    def l_open():
        p = l_made["path"] if os.path.exists(l_made["path"] or "") else \
            next(iter(l_copies()), "")
        if not p:
            messagebox.showinfo("musictool", "No copy yet -- press \u201cWatch "
                                "what's there so far\u201d first.")
            return
        log(f"opening {p}")
        try:
            open_path(p)
        except Exception as e:                              # noqa: BLE001
            log(f"!! could not open it: {e}")

    # ================================================== log pane (resizable)
    logf = ttk.LabelFrame(root, text="log")
    split.add(logf, minsize=90, stretch="always",
              height=uicmd.pane_split(800))

    # ---------------------------------------------- live download progress
    # A horizontal paned window: drag the sash to make the bars as wide or as
    # narrow as you like, which gives the text beside them more room. The width
    # you settle on is saved and restored next time.
    pstate = progress.State()
    pwrap = ttk.PanedWindow(logf, orient="horizontal")
    pwrap.pack(fill="x", padx=6, pady=(4, 0))
    pbars = ttk.Frame(pwrap)
    ptext = ttk.Frame(pwrap)
    pwrap.add(pbars, weight=0)
    pwrap.add(ptext, weight=1)
    overall_bar = ttk.Progressbar(pbars, mode="determinate", maximum=100)
    file_bar = ttk.Progressbar(pbars, mode="determinate", maximum=100)
    overall_bar.pack(fill="x", pady=(0, 3))
    file_bar.pack(fill="x")
    overall_lbl = tk.StringVar(value="playlist: —")
    file_lbl = tk.StringVar(value="file: —")
    ttk.Label(ptext, textvariable=overall_lbl, anchor="w").pack(fill="x")
    ttk.Label(ptext, textvariable=file_lbl, anchor="w",
              foreground="#444").pack(fill="x")

    # ttk.PanedWindow leaves the sash at 0 until it lays out, so the bars used
    # to open invisible and only appear once you dragged the slider. Give them
    # their width on the first layout that is wide enough, and never less than
    # BARS_MIN -- the drag still works, it just cannot start from nothing.
    BARS_MIN = 160
    _pw_want = max(int(cfg.get("progress_width") or 0), 300)
    _pw_mine = [False]          # True once you have dragged it yourself

    def place_bars(_ev=None):
        if _pw_mine[0]:
            return
        try:
            avail = int(pwrap.winfo_width())
            pwrap.sashpos(0, min(_pw_want, max(BARS_MIN, avail - 140)))
        except Exception:
            return              # not laid out yet -- the next <Configure> retries
        pwrap.unbind("<Configure>")

    root.after(120, place_bars)
    pwrap.bind("<Configure>", place_bars)

    def save_pane_width(_ev=None):
        _pw_mine[0] = True
        try:
            w = int(pwrap.sashpos(0))
        except Exception:
            return
        if w > 40:
            configmod.remember(progress_width=w)

    pwrap.bind("<ButtonRelease-1>", save_pane_width)

    def _apply_progress(ev):
        if ev == FINISH:
            overall_bar.configure(value=100)
            overall_lbl.set("done")
            file_lbl.set("file: —")
            return
        pstate.handle(ev)
        frac = pstate.fraction()
        overall_bar.configure(value=round((frac if frac is not None
                                           else pstate.pct / 100.0) * 100, 1))
        if pstate.total:
            head = f"video {pstate.item} of {pstate.total}"
        elif pstate.batch_n:
            head = f"video {pstate.batch_i} of {pstate.batch_n}"
        else:
            head = "single download"
        left = pstate.remaining()
        eta = pstate.eta_total()
        pct = (frac if frac is not None else pstate.pct / 100.0) * 100
        bits = [head, f"{pct:5.1f}%"]
        if pstate.total or pstate.batch_n:
            bits.append("this is the last one" if left <= 1
                        else f"{left - 1} still to go after this")
        bits.append(f"{pstate.elapsed_text()} elapsed")
        bits.append(f"about {eta} left in total" if eta
                    else "timing the first file…")
        overall_lbl.set("   ·   ".join(bits))
        file_bar.configure(value=pstate.pct)

        def short(p):
            # ntpath-style: a C:\Music\... path must not show up whole
            return os.path.basename((p or "…").replace("\\", "/"))

        if len(pstate.slots) > 1:
            # Several videos at once: one line each, so nothing hides behind
            # whichever one happened to report last.
            file_lbl.set("   ".join(
                f"{short(v.get('name'))} {v.get('pct', 0.0):4.1f}%"
                for _k, v in sorted(pstate.slots.items())))
        elif pstate.note or pstate.got or pstate.frags:
            # a live stream (no total), a dropped connection, or musictool
            # waiting to try again -- say that, not a frozen speed
            file_lbl.set(f"{short(pstate.filename)}   "
                         + (f"{pstate.pct:5.1f}%   " if pstate.pct and not
                            pstate.note else "") + pstate.file_text())
        else:
            file_lbl.set(f"{short(pstate.filename)}   {pstate.pct:5.1f}%"
                         f"   {pstate.speed}   ETA {pstate.eta or '—'}")

    def reset_progress():
        pstate.reset()
        overall_bar.configure(value=0)
        file_bar.configure(value=0)
        overall_lbl.set("playlist: —")
        file_lbl.set("file: —")

    # The hook runs on the worker thread: put only, never touch a widget here.
    progress.set_hook(pq.put)

    tools = ttk.Frame(logf)
    tools.pack(fill="x", padx=4, pady=(2, 0))
    ttk.Label(tools, foreground="#666",
              text="drag the bar above to resize, or:").pack(side="left", padx=4)

    def sash_y():
        try:
            return split.sash_coord(0)[1]
        except Exception:
            return 0

    def log_px():
        return max(split.winfo_height() - sash_y(), 1)

    def move_log(delta_px):
        """Positive = taller log, so the sash moves up."""
        split.sash_place(0, 0, uicmd.move_sash(sash_y(), split.winfo_height(),
                                               -delta_px))
    for label, delta in (("log taller", 90), ("log shorter", -90)):
        ttk.Button(tools, text=label,
                   command=lambda d=delta: move_log(d)).pack(side="left", padx=2)

    def reset_split(_ev=None):
        split.sash_place(0, 0, uicmd.pane_split(split.winfo_height()))
    ttk.Button(tools, text="reset split", command=reset_split).pack(side="left",
                                                                    padx=2)

    def clear_log():
        outbox.configure(state="normal")
        outbox.delete("1.0", "end")
        outbox.configure(state="disabled")
    ttk.Button(tools, text="Clear", command=clear_log).pack(side="right", padx=4)

    logbody = ttk.Frame(logf)
    logbody.pack(fill="both", expand=True)
    outbox = tk.Text(logbody, height=12, state="disabled", wrap="none")
    lsb = ttk.Scrollbar(logbody, command=outbox.yview)
    lhb = ttk.Scrollbar(logbody, orient="horizontal", command=outbox.xview)
    outbox.configure(yscrollcommand=lsb.set, xscrollcommand=lhb.set)
    outbox.pack(side="left", fill="both", expand=True)
    lsb.pack(side="right", fill="y")
    lhb.pack(side="bottom", fill="x")

    pump()
    log("musictool ready.  Every CLI option is available on these tabs;")
    log("each run prints the exact command it used, so you can copy it.")
    log("Tick files on the Fix lyrics tab to run a subset; untick to skip.\n")

    def _on_close():
        remember_all()          # closing the window saves, it does not discard
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", _on_close)
    root.update_idletasks()
    reset_split()
    root.mainloop()
