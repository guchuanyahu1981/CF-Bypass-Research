# CF-Bypass-Research 项目总状态与待办清单

> **本文件是本项目唯一的状态源与施工图。** 2026-11-09 由以下 6 份文档合并而成，原件已从工作树删除（`git show <hash>:<path>` 可完整找回）：
>
> | 原文档 | 最后提交 | 内容去向 |
> |---|---|---|
> | `计划书/CF-Bypass-Research-视觉交互与可发现性设计指南-V1.md` | `d28bc3a` | 落地状态 → §2.2/§2.5；残留项 → §3.2；详细设计论证留 git 历史 |
> | `参考的结果计划指南.md`（根） | `9dbb3ab` | 历史状态 → §4；参考库结论 → §4.3 |
> | `plans/状态/参考的结果计划指南.md` | `ade2ce7` | 第一轮状态，结论已被 v6.x 覆盖，仅留 git 历史 |
> | `plans/下一步改进指南.md`（v5.0.1→v6.0.0） | `6e036a4` | 已全部落地 → §2.4；待授权清单 → §3.3 |
> | `plans/审计与改进指南-v6.3→v7.0.md` | `fa5615c` | 未完成 9 项 → §3.1（本轮实测了 file:line）；风险决策 → §4 |
> | `workflow_status.md` | `0ed810b` | 仍被代码引用的风险 → §4.1；审查记录 → §4.4 |
>
> **基线**：`master` @ `83028f2`（v6.4.2，已推送、已打 tag）+ v6.4.3「2026-11-11 全量验收复核」 · 2026-11-11 · 证据分级：**已验证**（实际运行观察）/ **静态确认**（读代码确认）/ **合理推断** / **待验证**（缺环境或权限）

---

## 0. 一页速览

- **服务面**（`solver/` + `gocfsolve/`）：v6.4.0–v6.4.2 已推送并打 tag；v6.4.3 于 2026-11-11 完成全量验收复核（Python 1058 passed / Go 52 / 40 项真进程 E2E / 压测三模式），**无产品代码变更**。CI 双 workflow 在 v6.4.0 全绿（`ci` run 42419767594 / `site-ci` run 42419701920），v6.4.1 的 `site-ci` 因版本页未同步而红——由 v6.4.2 修复。
- **嫁接层**（`captchas/`）：40 文件 5,050 行已纳入覆盖率门禁，实测 22.21%（门槛 20%）。
- **公开站**（`site/`）：V1.1 落地——14 路由、axe 0、Lighthouse 桌面 **100/100/100/100**（中英两页，2026-11-11 复测；LCP 0.334s/0.296s、CLS≈0、TBT 0）、移动档本轮未复测（历史记录 Perf 97–98 / A11y 99–100 / BP 100 / SEO 100）。
- **现有两页**（`/`、`/console`）：36 单测 + 35/35 真浏览器 E2E（2026-11-11 复跑），wedged 徽章 6.47:1。
- **未闭环**：工程待办 **9/9 已闭环**（§3.1）、UI/UX 残留 **5/5 已处理**（§3.2）；§3.3 六项现状：**R-2 部分闭环**（审计 + 出口联动已验证，clearance 未拿到）、**R-1 / R-3 受阻**（许可证 / cfsolve 未到 stage2）、**D-1 / D-2 / D-3 未执行**（D-1 用户 2026-11-11 明确本轮不部署公开站；D-2/D-3 缺账号与人工）。**无 P0/P1 阻塞**。

---

## 1. 当前基线（全部实测）

| 项 | 值 | 口径 |
|---|---|---|
| 版本 | `6.4.3`（`docs/facts.json`） | 已验证 |
| Python 用例 | **1066 收集 / 1058 passed / 8 skipped** | 已验证（2026-11-11 复跑；第 8 个 skip 来自 `test_strategies.py:53`——本机装了 cloakbrowser，"缺失依赖"场景按设计转 skip） |
| 覆盖率 | `solver/` **90%**（89.93%，门槛 80）· `captchas/` **22.21%**（门槛 20）· 65 模块逐模块登记 | 已验证 |
| 模块级指标（接入事实源） | `engines.py` **100%** · `service.py` **3008 行** | 已验证（`docs/facts.json` + `validate-docs.mjs` 把关） |
| Go | 52 个 `Test*`，`-race` 全绿（2026-11-11 复跑） | 已验证 |
| findings | 54 条（三态 verdict），`validate-findings` exit 0 | 已验证 |
| openapi | 14 个操作与源码路由双向一致 | 已验证 |
| 公开站 | 17 HTML / 20 内联 hash / 12 可索引页 + 2 noindex + 404 | 已验证（`plans/证据/ui-v1/`） |
| 初始 JS / 3D chunk / CSS | 233,186 B（227.7 KB）/ 241,853 B（236.2 KB）/ 5,438 B（gzip） | 已验证（`bundle.json`） |
| 真机预算台账 | **60 / 80**，累计失败 **3 / 5** | 静态确认（`policy.md` §2，含 2026-11-10 R-E2E-4 两次） |
| 本机真进程 E2E | `scripts/e2e_local.py` **40/40**（31.2s） | 已验证（`evidence/e2e-local-20261111.json`） |
| 离线压测（2026-11-11 复跑） | hot **4929 QPS** / p50 1.54ms / p99 **3.43ms** / 0 错；cold 8/8 接受、**0 拒绝**、1.0 次求解/请求；share 4 进程两阶段均 **每轮只解 1 次** | 已验证（`scripts/loadtest.py` 三模式，证据 `evidence/loadtest-20261111-{hot,cold,share}.json`；v6.4 历史记录 hot 5483 QPS / p99 3.14ms） |

