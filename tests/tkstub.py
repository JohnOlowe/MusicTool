"""A minimal, headless stand-in for tkinter, for testing musictool.gui.

This does NOT render anything. It records the widget tree that gui.run() builds
so the tests can execute the real GUI code path -- constructing every tab,
invoking buttons, ticking boxes -- on a machine with no display and no tkinter
(there is no tkinter in CI, and none on Termux by default).

What it can prove: that run() builds without error, which widgets exist and
where, that a checkbox and the generated option widget share one variable, and
exactly which argv a button sends to the CLI.
What it cannot prove: pixels, font metrics, real sash dragging.
"""

import sys

ALL = []          # every widget created, in creation order
PACK_ORDER = []   # widgets in the order they were packed/grid/placed
ROOTS = []        # Tk instances
MESSAGES = []     # messagebox calls
DIALOG = {}       # canned return values for filedialog
DEFAULT_HEIGHT = 700
SCREEN = [1920, 1080]   # what winfo_screenwidth/height report; tests change it


class Event:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class Variable:
    kind = "string"

    def __init__(self, value=None, name=None, **kw):
        self._v = self._coerce(value)
        self.trace_calls = 0

    def _coerce(self, v):
        return v

    def get(self):
        return self._v

    def set(self, v):
        self._v = self._coerce(v)

    def trace_add(self, *a, **k):
        self.trace_calls += 1


class StringVar(Variable):
    def _coerce(self, v):
        return "" if v is None else str(v)


class BooleanVar(Variable):
    kind = "bool"

    def _coerce(self, v):
        return bool(v)


class IntVar(Variable):
    kind = "int"

    def _coerce(self, v):
        return int(v or 0)


class DoubleVar(Variable):
    kind = "double"

    def _coerce(self, v):
        return float(v or 0.0)


class Widget:
    """Base: records options, children, geometry calls and bindings."""

    kind = "widget"

    def __init__(self, master=None, cnf=None, **kw):
        self.master = master
        self.options = dict(cnf or {})
        self.options.update(kw)
        self.children = []
        self.bindings = {}
        self.global_bindings = {}
        self.geo = None          # ("pack"|"grid"|"place", kwargs, master)
        self.panes = []          # PanedWindow only
        self.pending = []        # after() callbacks
        self.destroyed = False
        if isinstance(master, Widget):
            master.children.append(self)
        ALL.append(self)

    # ------------------------------------------------------------- geometry
    forgotten = False      # True after pack_forget / grid_remove: hidden

    def _set_geo(self, how, kw):
        self.geo = (how, dict(kw), self.options.get("in_"))
        self.forgotten = False
        PACK_ORDER.append(self)

    def pack(self, **kw):
        self._set_geo("pack", kw)
        return self

    def grid(self, **kw):
        self._set_geo("grid", kw)
        return self

    def place(self, **kw):
        self._set_geo("place", kw)
        return self

    def pack_forget(self):
        self.geo = None
        self.forgotten = True

    def grid_remove(self):
        # real tk keeps the remembered options so a bare grid() brings it back
        self.geo = None
        self.removed = True

    def grid_info(self):
        return {} if self.geo is None else {"in": self.geo[2]}

    grid_forget = pack_forget

    def grid_rowconfigure(self, *a, **k):
        pass

    grid_columnconfigure = grid_rowconfigure

    # ------------------------------------------------------------- options
    def configure(self, cnf=None, **kw):
        self.options.update(cnf or {})
        self.options.update(kw)
        return self

    config = configure

    def cget(self, key):
        return self.options.get(key)

    def __setitem__(self, k, v):
        self.options[k] = v

    def __getitem__(self, k):
        return self.options.get(k)

    # ------------------------------------------------------------- events
    def bind(self, seq, func=None, add=None):
        self.bindings.setdefault(seq, []).append(func)
        return "bind"

    def unbind(self, seq, *a):
        self.bindings.pop(seq, None)

    def bind_all(self, seq, func=None, add=None):
        self.global_bindings.setdefault(seq, []).append(func)

    def unbind_all(self, seq):
        self.global_bindings.pop(seq, None)

    def fire(self, seq, **kw):
        ev = Event(**kw)
        out = None
        for fn in self.bindings.get(seq, []) + self.global_bindings.get(seq, []):
            out = fn(ev)
        return out

    # ------------------------------------------------------------- sizes
    def winfo_height(self):
        return int(self.options.get("_height", DEFAULT_HEIGHT))

    def winfo_width(self):
        return int(self.options.get("_width", 900))

    def winfo_reqwidth(self):
        return int(self.options.get("_reqwidth", 620))

    def winfo_reqheight(self):
        return 240

    def winfo_children(self):
        return list(self.children)

    def protocol(self, name=None, func=None):
        self.protocols[name] = func
        return func

    def close_window(self):
        """What a real window manager does when you press the X."""
        fn = self.protocols.get("WM_DELETE_WINDOW")
        return fn() if fn else None

    def update_idletasks(self):
        pass

    def update(self):
        pass

    def after(self, ms, func=None, *args):
        if func is not None:
            self.pending.append(func)
        return "after#1"

    after_idle = after

    def focus_set(self):
        pass

    # scroll protocols (Scrollbar(command=w.yview) needs these to exist)
    def yview(self, *a):
        pass

    def yview_scroll(self, *a):
        pass

    def xview(self, *a):
        pass

    def xview_scroll(self, *a):
        pass

    def destroy(self):
        self.destroyed = True

    # ------------------------------------------------------------- search
    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()

    def walk_shown(self):
        """Like walk(), but skipping anything taken off screen with
        pack_forget (and everything inside it). Folded option rows
        (grid_remove) still count: one click shows them."""
        if self.forgotten:
            return
        yield self
        for c in self.children:
            yield from c.walk_shown()

    def find(self, **pred):
        """First descendant (or self) whose options match every key."""
        for w in self.walk():
            if all(w.options.get(k) == v for k, v in pred.items()):
                return w
        return None

    def find_all(self, **pred):
        return [w for w in self.walk()
                if all(w.options.get(k) == v for k, v in pred.items())]


