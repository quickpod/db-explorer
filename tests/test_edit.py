"""Insert / update / delete round-trip by primary key."""

from __future__ import annotations

import pytest

from dbkit import DBError, delete_row, insert_row, run_sql, update_row


def _titles(engine):
    return [r[0] for r in run_sql(engine, "SELECT title FROM books ORDER BY id")["rows"]]


def test_insert_update_delete_roundtrip(populated_engine):
    eng = populated_engine

    n = insert_row(eng, "books",
                   {"id": 99, "title": "New", "year": 2000, "author_id": 1})
    assert n == 1
    got = run_sql(eng, "SELECT title, year FROM books WHERE id = 99")
    assert got["rows"] == [["New", 2000]]

    n = update_row(eng, "books", {"id": 99}, {"title": "Renamed", "year": 2001})
    assert n == 1
    got = run_sql(eng, "SELECT title, year FROM books WHERE id = 99")
    assert got["rows"] == [["Renamed", 2001]]

    n = delete_row(eng, "books", {"id": 99})
    assert n == 1
    assert run_sql(eng, "SELECT * FROM books WHERE id = 99")["rowcount"] == 0


def test_update_unknown_column_raises(populated_engine):
    with pytest.raises(DBError):
        update_row(populated_engine, "books", {"id": 1}, {"nope": 1})


def test_key_must_be_primary_key(populated_engine):
    with pytest.raises(DBError):
        update_row(populated_engine, "books", {"title": "Notes"}, {"year": 1})


def test_delete_missing_key_affects_zero(populated_engine):
    assert delete_row(populated_engine, "books", {"id": 123456}) == 0
    assert len(_titles(populated_engine)) == 3
