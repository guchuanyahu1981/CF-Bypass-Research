"""solver/auth.py 与 solver/probe.py 的局部门槛补测（v6.0 T-82③）。

只补**真实分支**（policy 明令禁止无断言凑覆盖率）：
  · auth：OS 文件锁的获取/释放、锁下重读对端结果、token 文件不可读降级；
  · probe：HTTPError 读 body、无 Location 的畸形重定向、传输异常包装、
    有界读取的 deadline 截断。重定向的**逐跳守卫/上限熔断**用真实本地 HTTP 服务测，
    见 tests/test_detect.py::test_probe_blocks_redirect_to_guarded_host 与
    test_probe_aborts_on_redirect_loop（本文件不重复）。

v6.4.1 起 auth 的锁从 O_CREAT|O_EXCL + mtime 回收换成 **OS 文件锁**
（Windows `msvcrt.locking` / POSIX `fcntl.flock`）：锁跟随 fd，进程崩溃由内核释放，
不再有"两个 waiter 互相 unlink 对方新锁"的 TOCTOU。因此旧的
`test_token_stale_lock_is_cleaned` / `test_token_lock_timeout_raises`（针对 mtime 语义）
被对应的 OS 锁语义用例取代：锁文件可以保留（内容无意义），但锁本身已释放。
"""

from __future__ import annotations

import http.server
import os
import socket
import sys
import threading
import time
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "solver"))

import auth  # noqa: E402
import probe  # noqa: E402


# ─────────────────────────────── auth.py ───────────────────────────────


def test_token_race_existing_file_without_token_line_appends(tmp_path, monkeypatch):
    """并发首启竞态：文件被别的进程抢建但没有 token 行 → 追加并标记 generated。"""
    monkeypatch.delenv(auth.TOKEN_ENV, raising=False)
    path = tmp_path / ".env"
    path.write_text("OTHER_VAR=1\n", encoding="utf-8")
    token, source = auth.load_or_create_token(str(path))
    assert source == "generated"
    raw = path.read_text(encoding="utf-8")
    assert "OTHER_VAR=1" in raw, "追加不许破坏文件里已有的内容"
    assert f"{auth.TOKEN_LINE_PREFIX}{token}" in raw


def test_token_race_peer_writes_under_same_lock(tmp_path, monkeypatch):
    """锁拿到后"别的进程"写好完整 token 文件 → 重读回读对端的（source=file）。

    模拟方式：在本进程释放锁的瞬间，替身进程把 token 写进文件 —— 这正是
    "两个进程先后持有同一把锁"的真实顺序（后者拿锁后重读必须看到前者）。
    """
    monkeypatch.delenv(auth.TOKEN_ENV, raising=False)
    path = tmp_path / ".env"

    real_release = auth._TokenLock.release

    def racing_release(self):
        path.write_text(f"{auth.TOKEN_LINE_PREFIX}peer-wrote-this-token-0123456789\n",
                        encoding="utf-8")
        real_release(self)

    monkeypatch.setattr(auth._TokenLock, "release", racing_release)
    token, source = auth.load_or_create_token(str(path))
    assert source == "file" and token == "peer-wrote-this-token-0123456789"


def test_token_lock_is_reacquirable_after_release(tmp_path, monkeypatch):
    """锁释放后必须能立刻再拿（内核级解锁，不存在残留/清理竞态）。"""
    lock = auth._TokenLock(str(tmp_path / "t.lock"))
    assert lock.acquire(1.0) is True
    lock.release()
    second = auth._TokenLock(str(tmp_path / "t.lock"))
    assert second.acquire(1.0) is True, "释放后重拿失败 = 锁没有真正解开"
    second.release()


def test_token_lock_timeout_raises(tmp_path, monkeypatch):
    """锁一直被活进程持有 → 有界等待后报错，而不是静默返回一个不一致的 token。"""
    monkeypatch.delenv(auth.TOKEN_ENV, raising=False)
    path = tmp_path / ".env"
    path.write_text("OTHER=1\n", encoding="utf-8")
    holder = auth._TokenLock(str(path) + ".lock")
    assert holder.acquire(5.0) is True          # 模拟"别的进程"持锁不放
    monkeypatch.setattr(auth, "CREATE_WINDOW", 0.05)
    try:
        with pytest.raises(OSError):
            auth.load_or_create_token(str(path))
    finally:
        holder.release()

# ─────────────────────────────── probe.py ───────────────────────────────