---

## 2. 已落地能力

### 2.1 服务面（`solver/`、`gocfsolve/`）

- 求解链：cfsolve（纯协议，首选）→ pydoll → Camoufox → FlareSolverr，`route=auto` 由 `/detect` 驱动。
- 回落链纪律：升级阶梯最多 2 跳，`ip-blocked` 不换后端，原因写入 `escalations[]`。
- 缓存：SQLite(WAL)，热路径零 Python 参与（p99 2.93ms）；命中/准入/指标对全部后端一致（P0-1 已修）。
- 观测：`/health`、`/stats`、`/metrics`、`/audit`、`X-Request-Id` 贯穿；审计 JSONL 有界倒读。
- 兼容层：`/createTask`、`/getTaskResult`，有界队列（64 pending / TTL 600s）。

### 2.2 公开站（`site/`）

- 14 路由中英双语，`hreflang` 三组互指，canonical 自指，JSON-LD 四种类型，OG 图 12 页齐全（`/og/{zh,en}.png` 1200×630）。
- 3D 场景 A（ChallengeWall）/ B（Pipeline）：DOM 按钮控制选中态（44×44、`aria-pressed`）、说明卡 `aria-live`、`frameloop="demand"`、静止无自动旋转（E2E 断言静止 1s 内新增帧 ≤ 1）。
- 三档降级：`full` / `lite` / `static`；`pointer: coarse`、reduced-motion、无 WebGL、save-data、低内存一律 `static`，且 static 档保留同一组控件。
- 构建期门禁：`check-env`（拒绝占位 origin）、`check-out`（12 页 canonical/hreflang/JSON-LD/OG/占位残留）、`gen-csp` + `check-csp`（hash 带单引号，17 HTML / 20 hash）。
- 验收数值（2026-11-11 桌面复测）：axe 0、Lighthouse 桌面 **100×4**、CLS≈0、TBT 0ms；移动档本轮未复测（2026-11-06 记录 Perf 98 / 其余 100；2026-11-10 验收报告记录 97/99/100/100）。E2E 19/19、content 11/11。

### 2.3 现有两页（`GET /`、`GET /console`）

- 共享令牌 `solver/ui_tokens.py`（含焦点环、reduced-motion、小屏修正）；两页 `noindex` meta + `X-Robots-Tag`。
- E-4 五条不变量由测试锁死：零写入、零新依赖、token 不入页面（`sessionStorage` 现问）、CSP `default-src 'none'`、白名单字段渲染。
- 36 单测（18 + 18）+ 35/35 真浏览器 E2E（6 状态 + 320/360 宽度）。

### 2.4 研究台（v6.0，已全部落地）

- `scripts/corpus.py`（归档/脱敏/校验）、`scripts/research.py`（doctor / assertions / diff / gaps / patch）、`run.py`（跨平台入口，`run.ps1` 转发）。
- 结构断言矩阵：6 类 stage2 断言 + 3 条 source 假设；合成样本用例永不 skip；`test_oracle_is_red_when_structure_changes` 证明断言真的在断言。
- **现状限制**：`corpus/` 有 **1 份真实样本**（`2026-11-04-6c02e277`，1,151,206 B，入口级 `orchestrator.js` + 反混淆副本），但没走到 stage2（无 `response.decoded.txt`）→ **6 条 stage2 结构断言在 CI 里恒 skip**。补样本见 §3.3 R-3（2026-11-10 尝试受阻）。

### 2.5 公开站设计口径（代码即事实源）

