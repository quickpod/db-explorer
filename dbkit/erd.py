"""Build an entity-relationship model as plain data for the GUI/CLI to render.

No Graphviz or other native dependency: :func:`build_erd` returns tables (with
their columns and primary keys) plus foreign-key *edges*, and
:func:`erd_summary` renders a dependency-free ASCII/text overview from it.
"""

from __future__ import annotations

from .errors import DBError
from . import introspect


def build_erd(engine, schema=None):
    """Return an ER model:

        {"schema": schema,
         "tables": [{"name", "columns": [...], "primary_key": [...]}...],
         "edges":  [{"source", "source_columns", "target",
                     "target_columns", "name"}...]}

    Each edge is one foreign key: ``source`` references ``target``.
    """
    try:
        table_names = introspect.list_tables(engine, schema=schema)
    except DBError:
        raise
    except Exception as exc:
        raise DBError(f"Could not build ER model: {exc}")

    tables = []
    edges = []
    known = set(table_names)
    for name in table_names:
        cols = introspect.get_columns(engine, name, schema=schema)
        pk = introspect.get_primary_key(engine, name, schema=schema)
        tables.append({
            "name": name,
            "columns": [{"name": c["name"], "type": c["type"]} for c in cols],
            "primary_key": pk,
        })
        for fk in introspect.get_foreign_keys(engine, name, schema=schema):
            target = fk.get("referred_table")
            edges.append({
                "name": fk.get("name"),
                "source": name,
                "source_columns": fk.get("columns", []),
                "target": target,
                "target_columns": fk.get("referred_columns", []),
                "target_missing": target not in known,
            })
    return {"schema": schema, "tables": tables, "edges": edges}


def erd_summary(model):
    """Render an ER *model* (from :func:`build_erd`) as a plain-text summary."""
    if not isinstance(model, dict):
        raise DBError("erd_summary expects the dict from build_erd().")
    lines = []
    tables = model.get("tables", [])
    edges = model.get("edges", [])
    lines.append(f"Tables: {len(tables)}   Relationships: {len(edges)}")
    lines.append("")
    for tbl in tables:
        pk = set(tbl.get("primary_key") or [])
        lines.append(f"[{tbl['name']}]")
        for col in tbl.get("columns", []):
            marker = " *PK" if col["name"] in pk else ""
            lines.append(f"    - {col['name']} : {col['type']}{marker}")
        lines.append("")
    if edges:
        lines.append("Relationships (foreign keys):")
        for e in edges:
            src = f"{e['source']}({', '.join(e.get('source_columns') or [])})"
            tgt = f"{e['target']}({', '.join(e.get('target_columns') or [])})"
            lines.append(f"    {src}  ->  {tgt}")
    else:
        lines.append("Relationships: none found.")
    return "\n".join(lines)
