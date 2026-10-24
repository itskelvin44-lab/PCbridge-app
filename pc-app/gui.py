"""
PC Bridge window (tkinter). Same core as the terminal version (core.py).

Threads: the window owns the main thread. The core runs in an asyncio loop on a
background thread and reports events into a queue; the window empties the queue
about ten times a second. Mouse and key messages never reach the window unless
"Show each key in Details" is on.

LOOK: every colour lives in THEME just below, fonts in App._fonts(). The window
opens maximised with the content centred in one column. F11 = full screen,
Esc = leave full screen. Rounded panels are drawn by the Card class (plain tkinter,
no extra library, so the PyInstaller bundle and the .deb are unchanged).
"""

import asyncio
import fcntl
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox

import paths
from core import BridgeCore

# ---------------------------------------------------------------- THEME (edit me)

THEME = {
    "bg":        "#080d18",   # window background
    "card":      "#111a2c",   # panels
    "card_edge": "#1f2d47",   # thin border around panels
    "tile":      "#18243b",   # digit tiles inside the code panel
    "text":      "#eaf0fa",   # main text
    "muted":     "#7d8ba6",   # small / secondary text
    "accent":    "#38bdf8",   # code digits, links
    "ok":        "#22c55e",   # connected, START button
    "warn":      "#f59e0b",   # connecting, wrong code
    "bad":       "#ef4444",   # lost, errors, STOP button
    "idle":      "#64748b",   # waiting
    "btn":       "#1b2740",   # neutral button
    "btn_hover": "#283858",
    "log_bg":    "#080d18",   # Details text box
}

STATE_TEXT = {
    "waiting": ("Waiting for phone", THEME["idle"]),
    "connecting": ("Connecting", THEME["warn"]),
    "connected": ("Phone connected", THEME["ok"]),
    "lost": ("Phone lost", THEME["bad"]),
}

ERROR_TEXT = {
    "no_permission": ("Setup is done, but the system has not applied it yet.\n"
                      "Log out and back in once, then start PC Bridge again.", None),
    "bluetooth_off": ("Bluetooth is off. Turn it on.", "Retry"),
    "no_adapter": ("No Bluetooth adapter found.", "Retry"),
    "other": ("Something went wrong. Open Details for the message.", "Retry"),
}

COLUMN_WIDTH = 580      # width of the centred column
MAX_LOG_LINES = 500
_lock_file = None       # kept open for the life of the program (it holds the lock)


def _pick(root, wanted, fallback):
    """First installed font family from `wanted`, else `fallback`."""
    have = set(tkfont.families(root))
    for name in wanted:
        if name in have:
            return name
    return fallback


