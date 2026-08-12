#!/usr/bin/env python3
r"""DBExplorer -- a pure-stdlib tkinter GUI on top of the ``dbkit`` API.

One main window: a left sidebar of sections (Connections, Browser, SQL editor,
ERD) and a main panel that swaps to the selected section.  Every database
operation calls the tested ``dbkit`` core (never re-implements SQL logic) and
runs on a background thread so the UI stays responsive; results are marshalled
back with ``self.after`` and reported in a clear inline bar -- a short success
line or the :class:`~dbkit.DBError` message (never a raw traceback) on failure.

Design goals baked in here (mirroring the sibling PDF Toolkit app):
  * pure standard-library tkinter/ttk -- NO third-party GUI deps.  Dark mode is
    a ttk-style + QuickOpen palette swap.
  * Importing this module does nothing.  Only :func:`main` builds a root window,
    and it degrades gracefully (prints a message, returns 0) with no display.
  * Frozen-exe safe: bundled assets resolve via ``sys._MEIPASS`` / the exe
    directory when ``sys.frozen`` is set -- never ``__file__``.

100% AI-built, open source, published on QuickOpen (quickopen.ai). Apache-2.0.
"""

from __future__ import annotations

import os
import sys
import threading

# NOTE: tkinter is imported lazily inside main()/build_app so that merely
# importing this module (packaging, headless CI) never fails.

APP_NAME = "DBExplorer"
APP_VERSION = "1.0.0"
WINDOW_TITLE = "DBExplorer — by QuickOpen (quickopen.ai)"
PROJECT_URL = "https://quickopen.ai"
PAGE_SIZE = 100

# ---- colour palettes (mirror the QuickOpen palette) -------------------------
PALETTES = {
    "light": {
        "bg": "#f5f7fa", "surface": "#ffffff", "text": "#141820",
        "muted": "#5b6472", "primary": "#2f5fe0", "primary_hi": "#2450c8",
        "entry": "#ffffff", "border": "#d5dae2", "sel": "#2f5fe0",
        "sel_fg": "#ffffff", "trough": "#e2e7ef", "ok": "#1f7a3d",
        "err": "#c0392b",
    },
    "dark": {
        "bg": "#0f1115", "surface": "#1a1e24", "text": "#f1f3f7",
        "muted": "#9aa4b2", "primary": "#5b86f7", "primary_hi": "#7098ff",
        "entry": "#1a1e24", "border": "#2a2f38", "sel": "#5b86f7",
        "sel_fg": "#0f1115", "trough": "#2a2f38", "ok": "#5bd68a",
        "err": "#ff6b5e",
    },
}

SECTIONS = [
    ("connections", "Connections"),
    ("browser", "Browser"),
    ("sql", "SQL editor"),
    ("erd", "ERD"),
]

SECTION_DESC = {
    "connections": "Add a SQLite file or a Postgres/MySQL URL, then connect. "
                   "Passwords are only saved to disk if you opt in.",
    "browser": "Browse schemas and tables; open a table to page through its "
               "rows in a data grid.",
    "sql": "Write and run SQL against the active connection; view the results "
           "grid and export to CSV or JSON.",
    "erd": "See tables and the foreign-key relationships between them.",
}


# ---------------------------------------------------------------------------
# Asset / frozen handling
# ---------------------------------------------------------------------------
def asset_path(name):
    """Locate a bundled asset from source OR a PyInstaller one-file build.

    For a frozen exe we consult only ``sys._MEIPASS`` and the executable's own
    directory (never ``__file__``).  From source we also try the package dir,
    the repo root and the CWD.  Returns an absolute path or ``None``.
    """
    roots = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            roots.append(meipass)
        roots.append(os.path.dirname(os.path.abspath(sys.executable)))
    else:
        here = os.path.dirname(os.path.abspath(__file__))
        roots += [here, os.path.dirname(here), os.getcwd()]
    for root in roots:
        candidate = os.path.join(root, name)
        if os.path.exists(candidate):
            return candidate
    return None


