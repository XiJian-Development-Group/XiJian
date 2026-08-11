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
import time
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

#: Legacy pre-unification database location (B2/S4).  The old layout
#: stored the DB directly under the app-data dir; the unified layout
#: moved it into ``CORE_ROOT``.  We only *detect* a recently-touched
#: legacy file and warn — we never migrate or read it here.
#: 存储统一前的旧版数据库位置 (B2/S4)。旧布局把 DB 直接放在应用数据
#: 目录下；统一后移入 ``CORE_ROOT``。这里只*检测*最近被触碰过的
#: 旧文件并警告——绝不迁移或读取它。
LEGACY_DB_PATH = Path("~/Library/Application Support/XiJian/xijian.db").expanduser()
_LEGACY_DETECTED = False

#: Detection window: a legacy DB is only worth flagging if it was
#: modified within the last 24 hours.
#: 检测窗口：旧版 DB 仅在最近 24 小时内被修改过才值得标记。
_LEGACY_DETECTION_WINDOW_SECONDS = 24 * 60 * 60


def detect_legacy_db() -> Path | None:
    """Check once for a recently-active legacy DB and warn (B2/S4).

    检查一次是否存在最近活跃的旧版 DB 并警告 (B2/S4)。

    Returns the legacy path when one exists with an mtime within the
    last 24 hours, else ``None``.  The check runs at most once per
    process (module-level flag) so per-connection ``stat`` overhead is
    avoided.  Failures (permissions, races) are swallowed — detection
    is advisory only.

    当存在 mtime 在最近 24 小时内的旧文件时返回其路径，否则返回
    ``None``。检查每个进程最多执行一次（模块级标志），避免每次
    连接都 ``stat``。失败（权限、竞态）被吞掉——检测仅作参考。
    """
    global _LEGACY_DETECTED  # noqa: PLW0603
    if _LEGACY_DETECTED:
        return None
    _LEGACY_DETECTED = True
    try:
        if not LEGACY_DB_PATH.is_file():
            return None
        mtime = LEGACY_DB_PATH.stat().st_mtime
        if time.time() - mtime > _LEGACY_DETECTION_WINDOW_SECONDS:
            return None
        _LOGGER.warning(
            "legacy database detected at %s (mtime=%s) — the active store is %s; "
            "consider consolidating old data",
            LEGACY_DB_PATH,
            mtime,
            _db_path(),
        )
        return LEGACY_DB_PATH
    except OSError:
        return None


def reset_legacy_detection_for_testing() -> None:
    """Re-arm the once-per-process legacy check (test-only).

    重新武装每进程一次的旧版检测（仅测试用）。
    """
    global _LEGACY_DETECTED  # noqa: PLW0603
    _LEGACY_DETECTED = False


#: Legacy DB detection runs at most once per process (B2/S4).  The
#: check is advisory — it never blocks or migrates — so it is wired
#: into the connection factory where every DB user passes through.
#: 旧版 DB 检测每进程最多运行一次 (B2/S4)。检测仅作参考——绝不阻塞
#: 或迁移——因此挂在连接工厂里，所有 DB 用户都会经过这里。


def _warn_legacy_db_once() -> None:
    """Call :func:`detect_legacy_db` at most once (module flag).

    调用 :func:`detect_legacy_db` 至多一次（模块标志）。
    """
    detect_legacy_db()


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
        # B2/S4 — flag a recently-active legacy DB once per process.
        # B2/S4 — 每进程一次，标记最近活跃的旧版 DB。
        detect_legacy_db()
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.row_factory = sqlite3.Row
        _tl.conn = conn
        # S4 — restrict the DB file to the owning user.  Best-effort:
        # on platforms without POSIX semantics (Windows) ``chmod`` may
        # raise; that is fine — the DB dir is already user-scoped.
        # S4 — 将 DB 文件权限收紧为属主用户。尽力而为：在不具备
        # POSIX 语义的平台（Windows）上 ``chmod`` 可能抛异常，
        # 无妨——DB 目录本身已是用户作用域。
        try:
            os.chmod(db_path, 0o600)
        except OSError:  # pragma: no cover - platform-dependent
            pass
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

    def _execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """Run a write statement, creating the table first if it is missing.

        执行写语句，若表不存在则先建表。

        Connections are thread-local and a fresh thread may open a
        brand-new database file (e.g. an env-overridden path) that has
        none of the ``store_*`` tables yet — the table is normally
        created in :meth:`DictDB.__init__` on whichever thread created
        the bucket.  Retrying once after :meth:`_ensure_table` makes
        writes from any thread self-sufficient.

        连接是线程本地的，新线程可能打开一个全新的数据库文件
        （例如被环境变量覆盖的路径），其中还没有任何 ``store_*`` 表
        ——表通常在创建桶的线程的 :meth:`DictDB.__init__` 中创建。
        在 :meth:`_ensure_table` 后重试一次，使任何线程的写入都自足。
        """
        try:
            return self._connection().execute(sql, params)
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc).lower():
                self._ensure_table()
                return self._connection().execute(sql, params)
            raise

    def _load_all(self) -> None:
        """Load all key-value pairs from SQLite into the cache.

        从 SQLite 将所有键值对加载到缓存中。

        Corrupt rows are logged, not fatal: a single bad JSON value
        must not prevent the rest of the bucket from loading.  We
        deliberately do **not** rename/delete the database file on
        corruption (E5) — this is a SQLite store, and a row-level JSON
        parse failure is data corruption in one row, not a broken
        whole-file; renaming the entire DB would destroy unrelated
        healthy data.  Logging the offending key gives operators the
        exact location to repair.

        损坏的行只记日志，不致命：单个坏 JSON 值不能阻止桶内其余
        数据加载。损坏时我们刻意**不**重命名/删除数据库文件 (E5)
        ——这是 SQLite 存储，行级 JSON 解析失败只是单行数据损坏，
        不是整个文件损坏；重命名整个 DB 会毁掉无关的健康数据。
        记录出错的 key 让运维能精确定位修复。
        """
        for row in self._connection().execute(
            f"SELECT key, value FROM {self._table}"
        ):
            key = row["key"]
            try:
                self._cache[key] = json.loads(row["value"])
            except json.JSONDecodeError as exc:
                _LOGGER.warning(
                    "corrupt JSON value in store %s key %r: %s",
                    self._table,
                    key,
                    exc,
                )
            except Exception as exc:  # noqa: BLE001 - keep loading
                _LOGGER.error(
                    "unexpected error loading store %s key %r: %s",
                    self._table,
                    key,
                    exc,
                    exc_info=True,
                )

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
        self._execute(
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
        cur = self._execute(
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
                self._execute(
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
        self._execute(f"DELETE FROM {self._table}")
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
    "LEGACY_DB_PATH",
    "detect_legacy_db",
    "reset_legacy_detection_for_testing",
]
