# workflow_status.md 存档（2026-11-09 文档合并时按 findings.json 证据引用摘出）

原文件已删除（git show 0ed810b:workflow_status.md 可查全文）。以下为 docs/findings.json 各条证据引用的原文段落。

| # | 缺陷 | 触发条件 | 证据等级 |
|---|---|---|---|
| **N-01** | **`/solve` 在配置额外后端时绕过 SSRF 守卫**（守卫写在 `Solver.solve` 内，handler 走 router 分支不进该方法） | 配 `--cfsolve-bin`/`--pydoll`/`--flaresolverr` 任一 | **已验证**（假 cfsolve 二进制实收 `--page http://127.0.0.1:8903/health`） |
| **N-02** | cfsolve/pydoll 返回 `expires_at=0` ⇒ Go 侧每次回放都重新求解 | 启用这两个后端 | 静态确认 → 已修（并行会话） |
| **N-03** | **多进程首启争抢建盐文件时 `os.replace` 抛 `PermissionError`（WinError 5）**，4 worker 同启约 1/4 概率起不来 | 空缓存目录 + 多进程同时启动 | **已验证**（loadtest burst 阶段抓到）→ 已修为 `O_CREAT\|O_EXCL` 抢占 |
| **N-04** | `FileStore.items()` 不过滤过期项 ⇒ `/health.cached_domains` 虚高 | 缓存里有过期项 | 已验证（新用例抓到）→ 已修 |
| **N-05** | 多进程**同时**抢同一新域会各解一次（无跨进程互斥） | 4 worker 同时收到新域 | **已验证**（burst 12/12）→ **未修**，列为已知限制（见 §7） |
| **N-06** | `Prewarmer` 用 `self._stop = threading.Event()` **覆盖了 `Thread._stop` 私有方法**，3.12 上线程收尾抛 `TypeError: 'Event' object is not callable` | 保活线程退出 | **已验证（CI, Linux/py3.12 抓到；本机 3.14 复现不出）** → 已改名 `_stop_event` |
| **N-07** | `test_filestore_corrupt_file_*` 用 `glob("*.bin")` 取文件，Linux 上先拿到 `salt.bin` ⇒ 用例实际在弄坏盐而不是缓存条目 | 非 NTFS 目录序 | **已验证（CI, Linux 抓到）** → 改用排除盐的 `cache_files()` 助手 |


| C1 | `tests/test_service.py` 用**脚本模式**（`sys.path` 插入 `solver/` 后 `import service`），而新测试用**包模式**（`from solver import service`）—— **两个模块对象、两个 Handler 类**，`Handler.solver` 设在一个上、读另一个上永远是空的 | P3（测试基建）；已在 `test_observability.py` 里注释说明并跟随既有写法。**生产路径 `python solver/service.py` 就是脚本模式，所以不是生产缺口** |
| C2 | `Policy.md` 的机器预算台账已按用户 2026-11-04 决策更新：原 60 / 已用 ≈46 → **追加 20，累计上限 80** | 已落地 |

### 批次 C 收尾时发现并修复的第二个真缺陷（已进 findings F-042）

| 项 | 内容 |
|---|---|
| 现象 | 全量跑时 `test_oversized_body_on_detect_also_413` 偶发 `ConnectionReset/Aborted`（拿不到 413）。单跑通过 |
| 量化 | `pytest tests/test_service.py -k oversized` 连跑 **30 次失败 1 次**（≈3%） |
| 性质 | **契约可观测性缺陷**，不是"测试抖动"：真实调用方会表现为"发大 body 时有时拿不到结构化错误，只看到连接被重置"，极易被误判为网络问题 |
| 根因 | `_read_body_raw` 在 `n > MAX_BODY_BYTES` 时只设 `close_connection=True` 就返回；未读 body 留在 socket，服务端 close 时内核发 RST，客户端还在写 → 读不到刚发出的 413 |
| 修法 | 新增 `Handler._drain_bounded()`：**有界**读掉前缀（`DRAIN_MAX_BYTES = 2 × MAX_BODY_BYTES`，`DRAIN_TIMEOUT = 1s` 短 socket 超时后恢复）。理由：读干净 = DoS 放大器；一点不读 = 契约不可观测。等价于 nginx 的 `lingering_close` |
| 验证 | 修后 `-k oversized` 连跑 **40 次 0 失败**；新增 `test_oversized_body_finish_is_bounded`（原始 socket：声明 10× 上限、只发 1KB，必须在 5s 内拿到 413）守"有界"这个性质 |
| 教训 | 这个 bug 是**加测试时撞出来的**，和 F-041 一样：**只有真正去覆盖那条路径，才会发现契约在网络层并不成立** |

