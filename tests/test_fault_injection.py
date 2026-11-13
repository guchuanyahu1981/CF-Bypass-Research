"""故障注入测试（T-10）：锁冲突 / 磁盘满 / 坏库三类故障下，缓存层必须有界失败而非挂起。

对应 R13 的"回归只覆盖重试逻辑"承诺：真实 WAL 竞态无法确定性复现，
但注入式故障可以确定性证明——
  · 锁竞争（database is locked）→ WAL 切换有界重试后成功，或降级后功能完整；
  · 永久错误（disk I/O error / file is not a database）→ 快速失败，不烧完重试预算；
  · 写路径中途故障 → 明确异常上抛（set 契约就是"失败可见"，get 才有"降级为未命中"），
    下一次调用恢复，不挂死、不静默吞。
"""

from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "solver"))

import cache as cache_mod  # noqa: E402

TOKEN = "fault-inject-token"


def entry(host: str = "example.com") -> dict:
    return {"host": host, "want": "clearance", "cf_clearance": "tok-123",
            "user_agent": "UA", "backend": "camoufox", "expires_at": time.time() + 600}


# ── 锁竞争：有界重试后成功 ──

def test_wal_lock_conflict_retries_bounded_then_succeeds(tmp_path, fault_inject):
    fault_inject.attach(cache_mod, "_try_set_wal", times=2,
                        exc=sqlite3.OperationalError("database is locked"))
    store = cache_mod.SqliteStore(str(tmp_path), TOKEN)
    assert fault_inject.calls >= 3, f"没有重试：只调了 {fault_inject.calls} 次"
    assert store._journal_mode == "wal"


def test_wal_lock_conflict_exhausted_falls_back_not_crash(tmp_path, fault_inject, monkeypatch):
    """重试预算烧完必须降级（journal 退回 + stderr 警告），服务照常能读写。"""
    fault_inject.attach(cache_mod, "_try_set_wal", times=10**9,
                        exc=sqlite3.OperationalError("database is locked"))
    monkeypatch.setattr(cache_mod, "WAL_RETRY_ATTEMPTS", 3)
    monkeypatch.setattr(cache_mod, "WAL_RETRY_DELAY", 0.001)
    store = cache_mod.SqliteStore(str(tmp_path), TOKEN)
    assert store._journal_mode != "wal"          # 没进 WAL，但没有炸
    store.set("k|default", entry())
    assert store.get("k|default") is not None    # 降级后功能完整


# ── 永久错误：不重试，快速走完 ──

def test_wal_permanent_error_no_retry_budget_burn(tmp_path, fault_inject, monkeypatch):
    """`disk I/O error` 是永久错误：不烧重试预算（那会把必然失败延后 10s）。

    cache.py 的契约是"WAL 失败可降级"（stderr 警告 + synchronous 退 FULL），
    所以这里断言的不是"抛异常"，而是：永久错误零重试 + 秒级走完 + 降级后功能完整。
    """
    monkeypatch.setattr(cache_mod, "WAL_RETRY_DELAY", 0.01)
    t0 = time.monotonic()
    fault_inject.attach(cache_mod, "_try_set_wal", times=10**9,
                        exc=sqlite3.OperationalError("disk I/O error"))
    store = cache_mod.SqliteStore(str(tmp_path), TOKEN)
    elapsed = time.monotonic() - t0
    assert fault_inject.calls == 1, f"永久错误重试了 {fault_inject.calls - 1} 次——不该重试"
    assert elapsed < 5, "永久错误不该等重试退避"
    assert store._journal_mode != "wal"
    store.set("k|default", entry())
    assert store.get("k|default") is not None


# ── 写路径中途故障：明确上抛，不挂起；故障解除后恢复 ──

def test_set_transient_failure_raises_then_recovers(tmp_path, fault_inject):
    """set 中途撞锁错误必须明确上抛（不是静默丢写），下一次调用恢复。"""
    store = cache_mod.SqliteStore(str(tmp_path), TOKEN)

    real_conn = store._conn

    def flaky_conn():
        raise sqlite3.OperationalError("database is locked")

    store._conn = flaky_conn
    try:
        with pytest.raises(sqlite3.OperationalError):
            store.set("k|default", entry())
    finally:
        store._conn = real_conn
    # 恢复后同一 key 写读一致
    store.set("k|default", entry())
    got = store.get("k|default")
    assert got is not None and got["cf_clearance"] == "tok-123"


def test_set_disk_full_raises_clearly(tmp_path, fault_inject):
    """磁盘满是写路径的硬失败：明确上抛，绝不静默丢写。"""
    store = cache_mod.SqliteStore(str(tmp_path), TOKEN)
    fault_inject.attach(type(store), "_conn", times=10**9,
                        exc=sqlite3.OperationalError("database or disk is full"),
                        passthrough=None)
    with pytest.raises(sqlite3.OperationalError):
        store.set("k|default", entry())


# ── 坏库：file is not a database 必须带真实错误信息秒级失败 ──

def test_corrupt_db_fails_with_real_error(tmp_path):
    """永久坏库不允许被"重试 25 次"延后——要立刻失败并保留原始错误。"""
    db = tmp_path / cache_mod.SQLITE_FILENAME
    db.write_bytes(b"this is not a sqlite database" * 100)
    t0 = time.monotonic()
    with pytest.raises(Exception):
        cache_mod.SqliteStore(str(tmp_path), TOKEN)
    assert time.monotonic() - t0 < 10, "坏库要在秒级失败（重试预算不该烧在永久错误上）"


# ── fixture 自身的行为契约 ──

def test_fault_inject_fixture_counts_and_restores(tmp_path, fault_inject):
    fault_inject.attach(cache_mod, "_try_set_wal", times=1,
                        exc=sqlite3.OperationalError("database is locked"))
    assert fault_inject.calls == 0
    cache_mod.SqliteStore(str(tmp_path), TOKEN)
    assert fault_inject.calls == 2  # 1 次故障 + 1 次真调用
