"""按域令牌桶（B10）：默认关、只对冷求解计数、按域隔离。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "solver"))

import ratelimit  # noqa: E402
from errors import ErrorCode, SolveError  # noqa: E402
from test_governance import HOST, FakeBackend, make_solver  # noqa: E402


def test_bucket_refills_over_time():
    rl = ratelimit.RateLimiter(1.0, burst=1)
    assert rl.allow("a.test", now=0.0) is True
    assert rl.allow("a.test", now=0.0) is False
    assert rl.allow("a.test", now=1.01) is True


def test_bucket_is_per_host():
    rl = ratelimit.RateLimiter(0.001, burst=1)
    assert rl.allow("a.test", now=0.0) is True
    assert rl.allow("b.test", now=0.0) is True
    assert rl.allow("a.test", now=0.0) is False


def test_bucket_is_bounded():
    rl = ratelimit.RateLimiter(1.0, burst=1, max_hosts=2)
    for i in range(10):
        rl.allow(f"h{i}.test", now=float(i))
    assert len(rl._buckets) <= 2


def test_default_off_does_not_reject():
    s = make_solver(rate_per_host=0.0)
    try:
        assert s.solve(HOST, timeout=10, force=True)["ok"] is True
        assert s.solve(HOST, timeout=10, force=True)["ok"] is True
    finally:
        s.close()


def test_cold_solve_consumes_but_cache_hit_does_not():
    s = make_solver(rate_per_host=0.001, rate_burst=1)
    try:
        first = s.solve(HOST, timeout=10)          # 冷求解，消耗 1 个令牌
        assert first["ok"] is True and first.get("cached") is False
        second = s.solve(HOST, timeout=10)         # 缓存命中，不经过限流
        assert second["ok"] is True and second.get("cached") is True
        with pytest.raises(SolveError) as ei:
            s.solve(HOST, timeout=10, force=True)  # 强制冷解 → 桶空
        assert ei.value.code is ErrorCode.RATE_LIMITED
        assert ei.value.stage == "admit"
        snap = s._rate_limiter.snapshot()
        assert snap["total"] == 1 and snap["hosts"][0]["host"] == "example.com"
    finally:
        s.close()


def test_router_path_also_gets_rate_limited():
    s = make_solver(rate_per_host=0.001, rate_burst=1)
    try:
        fake = FakeBackend()
        router = __import__("backends").BackendRouter([fake], governance=s)
        assert router.solve(HOST, timeout=10, force=True)["ok"] is True
        with pytest.raises(SolveError) as ei:
            router.solve(HOST, timeout=10, force=True)
        assert ei.value.code is ErrorCode.RATE_LIMITED
    finally:
        s.close()


def test_rejected_counter_is_bounded_with_buckets():
    """淘汰 bucket 时必须同步淘汰 _rejected，否则长期多域会无界增长（Critic P2-1）。"""
    rl = ratelimit.RateLimiter(0.001, burst=1, max_hosts=2)
    for i in range(100):
        rl.allow(f"h{i}.test", now=float(i))
        rl.allow(f"h{i}.test", now=float(i))  # 第二次必拒，写进 _rejected
    assert len(rl._buckets) <= 2
    assert len(rl._rejected) <= 2, f"_rejected 未随 bucket 淘汰: {len(rl._rejected)}"
    assert rl.snapshot()["total"] == 100


def test_solver_skips_rate_check_when_router_already_charged():
    """回归：路由层收过令牌后，Camoufox 后端不能对同一请求再收一次。"""
    s = make_solver(rate_per_host=0.001, rate_burst=1)
    try:
        s.solve(HOST, timeout=10, force=True)                 # 路由/直接调用收 1 个
        out = s.solve(HOST, timeout=10, force=True, rate_checked=True)
        assert out["ok"] is True
        assert s._rate_limiter.snapshot()["total"] == 0, "同一个请求被扣了两次（第二次应被拒绝）"
    finally:
        s.close()


def test_peer_cache_hit_does_not_consume_rate_token(monkeypatch):
    """等对端填好缓存后返回的 cached:true 不该消耗按域令牌。"""
    s = make_solver(rate_per_host=0.001, rate_burst=1)
    try:
        s._rate_limiter.allow("example.com")      # 先耗尽桶
        monkeypatch.setattr(s, "_claim", lambda key, budget: False)
        monkeypatch.setattr(s, "_wait_for_peer", lambda key, budget, want="clearance": {
            "ok": True, "solved": True, "cached": True, "cf_clearance": "PEER",
            "expires_at": 9999999999.0, "want": "clearance",
        })
        out = s.solve(HOST, timeout=10)
        assert out.get("cached") is True, out
        assert s._rate_limiter.snapshot()["total"] == 0, "对端缓存命中被限流拒绝"
    finally:
        s.close()


def test_router_fallback_chain_charges_rate_once():
    """cfsolve→pydoll→camoufox 的 fallback 每个逻辑请求只收一次令牌。"""
    from backends import BackendRouter, CamoufoxBackend
    from test_governance import FakeBackend

    def retryable():
        return SolveError(ErrorCode.TIMEOUT, "retry next", stage="wait")

    s = make_solver(rate_per_host=0.001, rate_burst=2)
    try:
        first = FakeBackend(raises=retryable())
        second = FakeBackend(raises=retryable())
        router = BackendRouter([first, second, CamoufoxBackend(s)], governance=s)
        out = router.solve(HOST, timeout=30)
        assert out.get("solved") is True, out
        assert first.calls == 1 and second.calls == 1
        assert s._rate_limiter.snapshot()["total"] == 0, "fallback 链把 burst 打空后误报 429"
    finally:
        s.close()


def test_router_fallback_does_not_drain_burst_across_camoufox_error():
    """Camoufox 报错后，后续 FlareSolverr 不能再扣第二个令牌。"""
    from backends import BackendRouter, CamoufoxBackend
    from test_governance import FakeBackend

    s = make_solver(rate_per_host=0.001, rate_burst=2)
    try:
        # 一个必然失败的 Camoufox 替身：只要它跑过，就必须已收过费。
        class FailingCamoufox(CamoufoxBackend):
            def solve(self, url, timeout, force=False, **kwargs):
                raise SolveError(ErrorCode.TIMEOUT, "browser timeout", stage="wait")

        flare = FakeBackend(out={
            "ok": True, "solved": True, "cf_clearance": "F1", "user_agent": "UA/1.0",
            "cookie_list": [{"name": "cf_clearance", "value": "F1"}],
            "expires_at": __import__("time").time() + 300, "want": "clearance",
        })
        router = BackendRouter([FailingCamoufox(s), flare], governance=s)
        out = router.solve(HOST, timeout=30, force=True)
        assert out.get("solved") is True, out
        # 第一请求只该收 1 个；再来一个强制请求必须还能过（burst=2）。
        out2 = router.solve(HOST, timeout=30, force=True)
        assert out2.get("solved") is True, "fallback 把 burst 打空，第二个请求被 429"
    finally:
        s.close()


def test_refund_returns_token_after_busy_rejection():
    rl = ratelimit.RateLimiter(0.001, burst=1)
    assert rl.allow("a.test") is True
    assert rl.allow("a.test") is False
    rl.refund("a.test")
    assert rl.allow("a.test") is True


def test_solver_refunds_rate_token_when_queue_is_busy(monkeypatch):
    s = make_solver(rate_per_host=0.001, rate_burst=1)
    try:
        real_submit = s._submit
        monkeypatch.setattr(s, "_submit", lambda fn, budget: (_ for _ in ()).throw(
            SolveError(ErrorCode.BUSY, "求解队列已满", stage="admit")))
        with pytest.raises(SolveError) as ei:
            s.solve(HOST, timeout=10, force=True)
        assert ei.value.code is ErrorCode.BUSY
        monkeypatch.setattr(s, "_submit", real_submit)
        out = s.solve(HOST, timeout=10, force=True)
        assert out.get("ok") is True, "BUSY 后令牌没退还，下一次被 429"
    finally:
        s.close()


def test_invalid_request_after_charge_does_not_refund(monkeypatch):
    """INVALID_REQUEST 也可能发生在后端启动后（逐跳守卫）；不能白送令牌。"""
    from errors import ErrorCode, SolveError

    s = make_solver(rate_per_host=0.001, rate_burst=1)
    try:
        real_submit = s._submit
        monkeypatch.setattr(s, "_submit", lambda fn, budget: (_ for _ in ()).throw(
            SolveError(ErrorCode.INVALID_REQUEST, "bad", stage="validate")))
        with pytest.raises(SolveError):
            s.solve(HOST, timeout=10, force=True)
        monkeypatch.setattr(s, "_submit", real_submit)
        with pytest.raises(SolveError) as ei:
            s.solve(HOST, timeout=10, force=True)
        assert ei.value.code is ErrorCode.RATE_LIMITED
    finally:
        s.close()


def test_rate_zero_disables_limiter_even_directly():
    rl = ratelimit.RateLimiter(0.0, burst=1)
    assert all(rl.allow("a.test") for _ in range(50))


def test_refund_accepts_fake_timebase():
    rl = ratelimit.RateLimiter(1.0, burst=1)
    assert rl.allow("a.test", now=0.0) is True
    rl.refund("a.test", now=0.0)
    assert rl.allow("a.test", now=0.0) is True


def test_camoufox_precharge_failure_still_charges_fallback():
    """Camoufox 在扣费前失败（NOT_CONFIGURED/超时）时，fallback 必须自己扣一次。"""
    from backends import BackendRouter, CamoufoxBackend
    from test_governance import FakeBackend

    class EarlyFail(CamoufoxBackend):
        def solve(self, url, timeout, force=False, **kwargs):
            raise SolveError(ErrorCode.TIMEOUT, "early", stage="wait")

    s = make_solver(rate_per_host=0.001, rate_burst=1)
    try:
        flare = FakeBackend(out={
            "ok": True, "solved": True, "cf_clearance": "F1", "user_agent": "UA/1.0",
            "cookie_list": [{"name": "cf_clearance", "value": "F1"}],
            "expires_at": __import__("time").time() + 300, "want": "clearance",
        })
        router = BackendRouter([EarlyFail(s), flare], governance=s)
        assert router.solve(HOST, timeout=30, force=True)["solved"] is True
        with pytest.raises(SolveError) as ei:
            router.solve(HOST, timeout=30, force=True)
        assert ei.value.code is ErrorCode.RATE_LIMITED, "fallback 未扣费，限流被绕过"
    finally:
        s.close()
