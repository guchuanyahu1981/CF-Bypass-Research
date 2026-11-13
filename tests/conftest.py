"""全局测试夹具（v5.0 T-55 测试卫生）。

问题：一批用例构造 `SqliteStore` 后没有 `close()`，`ResourceWarning:
unclosed database` 一发 100+ 条，把 pytest 的 warning 区撑爆 —— 而 warning 区
是抓真信号的地方（N-15 就是从这里发现的），不能被资源噪音污染。

做法：autouse fixture 用 monkeypatch 跟踪测试期间创建的 `SqliteStore`
（以及 `FileStore`，为对称起见），teardown 时统一 `close()`。
不改任何用例本体；显式 `close()` 过的 store 重复 close 是无害的
（`SqliteStore.close` 幂等）。
"""

from __future__ import annotations

import pytest


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """T-68 强制规则：结构断言矩阵有 skip 时打醒目汇总行，
    让"我们没有真样本"这件事不可被忘记（对接 /health 的 research 段，T-76）。"""
    stats = terminalreporter.stats
    skipped = stats.get("skipped", [])
    if not skipped:
        return
    assertion_skips = [
        r
        for r in skipped
        if "test_research_assertions" in getattr(r, "fspath", "")
    ]
    if assertion_skips:
        reasons = {getattr(r, "location", None) for r in assertion_skips}
        terminalreporter.write_sep(
            "⚠️",
            f"研究断言矩阵：{len(assertion_skips)} 条结构断言 skip "
            f"(涉及 {len(reasons)} 个参数化用例) —— 具体原因见 "
            "plans/证据/research-assertions-*.json 的 skip_reason："
            "「样本库为空」或「样本未走到 stage2」；"
            "补样本见 计划书/CF-Bypass-Research-项目总状态与待办清单.md §3.3 待授权清单",
        )

# hypothesis profile 注册（T-81）：性质测试默认跑 50 examples；
# CI 可用 `pytest --hypothesis-profile=ci` 加压到 200。
try:
    from hypothesis import settings as _hsettings
    from hypothesis._settings import Verbosity as _Verbosity

    _hsettings.register_profile(
        "cfsolve",
        max_examples=50,
        deadline=None,
        print_blob=True,
        verbosity=_Verbosity.normal,
    )
    _hsettings.register_profile("ci", max_examples=200, deadline=None, print_blob=True)
    _hsettings.load_profile("cfsolve")
except ImportError:  # hypothesis 是 dev 依赖；没装时不阻塞普通单测
    pass


@pytest.fixture(autouse=True)
def _close_tracked_sqlite_stores(monkeypatch):
    import sys as _sys

    from solver import cache as cache_mod

    # 同一个文件可能以两个模块对象存在：包导入（`solver.cache`）与脚本模式
    # （`sys.path` 插 `solver/` 后 `import cache`，test_cache_sqlite.py 就是这种）。
    # 只钩前者的话，后者创建的连接永远不会被 close —— 那正是 2026-11-06 实测
    # "带 --cov 跑出 45 条 unclosed database" 的来源。
    modules = [cache_mod]
    script_mod = _sys.modules.get("cache")
    if script_mod is not None and script_mod is not cache_mod:
        modules.append(script_mod)

    tracked: list[object] = []
    for mod in modules:
        for cls_name in ("SqliteStore", "FileStore"):
            cls = getattr(mod, cls_name, None)
            if cls is None:
                continue
            orig_init = cls.__init__

            def make_init(orig, bucket):
                def init(self, *args, **kwargs):
                    orig(self, *args, **kwargs)
                    bucket.append(self)
                return init

            monkeypatch.setattr(cls, "__init__", make_init(orig_init, tracked))

    yield

    for store in tracked:
        close = getattr(store, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass  # teardown 清理失败不掩盖测试本身的结论


@pytest.fixture(autouse=True)
def _close_test_doubles(monkeypatch):
    """T-82：统一关闭测试期间创建的浏览器替身（FakeContext 等）。

    ResourceWarning 的来源不止 SQLite：任何带 close() 的替身没关都会报。
    用 monkeypatch 钩住 tests/test_service.py 的 FakeContext.__init__，
    teardown 统一 close()。close 已是幂等的（FakeContext.close 只置标记）。
    """
    tracked: list[object] = []
    try:
        import test_service as ts_mod  # tests/ 不是包；pytest rootdir 插入后可导入
    except ImportError:
        ts_mod = None
    if ts_mod is not None and hasattr(ts_mod, "FakeContext"):
        orig = ts_mod.FakeContext.__init__

        def init(self, *args, **kwargs):
            orig(self, *args, **kwargs)
            tracked.append(self)

        monkeypatch.setattr(ts_mod.FakeContext, "__init__", init)
    yield
    for obj in tracked:
        close = getattr(obj, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass  # teardown 清理失败不掩盖测试本身的结论


class FaultInjector:
    """T-10：往任意可调用对象上注入前 N 次故障，之后放行真调用。

    用法（故障注入测试的统一入口）：
        fault_inject.attach(cache_mod, "_try_set_wal", times=2,
                            exc=sqlite3.OperationalError("database is locked"))
        ...构造真实对象，前 2 次调用抛 exc，之后走原实现...
        assert fault_inject.calls == 3   # 确认重试真的发生
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._mp = monkeypatch
        self.calls = 0

    def attach(self, owner: object, attr: str, *, times: int, exc: BaseException,
               passthrough=None) -> None:
        """owner.attr 前 times 次抛 exc，之后调 passthrough（缺省=原实现）。"""
        orig = getattr(owner, attr)
        state = {"n": 0}
        self.calls = 0

        def wrapper(*args, **kwargs):
            state["n"] += 1
            self.calls = state["n"]
            if state["n"] <= times:
                raise exc
            real = passthrough if passthrough is not None else orig
            return real(*args, **kwargs)

        self._mp.setattr(owner, attr, wrapper)


@pytest.fixture()
def fault_inject(monkeypatch):
    """故障注入 fixture：锁冲突 / 磁盘满 / 坏库都用它构造，不再各测试手写 wrapper。"""
    yield FaultInjector(monkeypatch)