### 批次 C 最终门禁（2026-11-04，全部真实执行）


pipe 给独立 `claude -p` 进程，明确"只输出审查结论、不改文件、不运行命令、不用工具"。
**通道有效**：返回了分级的完整审查（2 BLOCKER + 5 MAJOR + 18 MINOR + 10 NOTE）。

> 过程教训：第一次把输出管道过 `tail -120`，BLOCKER 段被截掉了 → 只能再跑一次专门取 BLOCKER。
> 审查类输出**必须完整落盘**，不要顺手 `tail`。

### 两个 BLOCKER（都是我自己这轮引入的，已修）

| # | 缺陷 | 失败场景 | 修法 |
|---|---|---|---|
| **B1** | `_drain_bounded` 只有**单次 recv** 超时，且用的是 `rfile.read(n)` —— 而 `BufferedReader.read(n)` 会**循环 raw read 直到凑满 n 字节** | 100 B / 0.9 s 滴流 → 一次 `read(64KB)` 阻塞约 590s，128KB 上限拦不住 → 一条连接钉住线程约 20 分钟，64~128 条就能打满线程池。**是修 F-042 时新引入的可用性回归** | 改用 `read1()`（最多一次 raw read）+ **墙钟 deadline** + 每轮按剩余预算收紧 socket 超时；`DRAIN_MAX_BYTES` 提到 1MB（真正的闸是 deadline，不是字节数） |
| **B2** | 收尾顺序反了：**先 drain 再写 413**。标准 lingering-close 是"先写响应、再读残余" | ①413 的发出被上传速度拖住；②body 超过 drain 上限时残余仍在缓冲就被 close → RST → 客户端拿到 `Connection reset` 而不是 413。等于把 F-042 的失效阈值从 0 挪到了上限值 | `_read_body_raw` 只登记 `_drain_after_send`；真正的有界读移到 `_send()` **写完响应之后**（`_drain_after_response`） |

用例：`test_oversized_body_drain_has_wall_clock_deadline`（滴流必须在数秒内拿到 413）、
`test_oversized_body_larger_than_drain_still_returns_readable_413`（声明 1MB、只发 200KB，仍须读到 413 的 **body**）。

### 5 个 MAJOR（已修 4，1 条经核实不成立）

| # | 缺陷 | 处置 |
|---|---|---|
| M1 | 兼容层 `_run` 的 try 只圈住 `solve()`，之后抛异常 → 任务**永远 processing** | 已修：整个函数体兜 `BaseException`，拆出 `_run_inner`，`_finish` 幂等 |
| M2 | `create()` 先登记再 `start()`，线程起不来不回滚 → 僵尸记录占配额 | 已修：`start()` 失败回滚登记并回滚 `created_total` |
| M3 | `validate-openapi.mjs` 的鉴权规则**只校验规格自洽、不看源码** → 「规格要鉴权、实现忘了」照样绿灯 | 已修：新增 `tests/test_auth_matrix.py`（起真实服务逐个端点断言 401/200/200+errorId）。这是复审建议的"更强做法"，且比解析源码稳 |
| M4 | `capture_sample.py` 的 import 回退是**死路**（`sys.path` 插 `ROOT/solver` 却回退 import `solver.*`），且 `detect_summary` 无保护 → 真抓一轮后 manifest 丢失（预算已花） | 已修：path 指仓库根 + 单一 import 形式；`_safe_detect()` 失败写进 `detect.error` 而不中断 |
| M5 | `self.started_at = self._started_at` 可能引用未赋值属性 → 服务起不来 | **核实不成立**：`_started_at` 在 384 行先赋值、本行在 440 行。已确认（复审当时标 UNVERIFIED，因为 diff 未含该段） |

