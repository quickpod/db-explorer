"""Connection profiles, engine creation, and the missing-driver error path."""

from __future__ import annotations

import pytest

from dbkit import (
    DBError,
    add_profile,
    connect,
    get_profile,
    list_profiles,
    make_profile,
    needs_password,
    remove_profile,
)
from dbkit import conn


def test_sqlite_profile_roundtrip_and_connect(sqlite_path):
    prof = add_profile("local", "sqlite", path=sqlite_path)
    assert prof["kind"] == "sqlite"
    assert get_profile("local")["path"] == prof["path"]
    engine = connect(prof)
    try:
        with engine.connect() as c:
            assert c.exec_driver_sql("SELECT 1").scalar() == 1
    finally:
        engine.dispose()


def test_list_and_remove_profiles(sqlite_path):
    add_profile("a", "sqlite", path=sqlite_path)
    add_profile("b", "sqlite", path=sqlite_path)
    names = [p["name"] for p in list_profiles()]
    assert names == ["a", "b"]  # sorted
    remove_profile("a")
    assert [p["name"] for p in list_profiles()] == ["b"]
    with pytest.raises(DBError):
        remove_profile("a")


def test_password_never_stored_unless_optin():
    prof = make_profile("pg", "url",
                        url="postgresql+psycopg2://user:secret@host/db")
    assert "secret" not in prof["url"]
    assert needs_password(prof) is True

    kept = make_profile("pg2", "url",
                        url="postgresql+psycopg2://user:secret@host/db",
                        store_password=True)
    assert "secret" in kept["url"]
    assert needs_password(kept) is False


def test_get_missing_profile_raises():
    with pytest.raises(DBError):
        get_profile("nope")


def test_missing_optional_driver_raises_clean_dberror():
    prof = make_profile("pg", "url",
                        url="postgresql+psycopg2://user@host/db")
    # psycopg2 is not installed in the test environment.
    with pytest.raises(DBError) as ei:
        connect(prof)
    msg = str(ei.value)
    assert "psycopg2" in msg
    assert "pip install" in msg


def test_unknown_kind_raises():
    with pytest.raises(DBError):
        make_profile("x", "mongodb", url="mongodb://x")
