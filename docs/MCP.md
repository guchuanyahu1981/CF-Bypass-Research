# MCP stdio 适配器

> 把本地求解服务暴露成 5 个 MCP 工具。零新依赖：只用 Python 标准库 + 现有 `solver/`。
> 不改 `.mcp.json`（那是你的客户端配置）；本文只给 snippet。

## 为什么是 stdio，不是 HTTP/SSE

MCP 的 stdio 传输就是「一行一个 JSON-RPC 消息」：客户端拉起 `python -m solver.mcp_server`，
通过 stdin/stdout 通信。它不需要端口、鉴权网关或额外服务，和本项目“单机 sidecar”的定位一致。

## 启动

```bash
# 服务端先跑起来（默认 127.0.0.1:8878）
python solver/service.py --port 8878

# 手动试跑 MCP（stdout 是协议通道，日志必须走 stderr）
python run.py mcp
```

环境变量：

| 变量 | 默认 | 说明 |
|---|---|---|
| `CF_SOLVE_URL` | `http://127.0.0.1:8878` | 求解服务地址 |
| `CF_SOLVE_TOKEN` | 读仓库根 `.env` | 只读，不自动生成；MCP 结果不回显 token |

客户端配置 snippet（例如 Claude Code / Cursor 的 MCP 配置）：

```json
{
  "mcpServers": {
    "cf-solver": {
      "command": "python",
      "args": ["-m", "solver.mcp_server"],
      "cwd": "C:\\path\\to\\CF-Bypass-Research",
      "env": { "CF_SOLVE_URL": "http://127.0.0.1:8878" }
    }
  }
}
```

## 工具面（5 个）

| 工具 | 作用 | 关键约束 |
|---|---|---|
| `cf_solve` | 取凭证（`clearance` / `awswaf` / `turnstile` / `recaptcha_v3` / 嫁接层） | 只支持本服务已接入的产物；不接图片验证码 |
| `cf_fetch` | 取凭证 + 同 UA/Cookie 回放；只回正文前 2000 字符 + spill 文件路径 | 避免整页 HTML 灌进模型上下文；**solver 用代理池时必须传同一个 `proxy`**（cf_clearance 绑出口 IP） |
| `cf_detect` | 只探测防护类型，不求解 | 一次普通 GET，可 `fresh=true` 跳过缓存 |
| `cf_health` | 健康状态 | 公开端点，不需要 token |
| `cf_capabilities` | 机器可读能力清单 | 主链路/嫁接层可解 want + 明确拒绝的厂商 |

**刻意不做**：`createTask/getTaskResult`（兼容层）、`shutdown`（运维面）、任何写入/管理工具。
MCP 是只读调用面，不是管理后台。

## 安全

- token 只从 `CF_SOLVE_TOKEN` 或 `.env` **读取**，不生成、不回显、不写日志。
- `cf_solve` 会回传凭证（这是工具产物）；调用方自己负责保管。
- `cf_fetch` 的完整正文写到本地临时文件（`spill_path`），只在结果里回 `head` 片段；
  只保留最近 20 份 spill 文件。
- `cf_fetch` 只允许公网 `http(s)`：回放前与每一跳重定向都会拒绝私网/回环/链路本地/
  云元数据地址；跨主机跳转时丢弃 Cookie，避免 `cf_clearance` 泄露到别的域。
- 不把 `GET /console`、`/logs`、`/audit` 暴露成工具（避免模型读运维数据）。

## 测试

```bash
python -m pytest tests/test_mcp_server.py -q
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  | python -m solver.mcp_server
```

## 限制

- 单次冷求解 8–31s；MCP 客户端自己的工具超时若小于这个值，需要调大。
- 协议版本为 `2024-11-05`；不同客户端对 resources 的支持程度不同，工具调用是主路径。
- 只支持 stdio，不提供 SSE/HTTP transport。