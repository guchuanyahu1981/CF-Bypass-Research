#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CF-Bypass-Research 跨平台统一入口（v5.1 T-80）。

与 run.ps1 的子命令一一对应；run.ps1 内部转发到这里，两套逻辑不再漂移。
仅用 stdlib；verify 按 run.ps1 的既有顺序 fail-fast：pytest → 覆盖率 → compileall →
go → findings → e2e → smoke → repo hygiene → research doctor → service-report(v6.2 golden)。

四类验收（v6.2 起全部可复跑，不再是人工一次性快照）：
    python run.py ui          # UI 截图验收：真 Chrome 跑 5 种状态 + 按钮交互，截图落 plans/证据/
    python run.py realcalls   # 真实调用验收：真进程 + 真 token，全端点 × 鉴权两态
    python run.py e2e         # 真实 E2E 验收：真进程 + 真浏览器（--skip-browser 可跳过浏览器段）
    python run.py report      # 服务报告 golden（响应字段形状）；--write 重新生成

用法：
    python run.py doctor [--port N]
    python run.py test | quick | go | findings | verify | e2e | bench | start | stop
    python run.py smoke [--port N] [--no-auth]
    python run.py ui [--only ready|wedged|degraded|no-token|audit|unreachable]
    python run.py realcalls | report [--write]
    python run.py egress --proxy URL [--proxy URL ...] [--proxy-egress-ip URL=IP ...]
                    [--egress-echo-url URL]          # 唯一会出站的子命令
    python run.py mcp        # MCP stdio 适配器（5 工具；CF_SOLVE_URL 指向服务）