class Misc(Widget):
    pass


class Tk(Widget):
    kind = "root"

    def __init__(self, *a, **k):
        super().__init__(None, *a, **k)
        self.title_text = ""
        self.geometry_text = ""
        self.looped = False
        self.clipboard = ""
        self.protocols = {}
        ROOTS.append(self)

    def title(self, t=None):
        if t is not None:
            self.title_text = t
        return self.title_text

    def geometry(self, g=None):
        if g is not None:
            self.geometry_text = g
        return self.geometry_text

    def minsize(self, *a):
        self.options["minsize"] = a

    def maxsize(self, *a):
        self.options["maxsize"] = a

    def winfo_screenwidth(self):
        return SCREEN[0]

    def winfo_screenheight(self):
        return SCREEN[1]

    def clipboard_get(self):
        return self.clipboard

    def mainloop(self, n=0):
        self.looped = True       # returns at once: the test drives it by hand

    # --- clipboard: the Quality tab copies the command it built
    def clipboard_clear(self):
        self.clipboard = ""

    def clipboard_append(self, text):
        self.clipboard += text


class Frame(Widget):
    kind = "frame"


class LabelFrame(Frame):
    kind = "labelframe"


class Label(Widget):
    kind = "label"


class Button(Widget):
    kind = "button"

    def invoke(self):
        cmd = self.options.get("command")
        return cmd() if cmd else None


class Checkbutton(Button):
    kind = "checkbutton"

    @property
    def var(self):
        return self.options.get("variable")

    def toggle(self):
        v = self.var
        v.set(not v.get())
        cmd = self.options.get("command")
        if cmd:
            cmd()
        return v.get()

    def invoke(self):
        # clicking a checkbutton changes it; only then does the command run
        if self.var is None:
            cmd = self.options.get("command")
            return cmd() if cmd else None
        return self.toggle()


class Radiobutton(Checkbutton):
    kind = "radiobutton"

    def invoke(self):
        # a real radiobutton selects itself before running its command
        v = self.options.get("variable")
        if v is not None:
            v.set(self.options.get("value"))
        cb = self.options.get("command")
        if callable(cb):
            cb()


class Entry(Widget):
    kind = "entry"

    def get(self):
        v = self.options.get("textvariable")
        return v.get() if v is not None else self.options.get("text", "")

    def set(self, val):
        v = self.options.get("textvariable")
        if v is not None:
            v.set(val)

    def delete(self, *a):
        self.set("")

    def insert(self, i, s):
        self.set(self.get() + str(s))


class Combobox(Entry):
    kind = "combobox"


class Spinbox(Entry):
    kind = "spinbox"