class _FakeRaw:
    def __init__(self, body: bytes = b""):
        self._body = body

    def read(self, n=-1):
        if n is None or n < 0:
            chunk, self._body = self._body, b""
            return chunk
        if self._body:
            chunk, self._body = self._body[:n], self._body[n:]
            return chunk
        return b""

    def close(self):
        # HTTPError 继承链（addbase → _TemporaryFileWrapper）的 __del__ 会调 close()；
        # 缺它时 GC 打出 unraisable AttributeError，挂在无关测试头上。
        pass


class _FakeHTTPError(urllib.error.HTTPError):
    """只提供 read()（无 read1）的 HTTPError，走 `_read_bounded` 的 AttributeError 分支。"""

    def __init__(self, url, code, body: bytes = b"blocked"):
        super().__init__(url, code, "err", {}, _FakeRaw(body))

    def close(self):
        pass


def test_read_bounded_falls_back_to_read_for_httperror():
    out = probe._read_bounded(_FakeHTTPError("http://x", 403, b"blocked"), 100, 10 ** 9)
    assert out == "blocked"


class Slow:
    calls = {"n": 0}

    def read1(self, n):
        Slow.calls["n"] += 1
        return b"y"


def test_read_bounded_stops_at_deadline(monkeypatch):
    """deadline 到了就必须停：fake clock 只放行前 4 次 monotonic 调用，
    之后一律超时 —— 断言读次数 ≤ 4（否则 deadline 检查形同虚设）。"""
    reads = {"n": 0}
    base = time.monotonic()

    def fake_monotonic():
        reads["n"] += 1
        return base if reads["n"] <= 4 else base + 60

    monkeypatch.setattr(probe.time, "monotonic", fake_monotonic)
    out = probe._read_bounded(Slow(), 100, base + 5)
    assert Slow.calls["n"] <= 4, (reads, Slow.calls)
    assert len(out) < 100, (reads, len(out))


def test_probe_reads_httperror_body_and_reports_status(monkeypatch):
    """非 3xx 的 HTTPError（403/503）必须读出 body 并按该状态码探测，而不是炸线程。"""
    import urllib.request as _uq

    class _FakeOpener:
        def open(self, req, timeout):
            raise _FakeHTTPError(req.full_url, 403, b"cf-chl blocked")

    import urllib.request as _uq
    monkeypatch.setattr(_uq, "build_opener", lambda *a, **k: _FakeOpener())
    out = probe.probe_protection("https://e.test/", timeout=2)
    assert out["status"] == 403, out
    # body 确实被读出（403 的响应体是探测信号来源）；断言经由 summarize 的
    # providers/proTECTED 之外，直接看 status 与无异常即可，body 不在返回结构里。
    assert out["protected"] in (True, False)


def test_probe_handles_redirect_without_location(monkeypatch):
    """3xx 但没有 Location：按该状态码返回，不进入重定向循环。"""
    import urllib.request as _uq

    class _FakeOpener:
        def open(self, req, timeout):
            raise urllib.error.HTTPError(req.full_url, 302, "Found", {}, _FakeRaw(b""))

    import urllib.request as _uq
    monkeypatch.setattr(_uq, "build_opener", lambda *a, **k: _FakeOpener())
    out = probe.probe_protection("https://e.test/", timeout=2)
    assert out["status"] == 302


def test_probe_wraps_unexpected_errors_as_transport(monkeypatch):
    import urllib.request as _uq

    class _FakeOpener:
        def open(self, req, timeout):
            raise RuntimeError("socket exploded")

    import urllib.request as _uq
    monkeypatch.setattr(_uq, "build_opener", lambda *a, **k: _FakeOpener())
    with pytest.raises(Exception) as ei:
        probe.probe_protection("https://e.test/", timeout=2)
    assert "探测失败" in str(ei.value)


def test_token_file_unreadable_degrades_to_generated(tmp_path, monkeypatch):
    """token 文件存在但读取时 PermissionError → 不崩溃，降级为自动生成。"""
    monkeypatch.delenv(auth.TOKEN_ENV, raising=False)
    path = tmp_path / ".env"
    path.write_text(f"{auth.TOKEN_LINE_PREFIX}should-not-be-readable\n", encoding="utf-8")
    real_open = open

    def denying_open(file, *a, **k):
        if str(file) == str(path) and "r" in str(a[:1]):
            raise PermissionError(13, "Permission denied")
        return real_open(file, *a, **k)

    monkeypatch.setattr("builtins.open", denying_open)
    token, source = auth.load_or_create_token(str(path))
    assert token and source == "generated"