"""
import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib import request as urlreq
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parent

# Windows 控制台默认 GBK，打印 '↔'/'✅' 等字符会 UnicodeEncodeError。
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

# 子命令 → 实现函数。键集合必须与 run.ps1 的 ValidateSet 完全相等
# （tests/test_runpy.py::test_runpy_targets_match_runps1 锁死）。
COMMANDS = {
    'doctor': None,
    'test': None,
    'quick': None,
    'go': None,
    'findings': None,
    'smoke': None,
    'e2e': None,
    'start': None,
    'stop': None,
    'bench': None,
    'verify': None,
    'ui': None,
    'realcalls': None,
    'accept': None,
    'review': None,
    'report': None,
    'egress': None,
    'mcp': None,
    'help': None,
}


def step(msg):
    print(f"\n=== {msg} ===")


def _redact(cmd) -> str:
    """回显命令时把 `scheme://user:pass@host` 里的口令抹掉。

    这一行是打在**终端**上的，而终端经常被重定向进文件 / CI 日志 / 贴进 issue
    （`python run.py egress --proxy http://u:p@h:8080` 是最常见的用法）。
    实际执行的 argv 不受影响 —— 只改显示。
    """
    return " ".join(re.sub(r"(://[^:/@\s]+):[^@\s]*@", r"\1:***@", str(c))
                    for c in cmd)


def run(cmd, cwd=None, **kw):
    """跑外部命令，透传退出码；command not found 视为失败 127。"""
    print(f"+ {_redact(cmd)}")
    try:
        return subprocess.call([str(c) for c in cmd],
                               cwd=str(cwd or ROOT), **kw)
    except FileNotFoundError:
        print(f"FAIL: 找不到可执行文件 {cmd[0]}", file=sys.stderr)
        return 127


def read_env_token():
    """读 CF_SOLVE_TOKEN：环境变量优先，否则 .env（与 run.ps1 stop 逻辑一致）。"""
    tok = os.environ.get("CF_SOLVE_TOKEN")
    if tok:
        return tok
    envf = ROOT / ".env"
    if envf.exists():
        for line in envf.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("CF_SOLVE_TOKEN="):
                return line.split("=", 1)[1].strip()
    return None


def cmd_doctor(a):
    return run([sys.executable, "scripts/doctor.py", "--port", a.port])


def cmd_test(a):
    # v6.3：覆盖率门禁按范围分算（solver ≥80 / captchas ≥20），逐模块下限在
    # docs/coverage-policy.json。`--cov-fail-under` 已由 check-coverage.mjs 接手：
    # 两个范围混在一个整体数字里会互相抵消。
    step("Python 全量测试 + 覆盖率（solver/ 与 captchas/）")
    rc = run([sys.executable, "-m", "pytest", "tests/", "-q",
              "--cov=solver", "--cov=captchas", "--cov-report=term-missing",
              "--cov-report=json:coverage.json"])
    if rc:
        return rc
    step("逐模块 + 分范围覆盖率门禁")
    return run(["node", "scripts/check-coverage.mjs", "--coverage-json=coverage.json"])


def cmd_quick(a):
    step("定向快测（改一处代码时用这个，别每次跑全量）")
    rc1 = run([sys.executable, "-m", "pytest",
               "tests/test_cache.py", "tests/test_cache_sqlite.py", "-q"])
    rc2 = run([sys.executable, "-m", "pytest", "tests/", "-q",
               "-k", "route or guard or ttl or budget"])
    return 1 if (rc1 or rc2) else 0


def cmd_go(a):
    step("Go: vet + test -race")
    rc = run(["go", "vet", "./..."], cwd=ROOT / "gocfsolve")
    if rc:
        return rc
    return run(["go", "test", "-race", "./..."], cwd=ROOT / "gocfsolve")  # 注：run() cwd 由 kw 覆盖为 gocfsolve


def cmd_findings(a):
    step("结论库一致性校验（findings.json ↔ 仓库文件）")
    if not shutil.which("node"):
        print("WARN: 未安装 node，跳过 findings 校验")
        return 0
    return run(["node", "scripts/validate-findings.mjs"])


def cmd_e2e(a):
    step("真进程 E2E 验收（离线）")
    args = [sys.executable, "scripts/e2e_local.py", "--json-out", "evidence/e2e-local.json"]
    if a.skip_browser:
        args.append("--skip-browser")
    return run(args)


def cmd_bench(a):
    step("离线压测（不打真实站点）")
    rcs = [
        run([sys.executable, "scripts/loadtest.py", "--mode", "hot",
             "--workers", "8", "--duration", "5"]),
        run([sys.executable, "scripts/loadtest.py", "--mode", "cold",
             "--workers", "16", "--solve-delay", "0.05"]),
        run([sys.executable, "scripts/loadtest.py", "--mode", "share",
             "--processes", "4", "--iterations", "2"]),
    ]
    return next((rc for rc in rcs if rc), 0)


def _free_port() -> int:
    """给 verify 里的 smoke 挑一个空闲回环端口（避免撞上已在跑的服务）。"""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _smoke_get(url: str, token: str, timeout: float = 20.0) -> tuple[int, dict]:
    req = urlreq.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urlreq.urlopen(req, timeout=timeout) as resp:
        return int(resp.status), json.loads(resp.read())


def cmd_smoke(a):
    step(f"起服务并做冒烟（port={a.port}）")
    cache_dir = Path(tempfile.mkdtemp(prefix="cfsolve-smoke-"))
    args = [sys.executable, "solver/service.py", "--port", str(a.port),
            "--cache-dir", str(cache_dir)]
    if a.no_auth:
        args.append("--no-auth")
    proc = subprocess.Popen(args, cwd=ROOT)
    try:
        base = f"http://127.0.0.1:{a.port}"
        deadline = time.monotonic() + 120
        ready = None
        while time.monotonic() < deadline:
            try:
                with urlreq.urlopen(f"{base}/health", timeout=3) as r:
                    ready = json.loads(r.read())
                if ready.get("state") == "ready":
                    break
            except (URLError, OSError, ValueError):
                time.sleep(0.5)
        if not ready or ready.get("state") != "ready":
            print("FAIL: 服务 120s 内未就绪（看 solver/service.py 的启动输出）",
                  file=sys.stderr)
            return 1
        print(f"OK  /health ready（browser={ready.get('browser')}, "
              f"cache={ready.get('cache_backend')}）")

        token = "" if a.no_auth else (read_env_token() or "")
        try:
            _, d = _smoke_get(f"{base}/detect?url=https://example.com/", token)
        except HTTPError as e:
            print(f"FAIL: /detect 返回 HTTP {e.code}。默认配置需要 Bearer："
                  "检查 .env 的 CF_SOLVE_TOKEN，或显式加 --no-auth 调试。", file=sys.stderr)
            return 1
        print(f"OK  /detect example.com → protected={d.get('protected')} "
              f"route={d.get('recommendation', {}).get('route')}")

        # SSRF 守卫：必须拿到 **400 + error_code=invalid-request**。
        # 不能把任何 URLError（401/连接失败）都当成“守卫生效”——那是假阳性。
        try:
            status, body = _smoke_get(
                f"{base}/detect?url=http://127.0.0.1:{a.port}/health", token, timeout=10)
            print(f"FAIL: SSRF 守卫没有拒绝内网 URL（HTTP {status}）", file=sys.stderr)
            return 1
        except HTTPError as e:
            if e.code != 400:
                print(f"FAIL: SSRF 守卫应返回 400 invalid-request，实际 {e.code}", file=sys.stderr)
                return 1
            try:
                body = json.loads(e.read().decode("utf-8", "replace"))
            except Exception:  # noqa: BLE001
                body = {}
            if body.get("error_code") != "invalid-request":
                print(f"FAIL: SSRF 拒绝缺少 error_code=invalid-request: {body}", file=sys.stderr)
                return 1
            print("OK  SSRF 守卫拦住了内网 URL（400 invalid-request）")
        except (URLError, OSError) as e:
            print(f"FAIL: SSRF 检查请求失败（{e}）——连接失败不等于守卫生效", file=sys.stderr)
            return 1
        return 0
    finally:
        proc.kill()
        shutil.rmtree(cache_dir, ignore_errors=True)


def cmd_start(a):
    step(f"前台启动（port={a.port}，Ctrl+C 退出）")
    args = [sys.executable, "solver/service.py", "--port", str(a.port),
            "--cache-dir", "./cache"]
    if a.no_auth:
        args.append("--no-auth")
    try:
        return subprocess.call(args, cwd=ROOT)
    except KeyboardInterrupt:
        return 0


def cmd_stop(a):
    """优雅停止监听 a.port 的服务：先 /shutdown（带 token），再强制终止。"""
    step(f"优雅停止监听 {a.port} 的服务")
    port = a.port

    def listening_pids():
        out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace").stdout
        pids = []
        # 必须精确匹配 `:<port>` 后紧跟空白：子串匹配会把 `:8878` 命中 `:88780`，
        # 于是"停 8878"顺手杀掉了另一个服务（破坏性误杀，复审点名）。
        pattern = re.compile(rf":{re.escape(str(port))}\s")
        for line in out.splitlines():
            if "LISTENING" in line and pattern.search(line):
                pid = line.split()[-1]
                if pid.isdigit():
                    pids.append(pid)
        return pids

    pids = listening_pids()
    if not pids:
        print(f"没有进程监听 {port}")
        return 0

    token = read_env_token()
    graceful = False
    try:
        req = urlreq.Request(f"http://127.0.0.1:{port}/shutdown", method="POST",
                             data=b"{}", headers={"Content-Type": "application/json"})
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        with urlreq.urlopen(req, timeout=5) as r:
            r.read()
        graceful = True
        print("已请求优雅退出（等 15s 让在途请求收尾）")
        for _ in range(30):
            time.sleep(0.5)
            if not listening_pids():
                break
    except (URLError, OSError) as e:
        print(f"优雅退出不可用（{e}），回退为强制终止")

    for pid in listening_pids():
        # 不要写成跨行 f-string：PEP 701（f-string 内换行）是 Python 3.12+，
        # CI 跑 3.11 会直接 SyntaxError（本轮 CI 抓到）。
        reason = "优雅退出未在 15s 内完成" if graceful else "无 token 或端点不可用"
        print(f"强制终止 PID {pid}（{reason}）")
        if os.name == "nt":
            subprocess.call(["taskkill", "/F", "/PID", pid])
        else:
            subprocess.call(["kill", "-9", pid])
    return 0


def run_steps(steps):
    """fail-fast：第一个非零返回即停，退出码 = 该步骤退出码。"""
    for name, fn in steps:
        rc = fn()
        if rc != 0:
            print(f"\nFAIL: {name} 未通过（exit={rc}），后续步骤不执行",
                  file=sys.stderr)
            return rc
    return 0


def cmd_verify(a):
    step("verify: Python 测试 + 覆盖率")
    if a.fast:
        pytest_files = ["tests/test_runpy.py"]
        cov = []
    else:
        pytest_files = ["tests/"]
        cov = ["--cov=solver", "--cov=captchas", "--cov-report=term",
               "--cov-report=json:coverage.json"]

    def py():
        return run([sys.executable, "-m", "pytest", *pytest_files, "-q", *cov])

    def compileall():
        return run([sys.executable, "-m", "compileall", "-q", "solver", "scripts", "tests", "captchas"])

    def coverage_gate():
        # --fast 模式没产出 coverage.json；拿旧文件判门禁是假信号。
        if getattr(a, "fast", False):
            print("SKIP: --fast 模式没有覆盖率产物，跳过逐模块门禁")
            return 0
        return run(["node", "scripts/check-coverage.mjs", "--coverage-json=coverage.json"])

    def validate_docs():
        # 与 CI 对齐：本地 verify 全绿不能掩盖文档数字/版本漂移。
        if getattr(a, "fast", False):
            print("SKIP: --fast 模式不跑文档数字校验（没有完整覆盖率）")
            return 0
        return run(["node", "scripts/validate-docs.mjs", "--coverage-json=coverage.json"])

    def validate_openapi():
        return run(["node", "scripts/validate-openapi.mjs"])

    def gates_selftest():
        return run(["node", "scripts/gates.selftest.mjs"])

    def go():
        return cmd_go(a)

    def findings():
        return cmd_findings(a)

    def e2e():
        rc = cmd_e2e(a)
        if rc:
            print("E2E 验收失败（看 evidence/e2e-local.json 的 failed 项）", file=sys.stderr)
        return rc

    def hygiene():
        p = ROOT / "scripts" / "check_repo_hygiene.py"
        if not p.exists():
            print("FAIL: scripts/check_repo_hygiene.py 不存在（T-69 未落地）。"
                  "落地后 verify 自动纳入；或先跑其余步骤定位问题。", file=sys.stderr)
            return 1
        return run([sys.executable, "scripts/check_repo_hygiene.py"])

    def service_report():
        """确定性服务报告 vs golden（v6.2，借鉴 pinchtab 的 docs/audit.md）。

        为什么放进 verify：`validate-openapi.mjs` 只校验**路由集合**，
        新加/删掉一个响应字段它完全看不见 —— v6.1 的 `captcha_types` 与
        v6.2 的 `wedge` 就是在"门禁全绿"的情况下漏进文档的。
        """
        p = ROOT / "scripts" / "service_report.py"
        if not p.exists():
            print("FAIL: scripts/service_report.py 不存在（v6.2 服务报告门禁未落地）。", file=sys.stderr)
            return 1
        return run([sys.executable, str(p)])

    def research_doctor():
        p = ROOT / "scripts" / "research.py"
        if not p.exists():
            print("FAIL: scripts/research.py 不存在（T-67 研究台未落地）。"
                  "其 doctor 子命令缺失，无法校验研究台环境。", file=sys.stderr)
            return 1
        return run([sys.executable, "scripts/research.py", "doctor"])

    def smoke():
        # 提交前验收必须包含"真进程冒烟"：/health → /detect → SSRF 400。
        # 这一步默认需要 Bearer（smoke 自己从 .env 读），不再要求调用方记得加 --no-auth。
        # 注意：smoke 会出网探测 example.com；离线环境必须显式 --skip-smoke（记 SKIP，不冒充通过）。
        if getattr(a, "skip_smoke", False):
            print("SKIP: --skip-smoke（离线环境；smoke 需要出网）", file=sys.stderr)
            return 0
        return cmd_smoke(argparse.Namespace(port=_free_port(), no_auth=False))

    rc = run_steps([
        ("pytest", py),
        ("coverage-gate", coverage_gate),
        ("validate-docs", validate_docs),
        ("validate-openapi", validate_openapi),
        ("gates-selftest", gates_selftest),
        ("compileall", compileall),
        ("go vet/test", go),
        ("findings", findings),
        ("e2e", e2e),
        ("smoke", smoke),
        ("repo-hygiene(T-69)", hygiene),
        ("research-doctor(T-67)", research_doctor),
        ("service-report(golden)", service_report),
    ])
    if rc == 0:
        print("\n全部通过 ✅")
    return rc


def cmd_ui(a):
    """UI 验收：真 Chrome 跑状态矩阵 + 按钮交互 + 截图 + axe（v6.3）。"""
    args = [sys.executable, str(ROOT / "scripts" / "verify_ui_e2e.py")]
    if getattr(a, "value", ""):
        args += ["--only", a.value]
    args += ["--json-out", getattr(a, "json_out", "") or "evidence/ui-acceptance.json"]
    return run(args)


def cmd_realcalls(a):
    """真实调用验收：真进程 + 真 token，全端点 × 鉴权两态（v6.2 脚本化）。"""
    out = getattr(a, "json_out", "") or "evidence/realcalls.json"
    return run([sys.executable, str(ROOT / "scripts" / "verify_real_calls.py"),
                "--json-out", out])


def cmd_review(a):
    """独立代码复审：按领域拆成三份 claude -p 复审，再汇总成一个 VERDICT。

    为什么不一次喂全量 diff：全量 ~700KB，单次 claude 复审会超时；拆成
    solver / runtime / tests 三份，每份都能在超时内给出可读完的结论。
    """
    script = ROOT / "scripts" / "review_diff.py"
    if not script.exists():
        print("FAIL: scripts/review_diff.py 不存在（四类验收的代码复审轨道缺失）。",
              file=sys.stderr)
        return 1
    base = Path(getattr(a, "out", "") or "evidence/code-review.md")
    if not base.is_absolute():
        base = ROOT / base
    areas = [
        ("service", ["solver/service.py"]),
        ("ui", ["solver/statuspage.py", "solver/console_html.py",
                "solver/ui_tokens.py", "solver/_page_common.py"]),
        ("core-detect", ["solver/detect.py"]),
        ("core-mcp", ["solver/client.py", "solver/mcp_server.py", "solver/ratelimit.py"]),
        ("core-runtime", ["solver/prewarm.py", "solver/auth.py", "solver/bootstrap.py",
                          "solver/backends.py", "solver/errors.py"]),
        ("runtime", ["gocfsolve/cfsolve.go", "gocfsolve/pool.go",
                     "gocfsolve/cfsolve_test.go", "gocfsolve/pool_test.go",
                     "run.py", ".github", "site"]),
        ("scripts", ["scripts"]),
        ("tests-core", ["tests/test_service.py", "tests/test_governance.py",
                        "tests/test_ratelimit.py", "tests/test_cache.py",
                        "tests/test_cache_sqlite.py", "tests/test_watchdog.py",
                        "tests/test_auth_probe_gaps.py", "tests/test_auth_token_race.py",
                        "tests/test_auth_matrix.py", "tests/test_mcp_server.py"]),
        ("tests-a", ["tests/test_acceptance_v41.py", "tests/test_observability.py",
                     "tests/test_detect.py", "tests/test_detect_golden.py",
                     "tests/test_detect_source.py", "tests/test_route.py",
                     "tests/test_runpy.py", "tests/test_acceptance_report.py"]),
        ("tests-b", ["tests/test_backends.py", "tests/test_bootstrap.py",
                     "tests/test_compat.py", "tests/test_multicaptcha.py",
                     "tests/test_prewarm.py", "tests/test_captcha_pure.py",
                     "tests/test_captchas_offline.py", "tests/test_record_traffic.py",
                     "tests/test_strategies.py", "tests/test_properties.py"]),
    ]
    reports = []
    for name, paths in areas:
        out = base.with_name(f"{base.stem}-{name}{base.suffix}")
        rc = run([sys.executable, str(script), "--paths", *paths, "--out", str(out)])
        if rc:
            return rc
        reports.append((name, out))
    verdicts = []
    for name, out in reports:
        try:
            text = out.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        all_v = re.findall(r"VERDICT:\s*(PASS|FAIL)", text, re.I)
        verdicts.append((name, all_v[-1].upper() if all_v else "UNKNOWN", out))
    overall = "PASS" if all(v == "PASS" for _, v, _ in verdicts) else "FAIL"
    lines = ["# 独立代码复审汇总", "",
             "| 领域 | 结论 | 详细报告 |", "|---|---|---|"]
    for name, verdict, out in verdicts:
        try:
            shown = out.relative_to(ROOT).as_posix()
        except ValueError:
            shown = out.as_posix()
        lines.append(f"| {name} | {verdict} | `{shown}` |")
    lines += ["", f"VERDICT: {overall}", ""]
    base.parent.mkdir(parents=True, exist_ok=True)
    base.write_text("\n".join(lines), encoding="utf-8")
    print(f"review aggregate: {base} -> {overall}")
    return 0 if overall == "PASS" else 1


def cmd_accept(a):
    """四类验收编排：verify → UI 截图 → 真实调用 → 真实 E2E → 代码复审 → 汇总。"""
    step("accept: 四类验收编排")
    verify_args = argparse.Namespace(
        port=a.port, no_auth=False, skip_browser=a.skip_browser,
        skip_smoke=a.skip_smoke, fast=getattr(a, "fast", False),
    )
    report_args = argparse.Namespace(write=False)

    def verify():
        return cmd_verify(verify_args)

    def ui():
        # UI 验收必须真浏览器；--skip-browser 与它互斥。冲突时写 SKIP 标记并继续
        # 其余轨道 —— acceptance_report 会把 overall 判为 partial，不中断整条验收。
        if a.skip_browser:
            marker = ROOT / "evidence" / "ui-acceptance-SKIPPED.json"
            marker.write_text(json.dumps({
                "schema_version": 1, "skipped": True,
                "reason": "accept --skip-browser：UI 截图验收必须真浏览器",
            }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print("SKIP: --skip-browser 与 UI 验收冲突；已写 ui-acceptance-SKIPPED.json，"
                  "汇总将标 partial")
            return 0
        return run([sys.executable, str(ROOT / "scripts" / "verify_ui_e2e.py"),
                    "--json-out", "evidence/ui-acceptance.json"])

    def realcalls():
        return cmd_realcalls(argparse.Namespace(json_out="evidence/realcalls.json"))

    def e2e():
        return cmd_e2e(a)

    def report():
        return cmd_report(report_args)

    def review():
        if getattr(a, "skip_review", False):
            marker = ROOT / "evidence" / "code-review-SKIPPED.md"
            marker.write_text(
                "# 独立代码复审\n\n"
                "- skipped: true\n"
                "- reason: accept --skip-review\n\n"
                "本文件表示本轮验收**没有跑**代码复审轨道。\n"
                "acceptance_report 会据此把 overall 判为 partial，不会复用旧 PASS。\n",
                encoding="utf-8")
            print("SKIP: --skip-review（已写 code-review-SKIPPED.md；汇总会标 partial）")
            return 0
        return cmd_review(argparse.Namespace(out="evidence/code-review.md"))

    rc = run_steps([
        ("verify", verify),
        ("ui-acceptance", ui),
        ("realcalls", realcalls),
        ("e2e", e2e),
        ("service-report", report),
        ("code-review", review),
    ])
    if rc:
        return rc
    return run([sys.executable, str(ROOT / "scripts" / "acceptance_report.py"),
                "--ui", "evidence/ui-acceptance.json",
                "--realcalls", "evidence/realcalls.json",
                "--e2e", "evidence/e2e-local.json",
                "--review", ("evidence/code-review-SKIPPED.md"
                             if getattr(a, "skip_review", False)
                             else "evidence/code-review.md"),
                "--ui-skipped", str(bool(a.skip_browser)),
                "--out", "evidence/acceptance.json"])


def cmd_report(a):
    """确定性服务报告 vs golden（响应字段形状门禁）。"""
    p = ROOT / "scripts" / "service_report.py"
    return run([sys.executable, str(p)] + (["--write"] if getattr(a, "write", False) else []))


def cmd_egress(a):
    """出口 IP 实测校验（v6.3，参考 proxypool 的 tester 思路）。

    为什么单独一个命令：它会**出站**（经每个代理请求一次 IP 回显）。
    doctor 默认不带网络等待；要实测就显式跑这个。
    """
    cmd = [sys.executable, str(ROOT / "scripts" / "doctor.py"),
           "--offline", "--verify-egress"]
    for p in getattr(a, "proxy", []) or []:
        cmd += ["--proxy", p]
    for p in getattr(a, "proxy_egress_ip", []) or []:
        cmd += ["--proxy-egress-ip", p]
    if getattr(a, "egress_echo_url", ""):
        cmd += ["--egress-echo-url", a.egress_echo_url]
    return run(cmd)


def cmd_mcp(a):
    """MCP stdio 适配器：stdout 是协议通道，不能混任何日志/提示。

    不能用 `run()`：它会先 `print("+ ...")` 到 stdout，直接破坏 MCP 握手。
    """
    return subprocess.call([sys.executable, "-m", "solver.mcp_server"], cwd=str(ROOT))


def cmd_help(a):
    print(__doc__)
    return 0


for _name in COMMANDS:
    fn = globals().get(f"cmd_{_name}")
    COMMANDS[_name] = fn or cmd_help


def build_parser():
    p = argparse.ArgumentParser(prog="run.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--port", type=int, default=8878)
    common.add_argument("--no-auth", action="store_true", help="鉴权关闭（smoke/start）")
    common.add_argument("--skip-browser", action="store_true", help="e2e 跳过真浏览器")
    common.add_argument("--skip-smoke", action="store_true",
                        help="verify 跳过 smoke（离线/隔离环境；smoke 需要出网探测 example.com）")
    common.add_argument("--fast", action="store_true",
                        help="verify 只跑定向测试不做覆盖率（T-80 验证用）")
    for name in COMMANDS:
        sp = sub.add_parser(name, parents=[common])
        if name == "ui":
            sp.add_argument("--only", dest="value", default="",
                            help="只跑某个状态：ready/wedged/degraded/no-token/invalid-token/audit/stale/dark/unreachable")
            sp.add_argument("--json-out", default="evidence/ui-acceptance.json",
                            help="验收结果 JSON（证据）")
        if name == "realcalls":
            sp.add_argument("--json-out", default="evidence/realcalls.json",
                            help="验收结果 JSON（证据）")
        if name == "accept":
            sp.add_argument("--skip-review", action="store_true",
                            help="跳过独立代码复审（汇总会标 partial，不作为完整验收）")
        if name == "review":
            sp.add_argument("--out", default="evidence/code-review.md",
                            help="复审报告落盘路径")
        if name == "report":
            sp.add_argument("--write", action="store_true", help="重新生成 golden")
        if name == "egress":
            sp.add_argument("--proxy", action="append", default=[], help="要实测的出口（可重复）")
            sp.add_argument("--proxy-egress-ip", action="append", default=[],
                            help="已登记的出口 IP，格式 scheme://host:port=1.2.3.4")
            sp.add_argument("--egress-echo-url", default="",
                            help="IP 回显地址（默认 Cloudflare /cdn-cgi/trace）")
    return p


def main(argv=None):
    a = build_parser().parse_args(argv)
    return COMMANDS[a.command](a)


if __name__ == "__main__":
    sys.exit(main())
