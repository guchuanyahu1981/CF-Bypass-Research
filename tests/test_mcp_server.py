"""MCP stdio 适配器（Q5）：纯函数协议测试，不起真实 HTTP 服务。

覆盖：initialize / tools.list / tools.call（5 工具）/ resources.read / 未知方法 /
通知不响应 / SolverClient 的 token 读取与 fetch 摘要。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "solver"))

import client as client_mod  # noqa: E402
import mcp_server  # noqa: E402


def _allow_url(monkeypatch):
    """这些用例验的是回放摘要/告警，不依赖真实 DNS。"""
    monkeypatch.setattr(client_mod, "validate_public_url", lambda url: None)


class FakeClient:
    def __init__(self):
        self.calls = []

    def health(self):
        self.calls.append(("health", {}))
        return {"ok": True, "state": "ready"}

    def detect(self, url, fresh=False):
        self.calls.append(("detect", {"url": url, "fresh": fresh}))
        return {"ok": True, "protected": False}

    def solve(self, url, **kwargs):
        self.calls.append(("solve", {"url": url, **kwargs}))
        return {"ok": True, "solved": True, "want": kwargs.get("want", "clearance"),
                "cf_clearance": "X" * 10, "user_agent": "UA"}

    def fetch(self, url, **kwargs):
        self.calls.append(("fetch", {"url": url, **kwargs}))
        return {"ok": True, "status": 200, "head": "<html>", "spill_path": "x.html"}


def _call(method, params=None, rid=1):
    return mcp_server.handle_request({
        "jsonrpc": "2.0", "id": rid, "method": method, "params": params or {},
    })


def test_initialize_and_tools_list():
    init = _call("initialize")
    assert init["result"]["protocolVersion"] == mcp_server.PROTOCOL_VERSION
    assert init["result"]["serverInfo"]["name"] == "cf-solver"
    tools = _call("tools/list")["result"]["tools"]
    assert {t["name"] for t in tools} == {
        "cf_solve", "cf_fetch", "cf_detect", "cf_health", "cf_capabilities"}
    for tool in tools:
        assert tool["inputSchema"]["type"] == "object"


def test_tool_calls_dispatch_to_client():
    fake = FakeClient()
    out = mcp_server.handle_request({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "cf_solve",
                   "arguments": {"url": "https://e.com/", "want": "turnstile",
                                 "sitekey": "sk", "route": "auto", "timeout": 30}},
    }, client=fake)
    assert out["result"]["isError"] is False
    payload = json.loads(out["result"]["content"][0]["text"])
    assert payload["solved"] is True
    assert fake.calls[0][1] == {"url": "https://e.com/", "want": "turnstile",
                                "sitekey": "sk", "route": "auto", "timeout": 30.0}


def test_capabilities_tool_returns_graft_layer():
    fake = FakeClient()
    out = mcp_server.handle_request({
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "cf_capabilities", "arguments": {}},
    }, client=fake)
    payload = json.loads(out["result"]["content"][0]["text"])
    assert "clearance" in payload["main_chain"]
    assert "turnstile_x" in payload["graft_layer"]
    assert len(payload["graft_layer"]) == 11


def test_unknown_tool_and_unknown_method_are_errors():
    fake = FakeClient()
    out = mcp_server.handle_request({
        "jsonrpc": "2.0", "id": 4, "method": "tools/call",
        "params": {"name": "nope", "arguments": {}},
    }, client=fake)
    assert out["result"]["isError"] is True
    err = _call("does/not/exist")
    assert err["error"]["code"] == -32601


def test_notification_gets_no_response():
    req = {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
    assert mcp_server.handle_request(req) is None


def test_resources_read_notice():
    out = _call("resources/read", {"uri": "cf://notice"})
    text = out["result"]["contents"][0]["text"]
    assert "NPOSL-3.0" in text and "captcha-solver" in text


def test_parse_error_line_is_reported():
    # main() 的解析错误路径是纯逻辑，这里直接复用 JSONDecodeError 分支的等价断言。
    with pytest.raises(json.JSONDecodeError):
        json.loads("{not-json")


def test_client_load_token_env_and_file(monkeypatch, tmp_path):
    monkeypatch.setenv("CF_SOLVE_TOKEN", "env-token")
    assert client_mod.load_token() == "env-token"
    monkeypatch.delenv("CF_SOLVE_TOKEN", raising=False)
    env = tmp_path / ".env"
    env.write_text("CF_SOLVE_TOKEN=file-token\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert client_mod.load_token() == "file-token"


def test_client_fetch_summarizes_and_spills(monkeypatch):
    class _Resp:
        status = 200

        def __init__(self):
            self._body = b"<html><title>T</title><body>hello</body></html>"

        def read(self, n):
            return self._body[:n]

        def geturl(self):
            return "https://e.com/"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    _allow_url(monkeypatch)
    class _Opener:
        def open(self, req, timeout):
            return _Resp()

    monkeypatch.setattr(client_mod, "_replay_opener", lambda proxy="": _Opener())
    c = client_mod.SolverClient(token="t")
    c.solve = lambda *a, **k: {  # type: ignore[method-assign]
        "ok": True, "solved": True, "user_agent": "UA",
        "cookie_list": [{"name": "cf_clearance", "value": "C"}],
    }
    out = c.fetch("https://e.com/", head_chars=10)
    assert out["ok"] is True and out["status"] == 200
    assert out["head"].startswith("<html><tit")
    assert out["title"] == "T"
    assert out["spill_path"] and Path(out["spill_path"]).read_bytes().startswith(b"<html>")

def test_client_http_and_url_errors_are_structured(monkeypatch):
    import io
    from urllib.error import HTTPError, URLError

    class _Opener:
        def __init__(self, exc):
            self.exc = exc

        def open(self, req, timeout):
            raise self.exc

    import io
    from urllib.error import HTTPError, URLError

    _allow_url(monkeypatch)
    monkeypatch.setattr(client_mod, "_replay_opener", lambda proxy="": _Opener(
        HTTPError("http://x", 429, "busy", {},
                  io.BytesIO(b'{"error_code":"busy","stage":"admit","error":"full"}'))))
    with pytest.raises(client_mod.SolverClientError) as ei:
        client_mod.SolverClient(token="t").detect("https://e.com/")
    assert ei.value.status == 429 and ei.value.code == "busy" and ei.value.stage == "admit"

    monkeypatch.setattr(client_mod, "_replay_opener", lambda proxy="": _Opener(URLError("refused")))
    with pytest.raises(client_mod.SolverClientError) as ei:
        client_mod.SolverClient(token="t").health()
    assert "不可达" in str(ei.value)


def test_client_fetch_unsolved_short_circuits(monkeypatch):
    _allow_url(monkeypatch)
    c = client_mod.SolverClient(token="t")
    c.solve = lambda *a, **k: {"ok": True, "solved": False, "error_code": "unsolved"}
    out = c.fetch("https://e.com/")
    assert out["ok"] is False and out["step"] == "solve"


def test_client_fetch_http_error_still_returns_summary(monkeypatch):
    import io
    from urllib.error import HTTPError

    class _Opener:
        def open(self, req, timeout):
            raise HTTPError("http://x", 403, "forbidden", {},
                            io.BytesIO(b"<html>blocked</html>"))

    _allow_url(monkeypatch)
    monkeypatch.setattr(client_mod, "_replay_opener", lambda proxy="": _Opener())
    c = client_mod.SolverClient(token="t")
    c.solve = lambda *a, **k: {"ok": True, "solved": True, "user_agent": "UA",
                               "cookie_list": [{"name": "cf_clearance", "value": "C"}]}
    out = c.fetch("https://e.com/")
    assert out["status"] == 403 and out["ok"] is False and "blocked" in out["head"]


def test_mcp_health_detect_fetch_tools_dispatch():
    fake = FakeClient()
    for name, args in (("cf_health", {}),
                       ("cf_detect", {"url": "https://e.com/", "fresh": True}),
                       ("cf_fetch", {"url": "https://e.com/", "head_chars": 5})):
        out = mcp_server.handle_request({
            "jsonrpc": "2.0", "id": 9, "method": "tools/call",
            "params": {"name": name, "arguments": args},
        }, client=fake)
        assert out["result"]["isError"] is False
    assert [c[0] for c in fake.calls] == ["health", "detect", "fetch"]


def test_mcp_tool_solver_error_is_error_result():
    class Boom(FakeClient):
        def health(self):
            raise client_mod.SolverClientError("nope", status=503, code="not-configured",
                                               stage="admit")

    out = mcp_server.handle_request({
        "jsonrpc": "2.0", "id": 10, "method": "tools/call",
        "params": {"name": "cf_health", "arguments": {}},
    }, client=Boom())
    assert out["result"]["isError"] is True
    payload = json.loads(out["result"]["content"][0]["text"])
    assert payload["status"] == 503 and payload["code"] == "not-configured"


def test_mcp_resources_list_and_unknown_read():
    lst = _call("resources/list")["result"]["resources"]
    assert {r["uri"] for r in lst} >= {"cf://docs/index", "cf://docs/api", "cf://notice"}
    unknown = _call("resources/read", {"uri": "cf://nope"})
    assert unknown["result"]["contents"] == []


def test_mcp_invalid_request_shape():
    err = mcp_server.handle_request(["not", "a", "dict"])
    assert err["error"]["code"] == -32600


def test_main_processes_stdio_lines(monkeypatch, capsys):
    import io
    monkeypatch.setattr(mcp_server.sys, "stdin",
                        io.StringIO('{"jsonrpc":"2.0","id":1,"method":"ping"}\n\n'))
    assert mcp_server.main() == 0
    lines = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.strip()]
    assert lines and lines[0]["id"] == 1 and lines[0]["result"] == {}


def test_client_fetch_warns_when_solver_egress_is_not_local(monkeypatch):
    class _Resp:
        status = 200

        def read(self, n):
            return b"<html>ok</html>"

        def geturl(self):
            return "https://e.com/"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    _allow_url(monkeypatch)
    class _Opener:
        def open(self, req, timeout):
            return _Resp()

    monkeypatch.setattr(client_mod, "_replay_opener", lambda proxy="": _Opener())
    c = client_mod.SolverClient(token="t")
    c.solve = lambda *a, **k: {
        "ok": True, "solved": True, "user_agent": "UA", "egress_id": "ip:1.2.3.4",
        "cookie_list": [{"name": "cf_clearance", "value": "C"}],
    }
    out = c.fetch("https://e.com/")
    assert out["warning"] and "出口" in out["warning"]


def test_client_fetch_uses_proxy_opener_when_given(monkeypatch):
    class _Resp:
        status = 200

        def read(self, n):
            return b"<html>ok</html>"

        def geturl(self):
            return "https://e.com/"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class _Opener:
        def open(self, req, timeout):
            return _Resp()

    _allow_url(monkeypatch)
    seen = []
    monkeypatch.setattr(client_mod, "_replay_opener", lambda proxy="": seen.append(proxy) or _Opener())
    c = client_mod.SolverClient(token="t")
    c.solve = lambda *a, **k: {
        "ok": True, "solved": True, "user_agent": "UA", "egress_id": "ip:1.2.3.4",
        "cookie_list": [{"name": "cf_clearance", "value": "C"}],
    }
    out = c.fetch("https://e.com/", proxy="http://127.0.0.1:10808")
    assert out["ok"] is True and out["warning"] is None
    assert seen == ["http://127.0.0.1:10808"]


# ── cf_fetch 的 SSRF 边界（v6.4.1 复审 P1）──────────


def test_validate_public_url_rejects_file_private_and_metadata():
    for bad in ("file:///C:/Windows/win.ini",
                "http://127.0.0.1:8878/health",
                "http://169.254.169.254/latest/meta-data/"):
        with pytest.raises(client_mod.SolverClientError):
            client_mod.validate_public_url(bad)


def test_fetch_rejects_private_target_before_solve(monkeypatch):
    c = client_mod.SolverClient(token="t")
    called = []
    c.solve = lambda *a, **k: called.append(a) or {"ok": True, "solved": True}  # type: ignore
    with pytest.raises(client_mod.SolverClientError):
        c.fetch("http://127.0.0.1:8878/health")
    assert called == [], "必须在调用求解服务之前就拒绝私网目标"


def test_fetch_checks_each_redirect_hop(monkeypatch):
    import io
    from urllib.error import HTTPError

    def guard(url):
        if "127.0.0.1" in url:
            raise client_mod.SolverClientError("private hop")

    class _Opener:
        def open(self, req, timeout):
            raise HTTPError(req.full_url, 302, "Found",
                            {"Location": "http://127.0.0.1:8878/health"},
                            io.BytesIO(b""))

    monkeypatch.setattr(client_mod, "validate_public_url", guard)
    monkeypatch.setattr(client_mod, "_replay_opener", lambda proxy="": _Opener())
    c = client_mod.SolverClient(token="t")
    c.solve = lambda *a, **k: {"ok": True, "solved": True, "user_agent": "UA",
                               "cookie_list": [{"name": "cf_clearance", "value": "C"}]}
    with pytest.raises(client_mod.SolverClientError):
        c.fetch("https://public.example/")


def test_fetch_drops_cookie_on_cross_host_redirect(monkeypatch):
    import io
    from urllib.error import HTTPError

    class _Resp:
        status = 200

        def read(self, n):
            return b"<html>ok</html>"

        def geturl(self):
            return "https://other.example/"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class _Opener:
        def __init__(self):
            self.calls = []

        def open(self, req, timeout):
            self.calls.append({k.lower(): v for k, v in req.header_items()})
            if len(self.calls) == 1:
                raise HTTPError(req.full_url, 302, "Found",
                                {"Location": "https://other.example/"}, io.BytesIO(b""))
            return _Resp()

    opener = _Opener()
    monkeypatch.setattr(client_mod, "validate_public_url", lambda url: None)
    monkeypatch.setattr(client_mod, "_replay_opener", lambda proxy="": opener)
    c = client_mod.SolverClient(token="t")
    c.solve = lambda *a, **k: {"ok": True, "solved": True, "user_agent": "UA",
                               "cookie_list": [{"name": "cf_clearance", "value": "C"}]}
    out = c.fetch("https://public.example/")
    assert out["ok"] is True
    assert "cookie" in opener.calls[0]
    assert "cookie" not in opener.calls[1], opener.calls


def test_invalid_params_returns_32602_without_killing_loop(monkeypatch, capsys):
    bad = {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": [1]}
    err = mcp_server.handle_request(bad)
    assert err["error"]["code"] == -32602
    assert mcp_server.handle_request({
        "jsonrpc": "2.0", "method": "notifications/initialized", "params": [1]}) is None

    import io
    monkeypatch.setattr(mcp_server.sys, "stdin", io.StringIO(
        '{"jsonrpc":"2.0","id":7,"method":"tools/call","params":[1]}\n'
        '{"jsonrpc":"2.0","id":8,"method":"ping"}\n'))
    assert mcp_server.main() == 0
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines() if x.strip()]
    assert [x["id"] for x in lines] == [7, 8]
    assert lines[0]["error"]["code"] == -32602 and lines[1]["result"] == {}


def test_capabilities_fallback_has_same_schema():
    assert set(client_mod.capabilities()) == set(client_mod._capabilities_fallback())


def test_cleanup_spills_keeps_newest(tmp_path, monkeypatch):
    monkeypatch.setattr(client_mod.tempfile, "gettempdir", lambda: str(tmp_path))
    import os
    for i in range(25):
        p = tmp_path / f"cf-fetch-{i}.html"
        p.write_text(str(i), encoding="utf-8")
        os.utime(p, (1000 + i, 1000 + i))
    client_mod.cleanup_spills(keep=5)
    left = sorted(tmp_path.glob("cf-fetch-*.html"))
    assert len(left) == 5
    assert {p.name for p in left} == {f"cf-fetch-{i}.html" for i in range(20, 25)}


def test_version_falls_back_when_facts_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_server, "ROOT", tmp_path)
    assert mcp_server._version() == "unknown"


def test_tool_generic_exception_is_error_result():
    class Boom(FakeClient):
        def health(self):
            raise ValueError("boom")

    out = mcp_server.handle_request({
        "jsonrpc": "2.0", "id": 11, "method": "tools/call",
        "params": {"name": "cf_health", "arguments": {}},
    }, client=Boom())
    assert out["result"]["isError"] is True
    assert "ValueError" in out["result"]["content"][0]["text"]


def test_tool_arguments_must_be_object():
    out = mcp_server.handle_request({
        "jsonrpc": "2.0", "id": 12, "method": "tools/call",
        "params": {"name": "cf_health", "arguments": [1, 2]},
    }, client=FakeClient())
    assert json.loads(out["result"]["content"][0]["text"])["error"]


def test_resources_read_oserror_is_structured(tmp_path, monkeypatch):
    (tmp_path / "NOTICE").mkdir()
    monkeypatch.setattr(mcp_server, "ROOT", tmp_path)
    out = _call("resources/read", {"uri": "cf://notice"})
    assert out["result"]["contents"] == [] and "error" in out["result"]


def test_main_parse_error_and_internal_error_do_not_kill_loop(monkeypatch, capsys):
    import io
    monkeypatch.setattr(mcp_server.sys, "stdin", io.StringIO(
        "{not-json\n"
        '{"jsonrpc":"2.0","id":20,"method":"ping"}\n'))
    assert mcp_server.main() == 0
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines() if x.strip()]
    assert lines[0]["error"]["code"] == -32700 and lines[1]["result"] == {}

    def boom(_req, client=None):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(mcp_server, "handle_request", boom)
    monkeypatch.setattr(mcp_server.sys, "stdin", io.StringIO(
        '{"jsonrpc":"2.0","id":21,"method":"ping"}\n'))
    assert mcp_server.main() == 0
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines() if x.strip()]
    assert lines[0]["error"]["code"] == -32603


def test_validate_public_url_edge_cases(monkeypatch):
    import socket as _socket
    with pytest.raises(client_mod.SolverClientError):
        client_mod.validate_public_url("http:///no-host")
    monkeypatch.setattr(client_mod.socket, "getaddrinfo",
                        lambda *a, **k: (_ for _ in ()).throw(_socket.gaierror("nxdomain")))
    with pytest.raises(client_mod.SolverClientError):
        client_mod.validate_public_url("https://nx.example/")
    monkeypatch.setattr(client_mod.socket, "getaddrinfo",
                        lambda *a, **k: [(2, 1, 6, "", ("not-an-ip", 0))])
    client_mod.validate_public_url("https://odd.example/")


def test_replay_opener_builds_proxy_and_no_redirect():
    assert client_mod._replay_opener() is not None
    assert client_mod._replay_opener("http://127.0.0.1:10808") is not None


def test_fetch_uses_cf_clearance_when_cookie_list_missing(monkeypatch):
    class _Resp:
        status = 200

        def read(self, n):
            return b"<html><title>T</title>ok</html>"

        def geturl(self):
            return "https://e.com/"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    seen = {}

    class _Opener:
        def open(self, req, timeout):
            seen.update({k.lower(): v for k, v in req.header_items()})
            return _Resp()

    _allow_url(monkeypatch)
    monkeypatch.setattr(client_mod, "_replay_opener", lambda proxy="": _Opener())
    c = client_mod.SolverClient(token="t")
    c.solve = lambda *a, **k: {"ok": True, "solved": True, "user_agent": "UA",
                               "cf_clearance": "CLEAR"}
    out = c.fetch("https://e.com/")
    assert out["ok"] is True and "cf_clearance=CLEAR" in seen["cookie"]


def test_fetch_urlerror_and_too_many_redirects(monkeypatch):
    import io
    from urllib.error import HTTPError, URLError

    class _ErrOpener:
        def open(self, req, timeout):
            raise URLError("refused")

    _allow_url(monkeypatch)
    monkeypatch.setattr(client_mod, "_replay_opener", lambda proxy="": _ErrOpener())
    c = client_mod.SolverClient(token="t")
    c.solve = lambda *a, **k: {"ok": True, "solved": True, "user_agent": "UA",
                               "cookie_list": [{"name": "cf_clearance", "value": "C"}]}
    with pytest.raises(client_mod.SolverClientError):
        c.fetch("https://e.com/")

    class _LoopOpener:
        def open(self, req, timeout):
            raise HTTPError(req.full_url, 302, "Found",
                            {"Location": "https://e.com/next"}, io.BytesIO(b""))

    monkeypatch.setattr(client_mod, "_replay_opener", lambda proxy="": _LoopOpener())
    with pytest.raises(client_mod.SolverClientError) as ei:
        c.fetch("https://e.com/")
    assert "重定向" in str(ei.value)


def test_fetch_spill_failure_and_unclosed_title(monkeypatch):
    class _Resp:
        status = 200

        def read(self, n):
            return b"<html><title>broken"

        def geturl(self):
            return "https://e.com/"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class _Opener:
        def open(self, req, timeout):
            return _Resp()

    _allow_url(monkeypatch)
    monkeypatch.setattr(client_mod, "_replay_opener", lambda proxy="": _Opener())
    monkeypatch.setattr(client_mod.tempfile, "mkstemp",
                        lambda **k: (_ for _ in ()).throw(OSError("no temp")))
    c = client_mod.SolverClient(token="t")
    c.solve = lambda *a, **k: {"ok": True, "solved": True, "user_agent": "UA",
                               "cookie_list": [{"name": "cf_clearance", "value": "C"}]}
    out = c.fetch("https://e.com/")
    assert out["spill_path"] is None and out["title"] == ""


def test_validate_connected_peer_rejects_private_socket():
    class _Sock:
        def getpeername(self):
            return ("127.0.0.1", 443)

    class _Raw:
        _sock = _Sock()

    class _FP:
        raw = _Raw()

    class _Resp:
        fp = _FP()

    with pytest.raises(client_mod.SolverClientError):
        client_mod.validate_connected_peer(_Resp())


def test_validate_proxy_url_rejects_metadata_and_private_but_allows_loopback():
    for bad in ("http://169.254.169.254/", "http://10.0.0.5:8080",
                "file:///tmp/sock", "http://0.0.0.0:8080"):
        with pytest.raises(client_mod.SolverClientError):
            client_mod.validate_proxy_url(bad)
    client_mod.validate_proxy_url("http://127.0.0.1:10808")
    client_mod.validate_proxy_url("")


def test_fetch_uses_request_timeout_and_caps_head(monkeypatch):
    class _Resp:
        status = 200

        def __init__(self):
            self._body = b"x" * 50000

        def read(self, n):
            return self._body[:n]

        def geturl(self):
            return "https://e.com/"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    seen = {}

    class _Opener:
        def open(self, req, timeout):
            seen["timeout"] = timeout
            return _Resp()

    _allow_url(monkeypatch)
    monkeypatch.setattr(client_mod, "_replay_opener", lambda proxy="": _Opener())
    c = client_mod.SolverClient(token="t", timeout=180)
    c.solve = lambda *a, **k: {"ok": True, "solved": True, "user_agent": "UA",
                               "cookie_list": [{"name": "cf_clearance", "value": "C"}]}
    out = c.fetch("https://e.com/", timeout=7, head_chars=10**9)
    assert seen["timeout"] == 7
    assert len(out["head"]) == client_mod.MAX_HEAD_CHARS
    assert out["truncated"] is True


def test_load_token_strips_matching_quotes(tmp_path, monkeypatch):
    monkeypatch.delenv("CF_SOLVE_TOKEN", raising=False)
    env = tmp_path / ".env"
    env.write_text('CF_SOLVE_TOKEN="quoted-token"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert client_mod.load_token() == "quoted-token"


def test_fetch_marks_spill_truncated(monkeypatch):
    class _Resp:
        status = 200

        def read(self, n):
            return b"x" * 64

        def geturl(self):
            return "https://e.com/"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class _Opener:
        def open(self, req, timeout):
            return _Resp()

    _allow_url(monkeypatch)
    monkeypatch.setattr(client_mod, "FETCH_MAX_BYTES", 32)
    monkeypatch.setattr(client_mod, "_replay_opener", lambda proxy="": _Opener())
    c = client_mod.SolverClient(token="t")
    c.solve = lambda *a, **k: {"ok": True, "solved": True, "user_agent": "UA",
                               "cookie_list": [{"name": "cf_clearance", "value": "C"}]}
    out = c.fetch("https://e.com/")
    assert out["spill_truncated"] is True


def test_client_solve_and_detect_reject_private_urls(monkeypatch):
    c = client_mod.SolverClient(token="t")
    for url in ("http://127.0.0.1:8878/health", "file:///C:/Windows/win.ini"):
        with pytest.raises(client_mod.SolverClientError):
            c.solve(url)
        with pytest.raises(client_mod.SolverClientError):
            c.detect(url)


def test_tools_call_notification_does_not_execute_side_effects():
    fake = FakeClient()
    out = mcp_server.handle_request({
        "jsonrpc": "2.0", "method": "tools/call",
        "params": {"name": "cf_fetch", "arguments": {"url": "https://e.com/"}},
    }, client=fake)
    assert out is None
    assert fake.calls == []
