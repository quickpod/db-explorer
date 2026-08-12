"""ERD model + summary."""

from __future__ import annotations

from dbkit import build_erd, erd_summary


def test_erd_contains_fk_edge(populated_engine):
    model = build_erd(populated_engine)
    names = {t["name"] for t in model["tables"]}
    assert names == {"authors", "books"}
    edges = model["edges"]
    assert len(edges) == 1
    edge = edges[0]
    assert edge["source"] == "books"
    assert edge["source_columns"] == ["author_id"]
    assert edge["target"] == "authors"
    assert edge["target_columns"] == ["id"]
    assert edge["target_missing"] is False


def test_erd_summary_text(populated_engine):
    text = erd_summary(build_erd(populated_engine))
    assert "books" in text
    assert "authors" in text
    assert "->" in text
