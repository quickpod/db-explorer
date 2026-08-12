#!/usr/bin/env python3
r"""DBExplorer -- an Aura (QuickOpen design system) GUI on top of the ``dbkit`` API.

A single Aura window: a sidebar of sections (Connections, Browser, SQL editor,
ERD, About) and a main panel that swaps to the selected section.  Every
database operation calls the tested ``dbkit`` core (never re-implements SQL
logic) and runs on a background thread so the UI stays responsive; results are
marshalled back with ``self.after`` and reported in the Aura status bar -- a
short success line or the :class:`~dbkit.DBError` message (never a raw
traceback) on failure.

Design goals baked in here (mirrors the QuickOpen house style):
  * built on the vendored ``dbkit/aura.py`` design system, which layers the
    quickopen.ai look (deep space + light) over CustomTkinter.  Runtime deps:
    ``customtkinter`` (+ ``darkdetect``) -- declared in requirements.txt; the
    PyInstaller build adds ``--collect-all customtkinter``.
  * Importing this module does nothing.  Only :func:`main` builds a root
    window, and it degrades gracefully (prints a message, returns 0) with no
    display or with customtkinter missing.
  * Frozen-exe safe: bundled assets resolve via ``sys._MEIPASS`` / the exe
    directory when ``sys.frozen`` is set -- never ``__file__``.
  * The SQL editor (``tk.Text``) and the results grids (``ttk.Treeview``)
    stay native tk/ttk -- restyled by Aura and registered with
    ``aura.track`` where raw so they follow the dark/light toggle.

100% AI-built, open source, published on QuickOpen (quickopen.ai). Apache-2.0.
"""

from __future__ import annotations

import os
import sys
import threading

# NOTE: tkinter/customtkinter are imported lazily inside main()/build_app so
# that merely importing this module (packaging, headless CI) never fails.

