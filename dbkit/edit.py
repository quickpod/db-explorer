"""Safe, parameterised single-row edits (insert / update / delete by PK).

These helpers reflect the target table so column and table identifiers are
quoted correctly by SQLAlchemy, and all values travel as bound parameters --
never string-formatted into SQL.  Updates and deletes are keyed by the table's
primary key to avoid accidentally touching more than one row.
"""

from __future__ import annotations

import sqlalchemy as sa

from .errors import DBError


def _reflect(engine, table, schema=None):
    try:
        md = sa.MetaData()
        return sa.Table(table, md, autoload_with=engine, schema=schema)
    except Exception as exc:
        raise DBError(f"Could not load table {table!r}: {exc}")


def _pk_columns(tbl):
    cols = list(tbl.primary_key.columns)
    if not cols:
        raise DBError(f"Table {tbl.name!r} has no primary key; "
                      f"row edit by key is not possible.")
    return cols


def _check_columns(tbl, values):
    unknown = [c for c in values if c not in tbl.c]
    if unknown:
        raise DBError(f"Unknown column(s) for {tbl.name!r}: "
                      f"{', '.join(map(str, unknown))}")


def insert_row(engine, table, values, schema=None):
    """Insert one row from a ``{column: value}`` dict. Returns rows affected (1)."""
    if not values:
        raise DBError("insert_row needs at least one column value.")
    tbl = _reflect(engine, table, schema=schema)
    _check_columns(tbl, values)
    try:
        with engine.begin() as conn:
            result = conn.execute(sa.insert(tbl).values(**values))
        return result.rowcount if result.rowcount is not None else 1
    except sa.exc.SQLAlchemyError as exc:
        raise DBError(_msg(exc))


def update_row(engine, table, key, values, schema=None):
    """Update the row identified by the ``{pk_col: value}`` *key* mapping.

    Returns the number of rows affected (0 if the key matched nothing).
    """
    if not values:
        raise DBError("update_row needs at least one column to change.")
    tbl = _reflect(engine, table, schema=schema)
    _check_columns(tbl, values)
    where = _key_clause(tbl, key)
    try:
        with engine.begin() as conn:
            result = conn.execute(sa.update(tbl).where(where).values(**values))
        return result.rowcount if result.rowcount is not None else 0
    except sa.exc.SQLAlchemyError as exc:
        raise DBError(_msg(exc))


def delete_row(engine, table, key, schema=None):
    """Delete the row identified by the ``{pk_col: value}`` *key* mapping."""
    tbl = _reflect(engine, table, schema=schema)
    where = _key_clause(tbl, key)
    try:
        with engine.begin() as conn:
            result = conn.execute(sa.delete(tbl).where(where))
        return result.rowcount if result.rowcount is not None else 0
    except sa.exc.SQLAlchemyError as exc:
        raise DBError(_msg(exc))


def _key_clause(tbl, key):
    """Build an AND-ed WHERE clause covering the whole primary key."""
    pk_cols = _pk_columns(tbl)
    if not key:
        raise DBError("A primary-key value mapping is required.")
    _check_columns(tbl, key)
    pk_names = {c.name for c in pk_cols}
    missing = [c.name for c in pk_cols if c.name not in key]
    if missing:
        raise DBError(f"Missing primary-key value(s): {', '.join(missing)}")
    extra = [k for k in key if k not in pk_names]
    if extra:
        raise DBError(f"Key contains non-primary-key column(s): "
                      f"{', '.join(map(str, extra))}")
    clause = sa.and_(*[tbl.c[c.name] == key[c.name] for c in pk_cols])
    return clause


def _msg(exc):
    orig = getattr(exc, "orig", None)
    text = str(orig or exc).strip()
    return text.splitlines()[0] if text else exc.__class__.__name__
