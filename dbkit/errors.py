"""Error types for dbkit."""


class DBError(Exception):
    """Raised for any recoverable failure in a dbkit operation.

    Every public function raises this (and only this) on an expected failure so
    that callers -- the CLI and the tkinter GUI -- have a single exception to
    catch and can show a clean one-line message instead of a traceback.
    """