### 已修的 MINOR（12 条）

`hint` 文案变更已在 API.md 说明 · `timeout` 夹取挪回 SSRF 之后（恢复逐字一致的报错顺序 + 顺序回归用例）·
`compare_digest` 非 ASCII 崩溃 · 兼容层无凭据时不再先暴露 400/413 · `_log_internal` 的 reason 脱敏（新增 `_redact`）·
`AUDIT_FIELDS` 各调用点补 `path` · 白名单**反向黑名单断言** · `validate-docs` 两条静默跳过 ·
`git describe` 版本不一致改为警告（未打 tag 属正常）· `check-coverage` 绝对路径误诊 ·
`--max-scripts` 真生效 · 删死代码（`CapturePlan`/`build_plan`）· 重复注释

### 明确未修（记录理由）

- **MINOR-9 版本号口径**：仓库最近 tag 是 `v5.0.0`，本轮改动未发版。`facts.json`/`openapi.json` 保持 `5.0.0`（= 已发布版本），
  代码注释里的 "v5.0.1" 指"未发布的下一版"。**发版时需五处同步**（CHANGELOG/Dockerfile/compose/facts/openapi）。
- **NOTE 级 10 条**（兼容层无幂等/无速率限制、`/status` 恒 200、`EventLog.total` 可提属性等）：记录不修。

### 新增 findings（7 条，43 → 50）

`F-044` drain 无墙钟上限（critical）· `F-045` 收尾顺序反了（critical）· `F-046` 兼容层两处兜底缺失（high）·
`F-047` 保留期从创建时刻算（medium）· `F-048` 非 ASCII clientKey 崩溃（medium）·
`F-049` 鉴权门禁形同虚设（high）· `F-050` 采集脚本 import 死路 + manifest 丢失（high）

### 修复后复验（全部真实执行）

## R-E2E-3 与发版 v5.0.3（2026-11-04）

### glassdoor.co.uk 端到端：**成功**（2 次目标请求）

| 步 | 结果 |
|---|---|
| 求解 | `POST /solve` **16.5s**，`cf_clearance` + **12 条 cookie**（含 `__cf_bm` 与 glassdoor 业务 cookie） |
| 回放（30s 后） | **200 / 735,957B** / 标题 `Glassdoor | Job Search and Career Community` / 前 2048 字节 **0 挑战 marker** |

**cookie 处置**：全程只在内存，未落盘（吸取 `_cookie_dump.json` 的教训）；服务缓存目录 `/tmp/realB` 用后即删。

### 结论（多站端到端）

| 站点 | challenge 形态 | 求解 | 回放 |
|---|---|---|---|
| udemy.com | CF managed | 18.32s / 462B | ✅ 200 / 306,752B 真实页面 |
| glassdoor.co.uk | CF managed | 16.5s / 12 cookie | ✅ 200 / 735,957B 真实页面 |

『求解 → 缓存 → 回放真实内容』在**两个不同站点**上成立（`F-053`）。
新增 `F-054`（工具契约：判挑战必须复用生产实现；验证脚本凭证只存内存）。

### 发版 v5.0.3
commit `c7cd76c` · tag `v5.0.3` · **CI 绿**（run 41759121251，4 job 全过，`gh api check-runs` 逐条核对）·
Release（Latest）: https://github.com/lza6/CF-Bypass-Research/releases/tag/v5.0.3
提交前按新纪律核对暂存区：无凭证文件 / 无临时文件 / 无 plans/。
门禁：Python 542 / Go 46 / 覆盖率 86% / 20 模块 / 12 操作 / 文档 9 处 / findings **54**（47/5/2）。

### 预算台账（最终）
R-E2E-1 4 次 → R-E2E-2 6 次 → R-E2E-3 2 次 = 本轮共 **12 次**；累计 ≈**58 / 80**；**累计失败 1/5**（远低于停止线）。