- 令牌单一来源 `site/app/globals.css`：Deep Signal——近黑蓝底 + 电光薄荷主色 `#4DE3C1` + 紫罗兰辅助；深浅双主题，正文对比度 ≥4.5，焦点环 ≥3。
- 预算硬上限：初始 JS 250 KB / 3D chunk 260 KB / CSS 25 KB / 首屏第三方请求 0（字体用系统栈，无 woff2）。
- 动效纪律：只动 `transform`/`opacity`；`prefers-reduced-motion` 下 CSS + JS 双静止；不做滚动劫持、自动旋转、粒子雨、鼠标光斑。
- SEO 纪律：关键词只有意图矩阵（`site/seo/keywords.json`，120 天新鲜度门禁），**不写 volume**；反作弊红线（无 cloaking、无 doorway、不编造成功率）。
- CSP 已知代价：`style-src` 需 `'unsafe-inline'`（R3F 内联样式），记录在案，无用户可控内容。

---

## 3. 待办清单（交付执行方的任务卡）

> 执行纪律（沿用，不重复定义）：红灯先行 → 改 → 定向验证；一卡一 commit；证据落盘 `plans/证据/`；L3 卡须独立 Critic；**不许为过门禁放宽断言**。真机动作一律先报批（`policy.md` §2）。

### 3.1 工程线（9 项，**全部闭环**）

| # | 项 | 状态 | 落地内容 | 验证证据 |
|---|---|---|---|---|
| **A1** | 文档数字漂移 + 模块指标进单一事实源 | ✅ | `docs/项目规格.md` L6/L7、`test_ledger.md` 三处数字清零（`engines.py` 57→**100%**、`service.py` 2073→**3006 行**）；`docs/facts.json` 增 `module_coverage{solver/engines.py}` 与 `service_py_lines`；`validate-docs.mjs` 增 3 条锚点规则 + "顶层键+子键"取值（子键本身含点，只在第一个点处切一次） | `validate-docs.mjs --no-test-run` → 12 处一致；负向注入（改回 57%/2073 行）→ exit 1 且指名模块；`gates.selftest.mjs` 新增 2 条样例（模块漂移、源文件行数漂移） |
| **A2** | 两份同名状态文件合并 | ✅ | 上一轮已完成（根与 `plans/状态/` 两处均已删） | `git ls-files -- '*参考的结果计划指南*'` 为空 |
| **C1** | 幻影参数 `--max-body-bytes` | ✅ | 413 的 `hint` 改为"上限固定 65536 字节（没有可调参数）"；`docs/API.md` 两处、`openapi.json` 的 413 描述同步；两条用例改为**断言不含**该参数名 | `rg max-body-bytes` 只剩 CHANGELOG 历史条目与归档计划书；`tests/test_request.py`、`tests/test_service.py` 相关用例全绿 |
| **C2** | `/detect` 写审计 | ✅ | `_run_detect()` 成功/缓存命中/失败各一行，`_handle_detect()` 的传输层失败也一行；字段与 `/solve` 同族（`host`/`ok`/`elapsed`/`ms`/`cached`/`error_code`/`stage`/`path`） | 新增 3 条用例（成功、缓存命中 `cached=true`、缺 url 失败）——都断言审计文件里真的出现 `event=detect`；成功那条还断言 `GET /audit` **读得到**这一行（只落盘不算闭环）；早退路径的 `elapsed` 在入口取时间戳，不再是恒 0 的假值 |
| **C3** | 404 进 `ErrorCode` + `HEAD`/`OPTIONS` 语义 | ✅ | 枚举增 `NOT_FOUND`/`METHOD_NOT_ALLOWED` 并入 `STATUS_BY_CODE`；两处裸字面量改成 `_send_not_found()`；新增 `do_HEAD`（复用 `do_GET`、经 `_write()` 丢 body）、`do_OPTIONS`（204 + `Allow`，**不带 `Content-Length`**：RFC 9110 §8.6 对 204 是 MUST NOT）、`PUT/DELETE/PATCH/TRACE/CONNECT` → **405 JSON + `Allow`**（原为 501 + `text/html`，与本服务"非 2xx 一律 JSON"的契约冲突）；405/OPTIONS 都**先读掉 body**（不读就回，残留字节会把同连接的下一条请求顶成 501 HTML —— 独立审查抓到的真缺陷） | 新增 9 条用例（HEAD 头与 GET 同源且无体、`Content-Length` 等于 GET body 长度、**同一条 keep-alive 连接上 HEAD→GET→OPTIONS→GET 不解体**、OPTIONS 204、三个方法 405、404 带 `not-found`、未鉴权仍 401）；`docs/API.md` 增"方法语义"段与两个错误码 |
| **C4** | `openapi.json` 补服务端错误分支 | ✅ | `/solve` 补 500/502/503/504、`/detect`（GET+POST）补 500/503/504；**404 没有逐操作声明**（原卡片写"补 404"，落地改了）：404 是路由层行为、对任意具体操作都不可达，声明在 `path` 下只会让代码生成器产出一条永不触发的分支 —— 改为在 `info.description` 里整体说明（"未知路径 → 404+not-found，未鉴权先 401"）；`validate-openapi.mjs` 增规则 4："非 2xx 响应码 ⊆ `STATUS_BY_CODE`"（码表从 `errors.py` 正则提取，提取失败即报错） | `validate-openapi.mjs` 通过；负向注入（声明一个 `418`）→ 红并指名；`gates.selftest.mjs` 新增该规则的负向样例，21/21 全绿 |
| **C5** | Go 客户端补 `Want`/`Route`/`Sitekey` | ✅ | 新增 `SolveOptions{Want,Route,Sitekey}` 与 `SolveWithOptions()`；`solveReq` 三字段 `omitempty`（省略时请求体与旧客户端逐字节一致）；`solve`/`solveShared` 内部签名加 options，`Get`/`GetFresh`/`revalidateOnce` 传零值；`Clearance` 补 `Token`/`AWSWAFToken`，产物校验按 `want` 选字段（独立审查指出：只查 `cf_clearance` 会让 `want=turnstile` **永远报错**，尽管 token 已经拿到） | 新增 **6 条** Go 用例：3 条钉透传（httptest 收**实际请求体** —— 三字段进入 JSON / `Get` 时三键不存在 / 不可解析目标直接报错），3 条钉产物解析（turnstile→`token`、awswaf→`aws_waf_token`、产物缺失必须报错）；`go test -race ./...` 全绿（52 个 `Test*`） |
| **D1** | 两页公共 JS 去重 | ✅ | 新增 `solver/_page_common.py`（`COMMON_JS` 字符串，`__COMMON_JS__` 占位符注入两页），收敛 `token`/`getJSON`/`setKV`/`setBanner`/`setState`/`setEmpty`；顺带修掉两页已漂移的行为（`getJSON` 统一到 text+parse，非 JSON body 也能显示原文；`setKV` 统一到 `removeChild`，符合控制台"禁 innerHTML"的硬约束） | 36 条两页单测全绿；**真 Chrome E2E 35/35**（`verify_ui_e2e.py`，含 wedged/无 token/不可达/审计区块/自动刷新）；覆盖率 `solver/_page_common.py` 100% 且已登记进门禁 |
| **D2** | `do_POST` 下沉嫁接层旁路 | ✅（部分） | 旁路 60 行搬到 `solver/multicaptcha.py::handle_compat_bypass()`（纯函数，注入 `check_target`/`entry_for` 两个回调；`body` 不可变）；`host_of()` 下沉到 `request.py`（`service._host_of` 留别名） | 新增 **17 条**纯函数边界用例（非扩展类型放行/不改调用方 dict/非 http/缺 url/self-url 类型/SSRF 传小写 host/守卫拒绝原样上抛/出口粘性/显式 proxy 优先/无出口/超时夹取/超时非法/`timeout_s` 回退/默认 0/`_t0` 打点/`routed_by`/失败透传）；`do_POST` **227 → 200 行** |

