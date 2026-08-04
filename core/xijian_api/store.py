"""SQLite-backed dict-like storage with write-through cache.

基于 SQLite 的类字典存储，带写透缓存。

Design
------
设计
------

Every ``DictDB`` instance keeps an in-memory ``_cache: dict`` that mirrors
the SQLite table.  Reads first check the cache (fast path, returns the
same object reference), then fall back to SQLite.  Writes update both
the cache and SQLite atomically.

每个 ``DictDB`` 实例维护一个镜像 SQLite 表的内存 ``_cache: dict``。
读取时先检查缓存（快速路径，返回同一对象引用），然后回退到 SQLite。
写入时原子性地更新缓存和 SQLite。

This is critical for compatibility with existing stub code that does::

这在与现有存根代码的兼容性上至关重要，例如::

    record = state.npcs[npc_id]
    record["field"] = "new_value"   # mutates the cached dict in-place
                                    # 原地修改缓存的字典

With a pure SQLite backend each read deserialises a new dict, so the
in-place mutation would be lost.  The cache ensures the same dict object
is returned on subsequent reads and auto-persisted on explicit writes.

使用纯 SQLite 后端时，每次读取都会反序列化一个新的字典，因此原地修改会丢失。
缓存确保后续读取返回相同的字典对象，并在显式写入时自动持久化。

The cache is **write-through**, not write-back — every ``__setitem__``
immediately commits to SQLite.  The cache only shadows the SQLite store
so repeated reads of the same key return the same object.

缓存是**写透**的，而非写回——每次 ``__setitem__`` 都会立即提交到 SQLite。
缓存仅作为 SQLite 存储的影子，使得对同一键的重复读取返回同一对象。
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

#: Unified storage root (CORE_ROOT) — dev mode and packaged mode share
#: the same location.  Overridable wholesale via ``XIJIAN_DATA_DIR``.
#: 统一存储根目录 (CORE_ROOT) — 开发模式与打包模式共用同一位置。
#: 可通过 ``XIJIAN_DATA_DIR`` 整体覆盖。
DEFAULT_DB_DIR = Path("~/Library/Application Support/XiJian/Core").expanduser()
DEFAULT_DB_PATH = DEFAULT_DB_DIR / "xijian.db"
ENV_DB_PATH = "XIJIAN_DB_PATH"
ENV_DATA_DIR = "XIJIAN_DATA_DIR"


def _db_path() -> str:
    """Return the database file path from environment or default.

    返回数据库文件路径，从环境变量或默认值获取。

    Priority: ``XIJIAN_DB_PATH`` > ``XIJIAN_DATA_DIR`` (→ ``<dir>/xijian.db``)
    > default (``CORE_ROOT/xijian.db``).

    优先级：``XIJIAN_DB_PATH`` > ``XIJIAN_DATA_DIR``（→ ``<dir>/xijian.db``）
    > 默认值（``CORE_ROOT/xijian.db``）。
    """
    env_db = os.environ.get(ENV_DB_PATH)
    if env_db:
        return env_db
    env_dir = os.environ.get(ENV_DATA_DIR)
    if env_dir:
        return str(Path(env_dir).expanduser() / "xijian.db")
    return str(DEFAULT_DB_PATH)


# ---------------------------------------------------------------------------
# Thread-local connection management
# 线程本地连接管理
# ---------------------------------------------------------------------------

_tl = threading.local()


def _connection() -> sqlite3.Connection:
    """Get or create a thread-local SQLite connection.

    获取或创建线程本地的 SQLite 连接。
    """
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
    """Close the thread-local SQLite connection if open.

    如果已打开，关闭线程本地的 SQLite 连接。
    """
    conn = getattr(_tl, "conn", None)
    if conn is not None:
        conn.close()
        _tl.conn = None


# ---------------------------------------------------------------------------
# DictDB — dict-like interface over a SQLite table with write-through cache
# DictDB — 基于 SQLite 表的类字典接口，带写透缓存
# ---------------------------------------------------------------------------


class DictDB(MutableMapping[str, Any]):
    """A dict backed by a SQLite table with an in-memory write-through cache.

    一个由 SQLite 表支持且带内存写透缓存的字典。

    Parameters
    ----------
    bucket:
        Logical name (e.g. ``"characters"``).  Backing table is
        ``store_{bucket}``.
        逻辑名称（例如 ``"characters"``）。对应的表是 ``store_{bucket}``。
    """

    def __init__(self, bucket: str):
        self._bucket = bucket
        self._table = f"store_{bucket}"
        self._cache: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._ensure_table()
        # Warm the cache from SQLite (populate on first access).
        # 从 SQLite 预热缓存（首次访问时填充）。
        self._load_all()

    def _connection(self) -> sqlite3.Connection:
        return _connection()

    def _ensure_table(self) -> None:
        """Create the SQLite table if it does not exist.

        如果 SQLite 表不存在则创建。
        """
        self._connection().execute(
            f"CREATE TABLE IF NOT EXISTS {self._table} ("
            "key TEXT PRIMARY KEY,"
            "value TEXT NOT NULL,"
            "created_at INTEGER NOT NULL DEFAULT (unixepoch()),"
            "updated_at INTEGER NOT NULL DEFAULT (unixepoch())"
            ")"
        )

    def _load_all(self) -> None:
        """Load all key-value pairs from SQLite into the cache.

        从 SQLite 将所有键值对加载到缓存中。
        """
        for row in self._connection().execute(
            f"SELECT key, value FROM {self._table}"
        ):
            try:
                self._cache[row["key"]] = json.loads(row["value"])
            except (json.JSONDecodeError, Exception):
                pass

    @staticmethod
    def _normalise_key(key: object) -> str:
        """Normalise a key to a string for SQLite storage.

        将键规范化为字符串以便 SQLite 存储。
        """
        if isinstance(key, str):
            return key
        return json.dumps(key, ensure_ascii=False, sort_keys=True)

    # ---- MutableMapping abstract methods ----
    # ---- MutableMapping 抽象方法 ----

    def __getitem__(self, key: str) -> Any:
        nk = self._normalise_key(key)
        with self._lock:
            cached = self._cache.get(nk)
            if cached is not None:
                return cached
        # Fallback to SQLite (should only happen for keys written by
        # a different thread / process).
        # 回退到 SQLite（仅应发生在由不同线程/进程写入的键上）。
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
    # ---- 为性能覆盖 ----

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
    # ---- 内省 ----

    @property
    def bucket(self) -> str:
        """Return the bucket name for this DictDB.

        返回此 DictDB 的桶名称。
        """
        return self._bucket

    def __repr__(self) -> str:
        with self._lock:
            return f"<DictDB bucket={self._bucket!r} len={len(self._cache)}>"

    # ---- test helpers ----
    # ---- 测试辅助函数 ----

    def _invalidate_cache(self) -> None:
        """Drop the in-memory cache and reload from SQLite.  Test-only.

        丢弃内存缓存并从 SQLite 重新加载。仅用于测试。
        """
        with self._lock:
            self._cache.clear()
        self._load_all()


# ---------------------------------------------------------------------------
# Registry
# 注册表
# ---------------------------------------------------------------------------

_store_registry: dict[str, DictDB] = {}
_registry_lock = threading.Lock()


def bucket(name: str) -> DictDB:
    """Return (or create) a DictDB for the given bucket name.

    返回（或创建）给定桶名称的 DictDB。
    """
    with _registry_lock:
        if name not in _store_registry:
            _store_registry[name] = DictDB(name)
        return _store_registry[name]


def reset_registry() -> None:
    """Drop all store tables and clear the registry.  Test-only.

    删除所有存储表并清除注册表。仅用于测试。
    """
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
    "ENV_DATA_DIR",
]
