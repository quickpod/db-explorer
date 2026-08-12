"""Schema introspection against the populated SQLite fixture."""

from __future__ import annotations

from dbkit import (
    get_columns,
    get_foreign_keys,
    get_primary_key,
    list_tables,
    table_ddl,
    table_info,
)


def test_list_tables(populated_engine):
    tables = list_tables(populated_engine)
    assert set(tables) == {"authors", "books"}


def test_columns_and_primary_key(populated_engine):
    cols = get_columns(populated_engine, "books")
    names = [c["name"] for c in cols]
    assert names == ["id", "title", "year", "author_id"]
    idcol = next(c for c in cols if c["name"] == "id")
    assert idcol["primary_key"] is True
    assert get_primary_key(populated_engine, "books") == ["id"]


def test_foreign_keys(populated_engine):
    fks = get_foreign_keys(populated_engine, "books")
    assert len(fks) == 1
    fk = fks[0]
    assert fk["columns"] == ["author_id"]
    assert fk["referred_table"] == "authors"
    assert fk["referred_columns"] == ["id"]


def test_table_ddl_and_info(populated_engine):
    ddl = table_ddl(populated_engine, "authors")
    assert "CREATE TABLE" in ddl
    assert "authors" in ddl
    info = table_info(populated_engine, "authors")
    assert info["primary_key"] == ["id"]
