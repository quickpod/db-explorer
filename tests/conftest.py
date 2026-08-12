"""Shared fixtures: an isolated config dir and a populated tmp SQLite database."""

from __future__ import annotations

import os

import pytest
import sqlalchemy as sa


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Point dbkit's per-user config dir at a throwaway tmp directory.

    Every test gets a clean profiles/config store and never touches the real
    ``~/.dbexplorer`` (or ``%LOCALAPPDATA%``).
    """
    cfg = tmp_path / "config"
    cfg.mkdir()
    from dbkit import guiconfig
    monkeypatch.setattr(guiconfig, "config_dir", lambda: str(cfg))
    return cfg


@pytest.fixture
def sqlite_url(tmp_path):
    return f"sqlite:///{tmp_path / 'test.db'}"


@pytest.fixture
def sqlite_path(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture
def populated_engine(sqlite_url):
    """A SQLite engine with authors + books (books.author_id -> authors.id)."""
    engine = sa.create_engine(sqlite_url)
    md = sa.MetaData()
    authors = sa.Table(
        "authors", md,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("country", sa.String(50)),
    )
    books = sa.Table(
        "books", md,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("year", sa.Integer),
        sa.Column("author_id", sa.Integer, sa.ForeignKey("authors.id")),
    )
    md.create_all(engine)
    with engine.begin() as conn:
        conn.execute(authors.insert(), [
            {"id": 1, "name": "Ada", "country": "UK"},
            {"id": 2, "name": "Grace", "country": "US"},
        ])
        conn.execute(books.insert(), [
            {"id": 1, "title": "Notes", "year": 1843, "author_id": 1},
            {"id": 2, "title": "Compilers", "year": 1952, "author_id": 2},
            {"id": 3, "title": "Loops", "year": 1959, "author_id": 2},
        ])
    yield engine
    engine.dispose()
