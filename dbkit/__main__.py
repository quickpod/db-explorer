"""Command-line interface: ``python -m dbkit <command> ...``.

Commands
    profiles list|add|remove ...   manage saved connection profiles
    tables   <profile>             list tables (and views)
    schema   <profile> <table>     show columns / PK / indexes / foreign keys
    ddl      <profile> <table>     print the CREATE TABLE statement
    query    <profile> "SQL"       run SQL (--limit, --format csv|json|table)
    export   <profile> "SQL" PATH  run SQL and write CSV/JSON to a file

Every expected failure prints ``error: <message>`` to stderr and exits non-zero;
tracebacks are never shown.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys

from . import (
    DBError,
    add_profile,
    build_erd,
    connect,
    erd_summary,
    export_result,
    get_profile,
    list_profiles,
    list_tables,
    list_views,
    needs_password,
    remove_profile,
    run_sql,
    table_ddl,
    table_info,
)


# --- helpers ---------------------------------------------------------------

def _resolve_password(profile, supplied):
    """Return the password to use, prompting only when needed and possible."""
    if supplied:
        return supplied
    env = os.environ.get("DBEXPLORER_PASSWORD")
    if env:
        return env
    if needs_password(profile):
        if sys.stdin and sys.stdin.isatty():
            try:
                return getpass.getpass(f"Password for {profile['name']}: ")
            except Exception:
                return None
        raise DBError(
            f"Profile {profile['name']!r} needs a password. Pass --password "
            f"or set DBEXPLORER_PASSWORD.")
    return None


def _engine(name, password=None):
    profile = get_profile(name)
    pw = _resolve_password(profile, password)
    return connect(profile, password=pw)


def _print_table(result):
    """Render a run_sql result dict as a monospace ASCII grid."""
    columns = result.get("columns") or []
    rows = result.get("rows") or []
    if not columns:
        print(f"OK ({result.get('rowcount', 0)} row(s) affected, "
              f"{result.get('elapsed', 0.0):.3f}s)")
        return
    cells = [[_s(v) for v in row] for row in rows]
    widths = [len(c) for c in columns]
    for row in cells:
        for i, val in enumerate(row):
            widths[i] = max(widths[i], len(val))
    sep = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
    header = "| " + " | ".join(c.ljust(widths[i]) for i, c in enumerate(columns)) + " |"
    print(sep)
    print(header)
    print(sep)
    for row in cells:
        print("| " + " | ".join(v.ljust(widths[i]) for i, v in enumerate(row)) + " |")
    print(sep)
    print(f"{len(rows)} row(s), {result.get('elapsed', 0.0):.3f}s")


def _s(value):
    return "" if value is None else str(value)


# --- command handlers ------------------------------------------------------

def cmd_profiles(a):
    action = a.action
    if action == "list":
        profs = list_profiles()
        if not profs:
            print("(no profiles yet -- add one with 'profiles add')")
            return
        for p in profs:
            if p.get("kind") == "sqlite":
                print(f"{p['name']:20} sqlite   {p.get('path', '')}")
            else:
                pw = "stored-pw" if p.get("store_password") else "prompt-pw"
                print(f"{p['name']:20} url      {p.get('url', '')}  [{pw}]")
    elif action == "add":
        if a.path:
            prof = add_profile(a.name, "sqlite", path=a.path)
        elif a.url:
            prof = add_profile(a.name, "url", url=a.url,
                               store_password=a.store_password)
        else:
            raise DBError("Provide --path (SQLite) or --url (Postgres/MySQL).")
        print(f"Saved profile {prof['name']!r} ({prof['kind']}).")
    elif action == "remove":
        remove_profile(a.name)
        print(f"Removed profile {a.name!r}.")
    else:  # pragma: no cover - argparse enforces choices
        raise DBError(f"Unknown profiles action {action!r}.")


def cmd_tables(a):
    engine = _engine(a.profile, a.password)
    try:
        tables = list_tables(engine, schema=a.schema)
        views = list_views(engine, schema=a.schema)
    finally:
        engine.dispose()
    for t in tables:
        print(t)
    for v in views:
        print(f"{v}  (view)")
    if not tables and not views:
        print("(no tables)")


def cmd_schema(a):
    engine = _engine(a.profile, a.password)
    try:
        info = table_info(engine, a.table, schema=a.schema)
    finally:
        engine.dispose()
    print(f"Table: {info['name']}")
    print("Columns:")
    for c in info["columns"]:
        flags = []
        if c["primary_key"]:
            flags.append("PK")
        if not c["nullable"]:
            flags.append("NOT NULL")
        extra = f"  [{', '.join(flags)}]" if flags else ""
        print(f"  {c['name']} : {c['type']}{extra}")
    if info["primary_key"]:
        print(f"Primary key: {', '.join(info['primary_key'])}")
    if info["indexes"]:
        print("Indexes:")
        for ix in info["indexes"]:
            uniq = " UNIQUE" if ix["unique"] else ""
            print(f"  {ix['name']}{uniq}: {', '.join(ix['columns'])}")
    if info["foreign_keys"]:
        print("Foreign keys:")
        for fk in info["foreign_keys"]:
            print(f"  {', '.join(fk['columns'])} -> "
                  f"{fk['referred_table']}({', '.join(fk['referred_columns'])})")


def cmd_ddl(a):
    engine = _engine(a.profile, a.password)
    try:
        print(table_ddl(engine, a.table, schema=a.schema))
    finally:
        engine.dispose()


def cmd_query(a):
    engine = _engine(a.profile, a.password)
    try:
        result = run_sql(engine, a.sql, limit=a.limit)
    finally:
        engine.dispose()
    fmt = a.format
    if fmt == "table":
        _print_table(result)
    elif fmt == "csv":
        _emit_csv(result)
    elif fmt == "json":
        records = [dict(zip(result["columns"], row)) for row in result["rows"]]
        print(json.dumps(records, indent=2, default=str))


def _emit_csv(result):
    import csv
    writer = csv.writer(sys.stdout)
    if result["columns"]:
        writer.writerow(result["columns"])
    for row in result["rows"]:
        writer.writerow(["" if v is None else v for v in row])


def cmd_erd(a):
    engine = _engine(a.profile, a.password)
    try:
        model = build_erd(engine, schema=a.schema)
    finally:
        engine.dispose()
    print(erd_summary(model))


def cmd_export(a):
    engine = _engine(a.profile, a.password)
    try:
        result = run_sql(engine, a.sql, limit=a.limit)
    finally:
        engine.dispose()
    n = export_result(result, a.output, fmt=a.format)
    print(f"Exported {n} row(s) to {a.output} ({a.format}).")


# --- parser ----------------------------------------------------------------

def _add_conn_args(sp):
    sp.add_argument("profile", help="name of a saved connection profile")
    sp.add_argument("--password", help="database password (else prompt/env)")
    sp.add_argument("--schema", help="schema name (backends that support it)")


def build_parser():
    p = argparse.ArgumentParser(
        prog="dbkit", description="DBExplorer command-line SQL client.")
    sub = p.add_subparsers(dest="command", required=True)

    pr = sub.add_parser("profiles", help="manage connection profiles")
    pra = pr.add_subparsers(dest="action", required=True)
    pl = pra.add_parser("list", help="list saved profiles")
    pl.set_defaults(func=cmd_profiles)
    pad = pra.add_parser("add", help="add or replace a profile")
    pad.add_argument("name")
    pad.add_argument("--path", help="SQLite database file path")
    pad.add_argument("--url", help="SQLAlchemy database URL")
    pad.add_argument("--store-password", action="store_true", dest="store_password",
                     help="keep the password in the URL on disk (opt-in)")
    pad.set_defaults(func=cmd_profiles)
    prm = pra.add_parser("remove", help="remove a profile")
    prm.add_argument("name")
    prm.set_defaults(func=cmd_profiles)

    t = sub.add_parser("tables", help="list tables and views")
    _add_conn_args(t)
    t.set_defaults(func=cmd_tables)

    s = sub.add_parser("schema", help="show a table's columns/keys")
    _add_conn_args(s)
    s.add_argument("table")
    s.set_defaults(func=cmd_schema)

    d = sub.add_parser("ddl", help="print CREATE TABLE for a table")
    _add_conn_args(d)
    d.add_argument("table")
    d.set_defaults(func=cmd_ddl)

    q = sub.add_parser("query", help="run a SQL statement")
    _add_conn_args(q)
    q.add_argument("sql", help="the SQL to run (quote it)")
    q.add_argument("--limit", type=int, default=1000,
                   help="max rows to fetch (0 = no limit)")
    q.add_argument("--format", choices=("table", "csv", "json"), default="table")
    q.set_defaults(func=cmd_query)

    e = sub.add_parser("export", help="run SQL and write CSV/JSON to a file")
    _add_conn_args(e)
    e.add_argument("sql")
    e.add_argument("output", help="destination file path")
    e.add_argument("--limit", type=int, default=0,
                   help="max rows to fetch (0 = no limit)")
    e.add_argument("--format", choices=("csv", "json"), default="csv")
    e.set_defaults(func=cmd_export)

    er = sub.add_parser("erd", help="print an ER model summary")
    _add_conn_args(er)
    er.set_defaults(func=cmd_erd)

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except DBError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