def open_with_default_app(path):
    """Open a file/URL with the OS default application, guarded on all platforms."""
    try:
        if hasattr(os, "startfile"):
            os.startfile(path)                # noqa: S606 - intended
        elif sys.platform == "darwin":
            import subprocess
            subprocess.Popen(["open", path])
        else:
            import subprocess
            subprocess.Popen(["xdg-open", path])
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# The app (built lazily; tkinter imported only inside build_app/main)
# ---------------------------------------------------------------------------
def build_app():
    """Construct and return the App class bound to a live tkinter import.

    Kept inside a function so this module imports cleanly without a display.
    """
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, simpledialog

    from . import guiconfig, conn, introspect, query, edit, erd
    from .errors import DBError

    FONT = "Segoe UI"

    class App(tk.Tk):
        def __init__(self):
            super().__init__()
            self.title(WINDOW_TITLE)
            self.geometry("1120x720")
            self.minsize(900, 580)

            self.theme = guiconfig.get_theme()
            self._busy = False
            self._tracked = []          # (tk_widget, role) for manual re-theming
            self._img_refs = []
            self._panels = {}
            self._current = None

            # active connection state
            self.engine = None
            self.active_name = None
            self.active_schema = None
            self.browse_table = None
            self.browse_offset = 0
            self.last_result = None     # last SQL/grid result dict (for export)

            self._set_icon()
            self._build_menu()
            self._build_layout()
            self._apply_theme()
            self.protocol("WM_DELETE_WINDOW", self._on_close)
            self.after(50, self._select_first)

        # ---- assets / icon ------------------------------------------------
        def _set_icon(self):
            try:
                ico = asset_path("db-explorer.ico")
                if ico:
                    self.iconbitmap(ico)
                    return
            except Exception:
                pass
            try:
                png = asset_path("db-explorer.png")
                if png:
                    img = tk.PhotoImage(file=png)
                    self._img_refs.append(img)
                    self.iconphoto(True, img)
            except Exception:
                pass  # icon is cosmetic; never block launch

        # ---- theming ------------------------------------------------------
        def track(self, widget, role):
            self._tracked.append((widget, role))

        def _pal(self):
            return PALETTES[self.theme]

        def _apply_theme(self):
            p = self._pal()
            style = ttk.Style(self)
            try:
                style.theme_use("clam")
            except Exception:
                pass
            self.configure(bg=p["bg"])
            style.configure(".", background=p["bg"], foreground=p["text"],
                            fieldbackground=p["entry"], bordercolor=p["border"],
                            font=(FONT, 10))
            style.configure("TFrame", background=p["bg"])
            style.configure("Sidebar.TFrame", background=p["surface"])
            style.configure("TLabel", background=p["bg"], foreground=p["text"])
            style.configure("Muted.TLabel", background=p["bg"], foreground=p["muted"])
            style.configure("Header.TLabel", background=p["bg"], foreground=p["text"],
                            font=(FONT, 15, "bold"))
            style.configure("Sub.TLabel", background=p["bg"], foreground=p["muted"],
                            font=(FONT, 10))
            style.configure("Brand.TLabel", background=p["surface"],
                            foreground=p["text"], font=(FONT, 12, "bold"))
            style.configure("Status.TLabel", background=p["surface"],
                            foreground=p["muted"])
            style.configure("TButton", background=p["surface"], foreground=p["text"],
                            bordercolor=p["border"], focuscolor=p["surface"],
                            padding=(10, 5))
            style.map("TButton",
                      background=[("active", p["trough"]), ("disabled", p["bg"])],
                      foreground=[("disabled", p["muted"])])
            style.configure("Accent.TButton", background=p["primary"],
                            foreground="#ffffff", padding=(12, 6))
            style.map("Accent.TButton",
                      background=[("active", p["primary_hi"]),
                                  ("disabled", p["border"])],
                      foreground=[("disabled", p["muted"])])
            style.configure("Toggle.TButton", background=p["surface"],
                            foreground=p["text"], padding=(8, 4))
            for name in ("TEntry", "TSpinbox"):
                style.configure(name, fieldbackground=p["entry"], foreground=p["text"],
                                insertcolor=p["text"], bordercolor=p["border"])
            style.configure("TCombobox", fieldbackground=p["entry"],
                            foreground=p["text"], background=p["surface"],
                            arrowcolor=p["text"])
            style.map("TCombobox", fieldbackground=[("readonly", p["entry"])],
                      foreground=[("readonly", p["text"])])
            style.configure("TCheckbutton", background=p["bg"], foreground=p["text"])
            style.map("TCheckbutton", background=[("active", p["bg"])])
            style.configure("TLabelframe", background=p["bg"], foreground=p["text"],
                            bordercolor=p["border"])
            style.configure("TLabelframe.Label", background=p["bg"],
                            foreground=p["muted"])
            style.configure("Treeview", background=p["surface"],
                            fieldbackground=p["surface"], foreground=p["text"],
                            bordercolor=p["border"], rowheight=22)
            style.map("Treeview", background=[("selected", p["primary"])],
                      foreground=[("selected", p["sel_fg"])])
            style.configure("Treeview.Heading", background=p["surface"],
                            foreground=p["muted"], font=(FONT, 9, "bold"))
            style.configure("Sidebar.Treeview", background=p["surface"],
                            fieldbackground=p["surface"])
            style.configure("TScrollbar", background=p["surface"],
                            troughcolor=p["bg"], bordercolor=p["border"],
                            arrowcolor=p["text"])
            style.configure("TSeparator", background=p["border"])

            for widget, role in list(self._tracked):
                try:
                    if role == "listbox":
                        widget.configure(bg=p["surface"], fg=p["text"],
                                         selectbackground=p["primary"],
                                         selectforeground=p["sel_fg"],
                                         highlightthickness=1,
                                         highlightbackground=p["border"], borderwidth=0)
                    elif role == "text":
                        widget.configure(bg=p["surface"], fg=p["text"],
                                         insertbackground=p["text"],
                                         selectbackground=p["primary"],
                                         selectforeground=p["sel_fg"],
                                         highlightthickness=1,
                                         highlightbackground=p["border"], borderwidth=0)
                except Exception:
                    pass

        def toggle_theme(self):
            self.theme = "dark" if self.theme == "light" else "light"
            guiconfig.set_theme(self.theme)
            self._apply_theme()
            self._theme_btn.configure(
                text="☀ Light mode" if self.theme == "dark" else "🌙 Dark mode")

        # ---- menu ---------------------------------------------------------
        def _build_menu(self):
            bar = tk.Menu(self)
            filem = tk.Menu(bar, tearoff=0)
            filem.add_command(label="Open SQLite file…", command=self._quick_open_sqlite)
            filem.add_separator()
            filem.add_command(label="Exit", command=self._on_close)
            bar.add_cascade(label="File", menu=filem)

            viewm = tk.Menu(bar, tearoff=0)
            viewm.add_command(label="Toggle dark mode", command=self.toggle_theme)
            bar.add_cascade(label="View", menu=viewm)

            helpm = tk.Menu(bar, tearoff=0)
            helpm.add_command(label="About", command=self._about)
            helpm.add_command(label="Open project page (quickopen.ai)",
                              command=lambda: open_with_default_app(PROJECT_URL))
            bar.add_cascade(label="Help", menu=helpm)
            self.configure(menu=bar)

        # ---- layout -------------------------------------------------------
        def _build_layout(self):
            top = ttk.Frame(self, style="Sidebar.TFrame", padding=(12, 8))
            top.pack(fill="x", side="top")
            ttk.Label(top, text="DBExplorer", style="Brand.TLabel").pack(side="left")
            ttk.Label(top, style="Status.TLabel",
                      text="  offline · open source · by QuickOpen").pack(side="left")
            self._theme_btn = ttk.Button(
                top, style="Toggle.TButton", command=self.toggle_theme,
                text="☀ Light mode" if self.theme == "dark" else "🌙 Dark mode")
            self._theme_btn.pack(side="right")
            self.conn_lbl = ttk.Label(top, style="Status.TLabel",
                                      text="not connected")
            self.conn_lbl.pack(side="right", padx=12)

            body = ttk.Frame(self, style="TFrame")
            body.pack(fill="both", expand=True)

            side = ttk.Frame(body, style="Sidebar.TFrame", width=190)
            side.pack(side="left", fill="y")
            side.pack_propagate(False)
            self.nav = ttk.Treeview(side, show="tree", selectmode="browse",
                                    style="Sidebar.Treeview")
            self.nav.pack(fill="both", expand=True, padx=6, pady=6)
            self._nav_ids = {}
            for sid, label in SECTIONS:
                iid = self.nav.insert("", "end", text="  " + label)
                self._nav_ids[iid] = sid
            self.nav.bind("<<TreeviewSelect>>", self._on_nav_select)

            main = ttk.Frame(body, style="TFrame", padding=(16, 12))
            main.pack(side="left", fill="both", expand=True)
            head = ttk.Frame(main, style="TFrame")
            head.pack(fill="x")
            self.title_lbl = ttk.Label(head, text="Welcome", style="Header.TLabel")
            self.title_lbl.pack(anchor="w")
            self.desc_lbl = ttk.Label(head, text="", style="Sub.TLabel",
                                      wraplength=760, justify="left")
            self.desc_lbl.pack(anchor="w", pady=(2, 8))
            ttk.Separator(main).pack(fill="x")
            self.container = ttk.Frame(main, style="TFrame")
            self.container.pack(fill="both", expand=True, pady=(10, 8))

            bar = ttk.Frame(self, style="Sidebar.TFrame", padding=(12, 6))
            bar.pack(fill="x", side="bottom")
            self.status_lbl = ttk.Label(bar, text="Ready", style="Status.TLabel",
                                        width=14, anchor="w")
            self.status_lbl.pack(side="left")
            self.result_lbl = ttk.Label(bar, text="", style="Status.TLabel",
                                        anchor="w", wraplength=820, justify="left")
            self.result_lbl.pack(side="left", fill="x", expand=True, padx=8)

        # ---- navigation ---------------------------------------------------
        def _select_first(self):
            for iid in self._nav_ids:
                self.nav.selection_set(iid)
                break

        def select_section(self, sid):
            for iid, s in self._nav_ids.items():
                if s == sid:
                    self.nav.selection_set(iid)
                    return

        def _on_nav_select(self, _e=None):
            sel = self.nav.selection()
            if not sel:
                return
            sid = self._nav_ids.get(sel[0])
            if sid:
                self._show_section(sid)

        def _show_section(self, sid):
            if self._current is not None:
                self._current.pack_forget()
            panel = self._panels.get(sid)
            if panel is None:
                panel = ttk.Frame(self.container, style="TFrame")
                getattr(self, "_build_" + sid)(panel)
                self._panels[sid] = panel
                self._apply_theme()
            panel.pack(fill="both", expand=True)
            self._current = panel
            self.title_lbl.configure(text=dict(SECTIONS)[sid])
            self.desc_lbl.configure(text=SECTION_DESC.get(sid, ""))
            self._clear_result()
            hook = getattr(self, "_enter_" + sid, None)
            if hook:
                hook()

        # ---- background runner --------------------------------------------
        def _bg(self, work, on_ok, button=None, busy="Working…"):
            """Run ``work()`` off the UI thread; call ``on_ok(result)`` back on it.

            Errors show inline (the DBError message, never a traceback).  Refuses
            to start a second op while one is in flight.
            """
            if self._busy:
                self._show_error("Please wait — an operation is already running.")
                return
            self._busy = True
            if button is not None:
                try:
                    button.state(["disabled"])
                except Exception:
                    pass
            self._set_status(busy, kind="working")

            def run():
                try:
                    res, err = work(), None
                except DBError as ex:
                    res, err = None, str(ex)
                except Exception as ex:  # never leak a traceback
                    res, err = None, f"Unexpected error: {ex}"
                self.after(0, lambda: finish(res, err))

            def finish(res, err):
                self._busy = False
                if button is not None:
                    try:
                        button.state(["!disabled"])
                    except Exception:
                        pass
                if err is not None:
                    self._set_status("error", kind="err")
                    self._show_error(err)
                    return
                self._set_status("done", kind="ok")
                try:
                    on_ok(res)
                except Exception as ex:
                    self._show_error(f"Post-processing error: {ex}")

            threading.Thread(target=run, daemon=True).start()

        # ---- result bar ---------------------------------------------------
        def _set_status(self, text, kind="idle"):
            p = self._pal()
            color = {"working": p["primary"], "ok": p["ok"], "err": p["err"]}.get(
                kind, p["muted"])
            self.status_lbl.configure(text=text, foreground=color)

        def _clear_result(self, keep_status=False):
            self.result_lbl.configure(text="")
            if not keep_status:
                self._set_status("Ready")

        def _show_error(self, message):
            self.result_lbl.configure(text="✕ " + message,
                                      foreground=self._pal()["err"])

        def _show_ok(self, message):
            self.result_lbl.configure(text="✓ " + message,
                                      foreground=self._pal()["ok"])
            self._set_status("done", kind="ok")

        # =================================================================
        # Section: Connections
        # =================================================================
        def _build_connections(self, parent):
            left = ttk.Frame(parent, style="TFrame")
            left.pack(side="left", fill="y", padx=(0, 12))
            ttk.Label(left, text="Saved profiles", style="Sub.TLabel").pack(anchor="w")
            self.prof_list = tk.Listbox(left, height=16, width=28, activestyle="none",
                                        exportselection=False)
            self.prof_list.pack(fill="y", expand=True, pady=(4, 6))
            self.track(self.prof_list, "listbox")
            self.prof_list.bind("<<ListboxSelect>>", lambda _e: self._load_selected_profile())
            row = ttk.Frame(left, style="TFrame")
            row.pack(fill="x")
            ttk.Button(row, text="Connect", style="Accent.TButton",
                       command=self._connect_selected).pack(side="left")
            ttk.Button(row, text="Delete", command=self._delete_selected).pack(
                side="left", padx=4)

            form = ttk.LabelFrame(parent, text="Add / edit profile", padding=10)
            form.pack(side="left", fill="both", expand=True)

            self.kind_var = tk.StringVar(value="sqlite")
            krow = ttk.Frame(form, style="TFrame")
            krow.pack(fill="x", pady=(0, 6))
            ttk.Radiobutton(krow, text="SQLite file", variable=self.kind_var,
                            value="sqlite", command=self._sync_kind).pack(side="left")
            ttk.Radiobutton(krow, text="Database URL", variable=self.kind_var,
                            value="url", command=self._sync_kind).pack(side="left",
                                                                       padx=12)

            self.name_var = tk.StringVar()
            self._form_field(form, "Profile name", self.name_var)

            # swappable body holding the SQLite path row OR the URL row
            body = ttk.Frame(form, style="TFrame")
            body.pack(fill="x")

            self.path_var = tk.StringVar()
            self.path_row = ttk.Frame(body, style="TFrame")
            ttk.Label(self.path_row, text="SQLite file", width=14, anchor="w").pack(
                side="left")
            ttk.Entry(self.path_row, textvariable=self.path_var).pack(
                side="left", fill="x", expand=True, padx=(0, 6))
            ttk.Button(self.path_row, text="Browse…", command=self._browse_sqlite,
                       width=10).pack(side="left")

            self.url_var = tk.StringVar()
            self.url_row = ttk.Frame(body, style="TFrame")
            ttk.Label(self.url_row, text="Database URL", width=14, anchor="w").pack(
                side="left")
            ttk.Entry(self.url_row, textvariable=self.url_var).pack(
                side="left", fill="x", expand=True)

            self.store_pw_var = tk.BooleanVar(value=False)
            self.pw_check = ttk.Checkbutton(
                body, text="Store password in the URL on disk (not recommended)",
                variable=self.store_pw_var)

            ttk.Label(form, style="Muted.TLabel", wraplength=520, justify="left",
                      text="Example URL: postgresql+psycopg2://user@host:5432/dbname "
                           "(leave the password out — you'll be prompted). SQLite "
                           "needs no driver; Postgres/MySQL need psycopg2 / pymysql."
                      ).pack(anchor="w", pady=(8, 6))

            brow = ttk.Frame(form, style="TFrame")
            brow.pack(fill="x", pady=(4, 0))
            ttk.Button(brow, text="Save profile", style="Accent.TButton",
                       command=self._save_profile).pack(side="left")
            ttk.Button(brow, text="Save & connect",
                       command=self._save_and_connect).pack(side="left", padx=6)
            self._sync_kind()

        def _form_field(self, parent, label, var):
            row = ttk.Frame(parent, style="TFrame")
            row.pack(fill="x", pady=4)
            ttk.Label(row, text=label, width=14, anchor="w").pack(side="left")
            ttk.Entry(row, textvariable=var).pack(side="left", fill="x", expand=True)

        def _sync_kind(self):
            if self.kind_var.get() == "sqlite":
                self.url_row.pack_forget()
                self.pw_check.pack_forget()
                self.path_row.pack(fill="x", pady=4)
            else:
                self.path_row.pack_forget()
                self.url_row.pack(fill="x", pady=4)
                self.pw_check.pack(anchor="w", pady=(2, 0))

        def _enter_connections(self):
            self._refresh_profiles()

        def _refresh_profiles(self):
            try:
                profiles = conn.list_profiles()
            except DBError as ex:
                self._show_error(str(ex))
                profiles = []
            self.prof_list.delete(0, "end")
            self._profile_names = []
            for p in profiles:
                self._profile_names.append(p["name"])
                tag = "sqlite" if p.get("kind") == "sqlite" else "url"
                self.prof_list.insert("end", f"{p['name']}  ({tag})")

        def _selected_profile_name(self):
            sel = self.prof_list.curselection()
            if not sel:
                return None
            return self._profile_names[sel[0]]

        def _load_selected_profile(self):
            name = self._selected_profile_name()
            if not name:
                return
            try:
                p = conn.get_profile(name)
            except DBError:
                return
            self.name_var.set(p.get("name", ""))
            if p.get("kind") == "sqlite":
                self.kind_var.set("sqlite")
                self.path_var.set(p.get("path", ""))
            else:
                self.kind_var.set("url")
                self.url_var.set(p.get("url", ""))
                self.store_pw_var.set(bool(p.get("store_password")))
            self._sync_kind()

        def _browse_sqlite(self):
            p = filedialog.askopenfilename(
                title="Choose a SQLite database",
                filetypes=[("SQLite databases", "*.db *.sqlite *.sqlite3"),
                           ("All files", "*.*")])
            if p:
                self.path_var.set(p)
                if not self.name_var.get().strip():
                    self.name_var.set(os.path.splitext(os.path.basename(p))[0])

        def _build_profile_from_form(self):
            name = self.name_var.get().strip()
            if self.kind_var.get() == "sqlite":
                return conn.make_profile(name, "sqlite", path=self.path_var.get().strip())
            return conn.make_profile(name, "url", url=self.url_var.get().strip(),
                                     store_password=self.store_pw_var.get())

        def _save_profile(self):
            try:
                prof = self._build_profile_from_form()
                conn.add_profile(prof["name"], prof["kind"],
                                 path=prof.get("path"), url=prof.get("url"),
                                 store_password=prof.get("store_password", False))
            except DBError as ex:
                self._show_error(str(ex))
                return None
            self._refresh_profiles()
            self._show_ok(f"Saved profile “{prof['name']}”.")
            return prof

        def _save_and_connect(self):
            prof = self._save_profile()
            if prof:
                self._do_connect(prof)

        def _delete_selected(self):
            name = self._selected_profile_name()
            if not name:
                return
            if not messagebox.askyesno("Delete profile",
                                       f"Delete profile “{name}”?"):
                return
            try:
                conn.remove_profile(name)
            except DBError as ex:
                self._show_error(str(ex))
                return
            self._refresh_profiles()
            self._show_ok(f"Deleted profile “{name}”.")

        def _connect_selected(self):
            name = self._selected_profile_name()
            if not name:
                self._show_error("Select a profile to connect to.")
                return
            try:
                prof = conn.get_profile(name)
            except DBError as ex:
                self._show_error(str(ex))
                return
            self._do_connect(prof)

        def _do_connect(self, prof):
            password = None
            if conn.needs_password(prof):
                password = simpledialog.askstring(
                    "Password", f"Password for “{prof['name']}”:", show="•",
                    parent=self)
                if password is None:
                    return  # cancelled

            def work():
                conn.test_connection(prof, password=password)
                return conn.connect(prof, password=password)

            def ok(engine):
                if self.engine is not None:
                    try:
                        self.engine.dispose()
                    except Exception:
                        pass
                self.engine = engine
                self.active_name = prof["name"]
                self.active_schema = None
                self.browse_table = None
                guiconfig.add_recent(prof["name"])
                self.conn_lbl.configure(text=f"connected: {prof['name']}")
                self._show_ok(f"Connected to “{prof['name']}”.")
                self.select_section("browser")

            self._bg(work, ok, busy="Connecting…")

        def _quick_open_sqlite(self):
            p = filedialog.askopenfilename(
                title="Open a SQLite database",
                filetypes=[("SQLite databases", "*.db *.sqlite *.sqlite3"),
                           ("All files", "*.*")])
            if not p:
                return
            name = os.path.splitext(os.path.basename(p))[0] or "sqlite"
            try:
                prof = conn.make_profile(name, "sqlite", path=p)
            except DBError as ex:
                self._show_error(str(ex))
                return
            self._do_connect(prof)

        # =================================================================
        # Section: Browser
        # =================================================================
        def _build_browser(self, parent):
            left = ttk.Frame(parent, style="TFrame")
            left.pack(side="left", fill="y", padx=(0, 12))
            top = ttk.Frame(left, style="TFrame")
            top.pack(fill="x")
            ttk.Label(top, text="Tables & views", style="Sub.TLabel").pack(side="left")
            ttk.Button(top, text="↻", width=3, command=self._refresh_tree).pack(
                side="right")
            self.tree = ttk.Treeview(left, show="tree", selectmode="browse",
                                     height=22)
            self.tree.pack(fill="y", expand=True, pady=(4, 0))
            self.tree.column("#0", width=230)
            self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

            right = ttk.Frame(parent, style="TFrame")
            right.pack(side="left", fill="both", expand=True)
            pg = ttk.Frame(right, style="TFrame")
            pg.pack(fill="x")
            self.grid_title = ttk.Label(pg, text="Select a table",
                                        style="Sub.TLabel")
            self.grid_title.pack(side="left")
            ttk.Button(pg, text="Next ▶", command=lambda: self._page(1)).pack(
                side="right")
            ttk.Button(pg, text="◀ Prev", command=lambda: self._page(-1)).pack(
                side="right", padx=4)
            ttk.Button(pg, text="Edit row…", command=self._edit_row_dialog).pack(
                side="right", padx=(0, 16))

            self.grid = self._make_grid(right)

        def _make_grid(self, parent):
            wrap = ttk.Frame(parent, style="TFrame")
            wrap.pack(fill="both", expand=True, pady=(6, 0))
            grid = ttk.Treeview(wrap, show="headings", selectmode="browse")
            vsb = ttk.Scrollbar(wrap, orient="vertical", command=grid.yview)
            hsb = ttk.Scrollbar(wrap, orient="horizontal", command=grid.xview)
            grid.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
            vsb.pack(side="right", fill="y")
            hsb.pack(side="bottom", fill="x")
            grid.pack(side="left", fill="both", expand=True)
            return grid

        def _enter_browser(self):
            if self.engine is None:
                self.grid_title.configure(text="Not connected — open Connections first.")
                return
            if not self.tree.get_children():
                self._refresh_tree()

        def _refresh_tree(self):
            if self.engine is None:
                self._show_error("Connect to a database first.")
                return
            self.tree.delete(*self.tree.get_children())

            def work():
                tables = introspect.list_tables(self.engine)
                views = introspect.list_views(self.engine)
                return tables, views

            def ok(res):
                tables, views = res
                tnode = self.tree.insert("", "end", text=f"Tables ({len(tables)})",
                                         open=True)
                for t in tables:
                    self.tree.insert(tnode, "end", text=t, values=("table", t))
                if views:
                    vnode = self.tree.insert("", "end",
                                             text=f"Views ({len(views)})", open=True)
                    for v in views:
                        self.tree.insert(vnode, "end", text=v, values=("view", v))
                self._show_ok(f"{len(tables)} table(s), {len(views)} view(s).")

            self._bg(work, ok, busy="Loading…")

        def _on_tree_select(self, _e=None):
            sel = self.tree.selection()
            if not sel:
                return
            vals = self.tree.item(sel[0], "values")
            if not vals:
                return
            self.browse_table = vals[1]
            self.browse_offset = 0
            self._load_page()

        def _page(self, direction):
            if not self.browse_table:
                return
            new_offset = self.browse_offset + direction * PAGE_SIZE
            if new_offset < 0:
                return
            self.browse_offset = new_offset
            self._load_page()

        def _load_page(self):
            table = self.browse_table
            offset = self.browse_offset

            def work():
                return query.fetch_table(self.engine, table, limit=PAGE_SIZE,
                                         offset=offset)

            def ok(result):
                self.last_result = result
                self._fill_grid(self.grid, result)
                start = offset + 1 if result["rows"] else offset
                end = offset + len(result["rows"])
                self.grid_title.configure(
                    text=f"{table}  —  rows {start}–{end}")
                self._show_ok(f"{len(result['rows'])} row(s) "
                              f"in {result['elapsed']:.3f}s.")

            self._bg(work, ok, busy="Loading…")

        def _fill_grid(self, grid, result):
            grid.delete(*grid.get_children())
            cols = result.get("columns") or []
            grid["columns"] = cols
            for c in cols:
                grid.heading(c, text=c)
                grid.column(c, width=max(80, min(260, len(str(c)) * 12)),
                            stretch=False, anchor="w")
            for row in result.get("rows") or []:
                grid.insert("", "end",
                            values=["" if v is None else v for v in row])

        def _edit_row_dialog(self):
            if not self.browse_table:
                self._show_error("Open a table first.")
                return
            sel = self.grid.selection()
            if not sel:
                self._show_error("Select a row in the grid to edit.")
                return
            cols = list(self.grid["columns"])
            values = self.grid.item(sel[0], "values")
            current = dict(zip(cols, values))
            RowEditor(self, self.browse_table, current)

        def apply_row_edit(self, table, key, new_values):
            """Called by the RowEditor dialog to persist an UPDATE."""
            def work():
                return edit.update_row(self.engine, table, key, new_values)

            def ok(n):
                self._show_ok(f"Updated {n} row(s).")
                self._load_page()

            self._bg(work, ok, busy="Saving…")

        def apply_row_delete(self, table, key):
            def work():
                return edit.delete_row(self.engine, table, key)

            def ok(n):
                self._show_ok(f"Deleted {n} row(s).")
                self._load_page()

            self._bg(work, ok, busy="Deleting…")

        # =================================================================
        # Section: SQL editor
        # =================================================================
        def _build_sql(self, parent):
            bar = ttk.Frame(parent, style="TFrame")
            bar.pack(fill="x")
            ttk.Button(bar, text="▶ Run", style="Accent.TButton",
                       command=self._run_sql).pack(side="left")
            ttk.Label(bar, text="  Limit").pack(side="left")
            self.limit_var = tk.StringVar(value="1000")
            ttk.Entry(bar, textvariable=self.limit_var, width=8).pack(side="left",
                                                                      padx=4)
            ttk.Button(bar, text="Export CSV…",
                       command=lambda: self._export("csv")).pack(side="right")
            ttk.Button(bar, text="Export JSON…",
                       command=lambda: self._export("json")).pack(side="right", padx=4)

            self.sql_text = tk.Text(parent, height=8, wrap="none", undo=True)
            self.sql_text.pack(fill="x", pady=(8, 6))
            self.track(self.sql_text, "text")
            self.sql_text.insert("1.0", "SELECT 1;")
            self.sql_text.bind("<Control-Return>", lambda _e: (self._run_sql(), "break"))

            self.sql_grid = self._make_grid(parent)

        def _run_sql(self):
            if self.engine is None:
                self._show_error("Connect to a database first.")
                return
            sql = self.sql_text.get("1.0", "end").strip()
            if not sql:
                self._show_error("Enter a SQL statement to run.")
                return
            try:
                limit = int(self.limit_var.get() or 0)
            except ValueError:
                limit = 1000

            def work():
                return query.run_sql(self.engine, sql, limit=limit)

            def ok(result):
                self.last_result = result
                self._fill_grid(self.sql_grid, result)
                if result["columns"]:
                    self._show_ok(f"{result['rowcount']} row(s) in "
                                  f"{result['elapsed']:.3f}s.")
                else:
                    self._show_ok(f"OK — {result['rowcount']} row(s) affected in "
                                  f"{result['elapsed']:.3f}s.")

            self._bg(work, ok, busy="Running…")

        def _export(self, fmt):
            if not self.last_result or not self.last_result.get("columns"):
                self._show_error("Run a query that returns rows first.")
                return
            ext = ".csv" if fmt == "csv" else ".json"
            path = filedialog.asksaveasfilename(
                title="Export results", defaultextension=ext,
                filetypes=[(fmt.upper(), "*" + ext), ("All files", "*.*")])
            if not path:
                return
            result = self.last_result

            def work():
                return query.export_result(result, path, fmt=fmt)

            def ok(n):
                self._show_ok(f"Exported {n} row(s) to {path}.")

            self._bg(work, ok, busy="Exporting…")

        # =================================================================
        # Section: ERD
        # =================================================================
        def _build_erd(self, parent):
            bar = ttk.Frame(parent, style="TFrame")
            bar.pack(fill="x")
            ttk.Button(bar, text="Build ER model", style="Accent.TButton",
                       command=self._build_erd_model).pack(side="left")
            self.erd_text = tk.Text(parent, wrap="none")
            self.erd_text.pack(fill="both", expand=True, pady=(8, 0))
            self.track(self.erd_text, "text")
            self.erd_text.configure(state="disabled")

        def _build_erd_model(self):
            if self.engine is None:
                self._show_error("Connect to a database first.")
                return

            def work():
                return erd.erd_summary(erd.build_erd(self.engine))

            def ok(text):
                self.erd_text.configure(state="normal")
                self.erd_text.delete("1.0", "end")
                self.erd_text.insert("1.0", text)
                self.erd_text.configure(state="disabled")
                self._show_ok("ER model built.")

            self._bg(work, ok, busy="Analysing…")

        # ---- misc ---------------------------------------------------------
        def _about(self):
            messagebox.showinfo(
                "About DBExplorer",
                f"{APP_NAME} {APP_VERSION}\n\n"
                "A fast, offline, 100% open-source universal SQL client.\n"
                "Built on SQLAlchemy. Apache-2.0.\n\n"
                "Published on QuickOpen — quickopen.ai")

        def _on_close(self):
            if self.engine is not None:
                try:
                    self.engine.dispose()
                except Exception:
                    pass
            self.destroy()

    class RowEditor(tk.Toplevel):
        """Modal single-row editor: edit non-PK columns, or delete by PK."""

        def __init__(self, app, table, current):
            super().__init__(app)
            self.app = app
            self.table = table
            self.title(f"Edit row — {table}")
            self.transient(app)
            self.resizable(False, False)
            self._vars = {}
            self._original = dict(current)

            # discover the primary key so we know what identifies the row
            try:
                self._pk = introspect.get_primary_key(app.engine, table)
            except Exception:
                self._pk = []

            frame = ttk.Frame(self, style="TFrame", padding=12)
            frame.pack(fill="both", expand=True)
            ttk.Label(frame,
                      text=("Primary key: " + ", ".join(self._pk)) if self._pk
                      else "This table has no primary key — editing is disabled.",
                      style="Muted.TLabel").grid(row=0, column=0, columnspan=2,
                                                 sticky="w", pady=(0, 8))
            for i, (col, val) in enumerate(current.items(), start=1):
                ttk.Label(frame, text=col, width=18, anchor="w").grid(
                    row=i, column=0, sticky="w", pady=2)
                var = tk.StringVar(value="" if val is None else str(val))
                ent = ttk.Entry(frame, textvariable=var, width=40)
                if col in self._pk:
                    ent.state(["readonly"])
                ent.grid(row=i, column=1, sticky="we", pady=2)
                self._vars[col] = var

            btns = ttk.Frame(frame, style="TFrame")
            btns.grid(row=len(current) + 1, column=0, columnspan=2,
                      sticky="e", pady=(10, 0))
            save = ttk.Button(btns, text="Save changes", style="Accent.TButton",
                              command=self._save)
            save.pack(side="left")
            ttk.Button(btns, text="Delete row", command=self._delete).pack(
                side="left", padx=6)
            ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="left")
            if not self._pk:
                save.state(["disabled"])
            app._apply_theme()
            try:
                self.grab_set()
            except Exception:
                pass

        def _key(self):
            return {c: self._original.get(c) for c in self._pk}

        def _save(self):
            if not self._pk:
                return
            new_values = {c: v.get() for c, v in self._vars.items() if c not in self._pk}
            changed = {c: val for c, val in new_values.items()
                       if str(self._original.get(c)) != str(val)}
            if not changed:
                self.destroy()
                return
            self.app.apply_row_edit(self.table, self._key(), changed)
            self.destroy()

        def _delete(self):
            if not self._pk:
                return
            if not messagebox.askyesno("Delete row",
                                       "Delete this row? This cannot be undone."):
                return
            self.app.apply_row_delete(self.table, self._key())
            self.destroy()

    return App


def main():
    """Entry point: build the root window and run. Degrades on headless hosts.

    Importing this module does nothing; only this function creates a Tk root.
    With no display (a server/CI box) it prints a friendly note and returns 0
    instead of raising.
    """
    try:
        import tkinter as tk
    except Exception as exc:
        print(f"{APP_NAME}: a graphical environment with tkinter is required to "
              f"run the GUI ({exc}).")
        return 0

    try:
        App = build_app()
        app = App()
    except tk.TclError as exc:
        print(f"{APP_NAME}: no graphical display available — cannot start the GUI "
              f"here ({exc}). This app is intended for the Windows desktop.")
        return 0
    except Exception as exc:
        print(f"{APP_NAME}: could not start the GUI ({exc}).")
        return 1

    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
