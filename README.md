# DBExplorer

A fast, **offline**, **100% open-source** universal SQL database client for Windows. Nothing is uploaded anywhere. Built entirely by AI with human testing and guidance, and published on [QuickOpen](https://quickopen.ai/projects/db-explorer).

> **100% AI-built and open source.** Apache-2.0.

## What it does

Connect to SQLite, PostgreSQL and MySQL databases; browse tables and schemas, run SQL with a results grid, edit rows, view an ER diagram, and export query results to CSV/JSON. Connection profiles are stored locally; queries never leave your machine.

## Install

Download **`DBExplorer-Setup.exe`** from the [QuickOpen page](https://quickopen.ai/projects/db-explorer) or the [GitHub release](https://github.com/quickpod/db-explorer/releases/latest) and double-click it. It installs per-user, adds Desktop and Start Menu shortcuts, and can optionally trust the QuickOpen Root CA. Authenticode-signed by the QuickOpen Code Signing CA — verify at [quickopen.ai/trust](https://quickopen.ai/trust).

## Run from source

```sh
pip install -r requirements.txt
python db_explorer_app.py          # GUI
python -m dbkit --help    # CLI
```


## Features

- **Universal, offline, permissive.** One SQLite/PostgreSQL/MySQL client built on SQLAlchemy; SQLite works out of the box, Postgres/MySQL need only `psycopg2` / `pymysql` and DBExplorer tells you exactly what to install when a driver is missing.
- **Connection profiles.** Save SQLite files or database URLs as named profiles in a per-user config dir. Passwords are **never** written to disk unless you opt in — otherwise the URL is stored without its password and you are prompted when connecting.
- **Schema browser.** List schemas, tables and views; inspect columns, primary keys, indexes and foreign keys; view a reflected `CREATE TABLE` statement.
- **Data grid with paging + row editing.** Open a table and page through its rows; edit or delete a row safely by primary key (fully parameterised — no string-built SQL).
- **SQL editor.** Run any read or write statement with a bound-parameter-safe core, see a results grid with elapsed time and row counts, and export results to CSV or JSON.
- **ER model.** Build a tables-and-foreign-keys model rendered as a dependency-free text/ASCII summary — no Graphviz required.
- **Desktop GUI, house style.** Pure-stdlib tkinter with a left sidebar, a dark-mode toggle in the QuickOpen palette, threaded operations that keep the UI responsive, and inline error messages (never raw tracebacks).

## CLI examples

```sh
# Manage connection profiles (stored per-user; passwords opt-in only)
python -m dbkit profiles add sales --path /data/sales.db
python -m dbkit profiles add prod  --url "postgresql+psycopg2://app@db.internal/prod"
python -m dbkit profiles list
python -m dbkit profiles remove sales

# Explore a database
python -m dbkit tables sales
python -m dbkit schema sales books
python -m dbkit ddl    sales books
python -m dbkit erd    sales

# Run SQL (table | csv | json output; --limit caps rows, 0 = no limit)
python -m dbkit query sales "SELECT title, year FROM books ORDER BY year" --limit 50
python -m dbkit query sales "SELECT * FROM authors" --format json

# Export query results to a file
python -m dbkit export sales "SELECT * FROM books" books.csv  --format csv
python -m dbkit export sales "SELECT * FROM books" books.json --format json
```

Every command exits non-zero and prints a single `error: …` line (no traceback) on failure. Where a profile needs a password, pass `--password` or set `DBEXPLORER_PASSWORD`.

## License

Apache-2.0 — see [LICENSE](LICENSE). A 100% AI-built project published on QuickOpen.