class Text(Widget):
    kind = "text"

    def __init__(self, master=None, cnf=None, **kw):
        super().__init__(master, cnf, **kw)
        self.buf = ""

    def insert(self, index, s):
        self.buf += str(s)

    def get(self, a="1.0", b="end"):
        return self.buf

    def delete(self, a="1.0", b="end"):
        self.buf = ""

    def see(self, index):
        pass


    def yview(self, *a):
        """(first, last) visible fraction, like real tk.

        A box with room for all of its text reports (0.0, 1.0) -- it cannot
        scroll, which is what lets an enclosing scroll area keep the wheel.
        """
        if a:
            return None
        lines = str(self.buf).count("\n") + 1
        cap = int(self.options.get("height") or 0) or lines
        if lines <= cap:
            return (0.0, 1.0)
        return (0.0, round(cap / float(lines), 3))


class Canvas(Widget):
    kind = "canvas"

    def __init__(self, master=None, cnf=None, **kw):
        super().__init__(master, cnf, **kw)
        self.windows = {}
        self.scrollregion = None

    def create_window(self, xy, **kw):
        wid = f"win{len(self.windows) + 1}"
        self.windows[wid] = dict(kw, xy=xy)
        return wid

    def itemconfigure(self, wid, **kw):
        self.windows.setdefault(wid, {}).update(kw)

    itemconfig = itemconfigure

    def bbox(self, tag="all"):
        return (0, 0, self.winfo_reqwidth(), 400)

    def yview(self, *a):
        pass

    def yview_scroll(self, n, unit="units"):
        self.options.setdefault("scrolled", 0)
        self.options["scrolled"] += n

    def yview_moveto(self, frac):
        self.options["moveto"] = frac

    def xview(self, *a):
        pass


class Scrollbar(Widget):
    kind = "scrollbar"

    def __init__(self, master=None, cnf=None, **kw):
        super().__init__(master, cnf, **kw)
        self.range = (0.0, 1.0)

    def set(self, first, last):
        self.range = (first, last)

    def get(self):
        return self.range


class Separator(Widget):
    kind = "separator"


class PanedWindow(Widget):
    kind = "panedwindow"

    def __init__(self, master=None, cnf=None, **kw):
        super().__init__(master, cnf, **kw)
        self.sashes = {}

    def add(self, child, **kw):
        self.panes.append(dict(kw, child=child))
        if isinstance(child, Widget):
            child.options["_pane"] = len(self.panes) - 1

    def panes(self):
        return [p["child"] for p in self.panes]

    def sash_place(self, index, x, y):
        self.sashes[index] = (x, y)
        return (x, y)

    def sash_coord(self, index):
        return self.sashes.get(index, (0, int(DEFAULT_HEIGHT * 0.6)))

    def sashpos(self, index, newpos=None):
        """ttk.PanedWindow sash position: read it, or move it."""
        if newpos is None:
            return self.sashes.get(index, (0, 0))[0]
        self.sashes[index] = (int(newpos), 0)
        return int(newpos)

    def paneconfig(self, child, **kw):
        for p in self.panes:
            if p["child"] is child:
                p.update(kw)

    paneconfigure = paneconfig


class Notebook(Widget):
    kind = "notebook"

    def __init__(self, master=None, cnf=None, **kw):
        super().__init__(master, cnf, **kw)
        self.tabs = []

    def add(self, child, **kw):
        self.tabs.append(dict(kw, child=child))

    def tab(self, child, **kw):
        for t in self.tabs:
            if t["child"] is child:
                t.update(kw)

    def select(self, child=None):
        return None

    def tab_for(self, text):
        for t in self.tabs:
            if t.get("text", "").strip() == text.strip():
                return t["child"]
        return None


class Treeview(Widget):
    kind = "treeview"

    def __init__(self, master=None, cnf=None, **kw):
        super().__init__(master, cnf, **kw)
        self.nodes = {}        # iid -> dict(text, values, parent, open)
        self.order = []
        self.headings = {}
        self.columns = {}
        self._selection = []

    def heading(self, cid, **kw):
        self.headings[cid] = kw

    def column(self, cid, **kw):
        self.columns[cid] = kw

    def insert(self, parent, index, iid=None, **kw):
        iid = str(iid) if iid is not None else f"i{len(self.order)}"
        self.nodes[iid] = dict(kw, parent=parent)
        if index == "end":
            self.order.append(iid)
        else:
            self.order.insert(int(index) if str(index).isdigit() else 0, iid)
        return iid

    def delete(self, *iids):
        for iid in iids:
            self.nodes.pop(iid, None)
            if iid in self.order:
                self.order.remove(iid)
        self._selection = [i for i in self._selection if i in self.nodes]

    def get_children(self, parent=""):
        return tuple(i for i in self.order if self.nodes[i]["parent"] == parent)

    def item(self, iid, key=None, **kw):
        node = self.nodes.setdefault(iid, {})
        if key is not None and not kw:
            return node.get(key)
        node.update(kw)
        return node

    def set(self, iid, col, value=None):
        self.nodes[iid].setdefault("values", {})[col] = value

    def identify_row(self, y):
        try:
            return self.order[int(y)]
        except (ValueError, IndexError):
            return ""

    def selection(self, *a):
        return tuple(self._selection)

    def selection_set(self, *iids):
        self._selection = list(iids)

    def see(self, iid):
        pass


