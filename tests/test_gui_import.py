"""The GUI must import with no side effects and run headless without raising."""

from __future__ import annotations

import os


def test_import_is_side_effect_free():
    from dbkit import gui
    assert hasattr(gui, "main")
    assert hasattr(gui, "build_app")


def test_main_headless_returns_zero(monkeypatch):
    # Ensure no display is available so main() hits the friendly-degrade path.
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setenv("DISPLAY", "")
    from dbkit import gui
    assert gui.main() == 0
