"""UI 验收（真 Chrome / Playwright）：状态页 `GET /` 与控制台 `GET /console` 的状态矩阵。

为什么必须单写一个脚本（而不是继续靠静态断言）：
  · 这两个页面是**单文件 HTML + 内联 JS**，没有任何构建步骤 —— 一行写错的 JS
    （比如把 `#` 当注释写进去）静态测试查不出来，只有浏览器执行时才会炸。
    实测：v6.2 就发生过一次，页面徽章永远停在"未知"，而所有 Python 测试全绿。
  · 用户的原话是"这个按钮到底能不能按成功" —— 按钮行为只能真点一次才算验证。

覆盖的状态矩阵（每个状态都断言徽章文字 / 颜色 / 横幅文案 / 面板内容）：
  1. ready（正常；保存/刷新/自动刷新/键盘/axe）
  2. wedged（worker 卡死：/health 返回 503 + state=wedged）
  3. degraded（浏览器挂了但服务还活着；横幅 + stale 标记）
  4. 无 token（/status 与 /logs 401 → 必须**明确说明去哪拿 token**，而不是空白）
  5. 错误 token（401 → 必须说"token 无效"，不能继续说"需要 token"）
  6. 审计（表格、审计刷新、自动刷新开关、清除 token 停定时器、320 内部滚动）
  7. 服务中途挂掉（stale：徽章降级 + 横幅 + 卡片过期）
  8. 深色主题（两页 axe 0 + 截图）
  9. 服务不可达（端口关掉 → 页面本身打不开，断言浏览器确实报错）

另外覆盖真实交互：
  · 状态页/控制台「保存」→ 写入 sessionStorage 并刷新
  · 状态页「刷新」「自动刷新」；控制台「刷新」「审计刷新」「自动刷新」「清除 token」
  · 两页都**不得有 JS 运行时错误**（pageerror / console.error）
  · 两页都跑 axe-core（WCAG A/AA）并落 JSON 证据

退出码：0=全绿；1=有断言失败。截图落 `plans/证据/ui-<状态>-<页面>.png`。
用法：
    python scripts/verify_ui_e2e.py                 # 全部状态
    python scripts/verify_ui_e2e.py --only wedged   # 只跑一个状态（调试用）
    python scripts/verify_ui_e2e.py --json-out evidence/ui-acceptance.json
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "solver"))
sys.path.insert(0, str(ROOT / "tests"))

import service  # noqa: E402
from test_service import FakeBrowser, factory_returning, wait_until  # noqa: E402

TOKEN = "ui-e2e-token"
OUT_DIR = ROOT / "plans" / "证据"
AXE_PATH = ROOT / "site" / "node_modules" / "axe-core" / "axe.min.js"

results: list[tuple[str, bool, str]] = []
axe_results: list[dict] = []
current_case = ""


def check(name: str, ok: bool, detail: str = "") -> bool:
    results.append((name, bool(ok), detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not ok else ""))
    return ok


class _Service:
    """起一个真服务（假浏览器：不联网、不出站；验的是 HTTP 面 + 页面本身）。"""

    def __init__(self, setup=None, audit_path: str = "") -> None:
        self.solver = service.Solver(
            browser_factory=factory_returning(FakeBrowser()), allow_private_hosts=True
        )
        assert wait_until(lambda: self.solver.health()[0] == 200)
        if setup:
            setup(self.solver)
        service.Handler.solver = self.solver
        service.Handler.token = TOKEN
        service.Handler.audit_path = audit_path
        self.srv = service.Server(("127.0.0.1", 0), service.Handler)
        threading.Thread(
            target=self.srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        ).start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def close(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()
        self.solver.close()


#: 徽章从占位「未知」变成任何真实状态 = 首屏数据已落地（/health 是公开的）。
#: 必须是**箭头函数**：服务端 CSP 是 `script-src 'unsafe-inline'`（无 `unsafe-eval`），
#: 纯表达式串会走 eval 而被 CSP 拒绝；函数形式走 callFunctionOn，不受此限制。
_STATE_READY = ("() => { const el = document.getElementById('state');"
                " return !!el && el.textContent.trim() !== '未知'; }")


def _poll(page, expr: str, timeout: int = 10000) -> bool:
    """轮询到条件成立；超时返回 False，由后续断言报失败。

    固定 `wait_for_timeout` 只能猜渲染耗时：猜短了在慢机器上红，猜长了每步都白等。
    这里等的是**条件**（数据落地 / sessionStorage 写入 / 行数变化），不是时长。
    """
    try:
        page.wait_for_function(expr, timeout=timeout)
        return True
    except Exception:  # noqa: BLE001 —— 条件没等到时让断言给出可读结论
        return False


def _open(page, url: str, ready: str = "") -> list[str]:
    """打开页面并收集 JS 错误（pageerror + console.error）。

    `ready` 是页面级额外就绪条件（例如审计表行数），不传则只等徽章脱离「未知」。
    """
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("console", lambda m: errors.append(f"console: {m.text}") if m.type == "error" else None)
    page.goto(url, wait_until="domcontentloaded")
    _poll(page, _STATE_READY, 15000)
    if ready:
        _poll(page, ready, 15000)
    return errors


def _badge(page):
    el = page.locator("#state")
    return (
        el.inner_text().strip(),
        el.get_attribute("class") or "",
        el.evaluate("e => getComputedStyle(e).color"),
        el.evaluate("e => getComputedStyle(e).backgroundColor"),
    )


def check_axe(page, label: str) -> None:
    """用站点已有的 axe-core 对服务 UI 跑 WCAG A/AA 扫描（零新依赖）。"""
    if not AXE_PATH.exists():
        check(f"{label}/axe 可用", False, f"缺少 {AXE_PATH}（site/node_modules/axe-core）")
        return
    page.add_script_tag(content=AXE_PATH.read_text(encoding="utf-8"))
    res = page.evaluate(
        "async () => await axe.run(document, {runOnly: {type: 'tag', "
        "values: ['wcag2a','wcag2aa','wcag21a','wcag21aa']}})"
    )
    violations = res.get("violations") or []
    axe_results.append({
        "label": label,
        "violations": [
            {"id": v.get("id"), "impact": v.get("impact"),
             "targets": [n.get("target") for n in (v.get("nodes") or [])[:5]]}
            for v in violations
        ],
    })
    detail = "；".join(f"{v.get('id')}:{v.get('impact')}" for v in violations[:3])
    check(f"{label}/axe 0 违规", not violations, detail or "0")


def case_ready(browser) -> None:
    svc = _Service()
    try:
        ctx = browser.new_context(extra_http_headers={"Authorization": f"Bearer {TOKEN}"})
        page = ctx.new_page()
        page.add_init_script(f"sessionStorage.setItem('cfsolve_token','{TOKEN}')")
        errs = _open(page, svc.base + "/")
        text, cls, *_ = _badge(page)
        check("ready/状态页徽章", text == "ready" and "ready" in cls, f"{text}/{cls}")
        check("ready/状态页无 JS 错误", not errs, str(errs[:2]))
        check("ready/健康面板有内容", "state" in page.inner_text("#health"))
        # 键盘可达性：token 输入 → 保存，焦点必须按这个顺序走（且 :focus-visible 有样式）。
        page.evaluate("() => document.body.focus()")
        page.keyboard.press("Tab")
        first = page.evaluate("() => document.activeElement && document.activeElement.id")
        check("ready/键盘首个焦点是 token", first == "token", str(first))
        page.keyboard.press("Tab")
        second = page.evaluate("() => document.activeElement && document.activeElement.id")
        check("ready/键盘第二个焦点是保存", second == "save", str(second))
        check_axe(page, "ready/状态页")
        page.screenshot(path=str(OUT_DIR / "ui-ready-statuspage.png"), full_page=True)

        page2 = ctx.new_page()
        page2.add_init_script(f"sessionStorage.setItem('cfsolve_token','{TOKEN}')")
        page2.on("dialog", lambda d: d.accept(TOKEN))
        errs2 = _open(page2, svc.base + "/console")
        text2, cls2, *_ = _badge(page2)
        check("ready/控制台徽章", text2 == "ready" and "ready" in cls2, f"{text2}/{cls2}")
        check("ready/控制台无 JS 错误", not errs2, str(errs2[:2]))
        page2.evaluate("() => document.body.focus()")
        page2.keyboard.press("Tab")
        first2 = page2.evaluate("() => document.activeElement && document.activeElement.id")
        check("ready/控制台键盘首个焦点是 token", first2 == "token", str(first2))
        page2.keyboard.press("Tab")
        second2 = page2.evaluate("() => document.activeElement && document.activeElement.id")
        check("ready/控制台键盘第二个焦点是保存", second2 == "save", str(second2))
        check_axe(page2, "ready/控制台")
        page2.screenshot(path=str(OUT_DIR / "ui-ready-console.png"), full_page=True)

        # 真实交互：保存按钮 → sessionStorage
        page.fill("#token", "new-token-value")
        page.click("#save")
        _poll(page, "() => sessionStorage.getItem('cfsolve_token') === 'new-token-value'")
        stored = page.evaluate("() => sessionStorage.getItem('cfsolve_token')")
        check("ready/保存按钮写入 token", stored == "new-token-value", repr(stored))
        # 真实交互：自动刷新切换
        page.click("#auto")
        _poll(page, "() => document.getElementById('auto').getAttribute('aria-pressed') === 'true'")
        pressed = page.get_attribute("#auto", "aria-pressed")
        label = page.inner_text("#auto")
        check("ready/自动刷新可开启", pressed == "true" and "开" in label, f"{pressed}/{label}")
        page.click("#auto")
        _poll(page, "() => document.getElementById('auto').getAttribute('aria-pressed') === 'false'")
        check("ready/自动刷新可关闭", page.get_attribute("#auto", "aria-pressed") == "false")
        # 「刷新」按钮：真实点一次，等它从 disabled 恢复，且不产生 JS 错误。
        page.click("#refresh")
        _poll(page, "() => !document.getElementById('refresh').disabled", 8000)
        check("ready/刷新按钮完成并恢复可用",
              page.get_attribute("#refresh", "disabled") is None and not errs, str(errs[:2]))

        # v6.3：320/360 小屏不得横向溢出（D-03）。表格在 `#audit`/`#inflight-table`
        # 内部滚动，页面本身必须保持 `scrollWidth <= clientWidth`。
        for width in (320, 360):
            page.set_viewport_size({"width": width, "height": 800})
            page.reload(wait_until="domcontentloaded")
            _poll(page, _STATE_READY)
            overflow = page.evaluate(
                "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
            )
            check(f"ready/{width}px 状态页无横向溢出", overflow <= 1, f"overflow={overflow}")
            page.screenshot(path=str(OUT_DIR / f"ui-ready-statuspage-{width}.png"), full_page=True)

            page2.set_viewport_size({"width": width, "height": 800})
            page2.reload(wait_until="domcontentloaded")
            _poll(page2, _STATE_READY)
            overflow2 = page2.evaluate(
                "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
            )
            check(f"ready/{width}px 控制台无横向溢出", overflow2 <= 1, f"overflow={overflow2}")
            page2.screenshot(path=str(OUT_DIR / f"ui-ready-console-{width}.png"), full_page=True)
        page.set_viewport_size({"width": 1280, "height": 900})
        page2.set_viewport_size({"width": 1280, "height": 900})
        ctx.close()
    finally:
        svc.close()


def case_wedged(browser) -> None:
    def setup(s):
        s._wedged = True
        s._wedged_reason = "任务超期未返回（budget=45s），worker 疑似卡死"
        s._browser_alive = False

    svc = _Service(setup)
    try:
        ctx = browser.new_context(extra_http_headers={"Authorization": f"Bearer {TOKEN}"})
        page = ctx.new_page()
        errs = _open(page, svc.base + "/")
        text, cls, color, bg = _badge(page)
        check("wedged/状态页徽章文字", text == "wedged", text)
        check("wedged/状态页徽章有专属类", "wedged" in cls, cls)
        check("wedged/徽章是实心红（不是默认深色）",
              bg not in ("rgb(255, 255, 255)", "rgba(0, 0, 0, 0)"), bg)
        body = page.inner_text("#health")
        check("wedged/显示卡死原因", "卡死原因" in body and "budget" in body, body[:80])
        check("wedged/显示处置动作",
              "处置" in body and ("75" in body or "手动重启" in body), body[:120])
        check("wedged/无 JS 错误", not [e for e in errs if "503" not in e], str(errs[:2]))
        page.screenshot(path=str(OUT_DIR / "ui-wedged-statuspage.png"), full_page=True)

        page2 = ctx.new_page()
        page2.add_init_script(f"sessionStorage.setItem('cfsolve_token','{TOKEN}')")
        page2.on("dialog", lambda d: d.accept(TOKEN))
        errs2 = _open(page2, svc.base + "/console")
        text2, cls2, *_ = _badge(page2)
        banner = page2.inner_text("#banner")
        check("wedged/控制台徽章是 wedged", text2 == "wedged" and "wedged" in cls2, f"{text2}/{cls2}")
        check("wedged/控制台横幅说明不会自愈", "不会自愈" in banner, banner[:80])
        check("wedged/控制台无 JS 错误", not [e for e in errs2 if "503" not in e], str(errs2[:2]))
        page2.screenshot(path=str(OUT_DIR / "ui-wedged-console.png"), full_page=True)
        ctx.close()
    finally:
        svc.close()


def case_degraded(browser) -> None:
    def setup(s):
        s._ever_alive = True
        s._browser_alive = False

    svc = _Service(setup)
    try:
        ctx = browser.new_context(extra_http_headers={"Authorization": f"Bearer {TOKEN}"})
        page = ctx.new_page()
        _open(page, svc.base + "/")
        text, cls, *_ = _badge(page)
        check("degraded/徽章不是 wedged", text != "wedged", text)
        banner = page.inner_text("#banner")
        check("degraded/横幅说明正在自愈或启动中",
              ("自愈" in banner or "启动" in banner or "503" in banner), banner[:120])
        check("degraded/卡片来自本次响应，不冒充旧快照",
              page.evaluate("() => document.querySelectorAll('.card.stale').length") == 0,
              str(page.evaluate("() => document.querySelectorAll('.card.stale').length")))
        page.screenshot(path=str(OUT_DIR / "ui-degraded-statuspage.png"), full_page=True)
        ctx.close()
    finally:
        svc.close()


def case_no_token(browser) -> None:
    svc = _Service()
    try:
        ctx = browser.new_context()
        page = ctx.new_page()
        errs = _open(page, svc.base + "/")
        banner = page.inner_text("#banner")
        inflight = page.inner_text("#inflight-summary")
        check("无token/状态页横幅指明去哪拿 token", "token" in banner and "env" in banner, banner[:80])
        check("无token/在途面板不是空白", "token" in inflight, inflight[:60])
        check("无token/健康信息仍可见（公开）", "state" in page.inner_text("#health"))
        check("无token/无 JS 错误（401 是预期）",
              not [e for e in errs if "401" not in e], str(errs[:2]))
        page.screenshot(path=str(OUT_DIR / "ui-notoken-statuspage.png"), full_page=True)
        ctx.close()
    finally:
        svc.close()


def case_invalid_token(browser) -> None:
    """错误 token 必须被识别为「无效」，不能继续说「需要 token」。"""
    svc = _Service()
    try:
        # 状态页本身公开；前端用自己的 sessionStorage token 打 /status、/logs。
        ctx = browser.new_context()
        page = ctx.new_page()
        _open(page, svc.base + "/")
        page.fill("#token", "wrong-token")
        page.click("#save")
        _poll(page, "() => document.getElementById('state').textContent.trim() === 'token 无效'", 8000)
        text, cls, *_ = _badge(page)
        banner = page.inner_text("#banner")
        check("invalid-token/状态页徽章", text == "token 无效" and "unauthorized" in cls,
              f"{text}/{cls}")
        check("invalid-token/状态页横幅指出 401",
              "401" in banner and "token" in banner, banner[:120])
        page.screenshot(path=str(OUT_DIR / "ui-invalid-token-statuspage.png"), full_page=True)
        ctx.close()

        # /console 在 HTTP 层就要 Bearer；先用正确 token 打开页面，再换错 token 刷新。
        ctx2 = browser.new_context(extra_http_headers={"Authorization": f"Bearer {TOKEN}"})
        page2 = ctx2.new_page()
        page2.add_init_script(f"sessionStorage.setItem('cfsolve_token','{TOKEN}')")
        errs = _open(page2, svc.base + "/console")
        check("invalid-token/控制台初始可加载", _badge(page2)[0] == "ready", _badge(page2)[0])
        page2.context.set_extra_http_headers({})      # 清掉 context 级 Bearer，让页面只用 sessionStorage
        page2.fill("#token", "wrong-token")
        page2.click("#save")
        _poll(page2, "() => document.getElementById('state').textContent.trim() === 'token 无效'", 10000)
        text2, cls2, *_ = _badge(page2)
        banner2 = page2.inner_text("#banner")
        check("invalid-token/控制台服务徽章仍由 health 驱动",
              text2 == "ready", f"{text2}/{cls2}")
        check("invalid-token/控制台横幅指出 401",
              "401" in banner2 and "token" in banner2, banner2[:120])
        check("invalid-token/审计面板指出 401",
              "401" in page2.inner_text("#audit"), page2.inner_text("#audit")[:80])
        check("invalid-token/控制台无 JS 错误",
              not [e for e in errs if "401" not in e], str(errs[:2]))
        page2.screenshot(path=str(OUT_DIR / "ui-invalid-token-console.png"), full_page=True)
        ctx2.close()
    finally:
        svc.close()


def case_audit(browser) -> None:
    """审计日志区块（v6.3，参考 logdy）。

    看真浏览器里的四件事（静态断言都覆盖不到）：
      · 表格真的渲染出来了（行数对得上）
      · 失败行有红色标记、成功行是 ok
      · **列宽合理**（曾经因为缺 table 样式，"时间"列被撑得极宽、后面几列挤成一坨）
      · 无 JS 错误（曾经因为本页没有 esc()、我误用 innerHTML+esc，首屏直接报错）
    """
    import json as _j
    import tempfile
    from pathlib import Path as _P

    NL = chr(10)
    d = _P(tempfile.mkdtemp(prefix="ui-audit-"))
    f = d / "audit.jsonl"
    rows = [{"event": "solve", "req_id": f"r{i}", "host": f"site{i}.example.com",
             "ok": i % 3 != 0, "error_code": "" if i % 3 else "unsolved",
             "ms": 16530, "backend": "camoufox", "ts": 1761000000 + i} for i in range(8)]
    f.write_text(NL.join(_j.dumps(r) for r in rows) + NL, encoding="utf-8")

    svc = _Service(audit_path=str(f))
    try:
        ctx = browser.new_context(extra_http_headers={"Authorization": f"Bearer {TOKEN}"},
                                  viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        page.add_init_script(f"sessionStorage.setItem('cfsolve_token','{TOKEN}')")
        page.on("dialog", lambda dlg: dlg.accept(TOKEN))
        audit_hits: list[float] = []
        page.on("request", lambda r: audit_hits.append(time.time())
                if "/audit" in r.url else None)
        errs = _open(page, svc.base + "/console",
                    ready="() => document.querySelectorAll('#audit tbody tr').length === 8")
        area = page.locator("#audit")
        text = area.inner_text()
        check("audit/表格渲染出 8 行", page.locator("#audit tbody tr").count() == 8,
              str(page.locator("#audit tbody tr").count()))
        check("audit/含表头", "事件" in text and "目标" in text, text[:60])
        check("audit/失败行有标记", "unsolved" in text, text[:120])
        check("audit/耗时按秒显示（不是 16530 ms）",
              "16.5 s" in text and "16530 ms" not in text, text[:120])
        # 列宽：没有任何一列 > 总宽的一半（曾经"时间"列独占 414px/1210px）
        widths = page.evaluate(
            "() => Array.from(document.querySelectorAll('#audit thead th'))"
            ".map(th => Math.round(th.getBoundingClientRect().width))")
        total = sum(widths)
        check("audit/列宽未被单列撑爆",
              bool(widths) and max(widths) < total * 0.5, f"{widths} total={total}")
        check("audit/无 JS 错误", not [e for e in errs if "401" not in e], str(errs[:2]))
        # 顶部「刷新」必须把审计区块一起刷 —— 曾经它只刷 /health，审计表停在旧数据。
        # 探法：往审计文件里再追加一行，点顶部刷新，表里必须多出这一行。
        with f.open("a", encoding="utf-8") as fh:
            fh.write(_j.dumps({"event": "solve", "req_id": "rnew", "host": "新增.example.com",
                               "ok": True, "ms": 120, "backend": "cfsolve",
                               "ts": 1761000100}) + NL)
        page.click("#refresh")
        _poll(page, "() => document.querySelectorAll('#audit tbody tr').length === 9", 8000)
        after = page.locator("#audit tbody tr").count()
        check("audit/顶部刷新会带上审计区块", after == 9, f"rows={after}")
        check("audit/顶部刷新无新增 JS 错误",
              not [e for e in errs if "401" not in e], str(errs[-2:]))
        # 「审计刷新」按钮：只重拉 /audit，不重拉 /health。
        page.click("#audit-refresh")
        _poll(page, "() => !document.getElementById('audit-refresh').disabled", 5000)
        check("audit/审计刷新按钮可用",
              page.locator("#audit tbody tr").count() == 9, str(page.locator("#audit tbody tr").count()))
        # 「审计自动刷新」：开启后 5s 内必须有新的 /audit 请求；关闭后不再增加。
        before_auto = len(audit_hits)
        page.check("#audit-auto")
        time.sleep(5.6)
        after_auto = len(audit_hits)
        check("audit/自动刷新开启后会拉取", after_auto > before_auto,
              f"{before_auto}->{after_auto}")
        page.uncheck("#audit-auto")
        before_off = len(audit_hits)
        time.sleep(5.6)
        check("audit/自动刷新关闭后不再拉取", len(audit_hits) == before_off,
              f"{before_off}->{len(audit_hits)}")
        # 「清除 token」：必须清 sessionStorage、停定时器、清旧审计表。
        page.click("#forget")
        _poll(page, "() => document.getElementById('state').textContent.trim() === '未鉴权'", 5000)
        stored = page.evaluate("() => sessionStorage.getItem('cfsolve_token')")
        check("audit/清除 token 后 sessionStorage 为空", stored is None, repr(stored))
        check("audit/清除 token 后自动刷新取消", page.is_checked("#audit-auto") is False,
              str(page.is_checked("#audit-auto")))
        audit_text = page.inner_text("#audit")
        check("audit/清除 token 后旧审计表被清空",
              "未加载" in audit_text or "需要 token" in audit_text, audit_text[:80])
        before_forget = len(audit_hits)
        time.sleep(5.6)
        check("audit/清除 token 后不再后台拉取", len(audit_hits) == before_forget,
              f"{before_forget}->{len(audit_hits)}")
        # 「保存」按钮：重新输入 token 后恢复读取。
        page.fill("#token", TOKEN)
        page.click("#save")
        _poll(page, "() => document.querySelectorAll('#audit tbody tr').length === 9", 8000)
        check("audit/保存 token 后恢复审计表", page.locator("#audit tbody tr").count() == 9,
              str(page.locator("#audit tbody tr").count()))
        # 320px：页面不横向溢出，宽表在 #audit 内部滚动。
        page.set_viewport_size({"width": 320, "height": 800})
        page.reload(wait_until="domcontentloaded")
        _poll(page, "() => document.querySelectorAll('#audit tbody tr').length === 9", 10000)
        box = page.evaluate(
            "() => { const a=document.getElementById('audit');"
            " return {overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,"
            " scroll: a.scrollWidth, client: a.clientWidth}; }")
        check("audit/320px 页面无横向溢出", box["overflow"] <= 1, str(box))
        check("audit/320px 宽表在容器内滚动", box["scroll"] > box["client"], str(box))
        page.screenshot(path=str(OUT_DIR / "ui-audit-console-320.png"), full_page=True)
        page.set_viewport_size({"width": 1280, "height": 900})
        page.screenshot(path=str(OUT_DIR / "ui-audit-console.png"), full_page=True)
        ctx.close()
    finally:
        svc.close()


def case_stale(browser) -> None:
    """页面已打开后服务挂掉：刷新必须降级到非 ready，并显示可读横幅。"""
    svc = _Service()
    try:
        ctx = browser.new_context(extra_http_headers={"Authorization": f"Bearer {TOKEN}"})
        page = ctx.new_page()
        page.add_init_script(f"sessionStorage.setItem('cfsolve_token','{TOKEN}')")
        errs = _open(page, svc.base + "/")
        check("stale/初始 ready", _badge(page)[0] == "ready", _badge(page)[0])
        svc.close()
        page.click("#refresh")
        _poll(page, "() => document.getElementById('state').textContent.trim() !== 'ready'", 8000)
        text, cls, *_ = _badge(page)
        banner = page.inner_text("#banner")
        stale = page.evaluate("() => document.querySelectorAll('.card.stale').length")
        check("stale/服务停止后徽章不再是 ready", text != "ready", f"{text}/{cls}")
        check("stale/横幅说明服务不可用", bool(banner.strip()), banner[:120])
        check("stale/503 是当前响应，不冒充旧快照", stale == 0, f"stale={stale}")
        check("stale/无新增 JS 错误",
              not [e for e in errs if "503" not in e and "401" not in e], str(errs[:2]))
        page.screenshot(path=str(OUT_DIR / "ui-stale-statuspage.png"), full_page=True)
        ctx.close()
    finally:
        try:
            svc.close()
        except Exception:  # noqa: BLE001
            pass


def case_dark(browser) -> None:
    """深色主题：两页都不得有 axe 违规；截图作为视觉证据。"""
    svc = _Service()
    try:
        ctx = browser.new_context(extra_http_headers={"Authorization": f"Bearer {TOKEN}"},
                                  color_scheme="dark")
        page = ctx.new_page()
        page.add_init_script(f"sessionStorage.setItem('cfsolve_token','{TOKEN}')")
        _open(page, svc.base + "/")
        check("dark/状态页 ready", _badge(page)[0] == "ready", _badge(page)[0])
        check_axe(page, "dark/状态页")
        page.screenshot(path=str(OUT_DIR / "ui-dark-statuspage.png"), full_page=True)

        page2 = ctx.new_page()
        page2.add_init_script(f"sessionStorage.setItem('cfsolve_token','{TOKEN}')")
        _open(page2, svc.base + "/console")
        check("dark/控制台 ready", _badge(page2)[0] == "ready", _badge(page2)[0])
        check_axe(page2, "dark/控制台")
        page2.screenshot(path=str(OUT_DIR / "ui-dark-console.png"), full_page=True)
        ctx.close()
    finally:
        svc.close()


def case_unreachable(browser) -> None:
    svc = _Service()
    base = svc.base
    svc.close()          # 端口立刻关掉
    ctx = browser.new_context()
    page = ctx.new_page()
    # 页面本身已经打不开了 —— goto 会直接抛 ERR_CONNECTION_REFUSED（这就是预期行为）
    try:
        page.goto(base + "/", wait_until="domcontentloaded", timeout=5000)
    except Exception as e:  # noqa: BLE001
        check("不可达/页面加载失败可被捕获", "ERR_CONNECTION_REFUSED" in str(e), str(e)[:60])
    else:
        check("不可达/页面加载失败可被捕获", False, "端口已关但仍加载成功？")
    ctx.close()


CASES = {
    "ready": case_ready,
    "wedged": case_wedged,
    "degraded": case_degraded,
    "no-token": case_no_token,
    "invalid-token": case_invalid_token,
    "audit": case_audit,
    "stale": case_stale,
    "dark": case_dark,
    "unreachable": case_unreachable,
}


def main() -> int:
    global current_case
    ap = argparse.ArgumentParser(description="UI 验收（真 Chrome）")
    ap.add_argument("--only", default="", help="只跑某个状态：" + ",".join(CASES))
    ap.add_argument("--json-out", default="", help="把验收结果写成 JSON（证据用）")
    args = ap.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    started = time.time()

    from playwright.sync_api import sync_playwright

    names = [args.only] if args.only else list(CASES)
    unknown = [n for n in names if n not in CASES]
    if unknown:      # 写错状态名不该甩一个 KeyError 栈出来
        print(f"未知状态：{unknown}\n可选：{', '.join(CASES)}", file=sys.stderr)
        return 2
    with sync_playwright() as p:
        # Playwright 自带的 Chromium 没装时，允许用系统 Chrome（与 site/verify-site.mjs 同一约定）。
        browser = p.chromium.launch(headless=True, executable_path=os.environ.get("CHROME_PATH") or None)
        try:
            for name in names:
                current_case = name
                print(f"\n[{name}]")
                CASES[name](browser)
        finally:
            browser.close()

    failed = [r for r in results if not r[1]]
    print(f"\nRESULT: {len(results) - len(failed)}/{len(results)} 通过")
    if args.json_out:
        try:
            sha = subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True,
                encoding="utf-8", errors="replace").strip()
        except (OSError, subprocess.CalledProcessError):
            sha = ""
        shots = sorted(
            str(p.relative_to(ROOT)).replace("\\", "/")
            for p in OUT_DIR.glob("ui-*.png") if p.stat().st_mtime >= started)
        payload = {
            "schema_version": 1,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "git_sha": sha,
            "total": len(results),
            "passed": len(results) - len(failed),
            "failed": len(failed),
            "checks": [
                {"case": name.split("/", 1)[0], "name": name, "ok": ok, "detail": detail}
                for name, ok, detail in results
            ],
            "axe": axe_results,
            "screenshots": shots,
        }
        # `check()` 只存 (name, ok, detail)，case 归属按名称前缀还原；
        # 名称本身以 `<状态>/` 开头，足够稳定。
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"JSON: {out}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())