def acquire_single_instance() -> bool:
    """flock is released by the system when the program ends, even after a crash."""
    global _lock_file
    _lock_file = open(paths.cache_dir() / "lock", "w")
    try:
        fcntl.flock(_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _fmt_duration(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# ---------------------------------------------------------------- rounded panel

def _round_rect(c, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return c.create_polygon(pts, smooth=True, **kw)


class Card(tk.Canvas):
    """A rounded panel. Put widgets into `.body`. Its height follows its content."""

    def __init__(self, parent, fill=None, edge=None, radius=18, padx=18, pady=14, outer=None):
        self.fill = fill or THEME["card"]
        self.edge = edge or THEME["card_edge"]
        self.r, self.padx, self.pady = radius, padx, pady
        super().__init__(parent, bg=outer or THEME["bg"], highlightthickness=0, height=20, width=10)
        self.body = tk.Frame(self, bg=self.fill)
        self._win = self.create_window(padx, pady, window=self.body, anchor="nw")
        self.bind("<Configure>", self._redraw)
        self.body.bind("<Configure>", self._fit)
        self.after_idle(self._fit)

    def _fit(self, _e=None):
        h = self.body.winfo_reqheight() + 2 * self.pady
        w = self.body.winfo_reqwidth() + 2 * self.padx   # used when the card is not stretched
        if int(self.cget("height")) != h:
            self.configure(height=h)
        if int(self.cget("width")) != w:
            self.configure(width=w)

    def _redraw(self, _e=None):
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4 or h < 4:
            return
        self.delete("bg")
        _round_rect(self, 1, 1, w - 1, h - 1, self.r, fill=self.fill, outline=self.edge,
                    width=1, tags="bg")
        self.tag_lower("bg")
        # never squeeze the content below the size it asks for (that made small panels collapse)
        self.itemconfigure(self._win, width=max(self.body.winfo_reqwidth(), w - 2 * self.padx))


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.settings = paths.load_settings()
        self.flog = paths.file_logger()
        self.q = queue.Queue()
        self.core = None
        self.closing = False
        self.injection_on = True
        self.lost_after = None
        self.connected_since = None
        self.sessions = 0
        self._cur_state = None
        self._tick = 0
        self._fullscreen = False

        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()

        root.title("PC Bridge")
        root.configure(bg=THEME["bg"])
        root.protocol("WM_DELETE_WINDOW", self.quit)
        self._fonts()
        self._build()
        self._place_window()
        root.bind("<F11>", lambda _e: self.toggle_fullscreen())
        root.bind("<Escape>", lambda _e: self.toggle_fullscreen(False))
        self.start_core()
        self.poll()

    # ------------------------------------------------------------ window

    def _place_window(self):
        r = self.root
        r.minsize(640, 600)
        sw, sh = r.winfo_screenwidth(), r.winfo_screenheight()
        w, h = min(1000, sw - 80), min(780, sh - 100)
        r.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")   # centred, in case maximise is refused
        try:
            r.attributes("-zoomed", True)        # Linux (X11 / XWayland)
        except tk.TclError:
            try:
                r.state("zoomed")                # Windows
            except tk.TclError:
                pass

    def toggle_fullscreen(self, on=None):
        self._fullscreen = (not self._fullscreen) if on is None else on
        try:
            self.root.attributes("-fullscreen", self._fullscreen)
        except tk.TclError:
            pass

    # ------------------------------------------------------------ look helpers

    def _fonts(self):
        ui = _pick(self.root, ["Inter", "Segoe UI", "Ubuntu", "Noto Sans", "Cantarell",
                               "DejaVu Sans", "Helvetica"], "TkDefaultFont")
        mono = _pick(self.root, ["JetBrains Mono", "Fira Code", "Consolas", "Ubuntu Mono",
                                 "DejaVu Sans Mono", "Courier New"], "TkFixedFont")
        self.F_TITLE = (ui, 17, "bold")
        self.F_CAP = (ui, 9)
        self.F_BODY = (ui, 11)
        self.F_BOLD = (ui, 12, "bold")
        self.F_BIG = (ui, 20, "bold")
        self.F_DIGIT = (mono, 54, "bold")
        self.F_MONO = (mono, 9)

    def _button(self, parent, text, command, bg=None, fg=None, hover=None):
        bg = bg or THEME["btn"]
        hover = hover or THEME["btn_hover"]
        b = tk.Button(parent, text=text, command=command, font=self.F_BOLD,
                      bg=bg, fg=fg or THEME["text"], activebackground=hover,
                      activeforeground=fg or THEME["text"], relief="flat", bd=0,
                      highlightthickness=0, cursor="hand2", padx=18, pady=12)
        b._base, b._hover = bg, hover
        b.bind("<Enter>", lambda _e: b.configure(bg=b._hover))
        b.bind("<Leave>", lambda _e: b.configure(bg=b._base))
        return b

    def _restyle(self, button, bg, hover):
        button._base, button._hover = bg, hover
        button.configure(bg=bg, activebackground=hover)

    def _toggle(self, parent, text, variable, command):
        """Text toggle: grey when off, accent colour when on."""
        return tk.Checkbutton(parent, text=text, variable=variable, command=command,
                              indicatoron=False, font=self.F_BODY, relief="flat", bd=0,
                              bg=THEME["bg"], fg=THEME["muted"],
                              activebackground=THEME["bg"], activeforeground=THEME["accent"],
                              selectcolor=THEME["bg"], highlightthickness=0,
                              cursor="hand2", padx=4, pady=4)

    def _tile(self, parent, caption):
        """A small stat panel: caption on top, big value below. Returns (card, value label)."""
        c = Card(parent, radius=16, padx=16, pady=12)
        tk.Label(c.body, text=caption.upper(), font=self.F_CAP, bg=THEME["card"],
                 fg=THEME["muted"], anchor="w").pack(anchor="w")
        v = tk.Label(c.body, text="-", font=self.F_BIG, bg=THEME["card"],
                     fg=THEME["text"], anchor="w")
        v.pack(anchor="w", pady=(2, 0))
        return c, v

    # ------------------------------------------------------------ layout

    def _build(self):
        shell = tk.Frame(self.root, bg=THEME["bg"])
        shell.pack(fill="both", expand=True)

        # one centred column: fixed width, full height, centred horizontally by pack
        col = tk.Frame(shell, bg=THEME["bg"], width=COLUMN_WIDTH)
        col.pack_propagate(False)
        col.pack(fill="y", expand=True, pady=18)
        self.main = col

        self.top_spacer = tk.Frame(col, bg=THEME["bg"])
        self.bottom_spacer = tk.Frame(col, bg=THEME["bg"])
        self.top_spacer.pack(expand=True, fill="both")

        # ---- header: logo + title on the left, status pill on the right
        head = tk.Frame(col, bg=THEME["bg"])
        head.pack(fill="x")
        logo = tk.Canvas(head, width=30, height=30, bg=THEME["bg"], highlightthickness=0)
        logo.create_oval(2, 2, 28, 28, fill=THEME["accent"], outline="")
        logo.create_oval(9, 9, 21, 21, fill=THEME["bg"], outline="")
        logo.pack(side="left")
        tk.Label(head, text="PC Bridge", font=self.F_TITLE, bg=THEME["bg"],
                 fg=THEME["text"]).pack(side="left", padx=(10, 0))

        self.status_var = tk.StringVar()
        self.status_row = Card(head, radius=16, padx=14, pady=7)   # the status pill
        self.status_row.pack(side="right")
        pill = self.status_row.body
        self.status_dot = tk.Canvas(pill, width=14, height=14, highlightthickness=0, bg=THEME["card"])
        self._dot = self.status_dot.create_oval(1, 1, 13, 13, fill=THEME["idle"], outline="")
        self.status_dot.pack(side="left")
        self.status_lbl = tk.Label(pill, textvariable=self.status_var, font=self.F_BODY,
                                   bg=THEME["card"], fg=THEME["text"])
        self.status_lbl.pack(side="left", padx=(8, 0))

        # ---- code panel: four digit tiles
        code = Card(col, radius=22, padx=20, pady=18)
        code.pack(fill="x", pady=(18, 0))
        self.code_card = code
        tk.Label(code.body, text="SESSION CODE", font=self.F_CAP, bg=THEME["card"],
                 fg=THEME["muted"]).pack()
        self.code_var = tk.StringVar(value="- - - -")
        row = tk.Frame(code.body, bg=THEME["card"])
        row.pack(pady=(10, 8))
        self.digit_labels = []
        for _ in range(4):
            t = Card(row, fill=THEME["tile"], edge=THEME["tile"], radius=16, padx=10, pady=4,
                     outer=THEME["card"])
            t.pack(side="left", padx=6)
            d = tk.Label(t.body, text="-", font=self.F_DIGIT, bg=THEME["tile"],
                         fg=THEME["accent"], width=2)
            d.pack()
            self.digit_labels.append(d)
        tk.Label(code.body, text="type this on your phone", font=self.F_BODY, bg=THEME["card"],
                 fg=THEME["muted"]).pack()
        self.auth_var = tk.StringVar()
        tk.Label(code.body, textvariable=self.auth_var, font=self.F_BODY, fg=THEME["warn"],
                 bg=THEME["card"]).pack(pady=(4, 0))

        # ---- error panel: appears under the code when something is wrong
        self.err_frame = Card(col, radius=18, padx=18, pady=14, edge=THEME["bad"])
        self.err_var = tk.StringVar()
        tk.Label(self.err_frame.body, textvariable=self.err_var, wraplength=COLUMN_WIDTH - 90,
                 font=self.F_BODY, fg=THEME["bad"], bg=THEME["card"], justify="left").pack(anchor="w")
        self.err_btn = self._button(self.err_frame.body, "Retry", self.retry)
        self.err_btn.pack(anchor="w", pady=(10, 0))

        # ---- stat tiles (2 x 2)
        grid = tk.Frame(col, bg=THEME["bg"])
        grid.pack(fill="x", pady=(12, 0))
        grid.columnconfigure(0, weight=1, uniform="t")
        grid.columnconfigure(1, weight=1, uniform="t")
        c1, self.t_conn = self._tile(grid, "Connected for")
        c2, self.t_last = self._tile(grid, "Last heard")
        c3, self.t_input = self._tile(grid, "Input")
        c4, self.t_sessions = self._tile(grid, "Sessions this run")
        c1.grid(row=0, column=0, sticky="we", padx=(0, 6), pady=(0, 12))
        c2.grid(row=0, column=1, sticky="we", padx=(6, 0), pady=(0, 12))
        c3.grid(row=1, column=0, sticky="we", padx=(0, 6))
        c4.grid(row=1, column=1, sticky="we", padx=(6, 0))
        self.inj_var = tk.StringVar(value="Input: ON")   # kept for compatibility; shown in the Input tile

        # ---- buttons
        bt = tk.Frame(col, bg=THEME["bg"])
        bt.pack(fill="x", pady=(14, 0))
        bt.columnconfigure(0, weight=2, uniform="b")
        bt.columnconfigure(1, weight=1, uniform="b")
        self.stop_btn = self._button(bt, "STOP", self.toggle_injection,
                                     bg="#b91c1c", hover=THEME["bad"], fg="#ffffff")
        self.stop_btn.grid(row=0, column=0, sticky="we", padx=(0, 6))
        self.quit_btn = self._button(bt, "Quit", self.quit)
        self.quit_btn.grid(row=0, column=1, sticky="we", padx=(6, 0))

        # ---- link-style toggles
        self.show_details = tk.BooleanVar(value=False)
        self.show_settings = tk.BooleanVar(value=False)
        bar = tk.Frame(col, bg=THEME["bg"])
        bar.pack(fill="x", pady=(10, 0))
        self.bar = bar
        self.details_toggle = self._toggle(bar, "Details", self.show_details, self._toggle_details)
        self.details_toggle.pack(side="left", padx=(0, 14))
        self.settings_toggle = self._toggle(bar, "Settings", self.show_settings, self._toggle_settings)
        self.settings_toggle.pack(side="left")
        tk.Button(bar, text="Full screen (F11)", command=self.toggle_fullscreen, font=self.F_CAP,
                  bg=THEME["bg"], fg=THEME["muted"], activebackground=THEME["bg"],
                  activeforeground=THEME["accent"], relief="flat", bd=0, highlightthickness=0,
                  cursor="hand2").pack(side="right")

        # ---- settings panel
        self.settings_frame = Card(col, radius=16, padx=16, pady=10)
        self.keys_var = tk.BooleanVar(value=bool(self.settings.get("show_keys")))
        tk.Checkbutton(self.settings_frame.body,
                       text="Show each key in Details  (off: typed passwords stay hidden)",
                       variable=self.keys_var, command=self._keys_changed, font=self.F_BODY,
                       anchor="w", bg=THEME["card"], fg=THEME["text"],
                       activebackground=THEME["card"], activeforeground=THEME["text"],
                       selectcolor=THEME["bg"], highlightthickness=0, bd=0,
                       cursor="hand2").pack(anchor="w")

        # ---- details panel (log): takes the free height when open
        self.details_frame = tk.Frame(col, bg=THEME["bg"])
        # Copy button is packed first at the bottom so it is never squeezed out on a short screen
        self.copy_btn = self._button(self.details_frame, "Copy log", self.copy_log)
        self.copy_btn.configure(font=self.F_CAP, padx=12, pady=5)
        self.copy_btn.pack(side="bottom", anchor="e", pady=(6, 0))
        box = Card(self.details_frame, radius=16, padx=10, pady=8)
        box.pack(fill="both", expand=True)
        self.text = tk.Text(box.body, width=1, height=8, state="disabled", wrap="word",
                            font=self.F_MONO, bg=THEME["card"], fg=THEME["muted"],
                            insertbackground=THEME["text"], relief="flat", bd=0,
                            padx=4, pady=2, highlightthickness=0)
        sb = tk.Scrollbar(box.body, command=self.text.yview, troughcolor=THEME["card"],
                          bg=THEME["btn"], relief="flat", bd=0, width=10)
        self.text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)

        self.bottom_spacer.pack(expand=True, fill="both")
        self._set_state("waiting")
        self._refresh_tiles()

    # ---- show / hide panels (the spacers centre the content only while the log is closed)

    def _relayout(self):
        log_open = self.show_details.get()
        for sp in (self.top_spacer, self.bottom_spacer):
            sp.pack_forget()
        if not log_open:
            self.top_spacer.pack(expand=True, fill="both", before=self._first_child())
            self.bottom_spacer.pack(expand=True, fill="both")

    def _first_child(self):
        for w in self.main.pack_slaves():
            if w not in (self.top_spacer, self.bottom_spacer):
                return w
        return None

    def _toggle_details(self):
        self._tint(self.details_toggle, self.show_details.get())
        if self.show_details.get():
            self.details_frame.pack(fill="both", expand=True, pady=(10, 0))
        else:
            self.details_frame.pack_forget()
        self._relayout()

    def _toggle_settings(self):
        self._tint(self.settings_toggle, self.show_settings.get())
        if self.show_settings.get():
            self.settings_frame.pack(fill="x", pady=(10, 0), after=self.bar)
        else:
            self.settings_frame.pack_forget()

    @staticmethod
    def _tint(widget, on):
        widget.configure(fg=THEME["accent"] if on else THEME["muted"])

    # ------------------------------------------------------------ core control

    def start_core(self):
        self.core = BridgeCore(self.q_emit, verbose=bool(self.keys_var.get()))
        asyncio.run_coroutine_threadsafe(self.core.start(), self.loop)

    def q_emit(self, name, **data):   # runs on the core's thread
        self.q.put((name, data))

    def retry(self):
        self.err_frame.pack_forget()
        self.status_row.pack(side="right")
        old = self.core
        fut = asyncio.run_coroutine_threadsafe(old.shutdown(), self.loop)
        try:
            fut.result(timeout=5)
        except Exception:
            pass
        self.start_core()

    def toggle_injection(self):
        if self.core:
            self.core.set_injection(not self.injection_on)

    def _keys_changed(self):
        self.settings["show_keys"] = bool(self.keys_var.get())
        paths.save_settings(self.settings)
        if self.core:
            self.core.verbose = self.settings["show_keys"]

    def quit(self):
        if self.closing:
            return
        self.closing = True
        if self.core:
            fut = asyncio.run_coroutine_threadsafe(self.core.shutdown(), self.loop)
            try:
                fut.result(timeout=5)
            except Exception:
                pass
        self.root.destroy()

    # ------------------------------------------------------------ events -> screen

    def poll(self):
        try:
            for _ in range(200):
                name, d = self.q.get_nowait()
                self.handle(name, d)
        except queue.Empty:
            pass
        if self.lost_after is not None and self.root.tk.call("clock", "milliseconds") > self.lost_after:
            self.lost_after = None
            if self.core and self.core.state == "lost":
                self._set_state("waiting")
        self._tick += 1
        if self._tick % 4 == 0:
            self._refresh_tiles()
        if not self.closing:
            self.root.after(100, self.poll)

    def _refresh_tiles(self):
        now = time.monotonic()
        # connected for
        if self.connected_since is not None:
            self.t_conn.configure(text=_fmt_duration(now - self.connected_since), fg=THEME["ok"])
        else:
            self.t_conn.configure(text="-", fg=THEME["muted"])
        # last heard (the phone sends a heartbeat every second)
        core = self.core
        if core is not None and getattr(core, "phone_present", False):
            age = now - getattr(core, "last_heard", now)
            if age < 2:
                self.t_last.configure(text="Live", fg=THEME["ok"])
            else:
                self.t_last.configure(text=f"{age:.0f} s ago", fg=THEME["warn"])
        else:
            self.t_last.configure(text="-", fg=THEME["muted"])
        # input + sessions
        if self.injection_on:
            self.t_input.configure(text="ON", fg=THEME["ok"])
        else:
            self.t_input.configure(text="STOPPED", fg=THEME["bad"])
        self.t_sessions.configure(text=str(self.sessions), fg=THEME["text"])

    def handle(self, name, d):
        if name == "code":
            c = d["value"]
            self.code_var.set(" ".join(c))
            for lbl, ch in zip(self.digit_labels, c.ljust(4, "-")):
                lbl.configure(text=ch)
            self.auth_var.set("")
        elif name == "state":
            self._set_state(d["value"])
            if d["value"] == "connected":
                self.auth_var.set("")
        elif name == "injection":
            self.injection_on = d["value"]
            self.inj_var.set("Input: ON" if d["value"] else "Input: STOPPED")
            if d["value"]:
                self.stop_btn.configure(text="STOP")
                self._restyle(self.stop_btn, "#b91c1c", THEME["bad"])
            else:
                self.stop_btn.configure(text="START")
                self._restyle(self.stop_btn, "#15803d", THEME["ok"])
            self._refresh_tiles()
        elif name == "auth":
            if d["kind"] == "locked":
                self.auth_var.set(f"Locked for {d['value']} s. New code shown.")
            else:
                self.auth_var.set(f"Wrong code. {d['value']} tries left.")
        elif name == "error":
            self._show_error(d["kind"], d["message"])
        elif name == "log":
            self.add_log(d["text"], to_file=True)
        elif name == "input":
            self.add_log(d["text"], to_file=False)

    def _set_state(self, s):
        text, color = STATE_TEXT[s]
        self.status_var.set(text)
        self.status_dot.itemconfigure(self._dot, fill=color)
        if s == "connected":
            if self._cur_state != "connected":
                self.sessions += 1
                self.connected_since = time.monotonic()
        else:
            self.connected_since = None
        self._cur_state = s
        if s == "lost":
            self.lost_after = self.root.tk.call("clock", "milliseconds") + 4000
        else:
            self.lost_after = None
        self._refresh_tiles()

    def _show_error(self, kind, message):
        text, button = ERROR_TEXT.get(kind, ERROR_TEXT["other"])
        self.err_var.set(text)
        if button:
            self.err_btn.configure(text=button, command=self.retry)
            self.err_btn.pack(anchor="w", pady=(10, 0))
        else:
            self.err_btn.configure(text="Quit", command=self.quit)
        self.status_row.pack_forget()
        self.err_frame.pack(fill="x", pady=(12, 0), after=self.code_card)
        self.add_log(f"[error] {kind}: {message}", to_file=True)

    def add_log(self, line, to_file):
        if to_file:
            self.flog.info(line)
        self.text.configure(state="normal")
        self.text.insert("end", line + "\n")
        n = int(self.text.index("end-1c").split(".")[0])
        if n > MAX_LOG_LINES:
            self.text.delete("1.0", f"{n - MAX_LOG_LINES}.0")
        self.text.see("end")
        self.text.configure(state="disabled")

    def copy_log(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.text.get("1.0", "end"))


def main():
    root = tk.Tk(className="pcbridge")   # window class "Pcbridge": the menu entry matches it
    if not acquire_single_instance():
        root.withdraw()
        messagebox.showinfo("PC Bridge", "PC Bridge is already open.")
        sys.exit(0)
    try:
        App(root)
        root.mainloop()
    except Exception:
        # No terminal in the installed app, so a crash must leave a record.
        import traceback
        paths.file_logger().error("CRASH\n" + traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
