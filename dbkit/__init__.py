"""dbkit -- the offline SQL client engine behind DBExplorer.

A thin, well-tested layer over SQLAlchemy: connection *profiles* (:mod:`.conn`),
schema :mod:`.introspect`ion, :mod:`.query` execution + export, single-row
:mod:`.edit`s, and an :mod:`.erd` model builder.  Every expected failure is
raised as :class:`DBError` so the CLI and GUI show a clean one-line message
instead of a traceback.

100% AI-built, open source, published on QuickOpen (quickopen.ai). Apache-2.0.
"""

from __future__ import annotations

from .errors import DBError

from .conn import (
    add_profile,
    connect,
    get_profile,
    list_profiles,
    load_profiles,
    make_profile,
    needs_password,
    profile_url,
    remove_profile,
    save_profiles,
    test_connection,
)
from .introspect import (
    get_columns,
    get_foreign_keys,
    get_indexes,
    get_primary_key,
    list_schemas,
    list_tables,
    list_views,
    table_ddl,
    table_info,
)
from .query import (
    count_rows,
    export_result,
    fetch_table,
    run_in_transaction,
    run_sql,
)
from .edit import delete_row, insert_row, update_row
from .erd import build_erd, erd_summary

__version__ = "1.0.0"

__all__ = [
    "DBError",
    "__version__",
    # conn
    "add_profile", "connect", "get_profile", "list_profiles", "load_profiles",
    "make_profile", "needs_password", "profile_url", "remove_profile",
    "save_profiles", "test_connection",
    # introspect
    "get_columns", "get_foreign_keys", "get_indexes", "get_primary_key",
    "list_schemas", "list_tables", "list_views", "table_ddl", "table_info",
    # query
    "count_rows", "export_result", "fetch_table", "run_in_transaction", "run_sql",
    # edit
    "delete_row", "insert_row", "update_row",
    # erd
    "build_erd", "erd_summary",
]