**D2 的残留**：`do_POST` 仍有 **200 行**，未达到项目自定的 50 行标准。剩下的部分读 body → 解析 → SSRF → route 探测 → 后端调用 → 审计，是**状态机本体**，继续拆等于重写（`docs/项目规格.md` L7 已把它记为 P3）。本轮只做"能纯函数化的那一小块"，这是刻意的边界。

**C1 的残留**：`CHANGELOG.md` 的 v5.0 条目里仍写着 `--max-body-bytes`。那是历史记录，不追改；现行契约以 `docs/API.md` 为准（已改）。

### 3.2 UI/UX 残留（5 项，**全部处理完毕**）

| # | 问题 | 实测位置 | 处理 |
|---|---|---|---|
| **V-1** | 死 npm 脚本：`"og": "node scripts/og.mjs"`，文件不存在（附录 B 已删该文件，脚本没清） | `site/package.json` | ✅ 已删该行（`scripts` 现存 17 条，无 `og`） |
| **V-2** | ~~文档写"18 hash"、实际 20~~ | 原 V1 指南 §9.2/AC-22 | **已随旧文档删除消解**；本文件 §1 记录 20 |
| **V-3** | `plans/证据/ui-v1/phase0-baseline.md` 不存在（原 §10.2 要求落盘） | `plans/证据/ui-v1/` | 不补——`bundle.json` 已覆盖同一批数字 |
| **V-4** | 空目录残留：`site/public/fonts/`、`site/public/og/`、`site/styles/` | `site/` | ✅ 已删除（三者均无引用：构建产物走 `out/og/`，字体走系统栈；`site/public/` 现只剩 `favicon.svg`） |
| **V-5** | 证据 README 引用的 CI run 是 v6.3.1 的 `42147228247` | `plans/证据/ui-v1/README.md:27` | ✅ 已更新为 v6.3.2 的 `site-ci` run `42419701920` + `ci` run `42419767594` |

### 3.3 待授权（6 项；需外部凭证或真机出站，未执行前不做）

