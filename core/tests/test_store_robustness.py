"""Robustness tests for ``xijian_api.store`` (E5 logging + B2/S4 legacy detection).

``xijian_api.store`` 的健壮性测试（E5 日志 + B2/S4 旧版检测）。

* E5 — corrupt JSON rows are logged (WARNING for JSONDecodeError, ERROR
  with traceback for anything else) instead of being silently skipped;
  the DB file is deliberately NOT renamed (row-level corruption).
* B2/S4 — a recently-active legacy DB is detected once per process with
  a WARNING; the DB file is chmod 0600 on connect.
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

import pytest

from xijian_api import store


@pytest.fixture(autouse=True)
def _reset_legacy_flag():
    store.reset_legacy_detection_for_testing()
    yield
    store.reset_legacy_detection_for_testing()


# ---------------------------------------------------------------------------
# B2/S4 — legacy DB detection
# ---------------------------------------------------------------------------


def test_detect_legacy_db_warns_for_recent_file(monkeypatch, tmp_path, caplog):
    """A legacy DB modified within 24h is flagged with a WARNING (B2/S4)."""
    legacy = tmp_path / "xijian.db"
    legacy.write_bytes(b"legacy")
    monkeypatch.setattr(store, "LEGACY_DB_PATH", legacy)

    with caplog.at_level("WARNING", logger="xijian_api.store"):
        found = store.detect_legacy_db()

    assert found == legacy
    assert any("legacy database detected" in rec.message for rec in caplog.records)


def test_detect_legacy_db_ignores_stale_file(monkeypatch, tmp_path):
    """A legacy DB untouched for >24h is not flagged (B2/S4)."""
    legacy = tmp_path / "xijian.db"
    legacy.write_bytes(b"legacy")
    old = time.time() - 48 * 3600
    os.utime(legacy, (old, old))
    monkeypatch.setattr(store, "LEGACY_DB_PATH", legacy)

    assert store.detect_legacy_db() is None


def test_detect_legacy_db_missing_file_returns_none(monkeypatch, tmp_path):
    """No legacy file → None (B2/S4)."""
    monkeypatch.setattr(store, "LEGACY_DB_PATH", tmp_path / "nope.db")
    assert store.detect_legacy_db() is None


def test_detect_legacy_db_runs_once_per_process(monkeypatch, tmp_path):
    """Detection latches after the first call (B2/S4)."""
    legacy = tmp_path / "xijian.db"
    legacy.write_bytes(b"legacy")
    monkeypatch.setattr(store, "LEGACY_DB_PATH", legacy)

    assert store.detect_legacy_db() == legacy
    # Second call: latched → None even though the file still exists.
    assert store.detect_legacy_db() is None
    # Re-arming re-enables detection.
    store.reset_legacy_detection_for_testing()
    assert store.detect_legacy_db() == legacy


def test_db_file_chmod_600_on_connect(monkeypatch, tmp_path):
    """A freshly-created DB file is restricted to 0600 (S4)."""
    db_file = tmp_path / "sub" / "xijian.db"
    monkeypatch.setenv("XIJIAN_DB_PATH", str(db_file))
    store.close_connections()
    try:
        conn = store._connection()
        assert conn is not None
        mode = db_file.stat().st_mode & 0o777
        assert mode == 0o600
    finally:
        store.close_connections()


# ---------------------------------------------------------------------------
# E5 — corrupt-row logging in _load_all
# ---------------------------------------------------------------------------


def _fresh_conn(db_file: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    return conn


def _make_table_with_rows(db_file: Path, table: str, rows: list[tuple[str, str]]) -> None:
    conn = sqlite3.connect(str(db_file))
    conn.execute(
        f"CREATE TABLE {table} (key TEXT PRIMARY KEY, value TEXT NOT NULL, "
        "created_at INTEGER NOT NULL DEFAULT (unixepoch()), "
        "updated_at INTEGER NOT NULL DEFAULT (unixepoch()))"
    )
    conn.executemany(
        f"INSERT INTO {table} (key, value) VALUES (?, ?)", rows
    )
    conn.commit()
    conn.close()


def test_load_all_logs_json_decode_error(monkeypatch, tmp_path, caplog):
    """A row with invalid JSON is skipped with a WARNING naming the key (E5)."""
    db_file = tmp_path / "corrupt.db"
    _make_table_with_rows(
        db_file,
        "store_test_corrupt_bucket",
        [("good", '{"a": 1}'), ("bad", "{not json")],
    )

    monkeypatch.setattr(store, "_connection", lambda: _fresh_conn(db_file))
    with caplog.at_level("WARNING", logger="xijian_api.store"):
        db = store.DictDB("test_corrupt_bucket")

    # Good row loaded, bad row skipped.
    assert db.get("good") == {"a": 1}
    assert "bad" not in db
    assert any("corrupt JSON value" in r.message and "test_corrupt_bucket" in r.message
               for r in caplog.records)


def test_load_all_logs_unexpected_error(monkeypatch, tmp_path, caplog):
    """A non-JSONDecodeError failure (e.g. RecursionError) logs ERROR (E5)."""
    db_file = tmp_path / "deep.db"
    deep = "[" * 20000 + "]" * 20000  # json.loads → RecursionError
    _make_table_with_rows(db_file, "store_test_deep_bucket", [("deep", deep)])

    monkeypatch.setattr(store, "_connection", lambda: _fresh_conn(db_file))
    with caplog.at_level("ERROR", logger="xijian_api.store"):
        store.DictDB("test_deep_bucket")

    assert any("unexpected error loading store" in r.message for r in caplog.records)
    assert any(r.levelno >= 40 for r in caplog.records)