APP_NAME = "DBExplorer"
APP_VERSION = "1.0.0"
WINDOW_TITLE = "DBExplorer — by QuickOpen (quickopen.ai)"
PROJECT_URL = "https://quickopen.ai"
PAGE_SIZE = 100
ACCENT = "#2f5fe0"      # publish/specs/db-explorer.json "accent": [47, 95, 224]

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
# The app (built lazily; tkinter/customtkinter imported only inside build_app)
# ---------------------------------------------------------------------------
def build_app():
    """Construct and return the App class bound to live GUI imports.

    Kept inside a function so this module imports cleanly without a display
    (and without customtkinter installed).
    """
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, simpledialog
    import customtkinter as ctk

    from . import aura, guiconfig, conn, introspect, query, edit, erd
    from .errors import DBError

    class App(aura.AuraApp):
        def __init__(self):
            super().__init__(
                title=WINDOW_TITLE, app_name=APP_NAME, accent=ACCENT,
                theme=guiconfig.get_theme(),
                icon_png=asset_path("db-explorer.png"), version=APP_VERSION,
                tagline="offline SQL client",
                on_theme_change=guiconfig.set_theme,
                size=(1120, 720), min_size=(900, 580))

            self._busy = False
            self._img_refs_gui = []

            # active connection state
            self.engine = None
            self.active_name = None
            self.active_schema = None
            self.browse_table = None
            self.browse_offset = 0
            self.last_result = None     # last SQL/grid result dict (for export)

            self._set_icon()
            self._build_menu()
            self.conn_lbl = aura.Caption(self.header_actions, "not connected")
            self.conn_lbl.pack(side="right")

            self.add_section("connections", "Connections", "⛁",
                             self._build_connections)
            self.add_section("browser", "Browser", "▤", self._build_browser)
            self.add_section("sql", "SQL editor", "✎", self._build_sql)
            self.add_section("erd", "ERD", "◈", self._build_erd)
            self.add_section("about", "About", "◉", self._build_about)
            self.show("connections")
            self.set_status("Ready")
            self.protocol("WM_DELETE_WINDOW", self._on_close)

        # ---- navigation ---------------------------------------------------
        def show(self, sid):
            """Raise a section, then run its optional ``_enter_<sid>`` hook."""
            super().show(sid)
            hook = getattr(self, "_enter_" + sid, None)
            if hook:
                hook()

        def _desc(self, parent, sid):
            """The short section description under the header (house style)."""
            text = SECTION_DESC.get(sid)
            if text:
                aura.Caption(parent, text, wraplength=780,
                             justify="left").pack(anchor="w", pady=(0, 10))

        # ---- assets / icon ------------------------------------------------
        def _set_icon(self):
            try:
                ico = asset_path("db-explorer.ico")
                if ico and os.name == "nt":
                    self.iconbitmap(ico)
                    return
            except Exception:
                pass
            try:
                png = asset_path("db-explorer.png")
                if png:
                    img = tk.PhotoImage(file=png)
                    self._img_refs_gui.append(img)
                    self.iconphoto(True, img)
            except Exception:
                pass  # icon is cosmetic; never block launch

        # ---- menu (native menus stay; theme lives in the sidebar toggle) --
        def _build_menu(self):
            bar = tk.Menu(self)
            filem = tk.Menu(bar, tearoff=0)
            filem.add_command(label="Open SQLite file…",
                              command=self._quick_open_sqlite)
            filem.add_separator()
            filem.add_command(label="Exit", command=self._on_close)
            bar.add_cascade(label="File", menu=filem)

            viewm = tk.Menu(bar, tearoff=0)
            viewm.add_command(
                label="Toggle dark mode",
                command=lambda: self.set_theme(
                    "light" if self.theme == "dark" else "dark"))
            bar.add_cascade(label="View", menu=viewm)

            helpm = tk.Menu(bar, tearoff=0)
            helpm.add_command(label="About",
                              command=lambda: self.show("about"))
            helpm.add_command(label="Open project page (quickopen.ai)",
                              command=lambda: open_with_default_app(PROJECT_URL))
            bar.add_cascade(label="Help", menu=helpm)
            self.configure(menu=bar)

        # ---- background runner --------------------------------------------
        def _bg(self, work, on_ok, button=None, busy="Working…"):
            """Run ``work()`` off the UI thread; call ``on_ok(result)`` back on it.

            Errors show in the status bar (the DBError message, never a
            traceback).  Refuses to start a second op while one is in flight.
            """
            if self._busy:
                self.set_error("Please wait — an operation is already running.")
                return
            self._busy = True
            if button is not None:
                try:
                    button.state(["disabled"])
                except Exception:
                    pass
            self.set_status(busy, kind="working")

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
                    self.set_error(err)
                    return
                try:
                    on_ok(res)
                except Exception as ex:
                    self.set_error(f"Post-processing error: {ex}")

            threading.Thread(target=run, daemon=True).start()

        # =================================================================
        # Section: Connections
        # =================================================================
        def _build_connections(self, parent):
            self._desc(parent, "connections")
            row = ctk.CTkFrame(parent, fg_color="transparent")
            row.pack(fill="both", expand=True)

            saved = aura.Card(row, title="Saved profiles")
            saved.pack(side="left", fill="y", padx=(0, 14))
            self.prof_list = tk.Listbox(saved.body, height=16, width=30,
                                        activestyle="none",
                                        exportselection=False)
            self.prof_list.pack(fill="both", expand=True, pady=(0, 10))
            aura.track(self.prof_list, "listbox")
            self.prof_list.bind("<<ListboxSelect>>",
                                lambda _e: self._load_selected_profile())
            btns = ctk.CTkFrame(saved.body, fg_color="transparent")
            btns.pack(fill="x")
            aura.AuraButton(btns, "Connect",
                            command=self._connect_selected).pack(side="left")
            aura.AuraButton(btns, "Delete", kind="danger",
                            command=self._delete_selected).pack(
                side="left", padx=(8, 0))

            form = aura.Card(row, title="Add / edit profile")
            form.pack(side="left", fill="both", expand=True)

            self.kind_var = tk.StringVar(value="sqlite")
            krow = ctk.CTkFrame(form.body, fg_color="transparent")
            krow.pack(fill="x", pady=(0, 10))
            ctk.CTkRadioButton(krow, text="SQLite file",
                               variable=self.kind_var, value="sqlite",
                               command=self._sync_kind,
                               font=aura.font()).pack(side="left")
            ctk.CTkRadioButton(krow, text="Database URL",
                               variable=self.kind_var, value="url",
                               command=self._sync_kind,
                               font=aura.font()).pack(side="left", padx=14)

            self.name_var = tk.StringVar()
            self._form_field(form.body, "Profile name", self.name_var)

            # swappable body holding the SQLite path row OR the URL row
            body = ctk.CTkFrame(form.body, fg_color="transparent")
            body.pack(fill="x")

            self.path_var = tk.StringVar()
            self.path_row = ctk.CTkFrame(body, fg_color="transparent")
            ctk.CTkLabel(self.path_row, text="SQLite file", width=110,
                         anchor="w", font=aura.font()).pack(side="left")
            aura.AuraEntry(self.path_row, textvariable=self.path_var).pack(
                side="left", fill="x", expand=True, padx=(0, 8))
            aura.AuraButton(self.path_row, "Browse…", kind="secondary",
                            command=self._browse_sqlite).pack(side="left")

            self.url_var = tk.StringVar()
            self.url_row = ctk.CTkFrame(body, fg_color="transparent")
            ctk.CTkLabel(self.url_row, text="Database URL", width=110,
                         anchor="w", font=aura.font()).pack(side="left")
            aura.AuraEntry(self.url_row, textvariable=self.url_var).pack(
                side="left", fill="x", expand=True)

            self.store_pw_var = tk.BooleanVar(value=False)
            self.pw_check = ctk.CTkCheckBox(
                body, text="Store password in the URL on disk (not recommended)",
                variable=self.store_pw_var, font=aura.font())

            aura.Caption(
                form.body, wraplength=540, justify="left",
                text="Example URL: postgresql+psycopg2://user@host:5432/dbname "
                     "(leave the password out — you'll be prompted). SQLite "
                     "needs no driver; Postgres/MySQL need psycopg2 / pymysql."
            ).pack(anchor="w", pady=(10, 8))

            brow = ctk.CTkFrame(form.body, fg_color="transparent")
            brow.pack(fill="x", pady=(4, 0))
            aura.AuraButton(brow, "Save profile",
                            command=self._save_profile).pack(side="left")
            aura.AuraButton(brow, "Save & connect", kind="secondary",
                            command=self._save_and_connect).pack(
                side="left", padx=(8, 0))
            self._sync_kind()

        def _form_field(self, parent, label, var):
            row = ctk.CTkFrame(parent, fg_color="transparent")
            row.pack(fill="x", pady=(0, 8))
            ctk.CTkLabel(row, text=label, width=110, anchor="w",
                         font=aura.font()).pack(side="left")
            aura.AuraEntry(row, textvariable=var).pack(side="left", fill="x",
                                                       expand=True)

        def _sync_kind(self):
            if self.kind_var.get() == "sqlite":
                self.url_row.pack_forget()
                self.pw_check.pack_forget()
                self.path_row.pack(fill="x", pady=(0, 8))
            else:
                self.path_row.pack_forget()
                self.url_row.pack(fill="x", pady=(0, 8))
                self.pw_check.pack(anchor="w", pady=(2, 0))

        def _enter_connections(self):
            self._refresh_profiles()

        def _refresh_profiles(self):
            try:
                profiles = conn.list_profiles()
            except DBError as ex:
                self.set_error(str(ex))
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
                return conn.make_profile(name, "sqlite",
                                         path=self.path_var.get().strip())
            return conn.make_profile(name, "url", url=self.url_var.get().strip(),
                                     store_password=self.store_pw_var.get())

        def _save_profile(self):
            try:
                prof = self._build_profile_from_form()
                conn.add_profile(prof["name"], prof["kind"],
                                 path=prof.get("path"), url=prof.get("url"),
                                 store_password=prof.get("store_password", False))
            except DBError as ex:
                self.set_error(str(ex))
                return None
            self._refresh_profiles()
            self.set_success(f"Saved profile “{prof['name']}”.")
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
                self.set_error(str(ex))
                return
            self._refresh_profiles()
            self.set_success(f"Deleted profile “{name}”.")

        def _connect_selected(self):
            name = self._selected_profile_name()
            if not name:
                self.set_error("Select a profile to connect to.")
                return
            try:
                prof = conn.get_profile(name)
            except DBError as ex:
                self.set_error(str(ex))
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
                self.show("browser")
                self.set_success(f"Connected to “{prof['name']}”.")

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
                self.set_error(str(ex))
                return
            self._do_connect(prof)

        # =================================================================
        # Section: Browser
        # =================================================================
        def _build_browser(self, parent):
            self._desc(parent, "browser")
            row = ctk.CTkFrame(parent, fg_color="transparent")
            row.pack(fill="both", expand=True)

            left = ctk.CTkFrame(row, fg_color="transparent")
            left.pack(side="left", fill="y", padx=(0, 14))
            top = ctk.CTkFrame(left, fg_color="transparent")
            top.pack(fill="x")
            aura.SectionLabel(top, "Tables & views").pack(side="left")
            aura.AuraButton(top, "↻", kind="ghost", width=30, height=26,
                            command=self._refresh_tree).pack(side="right")
            self.tree = ttk.Treeview(left, show="tree", selectmode="browse",
                                     height=22)
            self.tree.pack(fill="y", expand=True, pady=(6, 0))
            self.tree.column("#0", width=230)
            self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

            right = ctk.CTkFrame(row, fg_color="transparent")
            right.pack(side="left", fill="both", expand=True)
            pg = ctk.CTkFrame(right, fg_color="transparent")
            pg.pack(fill="x")
            self.grid_title = aura.Caption(pg, "Select a table")
            self.grid_title.pack(side="left")
            aura.AuraButton(pg, "Next ▶", kind="secondary",
                            command=lambda: self._page(1)).pack(side="right")
            aura.AuraButton(pg, "◀ Prev", kind="secondary",
                            command=lambda: self._page(-1)).pack(
                side="right", padx=(0, 8))
            aura.AuraButton(pg, "Edit row…", kind="secondary",
                            command=self._edit_row_dialog).pack(
                side="right", padx=(0, 16))

            self.grid = self._make_grid(right)

        def _make_grid(self, parent):
            wrap = ctk.CTkFrame(parent, fg_color="transparent")
            wrap.pack(fill="both", expand=True, pady=(8, 0))
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
                self.grid_title.configure(
                    text="Not connected — open Connections first.")
                return
            if not self.tree.get_children():
                self._refresh_tree()

        def _refresh_tree(self):
            if self.engine is None:
                self.set_error("Connect to a database first.")
                return
            self.tree.delete(*self.tree.get_children())

            def work():
                tables = introspect.list_tables(self.engine)
                views = introspect.list_views(self.engine)
                return tables, views

            def ok(res):
                tables, views = res
                tnode = self.tree.insert("", "end",
                                         text=f"Tables ({len(tables)})",
                                         open=True)
                for t in tables:
                    self.tree.insert(tnode, "end", text=t, values=("table", t))
                if views:
                    vnode = self.tree.insert(
                        "", "end", text=f"Views ({len(views)})", open=True)
                    for v in views:
                        self.tree.insert(vnode, "end", text=v,
                                         values=("view", v))
                self.set_success(f"{len(tables)} table(s), "
                                 f"{len(views)} view(s).")

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
                self.set_success(f"{len(result['rows'])} row(s) "
                                 f"in {result['elapsed']:.3f}s.")

            self._bg(work, ok, busy="Loading…")

        def _fill_grid(self, grid, result):
            grid.delete(*grid.get_children())
            cols = result.get("columns") or []
            rows = result.get("rows") or []
            grid["columns"] = cols
            for i, c in enumerate(cols):
                # size by header AND a sample of the data so values aren't
                # clipped; data columns keep their real names (spaced() would
                # mangle arbitrary SQL identifiers) — the Aura heading style
                # still applies the 9pt bold muted look.
                longest = len(str(c))
                for row in rows[:50]:
                    v = row[i]
                    if v is not None:
                        longest = max(longest, len(str(v)))
                grid.heading(c, text=c, anchor="w")
                grid.column(c, width=max(80, min(320, 24 + longest * 8)),
                            stretch=False, anchor="w")
            for row in rows:
                grid.insert("", "end",
                            values=["" if v is None else v for v in row])

        def _edit_row_dialog(self):
            if not self.browse_table:
                self.set_error("Open a table first.")
                return
            sel = self.grid.selection()
            if not sel:
                self.set_error("Select a row in the grid to edit.")
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
                self.set_success(f"Updated {n} row(s).")
                self._load_page()

            self._bg(work, ok, busy="Saving…")

        def apply_row_delete(self, table, key):
            def work():
                return edit.delete_row(self.engine, table, key)

            def ok(n):
                self.set_success(f"Deleted {n} row(s).")
                self._load_page()

            self._bg(work, ok, busy="Deleting…")

        # =================================================================
        # Section: SQL editor
        # =================================================================
        def _build_sql(self, parent):
            self._desc(parent, "sql")
            bar = ctk.CTkFrame(parent, fg_color="transparent")
            bar.pack(fill="x")
            aura.AuraButton(bar, "▶ Run",
                            command=self._run_sql).pack(side="left")
            ctk.CTkLabel(bar, text="Limit", font=aura.font()).pack(
                side="left", padx=(14, 6))
            self.limit_var = tk.StringVar(value="1000")
            aura.AuraEntry(bar, textvariable=self.limit_var, width=80).pack(
                side="left")
            aura.AuraButton(bar, "Export CSV…", kind="secondary",
                            command=lambda: self._export("csv")).pack(
                side="right")
            aura.AuraButton(bar, "Export JSON…", kind="secondary",
                            command=lambda: self._export("json")).pack(
                side="right", padx=(0, 8))

            # the SQL editor stays a raw tk.Text (undo stack, plain keymap);
            # aura.track keeps it in step with the dark/light toggle.
            self.sql_text = tk.Text(parent, height=8, wrap="none", undo=True)
            self.sql_text.pack(fill="x", pady=(10, 8))
            aura.track(self.sql_text, "text")
            self.sql_text.insert("1.0", "SELECT 1;")
            self.sql_text.bind("<Control-Return>",
                               lambda _e: (self._run_sql(), "break"))

            self.sql_grid = self._make_grid(parent)

        def _run_sql(self):
            if self.engine is None:
                self.set_error("Connect to a database first.")
                return
            sql = self.sql_text.get("1.0", "end").strip()
            if not sql:
                self.set_error("Enter a SQL statement to run.")
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
                    self.set_success(f"{result['rowcount']} row(s) in "
                                     f"{result['elapsed']:.3f}s.")
                else:
                    self.set_success(
                        f"OK — {result['rowcount']} row(s) affected in "
                        f"{result['elapsed']:.3f}s.")

            self._bg(work, ok, busy="Running…")

        def _export(self, fmt):
            if not self.last_result or not self.last_result.get("columns"):
                self.set_error("Run a query that returns rows first.")
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
                self.set_success(f"Exported {n} row(s) to {path}.")

            self._bg(work, ok, busy="Exporting…")

        # =================================================================
        # Section: ERD
        # =================================================================
        def _build_erd(self, parent):
            self._desc(parent, "erd")
            bar = ctk.CTkFrame(parent, fg_color="transparent")
            bar.pack(fill="x")
            aura.AuraButton(bar, "Build ER model",
                            command=self._build_erd_model).pack(side="left")
            # the ER overview stays a raw tk.Text (read-only rendering of
            # erd_summary()); tracked so it follows the theme toggle.
            self.erd_text = tk.Text(parent, wrap="none")
            self.erd_text.pack(fill="both", expand=True, pady=(10, 0))
            aura.track(self.erd_text, "text")
            self.erd_text.configure(state="disabled")

        def _build_erd_model(self):
            if self.engine is None:
                self.set_error("Connect to a database first.")
                return

            def work():
                return erd.erd_summary(erd.build_erd(self.engine))

            def ok(text):
                self.erd_text.configure(state="normal")
                self.erd_text.delete("1.0", "end")
                self.erd_text.insert("1.0", text)
                self.erd_text.configure(state="disabled")
                self.set_success("ER model built.")

            self._bg(work, ok, busy="Analysing…")

        # =================================================================
        # Section: About
        # =================================================================
        def _build_about(self, parent):
            card = aura.Card(parent, title="About DBExplorer")
            card.pack(fill="x")
            aura.Heading(card.body, APP_NAME).pack(anchor="w")
            aura.Caption(card.body, f"Version {APP_VERSION}").pack(
                anchor="w", pady=(0, 10))
            ctk.CTkLabel(
                card.body, font=aura.font(), justify="left", anchor="w",
                wraplength=520,
                text="A fast, offline, 100% open-source universal SQL client "
                     "— browse, query and edit SQLite, Postgres and MySQL "
                     "databases.\n\n"
                     "100% AI-built, open source, published on QuickOpen. "
                     "Nothing is ever uploaded anywhere.").pack(anchor="w")
            aura.Caption(card.body,
                         "Licensed under Apache-2.0. Built on SQLAlchemy "
                         "(MIT) and CustomTkinter (MIT).").pack(
                anchor="w", pady=(10, 4))
            aura.AuraButton(card.body, "Project page: quickopen.ai",
                            kind="ghost",
                            command=lambda: open_with_default_app(
                                PROJECT_URL)).pack(anchor="w", pady=(6, 0))

        # ---- misc ---------------------------------------------------------
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
            self.configure(bg=aura.P("bg"))
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
            save = aura.AuraButton(btns, "Save changes", command=self._save)
            save.pack(side="left")
            aura.AuraButton(btns, "Delete row", kind="danger",
                            command=self._delete).pack(side="left", padx=8)
            aura.AuraButton(btns, "Cancel", kind="secondary",
                            command=self.destroy).pack(side="left")
            if not self._pk:
                save.state(["disabled"])
            try:
                self.grab_set()
            except Exception:
                pass

        def _key(self):
            return {c: self._original.get(c) for c in self._pk}

        def _save(self):
            if not self._pk:
                return
            new_values = {c: v.get() for c, v in self._vars.items()
                          if c not in self._pk}
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
    With no display (a server/CI box) or without customtkinter installed, it
    prints a friendly note and returns 0 instead of raising.
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
    except ImportError as exc:
        print(f"{APP_NAME}: the GUI needs the 'customtkinter' package "
              f"({exc}). Install it with:  pip install customtkinter")
        return 0
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