| # | 项 | 需要什么 | 成本 | 状态（2026-11-11 复核） |
|---|---|---|---|---|
| **R-1** | `captchas/` 任一求解器真机跑通（先选 aliyun 或 turnstile 单次） | cloakbrowser 二进制 + 真实挑战页 | 真机 1–2 次 | **受阻**（2026-11-10）：`pip install cloakbrowser` 成功（PyPI 0.7.2），但 Chromium 二进制下载要 license key——未 `cloakbrowser login` 时 `ensure_binary`/`launch_async` 抛 `CloakBrowserLicenseError`。免费档需在 cloakbrowser.dev claim 并邮件确认（人工）。2026-11-11 复核仍为 `License: none`、二进制未安装。见 `evidence/R-1-20261110.json` |
| **R-2** | 真实 CF 站复验 `/audit` + 出口实测联动（确认 `egress_id` 与实测 IP 一致） | 允许出站 + 指定站点 | 真机 ≤3 次 | **部分闭环**（2026-11-10，用 1 次）：`run.py egress` 实测 IP `129.153.97.251` 与登记值一致；真机 `/solve` 的审计行落盘且 `GET /audit` 读回，`/shutdown` 也落审计。**未拿到 clearance**：camoufox 经 US 数据中心代理 120.5s 返回 `unsolved`，因此成功响应里的 `egress_id` 字段未观察到。2026-11-11 未重试（同站纪律 + 预算）。见 `evidence/R-2-20261110.json` |
| **R-3** | 补一份走到 stage2 的 cfsolve 会话快照（解除 6 条结构断言 skip + `corpus/` 空态文案） | 允许打 1 个真实站 + 内存护栏 | 真机 1–2 次 | **受阻**（2026-11-10，用 1 次）：`challenge --page --solve` 在 deobfuscate 之后卡住，240s 被超时杀掉；产物只有 `page.html` / `orchestrator.js`(642,751B) / `orchestrator.deobfuscated.js`(543,811B)，**没有 `response.decoded.txt`**，6 条 stage2 断言仍 skip。2026-11-11 未重试（同站纪律 + 预算）。见 `evidence/R-3-20261110.json` 与 `plans/证据/r3-stage2-20261110/` |
| **D-1** | 部署公开站（Cloudflare Pages 首选，支持 `_headers`）；绑定域名 | 域名 + API token | — | **未执行（用户决定）**：2026-11-11 用户明确本轮不部署公开站；`wrangler whoami` 仍为 `Not logged in`，环境无 `CLOUDFLARE_API_TOKEN` / `CLOUDFLARE_ACCOUNT_ID`。本地可部署性验证已完成：生产构建 + `verify:site`（14 路由 / axe 0）+ E2E 19/19 + Lighthouse 桌面 100×4 |
| **D-2** | Search Console / Bing / 百度验证与 sitemap 提交；关键词 volume 回填 | 账号 + DNS/HTML 验证 | — | **未执行（缺账号）**：三个平台账号与 DNS/HTML 验证都不在本机；关键词 volume 也没有数据源（`site/seo/keywords.json` 只有意图矩阵） |
| **D-3** | 英文母语审校签字（`site/content/EN-REVIEW.md` 的 Sign-off 仍 `(pending)`） | 人工 | — | **未执行（需人工）**：只有英文母语审校人能签；AI 不能代签，也不该把"机器读过一遍"写成签字 |

> **R-1/R-2/R-3 的执行纪律**（照 `policy.md` §2 走，不另立流程）：台账 **60/80**、累计失败 **3/5**；同站串行 ≥30s；换出口必须换 `--egress-id`；累计失败 ≥5 立即全停。证据按现有模板落 `plans/证据/`（真机）与 `evidence/`（版本验收）。
>
> **2026-11-11 复核**：本轮**未消耗真机预算**（台账维持 **60/80**、失败 **3/5**）。R-1 的阻塞条件未变化（`cloakbrowser info` → `License: none`、二进制未安装）；R-2/R-3 依赖成功 clearance 与 stage2 样本，本轮不重试。公开站部署按用户 2026-11-11 的决定不执行。
>
> **R-E2E-4 批次结果（2026-11-10）**：目标 `www.scrapingcourse.com/cloudflare-challenge`（公开定位为 CF 挑战练习页）。用 **2 次，全部失败**：① cfsolve 会话捕获 240s 未到 stage2；② 服务端 camoufox `/solve` 120s 未解出。**副产品**：装 `cloakbrowser` 后暴露了一个真缺陷——无许可证被报成 `500 internal` + traceback，已在 v6.4.1 归到 `not-configured`(503)，`e2e_local.py` 由 38/40 回到 **40/40**。计划与原始输出见 `plans/证据/real-machine-batch-20261110-plan.md`、`plans/证据/r2-20261110/`、`plans/证据/r3-stage2-20261110/`。

