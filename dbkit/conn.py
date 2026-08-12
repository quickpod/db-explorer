"""Connection profiles and SQLAlchemy engine creation for DBExplorer.

A *profile* is a small JSON-serialisable dict describing one database:

    {"name": "sales", "kind": "sqlite", "path": "/data/sales.db"}
    {"name": "prod",  "kind": "url",
     "url": "postgresql+psycopg2://app@db.internal/prod",
     "store_password": false}

Profiles live in ``profiles.json`` inside the same per-user config directory the
GUI uses (``%LOCALAPPDATA%\\DBExplorer`` on Windows, ``~/.dbexplorer`` else).

Password policy: we NEVER write a password to disk unless the user explicitly
opts in with ``store_password=True``.  Otherwise the URL is kept without its
password and :func:`connect` must be handed one at call time (the CLI/GUI
prompt for it).  SQLite always works with no extra drivers; Postgres/MySQL
need psycopg2 / pymysql, and a missing driver is reported as a clean
:class:`DBError` telling the user exactly what to install.
"""

from __future__ import annotations

import json
import os

import sqlalchemy as sa
from sqlalchemy.engine import make_url

from .errors import DBError
from . import guiconfig

PROFILES_NAME = "profiles.json"

# dialect (part before the '+') -> (pip package, import name) for the error hint.
_DRIVER_HINTS = {
    "postgresql": ("psycopg2-binary", "psycopg2"),
    "postgres": ("psycopg2-binary", "psycopg2"),
    "mysql": ("PyMySQL", "pymysql"),
    "mariadb": ("PyMySQL", "pymysql"),
}


def profiles_path():
    return os.path.join(guiconfig.config_dir(), PROFILES_NAME)


def load_profiles():
    """Return a ``{name: profile}`` dict (empty if the file is missing/corrupt)."""
    try:
        with open(profiles_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    except Exception as exc:
        raise DBError(f"Could not read profiles file: {exc}")
    out = {}
    if isinstance(data, dict):
        for name, prof in data.items():
            if isinstance(prof, dict):
                prof = dict(prof)
                prof["name"] = name
                out[name] = prof
    return out


def save_profiles(profiles):
    """Persist the ``{name: profile}`` mapping atomically."""
    try:
        os.makedirs(guiconfig.config_dir(), exist_ok=True)
        clean = {}
        for name, prof in profiles.items():
            p = {k: v for k, v in prof.items() if k != "name"}
            clean[name] = p
        tmp = profiles_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(clean, fh, indent=2)
        os.replace(tmp, profiles_path())
    except OSError as exc:
        raise DBError(f"Could not write profiles file: {exc}")


def list_profiles():
    """Return profiles as a list, sorted by name."""
    return [load_profiles()[n] for n in sorted(load_profiles())]


def get_profile(name):
    profiles = load_profiles()
    if name not in profiles:
        raise DBError(f"No connection profile named {name!r}. "
                      f"Use 'profiles add' to create one.")
    return profiles[name]


def make_profile(name, kind, path=None, url=None, store_password=False):
    """Validate inputs and return a normalised profile dict (not saved).

    ``kind='sqlite'`` needs ``path``; ``kind='url'`` needs ``url``.  For a URL
    profile the password is stripped from what we keep unless ``store_password``.
    """
    if not name or not str(name).strip():
        raise DBError("A profile name is required.")
    name = str(name).strip()
    if kind == "sqlite":
        if not path:
            raise DBError("A SQLite profile needs a database file path.")
        return {"name": name, "kind": "sqlite", "path": os.path.abspath(path)}
    if kind == "url":
        if not url:
            raise DBError("A URL profile needs a database URL "
                          "(e.g. postgresql+psycopg2://user@host/db).")
        try:
            parsed = make_url(url)
        except Exception as exc:
            raise DBError(f"Invalid database URL: {exc}")
        store_password = bool(store_password)
        if not store_password and parsed.password:
            # URL.set(password=None) keeps the old value, so rebuild without it.
            parsed = sa.engine.URL.create(
                drivername=parsed.drivername, username=parsed.username,
                password=None, host=parsed.host, port=parsed.port,
                database=parsed.database, query=dict(parsed.query))
        return {
            "name": name,
            "kind": "url",
            "url": parsed.render_as_string(hide_password=False),
            "store_password": store_password,
        }
    raise DBError(f"Unknown profile kind {kind!r} (expected 'sqlite' or 'url').")


def add_profile(name, kind, path=None, url=None, store_password=False,
                overwrite=True):
    """Create/replace a profile and persist it. Returns the stored profile."""
    prof = make_profile(name, kind, path=path, url=url,
                        store_password=store_password)
    profiles = load_profiles()
    if name in profiles and not overwrite:
        raise DBError(f"A profile named {name!r} already exists.")
    profiles[name] = prof
    save_profiles(profiles)
    return prof


def remove_profile(name):
    profiles = load_profiles()
    if name not in profiles:
        raise DBError(f"No connection profile named {name!r}.")
    del profiles[name]
    save_profiles(profiles)


def _dialect_name(profile):
    """Best-effort dialect (the bit before '+') for a profile's URL."""
    if profile.get("kind") == "sqlite":
        return "sqlite"
    try:
        return make_url(profile["url"]).get_backend_name()
    except Exception:
        return ""


def needs_password(profile):
    """True when a URL profile has a username but no stored password."""
    if profile.get("kind") != "url":
        return False
    try:
        u = make_url(profile["url"])
    except Exception:
        return False
    return bool(u.username) and not u.password


def profile_url(profile, password=None):
    """Return the SQLAlchemy URL object for *profile*, injecting *password*."""
    kind = profile.get("kind")
    if kind == "sqlite":
        path = profile.get("path")
        if not path:
            raise DBError("SQLite profile is missing its file path.")
        return sa.engine.URL.create("sqlite", database=path)
    if kind == "url":
        raw = profile.get("url")
        if not raw:
            raise DBError("URL profile is missing its database URL.")
        try:
            u = make_url(raw)
        except Exception as exc:
            raise DBError(f"Invalid database URL: {exc}")
        if password is not None and password != "":
            u = u.set(password=password)
        return u
    raise DBError(f"Unknown profile kind {kind!r}.")


def connect(profile, password=None):
    """Return a live SQLAlchemy :class:`~sqlalchemy.engine.Engine` for *profile*.

    Raises :class:`DBError` (never a raw driver traceback) when the dialect or
    its DB-API driver is unavailable, telling the user what to ``pip install``.
    """
    url = profile_url(profile, password=password)
    try:
        engine = sa.create_engine(url)
    except ModuleNotFoundError as exc:
        dialect = _dialect_name(profile)
        pkg, mod = _DRIVER_HINTS.get(dialect, (None, None))
        if pkg:
            raise DBError(
                f"The {dialect} driver ({mod}) is not installed. "
                f"Install it with:  pip install {pkg}")
        raise DBError(f"A database driver is missing: {exc}. "
                      f"Install the appropriate DB-API package.")
    except sa.exc.NoSuchModuleError as exc:
        raise DBError(f"Unknown or unsupported database dialect: {exc}")
    except Exception as exc:
        raise DBError(f"Could not create the database engine: {exc}")
    return engine


def test_connection(profile, password=None):
    """Open and immediately close a connection; raise DBError on failure."""
    engine = connect(profile, password=password)
    try:
        with engine.connect():
            pass
    except DBError:
        raise
    except Exception as exc:
        raise DBError(f"Could not connect: {exc}")
    finally:
        engine.dispose()
    return True
