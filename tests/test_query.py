"""Query execution, parameterisation and CSV/JSON export."""

from __future__ import annotations

import csv
import json

import pytest

from dbkit import DBError, export_result, run_sql


def test_select_returns_rows_and_rowcount(populated_engine):
    res = run_sql(populated_engine, "SELECT id, name FROM authors ORDER BY id")
    assert res["columns"] == ["id", "name"]
    assert res["rows"] == [[1, "Ada"], [2, "Grace"]]
    assert res["rowcount"] == 2
    assert res["elapsed"] >= 0


def test_limit_truncates(populated_engine):
    res = run_sql(populated_engine, "SELECT id FROM books ORDER BY id", limit=2)
    assert res["rowcount"] == 2


def test_parameterized_query(populated_engine):
    res = run_sql(populated_engine,
                  "SELECT title FROM books WHERE author_id = :aid ORDER BY id",
                  params={"aid": 2})
    assert res["rows"] == [["Compilers"], ["Loops"]]


def test_write_reports_affected_rows(populated_engine):
    res = run_sql(populated_engine,
                  "UPDATE authors SET country = :c WHERE id = :i",
                  params={"c": "GB", "i": 1})
    assert res["rowcount"] == 1
    check = run_sql(populated_engine, "SELECT country FROM authors WHERE id = 1")
    assert check["rows"] == [["GB"]]


def test_bad_sql_raises_clean_dberror(populated_engine):
    with pytest.raises(DBError):
        run_sql(populated_engine, "SELECT * FROM does_not_exist")


def test_export_csv(populated_engine, tmp_path):
    res = run_sql(populated_engine, "SELECT id, name FROM authors ORDER BY id")
    out = tmp_path / "a.csv"
    n = export_result(res, str(out), fmt="csv")
    assert n == 2
    rows = list(csv.reader(out.open()))
    assert rows[0] == ["id", "name"]
    assert rows[1] == ["1", "Ada"]


def test_export_json(populated_engine, tmp_path):
    res = run_sql(populated_engine, "SELECT id, name FROM authors ORDER BY id")
    out = tmp_path / "a.json"
    export_result(res, str(out), fmt="json")
    data = json.load(out.open())
    assert data == [{"id": 1, "name": "Ada"}, {"id": 2, "name": "Grace"}]


def test_export_bad_format_raises(populated_engine, tmp_path):
    res = run_sql(populated_engine, "SELECT 1 AS x")
    with pytest.raises(DBError):
        export_result(res, str(tmp_path / "x.xml"), fmt="xml")
