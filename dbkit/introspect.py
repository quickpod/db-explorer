"""Schema introspection built on SQLAlchemy's :func:`sqlalchemy.inspect`.

Everything here is read-only and returns plain Python data structures (lists /
dicts of strings) that the CLI can print and the GUI can drop straight into a
tree/grid.  Any driver or reflection failure is normalised to :class:`DBError`.
"""

from __future__ import annotations

import sqlalchemy as sa

from .errors import DBError


def _inspector(engine):
    try:
        return sa.inspect(engine)
    except Exception as exc:
        raise DBError(f"Could not inspect the database: {exc}")


def list_schemas(engine):
    """Return available schema names (empty list if the backend has no schemas)."""
    insp = _inspector(engine)
    try:
        return list(insp.get_schema_names())
    except Exception:
        return []


def list_tables(engine, schema=None):
    insp = _inspector(engine)
    try:
        return list(insp.get_table_names(schema=schema))
    except Exception as exc:
        raise DBError(f"Could not list tables: {exc}")


def list_views(engine, schema=None):
    insp = _inspector(engine)
    try:
        return list(insp.get_view_names(schema=schema))
    except Exception:
        return []


def get_columns(engine, table, schema=None):
    """Return a list of ``{name, type, nullable, default, primary_key}`` dicts."""
    insp = _inspector(engine)
    try:
        cols = insp.get_columns(table, schema=schema)
        pk = set(get_primary_key(engine, table, schema=schema))
    except DBError:
        raise
    except Exception as exc:
        raise DBError(f"Could not read columns of {table!r}: {exc}")
    out = []
    for c in cols:
        out.append({
            "name": c.get("name"),
            "type": str(c.get("type")),
            "nullable": bool(c.get("nullable", True)),
            "default": c.get("default"),
            "primary_key": c.get("name") in pk,
        })
    return out


def get_primary_key(engine, table, schema=None):
    """Return the ordered list of primary-key column names (possibly empty)."""
    insp = _inspector(engine)
    try:
        pk = insp.get_pk_constraint(table, schema=schema)
        return list(pk.get("constrained_columns") or [])
    except Exception:
        return []


def get_indexes(engine, table, schema=None):
    insp = _inspector(engine)
    try:
        return [
            {"name": ix.get("name"),
             "columns": list(ix.get("column_names") or []),
             "unique": bool(ix.get("unique"))}
            for ix in insp.get_indexes(table, schema=schema)
        ]
    except Exception:
        return []


def get_foreign_keys(engine, table, schema=None):
    """Return FK dicts: ``{name, columns, referred_table, referred_columns}``."""
    insp = _inspector(engine)
    try:
        fks = insp.get_foreign_keys(table, schema=schema)
    except Exception:
        return []
    out = []
    for fk in fks:
        out.append({
            "name": fk.get("name"),
            "columns": list(fk.get("constrained_columns") or []),
            "referred_table": fk.get("referred_table"),
            "referred_schema": fk.get("referred_schema"),
            "referred_columns": list(fk.get("referred_columns") or []),
        })
    return out


def table_info(engine, table, schema=None):
    """Bundle columns, primary key, indexes and foreign keys for one table."""
    return {
        "name": table,
        "schema": schema,
        "columns": get_columns(engine, table, schema=schema),
        "primary_key": get_primary_key(engine, table, schema=schema),
        "indexes": get_indexes(engine, table, schema=schema),
        "foreign_keys": get_foreign_keys(engine, table, schema=schema),
    }


def table_ddl(engine, table, schema=None):
    """Return a ``CREATE TABLE`` statement reflected from the live schema."""
    from sqlalchemy.schema import CreateTable
    try:
        md = sa.MetaData()
        tbl = sa.Table(table, md, autoload_with=engine, schema=schema)
    except Exception as exc:
        raise DBError(f"Could not reflect table {table!r}: {exc}")
    try:
        return str(CreateTable(tbl).compile(engine)).strip()
    except Exception as exc:
        raise DBError(f"Could not render DDL for {table!r}: {exc}")
