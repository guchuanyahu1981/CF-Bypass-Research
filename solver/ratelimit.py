"""按域令牌桶：给出口信誉加一道代码刹车（v6.4.3，B10）。

为什么需要：`policy.md` §2 的 R9b 实证——同一出口连打同一站 12 次后，60s 内解不出。
此前只有三条规定（同站 ≥30s、换出口换 id、失败即停），全是人的纪律；
凌晨的自动化任务不会遵守纪律。这个模块把"同域求解过频"变成 429。

语义（刻意保守）：
  · CLI 默认 `--rate-per-host 1.0/30`（同站约 30s 一次）；库/测试默认 0，保持零行为变化；
  · 只对**冷求解**计数：缓存命中在调用方之前就返回了，不消耗令牌；
  · 按域建桶，域数上限有界（默认 4096），LRU 淘汰，防标签基数爆炸；
  · 令牌桶允许短突发（`--rate-burst`，默认 2），匹配"预热/续期会连打两次"的真实模式。

它是**准入**而不是调度：拒绝发生在 claim/inflight 之前，不占任何求解额度。
"""

from __future__ import annotations

import threading
import time


class _Bucket:
    __slots__ = ("tokens", "updated")

    def __init__(self, tokens: float, updated: float) -> None:
        self.tokens = tokens
        self.updated = updated


class RateLimiter:
    """线程安全的按域令牌桶 + 拒绝计数。"""

    def __init__(self, rate_per_host: float, burst: int = 2, max_hosts: int = 4096) -> None:
        if rate_per_host < 0:
            raise ValueError("rate_per_host 不能为负")
        self.rate = float(rate_per_host)
        self.burst = max(1, int(burst))
        self.max_hosts = max(1, int(max_hosts))
        self._lock = threading.Lock()
        self._buckets: dict[str, _Bucket] = {}
        self._rejected: dict[str, int] = {}
        self._rejected_total = 0

    def allow(self, host: str, now: float | None = None) -> bool:
        """取一个令牌；桶空返回 False。"""
        host = (host or "").lower()
        if not host or self.rate <= 0:
            return True          # rate=0 = 关闭（库/测试默认），永不拒绝
        now = time.monotonic() if now is None else now
        with self._lock:
            bucket = self._buckets.get(host)
            if bucket is None:
                if len(self._buckets) >= self.max_hosts:
                    # 淘汰最早更新的一条；桶状态只是刹车，丢了最多多放一次。
                    oldest = min(self._buckets, key=lambda k: self._buckets[k].updated)
                    self._buckets.pop(oldest, None)
                    # 拒绝计数也必须随 bucket 一起淘汰，否则 max_hosts 只约束了桶、
                    # 没约束 _rejected：长期跑多域会无界增长，/metrics 还会对全量排序。
                    self._rejected.pop(oldest, None)
                bucket = _Bucket(float(self.burst), now)
                self._buckets[host] = bucket
            else:
                elapsed = max(0.0, now - bucket.updated)
                bucket.tokens = min(float(self.burst), bucket.tokens + elapsed * self.rate)
                bucket.updated = now
            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return True
            self._rejected[host] = self._rejected.get(host, 0) + 1
            self._rejected_total += 1
            return False

    def refund(self, host: str, now: float | None = None) -> None:
        """把刚扣掉的一个令牌还回去（准入 BUSY 时调用：请求并未真正求解）。"""
        with self._lock:
            bucket = self._buckets.get(host)
            if bucket is not None:
                bucket.tokens = min(float(self.burst), bucket.tokens + 1.0)
                bucket.updated = time.monotonic() if now is None else now

    def snapshot(self) -> dict:
        """给 /metrics 用的只读视图（按拒绝次数降序，最多 20 个域）。"""
        with self._lock:
            top = sorted(self._rejected.items(), key=lambda kv: kv[1], reverse=True)[:20]
            return {
                "total": self._rejected_total,
                "hosts": [{"host": h, "rejected": n} for h, n in top],
            }