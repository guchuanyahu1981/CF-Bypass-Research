"""MCP stdio 适配器：把本地求解服务暴露成 5 个工具（零新依赖，JSON-RPC over stdio）。

为什么不用 MCP SDK：本项目坚持零运行时依赖；MCP 的 stdio 传输就是
“一行一个 JSON-RPC 消息”，实现量很小，自己写反而可控。

工具面刻意小（5 个）：
  · cf_solve / cf_fetch / cf_detect / cf_health / cf_capabilities
  · 不做 /createTask、/getTaskResult、/shutdown —— 那是兼容层/运维面，不该交给模型。
  · cf_fetch 只回正文前 2000 字符 + spill 文件路径，避免把整页 HTML 灌进上下文。

安全：token 只从 `CF_SOLVE_TOKEN` 或 `.env` 读取；不自动生成、不回显、不写日志。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_VERSION = "2024-11-05"

try:
    from .client import SolverClient, SolverClientError, capabilities
except ImportError:  # 直接以脚本运行
    sys.path.insert(0, str(ROOT / "solver"))
    from client import SolverClient, SolverClientError, capabilities  # type: ignore[no-redef]


def _version() -> str:
    try:
        data = json.loads((ROOT / "docs" / "facts.json").read_text(encoding="utf-8"))
        return str(data["version"])
    except Exception:  # noqa: BLE001
        return "unknown"


def _schema(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required,
            "additionalProperties": False}


TOOLS = [
    {
        "name": "cf_solve",
        "description": "获取一个 URL 的 Cloudflare/AWS WAF 凭证（cf_clearance/token）。"
                       "只支持本服务已接入的产物类型；不接图片验证码。",
        "inputSchema": _schema({
            "url": {"type": "string", "description": "目标绝对 URL（http/https）"},
            "want": {"type": "string", "default": "clearance",
                     "description": "clearance/awswaf/turnstile/recaptcha_v3 或嫁接层类型"},
            "sitekey": {"type": "string", "description": "turnstile/recaptcha_v3 必填"},
            "route": {"type": "string", "default": "fixed",
                      "description": "fixed/auto/direct/pure/browser"},
            "timeout": {"type": "number", "default": 45, "description": "5–120 秒"},
        }, ["url"]),
    },
    {
        "name": "cf_fetch",
        "description": "先取凭证，再用同 UA/Cookie 回放目标页；只回正文前 2000 字符 + 本地 spill 路径。"
                       "只允许公网 http(s)；每一跳重定向都会重新校验，拒绝私网/回环/元数据地址。",
        "inputSchema": _schema({
            "url": {"type": "string"},
            "want": {"type": "string", "default": "clearance"},
            "sitekey": {"type": "string"},
            "route": {"type": "string", "default": "fixed"},
            "timeout": {"type": "number", "default": 45},
            "head_chars": {"type": "integer", "default": 2000},
            "proxy": {"type": "string", "description": "与求解时相同的出口代理（cf_clearance 绑 IP）"},
        }, ["url"]),
    },
    {
        "name": "cf_detect",
        "description": "只探测目标站用了哪种防护/验证码/指纹，不求解。",
        "inputSchema": _schema({
            "url": {"type": "string"},
            "fresh": {"type": "boolean", "default": False},
        }, ["url"]),
    },
    {
        "name": "cf_health",
        "description": "查看求解服务健康状态（公开端点，不需要 token）。",
        "inputSchema": _schema({}, []),
    },
    {
        "name": "cf_capabilities",
        "description": "机器可读能力清单：主链路/嫁接层可解的 want、明确拒绝的厂商。",
        "inputSchema": _schema({}, []),
    },
]


def _text(payload) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
            "isError": False}


def _error(message: str, **extra) -> dict:
    payload = {"ok": False, "error": message, **extra}
    return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
            "isError": True}


def _arg(args: dict, key: str, default=None):
    value = args.get(key, default)
    return default if value is None else value


def _call_tool(name: str, args: dict, client: SolverClient) -> dict:
    try:
        if name == "cf_health":
            return _text(client.health())
        if name == "cf_detect":
            return _text(client.detect(str(_arg(args, "url", "")),
                                       fresh=bool(_arg(args, "fresh", False))))
        if name == "cf_solve":
            return _text(client.solve(
                str(_arg(args, "url", "")),
                want=str(_arg(args, "want", "clearance")),
                sitekey=str(_arg(args, "sitekey", "")),
                route=str(_arg(args, "route", "fixed")),
                timeout=float(_arg(args, "timeout", 45)),
            ))
        if name == "cf_fetch":
            return _text(client.fetch(
                str(_arg(args, "url", "")),
                want=str(_arg(args, "want", "clearance")),
                sitekey=str(_arg(args, "sitekey", "")),
                route=str(_arg(args, "route", "fixed")),
                timeout=float(_arg(args, "timeout", 45)),
                head_chars=int(_arg(args, "head_chars", 2000)),
                proxy=str(_arg(args, "proxy", "")),
            ))
        if name == "cf_capabilities":
            return _text(capabilities())
        return _error(f"未知工具: {name}")
    except SolverClientError as e:
        return _error(str(e), status=e.status, code=e.code, stage=e.stage)
    except Exception as e:  # noqa: BLE001
        return _error(f"{type(e).__name__}: {str(e)[:200]}")


def handle_request(req: dict, client: SolverClient | None = None) -> dict | None:
    """纯函数：处理一条 JSON-RPC 请求；通知（无 id）返回 None。"""
    if not isinstance(req, dict):
        return {"jsonrpc": "2.0", "id": None,
                "error": {"code": -32600, "message": "Invalid Request"}}
    rid = req.get("id")
    method = req.get("method")
    is_notification = "id" not in req
    raw_params = req.get("params")
    if raw_params is None:
        params: dict = {}
    elif isinstance(raw_params, dict):
        params = raw_params
    else:
        # params 不是对象（数组/字符串）→ -32602；通知仍然不回包。
        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": rid,
                "error": {"code": -32602, "message": "Invalid params"}}

    def ok(result: dict) -> dict | None:
        return None if is_notification else {"jsonrpc": "2.0", "id": rid, "result": result}

    if method == "initialize":
        return ok({
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False},
                             "resources": {"subscribe": False, "listChanged": False}},
            "serverInfo": {"name": "cf-solver", "version": _version()},
        })
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": TOOLS})
    if method == "tools/call":
        if is_notification:
            return None       # 通知没有 id，不执行可能有副作用的工具调用
        name = str(params.get("name") or "")
        args = params.get("arguments", {})
        if args is None:
            args = {}
        if not isinstance(args, dict):
            return ok(_error("arguments 必须是对象"))
        cli = client or SolverClient()
        return ok(_call_tool(name, args, cli))
    if method == "resources/list":
        return ok({"resources": [
            {"uri": "cf://docs/index", "name": "docs/00-索引.md",
             "mimeType": "text/markdown", "description": "文档与知识库索引"},
            {"uri": "cf://docs/api", "name": "docs/API.md",
             "mimeType": "text/markdown", "description": "求解服务 API 契约"},
            {"uri": "cf://notice", "name": "NOTICE",
             "mimeType": "text/plain", "description": "第三方来源与许可证"},
        ]})
    if method == "resources/read":
        uri = str(params.get("uri") or "")
        files = {"cf://docs/index": ROOT / "docs" / "00-索引.md",
                 "cf://docs/api": ROOT / "docs" / "API.md",
                 "cf://notice": ROOT / "NOTICE"}
        path = files.get(uri)
        if path is None:
            return ok({"contents": []})
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return ok({"contents": [], "error": str(e)})
        return ok({"contents": [{"uri": uri, "mimeType": "text/markdown", "text": text}]})
    if is_notification:
        return None
    return {"jsonrpc": "2.0", "id": rid,
            "error": {"code": -32601, "message": f"Method not found: {method}"}}


def main() -> int:
    # stdio MCP：一行一个 JSON-RPC 消息；日志只能走 stderr（stdout 是协议通道）。
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            print(json.dumps({"jsonrpc": "2.0", "id": None,
                              "error": {"code": -32700, "message": "Parse error"}}), flush=True)
            continue
        try:
            resp = handle_request(req)
        except Exception as e:  # noqa: BLE001 —— 协议循环不能因单帧异常退出
            rid = req.get("id") if isinstance(req, dict) else None
            resp = {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32603,
                              "message": f"Internal error: {type(e).__name__}"}}
        if resp is not None:
            print(json.dumps(resp, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())