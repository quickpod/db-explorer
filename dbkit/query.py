"""Running SQL and exporting results.

:func:`run_sql` executes one statement (parameterised, read OR write) and
returns a small result dict the CLI and GUI both consume::

    {"columns": [...], "rows": [[...], ...], "rowcount": int, "elapsed": float}

Writes commit by default; :func:`run_in_transaction` runs several statements as
one unit.  :func:`export_result` writes a result dict to CSV or JSON.
"""

from __future__ import annotations

import csv
import json
import time

import sqlalchemy as sa

from .errors import DBError

DEFAULT_LIMIT = 1000


def _is_select(result):
    """Whether a SQLAlchemy result carries rows to fetch."""
    try:
        return result.returns_rows
    except Exception:
        return False


def run_sql(engine, sql, params=None, limit=DEFAULT_LIMIT, autocommit=True):
    """Execute *sql* and return a result dict.

    *params* enables safe parameter binding (``:name`` placeholders with a dict,
    or a sequence for a driver's positional style).  Row-returning statements are
    truncated to *limit* rows (``None``/0 means no cap); other statements report
    the affected ``rowcount``.  Writes are committed unless ``autocommit=False``.
    """
    if not sql or not str(sql).strip():
        raise DBError("No SQL statement was provided.")
    text = sa.text(sql)
    started = time.perf_counter()
    try:
        with engine.connect() as conn:
            result = conn.execute(text, params or {})
            if _is_select(result):
                columns = list(result.keys())
                rows = []
                capped = limit if limit else None
                for i, row in enumerate(result):
                    if capped is not None and i >= capped:
                        break
                    rows.append(list(row))
                out = {
                    "columns": columns,
                    "rows": rows,
                    "rowcount": len(rows),
                    "elapsed": time.perf_counter() - started,
                }
            else:
                if autocommit:
                    conn.commit()
                out = {
                    "columns": [],
                    "rows": [],
                    "rowcount": result.rowcount if result.rowcount is not None else -1,
                    "elapsed": time.perf_counter() - started,
                }
            return out
    except DBError:
        raise
    except sa.exc.SQLAlchemyError as exc:
        raise DBError(_clean_db_message(exc))
    except Exception as exc:
        raise DBError(f"Query failed: {exc}")


def fetch_table(engine, table, schema=None, limit=100, offset=0):
    """Page through a table's rows portably (reflected select + LIMIT/OFFSET).

    Returns the same dict shape as :func:`run_sql`.  Used by the GUI data grid.
    """
    try:
        md = sa.MetaData()
        tbl = sa.Table(table, md, autoload_with=engine, schema=schema)
    except Exception as exc:
        raise DBError(f"Could not load table {table!r}: {exc}")
    stmt = sa.select(tbl)
    if limit:
        stmt = stmt.limit(limit)
    if offset:
        stmt = stmt.offset(offset)
    started = time.perf_counter()
    try:
        with engine.connect() as conn:
            result = conn.execute(stmt)
            columns = list(result.keys())
            rows = [list(r) for r in result]
        return {"columns": columns, "rows": rows, "rowcount": len(rows),
                "elapsed": time.perf_counter() - started}
    except sa.exc.SQLAlchemyError as exc:
        raise DBError(_clean_db_message(exc))


def count_rows(engine, table, schema=None):
    """Return the total row count of a table (for paging), or -1 on failure."""
    try:
        md = sa.MetaData()
        tbl = sa.Table(table, md, autoload_with=engine, schema=schema)
        stmt = sa.select(sa.func.count()).select_from(tbl)
        with engine.connect() as conn:
            return int(conn.execute(stmt).scalar_one())
    except Exception:
        return -1


def run_in_transaction(engine, statements):
    """Run a list of ``(sql, params)`` (or bare sql) items as one transaction.

    Rolls the whole thing back on any error.  Returns the total affected
    rowcount across the statements.
    """
    total = 0
    try:
        with engine.begin() as conn:
            for item in statements:
                if isinstance(item, (list, tuple)):
                    sql, params = (item + (None,))[:2] if len(item) < 2 else item[:2]
                else:
                    sql, params = item, None
                result = conn.execute(sa.text(sql), params or {})
                if not _is_select(result) and result.rowcount and result.rowcount > 0:
                    total += result.rowcount
        return total
    except sa.exc.SQLAlchemyError as exc:
        raise DBError(_clean_db_message(exc))
    except Exception as exc:
        raise DBError(f"Transaction failed: {exc}")


def _clean_db_message(exc):
    """Turn a SQLAlchemy exception into a short human line (no stack, no wrapper)."""
    msg = str(getattr(exc, "orig", None) or exc)
    msg = msg.strip().splitlines()[0] if msg.strip() else exc.__class__.__name__
    return msg


def export_result(result, path, fmt="csv"):
    """Write a :func:`run_sql` *result* dict to *path* as ``csv`` or ``json``.

    Returns the number of data rows written.
    """
    if not isinstance(result, dict) or "columns" not in result:
        raise DBError("export_result expects a run_sql() result dict.")
    fmt = (fmt or "csv").lower()
    columns = result.get("columns") or []
    rows = result.get("rows") or []
    try:
        if fmt == "csv":
            with open(path, "w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                if columns:
                    writer.writerow(columns)
                for row in rows:
                    writer.writerow(["" if v is None else v for v in row])
        elif fmt == "json":
            records = [dict(zip(columns, row)) for row in rows]
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(records, fh, indent=2, default=_json_default)
        else:
            raise DBError(f"Unknown export format {fmt!r} (use 'csv' or 'json').")
    except DBError:
        raise
    except OSError as exc:
        raise DBError(f"Could not write export file: {exc}")
    return len(rows)


def _json_default(value):
    """Best-effort JSON encoding for exotic column values (dates, Decimal...)."""
    try:
        return str(value)
    except Exception:
        return None