> 另有两项已知偏离授权，**仍未做**：根 README 顶部加公开站链接、`llms.txt`。两者都要求一个真实公开 origin，而 D-1 未部署——现在加链接只会指向不存在的站点，等部署落地后一并做。
> **工作区注记**：根目录 `local_tok.txt`（43 B，本地求解 token）、`local_solver.log`、`tmpfin2/` 未跟踪且未被 `.gitignore` 覆盖。用户 2026-11-09 拍板**不处理**（接受相关产物随后续研究一起公开）；执行方提交时请用显式路径 `git add`，不要 `git add -A`。
> **真机纪律（`policy.md` §2，遇到时照此执行）**：台账 **60/80**、累计失败 **3/5**；累计失败 ≥5 立即全停；同站串行 ≥30s；换出口必须换 `--egress-id`；**CI 永远离线**。

---

## 4. 历史风险与决策存档（被代码/门禁引用，必须保留）

### 4.1 风险登记（仍有约束力）

| 编号 | 内容 | 约束 |
|---|---|---|
| **R9b** | glassdoor.co.uk 出口疑似被提分：同站当天连打 12 次后 60s 解不出 | 同一出口不反复打同一站；换出口必须换 `--egress-id`（被 `policy.md` §2 引用） |
| **R13** | WAL 切换竞态未确定性复现（同进程/跨进程持锁、90 次裸构造均未稳定命中） | 已有有界重试把概率压到观察不到，**不能宣称"不可能再发生"**；回归只覆盖重试逻辑（被 `solver/cache.py:401` 引用） |
| **R14** | 测试侧 `ResourceWarning: unclosed database` | 已修（v6.3 B4）：根因是 conftest 只钩了 `solver.cache`、漏了脚本模式 `cache` 模块 |
| **批次 C 抖动** | 超大 body 客户端写一半收到 RST，约 **1/30** 读不到 413 | `DRAIN_MAX_BYTES`/`DRAIN_TIMEOUT` 有界读；用例同时守"拿到 413"与"秒级收尾"两个边界（被 `tests/test_service.py:912` 引用） |
| **R10/R15** | 缓存命中路径 32 并发 p99 ≈1s；读路径串行进锁 | 单进程建议 ≤16 并发；实测无退化（hot 5959.6 QPS / p99 2.72ms） |

### 4.2 决策记录（摘要；完整背景在 git 历史与本文件各节）

| 编号 | 决策 |
|---|---|
| D-1 / D-2 / D-3 / D-4 | v4.0：全波次交付；SQLite(WAL) 缓存保留 FileStore 降级；删 `node-shim`/`go-jseng`/`go-replay`；真机预算 60 次（2026-11-04 追加 20 → 上限 80） |
| E-1 / E-2 / E-3 / E-4 | v6.0：主题取研究工程化（A/B/C 类降到下版）；真机预算 0（全离线，动作进待授权清单）；运行时依赖一次只引一个；只读控制台要（单文件零写入五条约束） |
| 依赖取舍（v6.0 T-81） | 生产不引 `prometheus_client`（手写 metrics 已 95% 覆盖）；dev 引 `hypothesis` + `ruff`（仅 E/F 规则）；`mypy` 不引 |
| **geoip 否决** | camoufox `geoip=True` 会 `download_mmdb()` 下载外部数据库——运行期外部依赖 + 隐私面。重开需同时满足：①离线可用 MMDB 供给 ②同站双臂实测收益。保持 `geoip=False`（被 `.claude/skills/cf-bypass-methods/SKILL.md` 引用） |
| 覆盖口径 | 门槛是 floor（防向下漂移），不逼堆无断言测试；`captchas/` 22.21% 为如实值，`gap_yolo`/`gap_vlm`/`gap_cv` 0–8% 按能力边界登记 |
| **不支持的方法回 405 而非 501**（v6.4 C3，偏离卡片原写法） | 卡片原写「其余方法保持 501、只把 HTML 换成 JSON」。落地改成 **405 + `Allow`**：501 的语义是「服务不认识这个方法」，而这里是「该资源不支持它」；更关键的是 `ErrorCode ↔ STATUS_BY_CODE` 是对外契约，501 不在码表里 —— 硬用就得为 501 编一个错误码，或者让 `error_code` 与状态码对不上（按契约分支的客户端会走错）。405 同时满足 HTTP 语义与契约自洽 |
| **`SolveWithOptions` 不碰本地缓存**（v6.4 C5） | 本地缓存键是「域 + 出口」，成立前提是产物恒为 `cf_clearance`；`want=turnstile` 之类是一次性 token，混进同一键会让后续 `Get` 拿到一个不是 clearance 的东西，且整个 TTL 内不会自愈。宁可要求调用方显式只用一次，也不给缓存埋语义雷 |

### 4.3 参考库结论（2026-11-05 重分类后复核）

