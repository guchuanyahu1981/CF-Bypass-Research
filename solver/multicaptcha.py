"""万盾嫁接层：把 captchas/ 子包（源自 captcha-solver 的 11 厂商求解器）接入主服务。

设计约束（用户明确的架构决策）：
  · CF 主线（Want 枚举 / backends 回落链 / 缓存 / Go 客户端）**一行不动**。
  · 本层是**旁路**：主 /solve 的 want 命中主项目四类 → 走原链路；
    命中扩展类型 → 分发到 captchas/*，结果归一成主服务的响应形状。
  · captchas/ 是我们自己的副本，不追上游——上游更新自行 cherry-pick。

归一契约（与主 /solve 对齐）：
  · 成功 → {"ok": True, "solved": True, "type": ..., <产物字段>, "elapsed": ...}
  · 跑了但没解出 → {"ok": True, "solved": False, "error": ..., "elapsed": ...}（HTTP 200）
  · 参数错 → 抛 SolveError(INVALID_REQUEST)；内部崩溃 → SolveError(INTERNAL)
  · 一次性 token 绝不进缓存（与主项目 TOKEN_WANTS 同一纪律）

CF 类型不经过本层：turnstile/clearance/awswaf/recaptcha_v3 由主链路处理
（本层的 turnstile/recaptcha 是**另一套实现**——CloakBrowser 引擎 + route 拦截，
用于非 CF 站点上的同类 widget；对 CF 站点请用主链路，别在这里重复求解）。
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time
from pathlib import Path

try:
    from .errors import ErrorCode, SolveError
    from .request import clamp_timeout as _clamp_timeout, host_of
except ImportError:
    from errors import ErrorCode, SolveError  # type: ignore[no-redef]
    from request import clamp_timeout as _clamp_timeout, host_of  # type: ignore[no-redef]

log = logging.getLogger("multicaptcha")

#: 仓库根。`captchas` 是正规包（一律 `captchas.*` 绝对导入），所以只需要仓库根
#: 在 path 上。**不要**再把 `captchas/` 自己塞进去：同一模块会以两个名字各导入
#: 一份，模块级的 `_solve_lock` / KeyPool 会各有一把，串行保护形同虚设。
_REPO_ROOT = Path(__file__).resolve().parent.parent

#: 扩展求解类型 → captchas 子模块。**刻意避开**主链路 want 名：
#: 主链路已占 turnstile / recaptcha_v3 / clearance / awswaf；
#: 扩展类型带 `_x` 后缀命名（eXtended / 嫁接），永不冲突。
CAPTCHA_TYPES: dict[str, str] = {
    "turnstile_x": "turnstile",
    "recaptcha_x": "recaptcha",
    "hcaptcha": "hcaptcha",
    "cloudflare_x": "cloudflare",
    "awswaf_x": "awswaf",
    "botguard": "botguard",
    "datadome": "datadome",
    "perimeterx": "perimeterx",
    "akamai": "akamai",
    "aliyun": "aliyun",
    "arkose": "arkose",
}

#: 页面级 cookie 采集类（无 sitekey，产物是 cookie 记录）
_PAGE_LEVEL = ("cloudflare_x", "awswaf_x", "botguard", "datadome", "perimeterx", "akamai")
#: 自带规范 URL、调用方可不传 url 的类型
SELF_URL_TYPES = _SELF_URL = ("botguard", "perimeterx", "aliyun", "arkose")

#: 每类型并发锁：不同类型可并行，同类型串行（与原 server.py 同语义）。
#:
#: ⚠️ 必须是 **threading.Lock**，不能是 asyncio.Lock：主服务是 ThreadingHTTPServer，
#: 每个请求在**自己的线程里 asyncio.run 一个新事件循环**。asyncio.Lock 会把自身绑定到
#: 首次使用它的事件循环，第二个同类型请求在另一个循环里 await 它 → RuntimeError
#: "bound to a different event loop" 并**永久挂住**（已实测复现：4 线程同时 datadome，
#: 2 个直接报错、进程需要 kill）。同一把 threading.Lock 跨循环跨线程都安全。
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()

#: 在途表：ThreadingHTTPServer 每请求一线程，增删与 /status 的快照遍历会并发。
#: 不加锁时 `list(dict.values())` 可能撞上"dictionary changed size during iteration"。
_inflight_guard = threading.Lock()
_inflight: dict[int, dict] = {}
_task_ids = 0

#: 默认超时（秒），按类型可覆盖（与原 server.py 一致）
_DEFAULT_TIMEOUTS = {"perimeterx": 200, "arkose": 120, "botguard": 90, "akamai": 90}

#: 求解器自己该在 deadline 内超时返回；这条外层宽限只兜"它自己卡死"的情况
#: （多等 30s 后强制取消，把锁释放掉）。测试里可覆盖以缩短等待。
_TIMEOUT_GRACE = 30.0


def is_multicaptcha_type(want: str) -> bool:
    return (want or "").strip().lower() in CAPTCHA_TYPES


def supported_types() -> list[str]:
    return sorted(CAPTCHA_TYPES)


def _lock_for(type_: str) -> threading.Lock:
    with _locks_guard:
        lock = _locks.get(type_)
        if lock is None:
            lock = _locks[type_] = threading.Lock()
        return lock


def _normalize(type_: str, result: dict, elapsed: float) -> dict:
    """把 captchas/ 各求解器的五花八门成功信号归一成主服务响应形状。

    单一成功谓词（源自原 server.py 的 _is_solved）：token / cf_clearance /
    verify_success / success 任一为真 = 解出。统一加顶层 solved bool。
    """
    result = dict(result or {})
    solved = bool(result.get("token") or result.get("cf_clearance")
                  or result.get("verify_success") or result.get("success"))
    out = {"type": type_, "solved": solved, "ok": True, "elapsed": round(elapsed, 3)}
    out.update(result)
    out["solved"] = solved          # 防止 result 里带 solved 覆盖归一结果
    # 失败必须带一句可行动的原因：求解器可能返回 {"error": None} 或干脆不给 error，
    # setdefault 对"键存在但值为 None"无效，所以显式判空。
    if solved:
        out.setdefault("error", None)
    elif not out.get("error"):
        out["error"] = "no product harvested"
    return out


def validate_request(body: dict) -> None:
    """参数校验（移植自原 server.py /solve 的前置检查）。失败抛 SolveError。"""
    type_ = (body.get("type") or body.get("want") or "").strip().lower()
    if type_ not in CAPTCHA_TYPES:
        raise SolveError(
            ErrorCode.INVALID_REQUEST,
            f"未知扩展类型 {type_!r}；可选：{supported_types()}",
            stage="validate",
            hint="扩展类型带 _x 后缀以避开主链路 want 名；不带后缀的（hcaptcha/datadome 等）无冲突直接用",
        )
    url = body.get("url") or ""
    if not url and type_ not in _SELF_URL:
        raise SolveError(
            ErrorCode.INVALID_REQUEST, f"type={type_} 需要 url", stage="validate")
    if type_ in ("turnstile_x", "recaptcha_x", "hcaptcha") and not body.get("sitekey"):
        raise SolveError(
            ErrorCode.INVALID_REQUEST, f"type={type_} 需要 sitekey", stage="validate")
    if type_ == "aliyun" and not (body.get("scene_id") and body.get("prefix")):
        raise SolveError(
            ErrorCode.INVALID_REQUEST, "type=aliyun 需要 scene_id + prefix", stage="validate")
    if type_ == "arkose" and not body.get("public_key"):
        raise SolveError(
            ErrorCode.INVALID_REQUEST, "type=arkose 需要 public_key", stage="validate")


async def solve_async(body: dict, timeout_s: float = 0) -> dict:
    """分发到 captchas/ 对应求解器并归一结果。同类型串行，跨类型并行。"""
    type_ = (body.get("type") or body.get("want") or "").strip().lower()
    module = CAPTCHA_TYPES[type_]
    # 求解器一律以 `captchas.<vendor>.solve` 导入（与 `captchas/README.md` 的约定一致）。
    # 脚本模式（`python solver/service.py`）的 sys.path[0] 是 `solver/`，仓库根不在
    # 里面，所以这一次插入仍然必要。
    with _locks_guard:      # 复用同一把守卫：都是"一次性全局副作用"
        if str(_REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(_REPO_ROOT))
    deadline = timeout_s or body.get("timeout_s") or _DEFAULT_TIMEOUTS.get(type_, 60)
    proxy = body.get("proxy") or ""
    url = body.get("url") or ""
    sitekey = body.get("sitekey") or ""

    task_id = id(body)
    with _inflight_guard:
        _inflight[task_id] = {"type": type_, "url": (url or "")[:60], "started": time.time()}
    t0 = time.monotonic()
    # 等锁的时间也算进 deadline：同类型排队过久应当明确报忙，而不是无限挂住。
    lock = _lock_for(type_)
    if not lock.acquire(timeout=deadline + _TIMEOUT_GRACE):
        with _inflight_guard:
            _inflight.pop(task_id, None)
        return {"type": type_, "solved": False, "ok": True,
                "error": f"同类求解排队超时（>{deadline}s）", "elapsed": 0.0}
    try:
        return await asyncio.wait_for(
            _dispatch(module, type_, body, url, sitekey, proxy, deadline),
            timeout=deadline + _TIMEOUT_GRACE)
    except (TimeoutError, asyncio.TimeoutError):
        elapsed = time.monotonic() - t0
        return {"type": type_, "solved": False, "ok": True,
                "error": f"solve timed out after {deadline}s", "elapsed": round(elapsed, 3)}
    except SolveError:
        raise
    except ImportError as e:
        # 缺 cloakbrowser / onnxruntime 等依赖：这是**环境级**错误，给 NOT_CONFIGURED
        # 而不是 internal —— 调用方看到 5xx + internal 只会以为服务崩了，看不到"该装什么"。
        raise SolveError(
            ErrorCode.NOT_CONFIGURED, f"万盾求解依赖缺失：{e}", stage="admit",
            hint="pip install cloakbrowser（自托管反检测 Chromium）；"
                 "aliyun/arkose 另需 onnxruntime + opencv-python-headless",
        ) from e
    except Exception as e:
        log.error("multicaptcha solve failed: %s", e, exc_info=True)
        raise SolveError(ErrorCode.INTERNAL, str(e), stage="solve") from e
    finally:
        lock.release()
        with _inflight_guard:
            _inflight.pop(task_id, None)


def inflight_tasks() -> list[dict]:
    with _inflight_guard:
        return list(_inflight.values())


async def _dispatch(module, type_, body, url, sitekey, proxy, deadline) -> dict:
    """与原 server.py._dispatch 同构的分发；超时语义靠外层 wait_for 兜底。"""
    real_page = bool(body.get("real_page"))
    pre = body.get("pre_actions")
    post = body.get("post_fetch")
    action = body.get("action")

    if type_ == "turnstile_x":
        from captchas.turnstile.solve import solve_turnstile
        r = await solve_turnstile(sitekey, url, action, body.get("cdata"), proxy=proxy)
        return _normalize(type_, r, time.monotonic() - _t0_of(body))

    if type_ == "hcaptcha":
        from captchas.hcaptcha.solve import solve_hcaptcha, solve_hcaptcha_invisible
        if action == "invisible":
            r = await solve_hcaptcha_invisible(sitekey, url, proxy=proxy)
        else:
            r = await solve_hcaptcha(sitekey, url, proxy=proxy)
        return _normalize(type_, r, time.monotonic() - _t0_of(body))

    if type_ == "cloudflare_x":
        from captchas.cloudflare.solve import solve_cf_clearance
        r = await solve_cf_clearance(url, proxy, deadline, pre, post)
        return _normalize(type_, r, time.monotonic() - _t0_of(body))

    if type_ == "awswaf_x":
        from captchas.awswaf.solve import solve_aws_waf
        r = await solve_aws_waf(url, proxy, deadline, pre, post)
        return _normalize(type_, r, time.monotonic() - _t0_of(body))

    if type_ == "botguard":
        from captchas.botguard.solve import solve_botguard
        r = await solve_botguard(url=url, email=body.get("email"),
                                 password=body.get("password"), proxy=proxy,
                                 timeout_s=deadline, pre_actions=pre)
        return _normalize(type_, r, time.monotonic() - _t0_of(body))

    if type_ == "datadome":
        from captchas.datadome.solve import solve_datadome
        r = await solve_datadome(url=url, referer=body.get("referer"),
                                 proxy=proxy, timeout_s=deadline)
        return _normalize(type_, r, time.monotonic() - _t0_of(body))

    if type_ == "perimeterx":
        from captchas.perimeterx.solve import solve_perimeterx
        r = await solve_perimeterx(url=url, render_flow=body.get("render_flow") or "outlook_signup",
                                   proxy=proxy, timeout_s=deadline)
        return _normalize(type_, r, time.monotonic() - _t0_of(body))

    if type_ == "akamai":
        from captchas.akamai.solve import solve_akamai
        r = await solve_akamai(url, proxy, deadline, pre, post)
        return _normalize(type_, r, time.monotonic() - _t0_of(body))

    if type_ == "aliyun":
        # 原实证结论：拖动轨迹的 CDP 时序对主线程事件循环敏感，必须子进程跑
        # （asyncio.to_thread 也失败）。保留该模式（见 captchas/aliyun/）。
        import json as _json
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "captchas.aliyun._run",
            body.get("scene_id") or "", body.get("prefix") or "",
            body.get("region") or "sgp", str(deadline), proxy,
            cwd=str(_REPO_ROOT),
            env=_subprocess_env(),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, _err = await asyncio.wait_for(proc.communicate(), timeout=deadline + 30)
        except asyncio.TimeoutError:
            proc.kill()
            return {"type": type_, "solved": False, "ok": True,
                    "error": "subprocess deadline", "elapsed": deadline}
        r = {"error": "no result from runner"}
        for line in (out or b"").decode(errors="replace").splitlines():
            if line.startswith("__ALIYUN_RESULT__"):
                r = _json.loads(line[len("__ALIYUN_RESULT__"):])
                break
        return _normalize(type_, r, time.monotonic() - _t0_of(body))

    if type_ == "arkose":
        from captchas.arkose.solve import solve_arkose
        r = await solve_arkose(public_key=body.get("public_key") or "",
                               page_url=url, game_type=body.get("game_type") or "4",
                               proxy=proxy, timeout_s=deadline, pre_actions=pre)
        return _normalize(type_, r, time.monotonic() - _t0_of(body))

    # recaptcha_x：v2/v3/invisible × real_page
    from captchas.recaptcha.solve import (
        solve_recaptcha_v3, solve_recaptcha_v3_realpage, solve_recaptcha_invisible,
        solve_recaptcha_invisible_realpage, solve_recaptcha_v2, solve_recaptcha_v2_realpage,
    )
    version = body.get("version") or "v2"
    enterprise = bool(body.get("enterprise"))
    classifier = body.get("classifier")
    a = body.get("action") or "submit"
    if version == "v3":
        if real_page:
            r = await solve_recaptcha_v3_realpage(url, sitekey, a, enterprise=enterprise,
                                                  timeout_s=deadline, pre_actions=pre, proxy=proxy)
        else:
            r = await solve_recaptcha_v3(sitekey, url, a, body.get("secret"),
                                         enterprise=enterprise, proxy=proxy)
    elif version == "invisible":
        if real_page:
            r = await solve_recaptcha_invisible_realpage(url, sitekey, a, enterprise=enterprise,
                                                         timeout_s=deadline, pre_actions=pre,
                                                         proxy=proxy, classifier=classifier)
        else:
            r = await solve_recaptcha_invisible(sitekey, url, a, enterprise=enterprise, proxy=proxy)
    elif version == "v2":
        if real_page:
            r = await solve_recaptcha_v2_realpage(url, sitekey, pre, post,
                                                  timeout_s=deadline, proxy=proxy,
                                                  classifier=classifier)
        else:
            r = await solve_recaptcha_v2(sitekey, url, enterprise=enterprise,
                                         proxy=proxy, classifier=classifier)
    else:
        raise SolveError(ErrorCode.INVALID_REQUEST,
                         f"未知 version: {version}（v2|v3|invisible）", stage="validate")
    return _normalize(type_, r, time.monotonic() - _t0_of(body))


def _subprocess_env() -> dict:
    """子进程环境：PYTHONPATH 含仓库根（`captchas` 包的绝对导入需要）。"""
    env = dict(os.environ)
    parts = [str(_REPO_ROOT)]
    old = env.get("PYTHONPATH")
    if old:
        parts.append(old)
    env["PYTHONPATH"] = os.pathsep.join(parts)
    return env


def _t0_of(body: dict) -> float:
    return body.get("_t0") or time.monotonic()


def mark_start(body: dict) -> None:
    """在请求入口打时间戳，供 _normalize 计 elapsed。"""
    body["_t0"] = time.monotonic()


def handle_compat_bypass(
    body: dict,
    *,
    check_target,
    entry_for,
    clamp_timeout=None,
) -> dict | None:
    """`/solve` 入口的嫁接层旁路（v6.4 D2：自 `service.do_POST` 下沉，纯函数）。

    为什么必须是纯函数：这段逻辑原来嵌在 `do_POST` 的 `try` 里，占 90 行、和 SSRF 守卫、
    路由决策、后端调用混在同一个作用域 —— 既没法单独测边界，也没法说清"改一行会不会动到
    主链路"。下沉之后它只依赖三个注入进来的能力，不碰 socket、不碰 handler 状态：

      · `check_target(host)` —— 主链路的 SSRF 守卫（由 `Solver._check_target` 提供）；
      · `entry_for(host)`    —— 该域粘住的出口（由 `Solver._entry_for` 提供），可返回 None；
      · `clamp_timeout(t)`   —— 超时夹取（默认用 `request.clamp_timeout`）。

    返回 `None` = 这条请求不属于嫁接层，调用方继续走主链路（行为与 v6.3 完全一致）；
    返回 `dict` = 归一后的响应体，调用方只需 `_send(200, ...)` 加一行审计。
    参数/目标不合法时抛 `SolveError`（错误码与主链路同一套）。
    """
    want = (body.get("want") or "").strip().lower()
    if not is_multicaptcha_type(want):
        return None

    mc_body = dict(body)          # 不可变：不改调用方的 dict（它后面主链路还要用）
    mc_body["type"] = want
    url = mc_body.get("url") or ""
    # 自带规范 URL 的类型（botguard/perimeterx/aliyun/arkose）允许省略 url
    if url or want not in SELF_URL_TYPES:
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            raise SolveError(ErrorCode.INVALID_REQUEST,
                             "url 必须是 http(s) 绝对地址", stage="validate")
        check_target(host_of(url))      # 同主链路的 SSRF 守卫
    validate_request(mc_body)

    # 出口粘性：cookie 类产物（DataDome/PerimeterX/Akamai/CF）**绑出口 IP**，
    # 同一个域必须用同一个出口采集与回放。主链路由代理池保证这一点；
    # 嫁接层若默认裸连，拿到的 cookie 在客户端换 IP 回放就是静默 403 ——
    # 所以调用方没显式给 proxy 时，沿用主链路为该域粘住的出口。
    entry = None
    if not mc_body.get("proxy") and url:
        entry = entry_for(host_of(url))
        if entry is not None:
            mc_body["proxy"] = entry.raw_url      # 含凭据，仅内存传递，不进日志

    # 超时：主链路的 `timeout`（[5,120]）与扩展层的 `timeout_s` 都认，
    # `timeout` 优先做夹取 —— 调用方不该为两套字段名记两份约定。
    clamp = clamp_timeout or _clamp_timeout
    timeout_s = 0.0
    if mc_body.get("timeout") is not None:
        timeout_s = clamp(mc_body.get("timeout"))
    elif mc_body.get("timeout_s"):
        timeout_s = mc_body.get("timeout_s")

    mark_start(mc_body)
    result = asyncio.run(solve_async(mc_body, timeout_s=timeout_s))
    result["routed_by"] = {"mode": "multicaptcha", "backend": "captchas",
                           "type": result.get("type")}
    if entry is not None:
        # 与主链路同一约定：出口标识回传，客户端回放必须用同一出口
        result["egress_id"] = entry.egress_id
    return result