class Listbox(Widget):
    kind = "listbox"


class Menu(Widget):
    kind = "menu"

    def add_command(self, **kw):
        self.options.setdefault("items", []).append(kw)

    def add_separator(self, **kw):
        pass

    def post(self, *a):
        pass


# ---------------------------------------------------------------- sub-modules
class _TTK:
    Frame = Frame
    LabelFrame = LabelFrame
    Label = Label
    Button = Button
    Checkbutton = Checkbutton
    Radiobutton = Radiobutton
    Entry = Entry
    Combobox = Combobox
    Spinbox = Spinbox
    Notebook = Notebook
    Treeview = Treeview
    Scrollbar = Scrollbar
    Separator = Separator
    PanedWindow = PanedWindow
    Style = type("Style", (), {"theme_use": lambda self, *a: None,
                               "configure": lambda self, *a, **k: None,
                               "layout": lambda self, *a, **k: None})
    Progressbar = Widget


class _FileDialog:
    @staticmethod
    def askdirectory(**kw):
        return DIALOG.get("directory", "")

    @staticmethod
    def askopenfilename(**kw):
        return DIALOG.get("open", "")

    @staticmethod
    def asksaveasfilename(**kw):
        return DIALOG.get("save", "")


class _MessageBox:
    @staticmethod
    def _rec(kind, title, message, **kw):
        MESSAGES.append((kind, title, message))
        return True

    @classmethod
    def showinfo(cls, title, message, **kw):
        return cls._rec("info", title, message)

    @classmethod
    def showwarning(cls, title, message, **kw):
        return cls._rec("warning", title, message)

    @classmethod
    def showerror(cls, title, message, **kw):
        return cls._rec("error", title, message)

    @classmethod
    def askyesno(cls, title, message, **kw):
        cls._rec("yesno", title, message)
        return DIALOG.get("yesno", True)


class _TkModule:
    """Looks like the tkinter package to `import tkinter as tk`."""

    Tk = Tk
    Frame = Frame
    LabelFrame = LabelFrame
    Label = Label
    Button = Button
    Checkbutton = Checkbutton
    Radiobutton = Radiobutton
    Entry = Entry
    Text = Text
    Canvas = Canvas
    Scrollbar = Scrollbar
    Menu = Menu
    Listbox = Listbox
    PanedWindow = PanedWindow
    StringVar = StringVar
    BooleanVar = BooleanVar
    IntVar = IntVar
    DoubleVar = DoubleVar
    END = "end"
    BOTH = "both"
    X = "x"
    Y = "y"
    LEFT = "left"
    RIGHT = "right"
    NORMAL = "normal"
    DISABLED = "disabled"
    VERTICAL = "vertical"
    HORIZONTAL = "horizontal"

    def __getattr__(self, name):       # ttk / filedialog / messagebox
        return {"ttk": _TTK, "filedialog": _FileDialog,
                "messagebox": _MessageBox}.get(name)


def install():
    """Put the stub into sys.modules so `import tkinter` picks it up."""
    mod = _TkModule()
    sys.modules["tkinter"] = mod
    sys.modules["tkinter.ttk"] = _TTK
    sys.modules["tkinter.filedialog"] = _FileDialog
    sys.modules["tkinter.messagebox"] = _MessageBox
    return mod


def dispatch(seq, widget=None, **kw):
    """Deliver an event the way bind_all does: to every registered handler.

    Real Tk routes a bind_all binding to whichever widget the pointer is over,
    with ev.widget set to it -- which is how ScrollArea tells whether the wheel
    event belongs to it. This replays that for every widget that bound seq.
    """
    ev = Event(widget=widget, **kw)
    n = 0
    for w in list(ALL):
        for fn in w.global_bindings.get(seq, []):
            fn(ev)
            n += 1
    return n


def reset():
    ALL.clear()
    PACK_ORDER.clear()
    ROOTS.clear()
    MESSAGES.clear()
    DIALOG.clear()


def reset_state():
    """Alias kept for readability at the call site."""
    reset()