- `captcha-solver` 系已以 `captchas/` 形式接入；`ohmycaptcha`/`cipherwire__disarm`/`FlareSolverr` 已在 v6.2 整合；双档门禁/流量录制思路已落地。
- `lightpanda-io__browser`（Zig 无头浏览器）：**P3 观察**，JS 覆盖面与反检测能力未知，不建议换底座。
- 纯协议复现类（`CaptchaFoxChallengeSolver`、`CloudFlareInvisibleSolver`）与本项目 cfsolve 同族，无新增工程手段。
- 付费 API 客户端（`nonecap`/`nopecha`/`cf-turnstile-token`）违反 `policy.md` 红线，排除。

### 4.4 审查记录

| 轮次 | 范围 | 结论 |
|---|---|---|
| v6.0 独立复审 ×3 | 研究工程化 | 修 7 条真缺陷（研究指标 schema 错配/三态伪造、脱敏闸门失效、diff 判据回退等） |
| v6.3.1 收口 | 公开站 + 两页 UI | 门禁补齐（check-env/check-out/OG/og:image 12 页）；CI 全绿 |
| v6.3.2 独立审查 | `74c72db..928ab6a` | 3 Critical + 7 Should-fix，逐条核实全部成立并修复（idle E2E 恒真、窄屏遮挡、视差失效、标签数值、版本漂移、门禁自测未接 CI、`importorskip` 缺依赖、裸导入扫描可绕过、覆盖率命令形式、占位符边界） |

| v6.4 独立审查（本轮） | 工作树全部改动（C1–C5、D1–D2、A1、V-1/V-4/V-5、三道门禁脚本） | 1 P1 + 1 P2 + 6 P3，逐条核实全部成立并已修：**P1** Go 客户端只认 `cf_clearance`，`want=turnstile` 永远报错（产物字段随 want 变）→ `Clearance` 补 `Token`/`AWSWAFToken`、校验按 want 选字段、补 3 条产物用例；**P2** 405/OPTIONS 不读 body → 同连接下一条请求被残留字节顶成 501 HTML（C3 引入的回归）→ 两处先 `_read_body_raw()`，补 2 条 keep-alive 错位用例；**P3** 逐操作 404 对任何操作都不可达（C4 自相矛盾）→ 改成 `info.description` 整体说明；204 带 `Content-Length` 违反 RFC 9110 §8.6 → 删；状态页空态丢 `chips` class → 补回；`CONNECT` 仍 501 HTML → 并入 405；detect 早退路径审计 `elapsed` 恒 0 → 入口取时间戳；`toleranceFor` 注释与实现不符 → 给 `module_coverage` 补 ±2 容差。修复后定向测试与全量复跑均绿 |

| v6.4.1 真进程 E2E + 独立审查（2026-11-10） | `scripts/e2e_local.py` 全 40 条；独立审查员只读核 `git diff` + `cloakbrowser` 包源码 | **1 P1 已修**：`cloakbrowser` 装上但无许可证时，`multicaptcha.solve_async` 只接 `ImportError`，`CloakBrowserLicenseError` 落到 `INTERNAL` → HTTP **500** + 日志 traceback（A26/A16 两条红）。修法：按"依赖不可用"归 `NOT_CONFIGURED`(503) + 登录提示、不打 traceback；补 1 条单测（先红后绿）。**独立审查 Verdict: Approve**，并按审查意见补三点：许可证路径保留原文日志（不带栈）、归因文案覆盖"缺 key / 过期 / 会话满"、主链启动失败提示不再只提 camoufox。审查另提 2 条**记录不修**（既有缺口，P3）：① 主链"握手后"的许可证拒绝（免费档 1 并发被占）在 `solver/service.py:1146` `new_context()` 抛出，经 `:643` 原样上抛到 `:2544` → `INTERNAL` 500；② key 有效但二进制下载失败（`cloakbrowser/download.py:229`）是普通 `RuntimeError` → 500。两者都要先有可用许可证才能真机触发 |
| v6.4.2 站点同步（2026-11-10） | v6.4.1 的 `site-ci` 失败日志 | **1 P2 已修**：站点 `/changelog` 头条停在 6.4.0、`tests.python` 仍是 1065，与 `docs/facts.json` 的 6.4.1 / 1066 不一致——版本号只在仓库侧同步，漏了站点内容。修法：中英 `/changelog` 补条目、benchmark 值同步；重跑 `test:content` 11/11、`verify:site` 14 路由 / axe 0、`test:e2e` 19/19 |
| v6.4.3 全量验收复核（2026-11-11） | 发布候选：五处版本号 / 站点版本页 / 状态文档 / 验收证据 | 首轮 **Request Changes**（1 P1 + 1 P2 + 4 P3，逐条核实成立并修复）：P1 证据文件未入库 → 补齐验收报告与 RELEASE 证据；P2 LCP 0.32/0.29 → 实测 0.334/0.296；P3 UI E2E 文本证据、压测 manifest、CHANGELOG 证据段 → 已补；热更新 cmd 字段记录不改代码。修复后复审 **Verdict: Approve**；CI：`ci` 42942791503 / `site-ci` 42942797220、42942791511 全绿 |

