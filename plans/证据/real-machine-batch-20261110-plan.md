# 真机批次计划 R-E2E-4（2026-11-10）

按 `policy.md` §2：实验前报计划。

| 项 | 内容 |
|---|---|
| 批次 | R-E2E-4（对应总状态清单 R-2 / R-3） |
| 目标站 | `https://www.scrapingcourse.com/cloudflare-challenge`（该页公开定位为 Cloudflare 挑战的练习/演示页，用于爬虫教程验证过盾，非业务数据页） |
| 计划请求数 | ≤ 3 次（cfsolve 会话捕获 1 次；服务端 /solve 真机复验 1 次；失败不重试） |
| 台账 | 起始 58 / 80 → 预计 61 / 80；累计失败 1 / 5 → 若两次均失败则 3 / 5，触及即停 |
| 出口 | 本机代理 `127.0.0.1:10808`，实测出口 IP 见 `plans/证据/r3-stage2-20261110/egress.txt` |
| 同站串行 | 两次请求之间 ≥30s |
| 失败即停 | 任何一个目标出现 `ip-blocked` / 连续失败即停，不"再试一次" |

## 预期证据

- R-3：`challenge --page <url> <outdir> --solve --fingerprint safari-26-ios.json` 的输出目录里出现 `response.decoded.txt` → `scripts/corpus.py add` → `research.py assertions` 的 6 条 stage2 断言由 skip 变为可判定。
- R-2：`python solver/service.py --proxy ... --proxy-egress-ip ... --audit-log ...` 起服务，`POST /solve` 一次，`GET /audit` 读回同一行，`GET /health` 的 `egress_id` 与实测 IP 一致。
