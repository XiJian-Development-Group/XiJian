"""SQLite-backed dict-like storage with write-through cache.

Design
------
Every ``DictDB`` instance keeps an in-memory ``_cache: dict`` that mirrors
the SQLite table.  Reads first check the cache (fast path, returns the
same object reference), then fall back to SQLite.  Writes update both
the cache and SQLite atomically.

This is critical for compatibility with existing stub code that does::

    record = state.npcs[npc_id]
    record["field"] = "new_value"   # mutates the cached dict in-place

With a pure SQLite backend each read deserialises a new dict, so the
in-place mutation would be lost.  The cache ensures the same dict object
is returned on subsequent reads and auto-persisted on explicit writes.

The cache is **write-through**, not write-back — every ``__setitem__``
immediately commits to SQLite.  The cache only shadows the SQLite store
so repeated reads of the same key return the same object.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from collections.abc import Iterator, MutableMapping
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)

#: Default database location.
DEFAULT_DB_DIR = Path.home() / ".xijian"
DEFAULT_DB_PATH = DEFAULT_DB_DIR / "xijian.db"
ENV_DB_PATH = "XIJIAN_DB_PATH"


def _db_path() -> str:
    return os.environ.get(ENV_DB_PATH, str(DEFAULT_DB_PATH))


# ---------------------------------------------------------------------------
# Thread-local connection management
# ---------------------------------------------------------------------------

_tl = threading.local()


def _connection() -> sqlite3.Connection:
    db_path = _db_path()
    conn = getattr(_tl, "conn", None)
    if conn is None:
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.row_factory = sqlite3.Row
        _tl.conn = conn
    return conn


def close_connections() -> None:
    conn = getattr(_tl, "conn", None)
    if conn is not None:
        conn.close()
        _tl.conn = None


# ---------------------------------------------------------------------------
# DictDB — dict-like interface over a SQLite table with write-through cache
# ---------------------------------------------------------------------------


class DictDB(MutableMapping[str, Any]):
    """A dict backed by a SQLite table with an in-memory write-through cache.

    Parameters
    ----------
    bucket:
        Logical name (e.g. ``"characters"``).  Backing table is
        ``store_{bucket}``.
    """

    def __init__(self, bucket: str):
        self._bucket = bucket
        self._table = f"store_{bucket}"
        self._cache: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._ensure_table()
        # Warm the cache from SQLite (populate on first access).
        self._load_all()

    def _connection(self) -> sqlite3.Connection:
        return _connection()

    def _ensure_table(self) -> None:
        self._connection().execute(
            f"CREATE TABLE IF NOT EXISTS {self._table} ("
            "key TEXT PRIMARY KEY,"
            "value TEXT NOT NULL,"
            "created_at INTEGER NOT NULL DEFAULT (unixepoch()),"
            "updated_at INTEGER NOT NULL DEFAULT (unixepoch())"
            ")"
        )

    def _load_all(self) -> None:
        """Load all key-value pairs from SQLite into the cache."""
        for row in self._connection().execute(
            f"SELECT key, value FROM {self._table}"
        ):
            try:
                self._cache[row["key"]] = json.loads(row["value"])
            except (json.JSONDecodeError, Exception):
                pass

    @staticmethod
    def _normalise_key(key: object) -> str:
        if isinstance(key, str):
            return key
        return json.dumps(key, ensure_ascii=False, sort_keys=True)

    # ---- MutableMapping abstract methods ----

    def __getitem__(self, key: str) -> Any:
        nk = self._normalise_key(key)
        with self._lock:
            cached = self._cache.get(nk)
            if cached is not None:
                return cached
        # Fallback to SQLite (should only happen for keys written by
        # a different thread / process).
        row = self._connection().execute(
            f"SELECT value FROM {self._table} WHERE key = ?", (nk,)
        ).fetchone()
        if row is None:
            raise KeyError(key)
        value = json.loads(row["value"])
        with self._lock:
            self._cache[nk] = value
        return value

    def __setitem__(self, key: str, value: Any) -> None:
        nk = self._normalise_key(key)
        encoded = json.dumps(value, ensure_ascii=False, default=str)
        now = int(__import__("time").time())
        with self._lock:
            self._cache[nk] = value
        self._connection().execute(
            f"INSERT INTO {self._table} (key, value, created_at, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
            "updated_at=excluded.updated_at",
            (nk, encoded, now, now),
        )
        self._connection().commit()

    def __delitem__(self, key: str) -> None:
        nk = self._normalise_key(key)
        with self._lock:
            self._cache.pop(nk, None)
        cur = self._connection().execute(
            f"DELETE FROM {self._table} WHERE key = ?", (nk,)
        )
        self._connection().commit()
        if cur.rowcount == 0:
            raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        with self._lock:
            keys = list(self._cache.keys())
        yield from keys

    def __len__(self) -> int:
        with self._lock:
            return len(self._cache)

    # ---- overridden for performance ----

    def __contains__(self, key: object) -> bool:
        nk = self._normalise_key(key)
        with self._lock:
            return nk in self._cache

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self.__getitem__(key)
        except KeyError:
            return default

    def pop(self, key: str, *args: Any) -> Any:
        nk = self._normalise_key(key)
        with self._lock:
            cached = self._cache.pop(nk, None)
            if cached is not None:
                self._connection().execute(
                    f"DELETE FROM {self._table} WHERE key = ?", (nk,)
                )
                self._connection().commit()
                return cached
        if args:
            return args[0]
        raise KeyError(key)

    def values(self) -> list[Any]:
        with self._lock:
            return list(self._cache.values())

    def items(self) -> list[tuple[str, Any]]:
        with self._lock:
            return list(self._cache.items())

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
        self._connection().execute(f"DELETE FROM {self._table}")
        self._connection().commit()

    def update(self, other: dict[str, Any]) -> None:
        for k, v in other.items():
            self.__setitem__(k, v)

    def keys(self) -> list[str]:
        with self._lock:
            return list(self._cache.keys())

    # ---- introspection ----

    @property
    def bucket(self) -> str:
        return self._bucket

    def __repr__(self) -> str:
        with self._lock:
            return f"<DictDB bucket={self._bucket!r} len={len(self._cache)}>"

    # ---- test helpers ----

    def _invalidate_cache(self) -> None:
        """Drop the in-memory cache and reload from SQLite.  Test-only."""
        with self._lock:
            self._cache.clear()
        self._load_all()


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_store_registry: dict[str, DictDB] = {}
_registry_lock = threading.Lock()


def bucket(name: str) -> DictDB:
    """Return (or create) a DictDB for the given bucket name."""
    with _registry_lock:
        if name not in _store_registry:
            _store_registry[name] = DictDB(name)
        return _store_registry[name]


def reset_registry() -> None:
    """Drop all store tables and clear the registry.  Test-only."""
    conn = _connection()
    with _registry_lock:
        for name in list(_store_registry.keys()):
            try:
                conn.execute(f"DROP TABLE IF EXISTS store_{name}")
            except Exception:
                pass
            _store_registry.pop(name, None)
    conn.commit()
    close_connections()


__all__ = [
    "DictDB",
    "bucket",
    "reset_registry",
    "close_connections",
    "DEFAULT_DB_PATH",
    "ENV_DB_PATH",
]