**Approve 语义**：严格审查后未发现阻塞性问题，不是"代码完美"。新节点标 Done 前必须有 Approve 记录（写进本节）。

---

## 5. 明确不做（被否决且有理由）

| 方案 | 否决理由 |
|---|---|
| 给 `captchas/` 11 个求解器全部做真机验证 | 需专有二进制 + 真实挑战页，属真机预算与外部依赖问题，不是工程问题 |
| 把 `captchas/` 提升为主链路求解器 | 与"CF 凭证获取服务"定位漂移；主链路已有 7 种 want |
| 引入 FastAPI/pydantic 自动生成 OpenAPI | 违反零新依赖 |
| 换浏览器底座（含 lightpanda） | 当前 camoufox 已实测有效；换底座=重赌一次性价比 |
| 为 `do_POST` 超标而拆 `Solver` 类 | 状态机+锁+队列，拆=重写；只做能纯函数化的一小块（D2） |
| 给公开站做在线 demo / 账号 / 支付 | 静态站不持有求解凭证；转化走 GitHub |
| 全站 3D / 滚动劫持 / 自动旋转 / 粒子雨 / 字体图标分析 CDN | 性能与可访问性预算不允许；首屏第三方请求必须为 0 |
| 用评分表/覆盖率数字"做高" | 门槛是防漂移 floor |

---

## 6. 文档地图（合并后）

| 想知道什么 | 看哪里 |
|---|---|
| 系统是什么、边界、承诺什么 | `docs/项目规格.md`（单一事实源，冲突以它为准） |
| 接口契约 | `docs/API.md` + `docs/openapi.json` |
| 质量门禁（写死数值） | `policy.md` |
| 测试数/覆盖率/版本号的单一事实源 | `docs/facts.json`（`validate-docs.mjs` 把关） |
| 结论库 | `docs/findings.json`（54 条，`validate-findings.mjs`） |
| **项目状态 / 待办 / 决策 / 风险** | **本文件** |
| 历史施工图（v1.1→v5.0 四版） | `plans/archive/`（结论勿再引用） |
| 验收证据 | `plans/证据/`（UI 与真机）+ `evidence/`（版本验收） |
| 公开站本地构建/预览/部署 | `site/README.md` |
| 过盾方法选型与新增方法判定标准 | `.claude/skills/cf-bypass-methods/SKILL.md` |
| 验收项 ↔ 用例映射 | `test_ledger.md` |
| 版本变更史 | `CHANGELOG.md` |

---

## 7. 验证命令速查

```text
python -m pytest tests/ -q                                        # 全量（1066 收集）
python -m pytest tests/test_console.py tests/test_statuspage.py -q # 两页 UI（36）
python scripts/verify_ui_e2e.py                                    # 两页真浏览器 35/35（需 CHROME_PATH）
cd gocfsolve && go test -race ./...                                # Go 52
node scripts/validate-docs.mjs --coverage-json=coverage.json       # 文档数字一致
node scripts/validate-findings.mjs / validate-openapi.mjs          # 结论库 / 契约
node scripts/gates.selftest.mjs                                    # 门禁自测 21/21
node scripts/check_repo_hygiene.py 2>/dev/null || python scripts/check_repo_hygiene.py
cd site && npm run build && npm run csp && npm run verify:csp && npm run verify:site && npm run test:e2e && npm run test:content && npm run test:tokens && npm run test:contrast && npm run test:seo
```

---

*2026-11-09 · 合并自 6 份文档 · 基线 v6.3.2（`0ed810b`）*
*2026-11-09：v6.4.0 工程债一轮发版（§3.1 九项 + §3.2 五项，commit `d8a410c` / tag `v6.4.0`）*
*2026-11-10：v6.4.1——真进程 E2E 抓到「cloakbrowser 装了但无许可证」被误报 500，已归位到 `not-configured`；§3.3 真机批次 R-E2E-4 执行（R-2 部分闭环，R-1/R-3 受阻），台账 60/80、失败 3/5*
*2026-11-10：v6.4.2——v6.4.1 的 `site-ci` 因站点 `/changelog` 未跟上事实源而红；补中英条目与 `tests.python` 1066，站点门禁复绿*
*2026-11-11：v6.4.3——全量验收复核（无产品代码变更）；Python / Go / 门禁 / 40 项真进程 E2E / 两页 35 项浏览器 E2E / 站点 19 项 / 压测三模式 / Lighthouse 100×4 全部复跑通过*
