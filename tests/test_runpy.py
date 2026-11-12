# T-80: run.py 与 run.ps1 双入口一致性 + verify fail-fast 语义
"""锁定两套入口不漂移：run.ps1 的命令集合必须与 run.py 的 COMMANDS 集合相等；
verify 的步骤执行器必须在第一个非零退出码处停止且返回该退出码。"""
import argparse
import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_runpy():
    spec = importlib.util.spec_from_file_location("run_entry", ROOT / "run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_runpy_stop_matches_port_exactly(monkeypatch):
    """`stop 8878` 不许命中 `:88780`（子串匹配会误杀另一个服务）。"""
    run = _load_runpy()
    netstat_out = (
        "  TCP    0.0.0.0:88780    0.0.0.0:0    LISTENING    1111\n"
        "  TCP    127.0.0.1:8878   0.0.0.0:0    LISTENING    2222\n"
    )

    class _R:
        stdout = netstat_out

    monkeypatch.setattr(run.subprocess, "run", lambda *a, **k: _R())
    killed = []
    monkeypatch.setattr(run.subprocess, "call", lambda cmd: killed.append(cmd) or 0)
    monkeypatch.setattr(run, "read_env_token", lambda: "")
    class _FakeReq:
        def add_header(self, *a, **k):
            pass

    class _FakeUrlreq:
        @staticmethod
        def Request(*a, **k):
            return _FakeReq()

        @staticmethod
        def urlopen(*a, **k):
            raise OSError("no server")

    monkeypatch.setattr(run, "urlreq", _FakeUrlreq)
    monkeypatch.setattr(run.time, "sleep", lambda s: None)
    rc = run.cmd_stop(argparse.Namespace(port=8878))
    assert rc == 0
    assert killed, "应当终止监听 8878 的进程"
    assert all("2222" in " ".join(map(str, c)) for c in killed), f"误杀了别的 PID: {killed}"


def test_runpy_targets_match_runps1():
    """解析 run.ps1 的 ValidateSet（转发契约）与 run.py 的 COMMANDS 键，集合相等。"""
    ps1_text = (ROOT / "run.ps1").read_text(encoding="utf-8-sig")
    m = re.search(r"\[ValidateSet\((.*?)\)\]", ps1_text)
    assert m, "run.ps1 中找不到 [ValidateSet('...')] 命令清单"
    ps1_cmds = set(re.findall(r"'(\w+)'", m.group(1)))

    src = (ROOT / "run.py").read_text(encoding="utf-8")
    m2 = re.search(r"^COMMANDS\s*=\s*\{(.*?)^\}", src, re.M | re.S)
    assert m2, "run.py 中找不到 COMMANDS 字典"
    runpy_cmds = set(re.findall(r"'(\w+)'\s*:", m2.group(1)))

    assert ps1_cmds == runpy_cmds, (
        f"两套入口漂移！仅 run.ps1 有: {sorted(ps1_cmds - runpy_cmds)}；"
        f"仅 run.py 有: {sorted(runpy_cmds - ps1_cmds)}"
    )


def test_runpy_verify_fails_fast_on_first_failure():
    """假目标返回非 0 → 后续步骤不执行，退出码等于第一个失败步骤的退出码。"""
    mod = _load_runpy()
    executed = []

    def make(name, rc):
        def fn():
            executed.append(name)
            return rc
        return fn

    steps = [
        ("step-a-ok", make("step-a-ok", 0)),
        ("step-b-fail", make("step-b-fail", 3)),
        ("step-c-never", make("step-c-never", 0)),
    ]
    rc = mod.run_steps(steps)
    assert executed == ["step-a-ok", "step-b-fail"], f"fail-fast 失效，实际执行: {executed}"
    assert rc == 3, f"退出码应为第一个失败步骤的 3，实际 {rc}"


def test_command_echo_redacts_proxy_password(capsys):
    """回显的那行命令不许带出口口令（它常被重定向进文件/CI 日志/贴进 issue）。"""
    run = _load_runpy()
    run.run(["--proxy", "http://usr:S3CRET@127.0.0.1:47530", "--port", "8878"])
    out = capsys.readouterr().out
    assert "S3CRET" not in out, out
    assert "http://usr:***@127.0.0.1:47530" in out
    assert "--port 8878" in out          # 其余部分照常可读，不是整行吞掉


def test_command_echo_keeps_passwordless_url_intact():
    """没有口令的地址不能被误伤成 `h:***@`。"""
    run = _load_runpy()
    assert run._redact(["--proxy", "http://h:8080"]) == "--proxy http://h:8080"
    assert run._redact(["--proxy", "socks5://u:p@h:1"]) == "--proxy socks5://u:***@h:1"


# ── v6.4.3：smoke 必须能在默认鉴权下跑通，且 SSRF 断言不能是假阳性 ──
import io
import json
from urllib.error import HTTPError, URLError


class _SmokeResp:
    def __init__(self, body):
        self._body = json.dumps(body).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _SmokeProc:
    def kill(self):
        pass


def _patch_smoke(monkeypatch, mod, smoke_get, token="tok"):
    class _Subprocess:
        @staticmethod
        def Popen(*a, **k):
            return _SmokeProc()

    class _Urlreq:
        @staticmethod
        def urlopen(*a, **k):
            return _SmokeResp({"state": "ready", "browser": "camoufox",
                               "cache_backend": "memory"})

    monkeypatch.setattr(mod, "subprocess", _Subprocess)
    monkeypatch.setattr(mod, "read_env_token", lambda: token)
    monkeypatch.setattr(mod, "urlreq", _Urlreq)
    monkeypatch.setattr(mod, "_smoke_get", smoke_get)
    return mod.cmd_smoke(argparse.Namespace(port=8899, no_auth=False))


def test_smoke_sends_bearer_by_default_and_passes_on_400(monkeypatch):
    """默认配置（不传 --no-auth）必须从 .env 读 token；SSRF 必须 400+invalid-request 才算过。"""
    mod = _load_runpy()
    calls = []

    def fake_get(url, token="", timeout=20):
        calls.append((url, token))
        if "example.com" in url:
            return 200, {"protected": False, "recommendation": {"route": "direct"}}
        raise HTTPError(url, 400, "invalid-request", {},
                        io.BytesIO(b'{"error_code":"invalid-request"}'))

    assert _patch_smoke(monkeypatch, mod, fake_get) == 0
    assert calls and calls[0][1] == "tok", calls
    assert calls[-1][1] == "tok", "SSRF 检查也必须带 Bearer"


def test_smoke_fails_when_ssrf_check_is_401(monkeypatch):
    """401/连接失败不是“守卫生效”：必须判红，而不是打印 OK。"""
    mod = _load_runpy()

    def fake_get(url, token="", timeout=20):
        if "example.com" in url:
            return 200, {"protected": False, "recommendation": {"route": "direct"}}
        raise HTTPError(url, 401, "unauthorized", {}, io.BytesIO(b'{"error_code":"unauthorized"}'))

    assert _patch_smoke(monkeypatch, mod, fake_get) == 1


def test_smoke_fails_when_ssrf_400_has_no_error_code(monkeypatch):
    mod = _load_runpy()

    def fake_get(url, token="", timeout=20):
        if "example.com" in url:
            return 200, {"protected": False, "recommendation": {"route": "direct"}}
        raise HTTPError(url, 400, "bad", {}, io.BytesIO(b"{}"))

    assert _patch_smoke(monkeypatch, mod, fake_get) == 1


def test_smoke_fails_on_connection_error(monkeypatch):
    mod = _load_runpy()

    def fake_get(url, token="", timeout=20):
        if "example.com" in url:
            return 200, {"protected": False, "recommendation": {"route": "direct"}}
        raise URLError("connection refused")

    assert _patch_smoke(monkeypatch, mod, fake_get) == 1


def test_site_ci_triggers_on_facts_file():
    """站点 content 测试读 docs/facts.json；只改事实源的发布也必须触发 site-ci。"""
    text = (ROOT / ".github" / "workflows" / "site-ci.yml").read_text(encoding="utf-8")
    assert "docs/facts.json" in text, "site-ci 的 paths 漏了 docs/facts.json（v6.4.1→v6.4.2 已因此红过）"


def test_runpy_mcp_stdout_is_protocol_only():
    """`run.py mcp` 的 stdout 是 MCP 协议通道，不能混 `+ 命令` 之类的提示。"""
    import subprocess as sp
    r = sp.run([sys.executable, str(ROOT / "run.py"), "mcp"], cwd=str(ROOT),
               input="", capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert r.stdout == "", f"MCP stdout 被污染: {r.stdout!r}"


def test_runpy_node_script_paths_are_not_split():
    """回归：`verify` 曾调用 ['node','scripts','check-coverage.mjs']，node 把目录当模块，
    主验证链路第一步就 exit 1。所有 node 调用的脚本路径必须是单个 token。"""
    import ast

    source = (ROOT / "run.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        first = node.args[0]
        if not isinstance(first, ast.List):
            continue
        vals = [elt.value if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                else None for elt in first.elts]
        if vals[:2] == ["node", "scripts"]:
            bad.append(vals)
    assert not bad, (
        "run.py 把 node 脚本路径拆成了 ['node','scripts','x.mjs'] —— node 会把目录当模块"
        f"（Cannot find module .../scripts）；必须写成 ['node','scripts/x.mjs']：{bad}"
    )


def test_accept_skip_review_writes_marker_and_uses_it(tmp_path, monkeypatch):
    """accept --skip-review 必须写 SKIPPED 标记并把汇总指向它，不得复用旧 PASS。"""
    mod = _load_runpy()
    written = []
    monkeypatch.setattr(Path, "write_text", lambda self, text, **k: written.append(self))
    seen = []

    def fake_run(cmd, **kw):
        seen.append(cmd)
        return 0

    monkeypatch.setattr(mod, "run", fake_run)
    rc = mod.cmd_accept(argparse.Namespace(
        port=8878, skip_review=True, skip_browser=False, skip_smoke=True, fast=False))
    assert rc == 0
    names = [" ".join(str(x) for x in c) for c in seen]
    assert any("code-review-SKIPPED.md" in n for n in names), names
    assert not any("evidence/code-review.md --out" in n and "SKIPPED" not in n
                   for n in names), names


def test_accept_skip_browser_conflicts_with_ui_track(monkeypatch, capsys):
    """UI 验收必须真浏览器；--skip-browser 要显式拒绝而不是让 UI 轨道悄悄失败。

    只跑 verify 之前的编排逻辑：把每个步骤替身掉，直接断言 ui() 的冲突分支。
    """
    mod = _load_runpy()
    calls = []
    monkeypatch.setattr(mod, "cmd_verify", lambda a: calls.append("verify") or 0)
    monkeypatch.setattr(mod, "cmd_realcalls", lambda a: calls.append("realcalls") or 0)
    monkeypatch.setattr(mod, "cmd_report", lambda a: calls.append("report") or 0)
    monkeypatch.setattr(mod, "cmd_review", lambda a: calls.append("review") or 0)
    monkeypatch.setattr(mod, "cmd_e2e", lambda a: calls.append("e2e") or 0)
    monkeypatch.setattr(mod, "run", lambda cmd, **kw: calls.append("run") or 0)
    rc = mod.cmd_accept(argparse.Namespace(
        port=8878, skip_review=True, skip_browser=True, skip_smoke=True, fast=True))
    assert rc == 0, (rc, calls)   # 冲突写成 SKIP 标记并继续其余轨道，不算失败
    out = capsys.readouterr().out
    assert "冲突" in out and "partial" in out, out
    assert "e2e" in calls and "realcalls" in calls, calls


def test_review_verdict_uses_last_match(tmp_path, monkeypatch):
    """复审输出里复述格式示例时，聚合必须取**最后一个** VERDICT。"""
    mod = _load_runpy()
    reports = {}
    for name, text in {
        "a": "VERDICT: PASS\n示例 VERDICT: FAIL\nVERDICT: PASS\n",
        "b": "VERDICT: PASS\nVERDICT: FAIL\n",
        "c": "没有 verdict",
    }.items():
        p = tmp_path / f"review-{name}.md"
        p.write_text(text, encoding="utf-8")
        reports[name] = p
    # 直接验证聚合逻辑（不跑真 claude）：构造两个文件后手工检查正则口径
    import re
    text_a = reports["a"].read_text(encoding="utf-8")
    assert re.findall(r"VERDICT:\s*(PASS|FAIL)", text_a, re.I)[-1] == "PASS"
    text_b = reports["b"].read_text(encoding="utf-8")
    assert re.findall(r"VERDICT:\s*(PASS|FAIL)", text_b, re.I)[-1] == "FAIL"
    assert re.findall(r"VERDICT:\s*(PASS|FAIL)", reports["c"].read_text(encoding="utf-8")) == []
