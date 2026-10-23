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
from . import config as configmod
from . import download
from . import formats
from . import progress
from . import uicmd
from .cli import build_parser, main as cli_main

BOX_ON, BOX_OFF = "[x]", "[ ]"        # ASCII: renders in every Tk font
WHEEL = ("<MouseWheel>", "<Button-4>", "<Button-5>")   # Windows/mac, then X11


FINISH = "__finish__"     # sentinel: worker done -> bars to 100%


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
            if isinstance(w, (ttk.Treeview, tk.Text, tk.Listbox)):
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


class OptionsPane(ttk.Frame):
    """Auto-generated widgets for every option of one subcommand."""

    COLUMNS = 2

    def __init__(self, parent, sub, cfg, on_change=None):
        super().__init__(parent)
        self.sub = sub
        self.vars = {}
        self.on_change = on_change
        self.parser = build_parser()
        self._build(cfg)

    def touched(self):
        """The user moved something by hand -- modes stop overriding it."""
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
            if isinstance(v, tk.BooleanVar):
                v.set(bool(val))
            else:
                v.set("" if val is None else str(val))

    def _build(self, cfg):
        """Widgets come from uicmd.widget_specs, i.e. from the parser itself."""
        specs = uicmd.widget_specs(self.sub, cfg, self.parser)
        groups = {}
        for spec in specs:
            groups.setdefault(spec["group"], []).append(spec)
        for title, items in groups.items():
            box = ttk.LabelFrame(self, text=title)
            box.pack(fill="x", padx=6, pady=4)
            for col, spec in enumerate(items):
                self._widget(box, spec, col)

    def _widget(self, parent, spec, col):
        dest, kind, label = spec["dest"], spec["kind"], spec["label"]
        start = spec["default"]
        row, column = divmod(col, self.COLUMNS)
        if kind in ("check", "check_off"):
            self._toggle(parent, spec, row=row, column=column)
            return
        v = tk.StringVar(value="" if start in (None, "") else str(start))
        outer = ttk.Frame(parent)
        outer.grid(row=row, column=column, sticky="we", padx=6, pady=2)
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
        q.put(str(msg))

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
        newest_pct, others, finished = None, [], False
        try:
            while True:
                ev = pq.get_nowait()
                if ev == FINISH:
                    finished = True
                elif (ev or {}).get("type") == "pct":
                    newest_pct = ev      # redraws: only the last one matters
                else:
                    others.append(ev)    # item/file/done: none may be dropped,
        except queue.Empty:              # or the bar loses the playlist position
            pass
        for ev in others:
            _apply_progress(ev)
        if newest_pct is not None:
            _apply_progress(newest_pct)
        if finished:
            _apply_progress(FINISH)

    def busy(on):
        state = "disabled" if on else "normal"
        for w in run_buttons:
            try:
                w.configure(state=state)
            except Exception:
                pass
        # Stop deliberately keeps its state: it stays clickable while every
        # other button is greyed out, and while idle it can clear a request
        # you made by mistake.
        status.set("stopping..." if cancel.requested() else
                   ("working..." if on else "idle"))

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
        log("\nstopping -- the current file finishes, then the run ends "
            "(files already downloaded keep their lyrics)")

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

    def do_argv(argv, after=None):
        """Run a CLI command on the worker thread, echoing it first."""
        cancel.reset()
        reset_progress()
        log("\n$ " + uicmd.quote_cmd(argv) + "\n")

        def job():
            with contextlib.redirect_stdout(QueueStream(q)), \
                    contextlib.redirect_stderr(QueueStream(q)):
                return cli_main(argv)
        worker(job, after=after)

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
    run_buttons = []
    path_combos = []
    url_combos = []

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
    ttk.Label(dl, text="URL(s), one per line — video or playlist:").pack(
        anchor="w", padx=8, pady=(8, 2))
    urls = tk.Text(dl, height=3)
    urls.pack(fill="x", padx=8)
    ucb = ttk.Combobox(dl, values=url_history_values, state="readonly", width=70)
    ucb.pack(fill="x", padx=8, pady=2)
    ucb.bind("<<ComboboxSelected>>", lambda e: urls.insert("end", ucb.get() + "\n"))
    url_combos.append(ucb)
    ttk.Label(dl, text="↑ recently used URLs (select to insert)",
              foreground="#888").pack(anchor="w", padx=8)

    # ================================================= the mode
    # Two questions decide nine checkboxes, so ask those two and set the rest.
    # "Music or video" and "one item or a playlist" are shown as a grid because
    # they are independent: every combination is a mode that already exists.
    user_touched = {"v": False}

    def on_options_changed():
        user_touched["v"] = True
        describe_mode()

    whatvar = tk.StringVar(value="music")
    scopevar = tk.StringVar(value="single")
    mode_desc = tk.StringVar(value="")

    mbox = ttk.LabelFrame(dl, text="what are you downloading?")
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
    ttk.Label(dl, textvariable=mode_desc, foreground="#06a",
              wraplength=900, justify="left").pack(anchor="w", padx=10)

    areas["get"] = ScrollArea(dl)
    panes["get"] = OptionsPane(areas["get"].frame, "get", cfg,
                               on_change=on_options_changed)
    # The handful of toggles worth seeing stay on the tab; the long tail of
    # options (cookies, proxy, speed, retries, subtitle languages) hides behind
    # one checkbox, because a mode has already set all of it sensibly.
    quickbar = ttk.Frame(dl)
    quickbar.pack(fill="x", padx=8)
    panes["get"].quick_row(quickbar, text="fine-tune")
    adv_open = tk.BooleanVar(value=False)
    ttk.Checkbutton(
        dl, text="More options  (cookies, proxy, speed limit, subtitles, "
                 "retries, filename template\u2026)",
        variable=adv_open, command=lambda: toggle_adv()).pack(anchor="w", padx=8)

    def toggle_adv():
        """The whole generated option grid appears and disappears as one unit,
        so the tab does not show a tall empty box when it is not wanted."""
        if adv_open.get():
            areas["get"].pack(fill="both", expand=True)
            panes["get"].pack(fill="x")
        else:
            panes["get"].pack_forget()
            areas["get"].pack_forget()

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
            bget.configure(text="Download video"
                           if uicmd.MODE_WHAT[mode] == "video"
                           else "Download + lyrics")
        except NameError:
            pass            # first apply happens before the buttons exist

    def mode_picked():
        """The user chose a mode by hand: obey it, and stop guessing."""
        user_touched["v"] = False
        apply_mode(current_mode())

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

    url_seen = {"text": ""}
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
    bfmt.pack(side="left", padx=4)
    bget = ttk.Button(brow, text="Download + lyrics",
                      command=lambda: go_get())
    bget.configure(text="Download video"
                   if uicmd.MODE_WHAT[current_mode()] == "video"
                   else "Download + lyrics")
    bget.pack(side="left", padx=4)
    grow.pack(side="bottom", fill="x")
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
        do_argv(uicmd.build_argv("formats", vals, positionals=[u[0]],
                                 parser=build_parser()))

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

    # ================================================== Lyrics tab
    ly = ttk.Frame(nb)
    nb.add(ly, text="  Fix lyrics  ")
    lvar = path_field(ly, cfg.get("output_dir", "."))

    areas["lyrics"] = ScrollArea(ly)
    panes["lyrics"] = OptionsPane(areas["lyrics"].frame, "lyrics", cfg)
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
                                      cfg)
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
    panes["providers"] = OptionsPane(tframe, "providers", cfg)
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
    panes["extract"] = OptionsPane(areas["lyricsfiles"].frame, "extract", cfg)
    panes["extract"].pack(fill="x")
    panes["embed"] = OptionsPane(areas["lyricsfiles"].frame, "embed", cfg)
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

    qcols = ("id", "what", "container", "video", "codec", "size", "variants")
    qwrap = ttk.Frame(qtab)
    qwrap.pack(fill="both", expand=True, padx=8)
    qtree = ttk.Treeview(qwrap, columns=qcols, show="headings", height=10)
    qsb = ttk.Scrollbar(qwrap, orient="vertical", command=qtree.yview)
    qtree.configure(yscrollcommand=qsb.set)
    qsb.pack(side="right", fill="y")
    qtree.pack(side="left", fill="both", expand=True)
    for cid, txt, wd in (
            ("id", "id", 70), ("what", "what", 86), ("container", "container", 80),
            ("video", "video", 60), ("codec", "codec", 66), ("size", "size", 88),
            ("variants", "variants", 92)):
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
        row, audio = formats.pair_with_audio(r, q_rows, q_cap())
        sel = formats.selector_for(row, audio)
        qfmt.set(sel)
        cap = q_cap()
        total = formats.total_bytes(row, audio)
        extra = ""
        if cap and total and total > cap:
            extra = (f"  (over your {formats.human_bytes(cap)} cap by "
                     f"{formats.human_bytes(total - cap)})")
        audio_txt = (f" + {audio['acodec']} audio" if audio
                     else " (audio included)")
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
                formats.human_bytes(r["bytes"]),
                f"{n} merged" if n > 1 else ""))
            q_iids[iid] = r
        if qmode.get() == "auto":
            preset = next((m for m in formats.AUTO_MODES
                           if formats.AUTO_LABELS[m] == qpreset.get()), "best")
            sel, v, a, why = formats.auto_pick(q_pool(), mode=preset,
                                               max_bytes=q_cap())
            qfmt.set(sel or "")
            qinfo.set(("auto: " if sel else "auto: nothing matched — ") + why)
        else:
            q_picked()
        qinfo.set(qinfo.get() + f"     [{len(rows)} of {len(q_rows)} shown]")

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
                "cookies_from_browser": g.get("cookies_from_browser")}

        def job():
            return download.probe(url, opts)

        def after(info):
            if not isinstance(info, dict) or info.get("error"):
                err = str((info or {}).get("error") or "no answer")
                q_rows.clear()
                qinfo.set("could not read the qualities: "
                          + err.splitlines()[0][:240])
                hint = download.ADVICE.get(download.classify_error(err))
                if hint:
                    qinfo.set(qinfo.get() + "\n-> " + hint)
                q_draw()
                return
            q_rows[:] = formats.rows_from_info(info)
            title = info.get("title") or url
            log(f"{len(q_rows)} raw qualities for {title!r}")
            q_draw()

        qinfo.set(f"reading the qualities of {url} …")
        worker(job, after=after)

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

    _saved_pw = int(cfg.get("progress_width") or 0)
    if _saved_pw > 40:
        root.after(150, lambda: pwrap.sashpos(0, _saved_pw))

    def save_pane_width(_ev=None):
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
        if frac is not None:
            overall_bar.configure(value=round(frac * 100, 1))
            overall_lbl.set(
                f"playlist {frac * 100:5.1f}%   item {pstate.item} of "
                f"{pstate.total}   ·   {pstate.elapsed_text()} elapsed, "
                f"est. {pstate.eta_total() or '…'} left")
        else:
            overall_bar.configure(value=pstate.pct)
            overall_lbl.set(f"single download {pstate.pct:5.1f}%"
                            f"   ·   {pstate.elapsed_text()} elapsed")
        file_bar.configure(value=pstate.pct)
        # ntpath-style: a C:\Music\... path must not show up whole
        name = os.path.basename(pstate.filename.replace("\\", "/")) \
            if pstate.filename else "…"
        file_lbl.set(f"{name}   {pstate.pct:5.1f}%   {pstate.speed}"
                     f"   ETA {pstate.eta or '—'}")

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

    root.update_idletasks()
    reset_split()
    root.mainloop()
