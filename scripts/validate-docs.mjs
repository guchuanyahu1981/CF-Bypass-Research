#!/usr/bin/env node
/**
 * 校验"文档里写的数字"与"实际测出来的数字"是否一致。
 *
 * 为什么需要它：`scripts/validate-findings.mjs` 守住了"结论 → 证据文件"这条链，
 * 但**没有守住数字**。2026-11-04 的审计实测到同一件事被写成三种值：
 *   README.md `314 项` / docs/00-索引.md `279 项` / test_ledger.md `311` / 实际 `328`。
 * 四处不同，且没有任何东西会报错 —— 读者只能靠运气判断哪个是真的。
 * 这个脚本把"文档里的数字"也变成 exit code，口径与 validate-findings.mjs 一致。
 *
 * 设计（刻意简单）：
 *   · 单一事实源是 `docs/facts.json`（由本脚本 --write 生成，不手改）；
 *   · 每条 RULES 声明"哪个文件的哪一行数字对应哪个键"，文档数字必须等于事实源；
 *   · 事实源本身也要与本次实测一致，否则先 --write 再改文档。
 *
 * 零依赖、不联网。唯一的子进程调用是 `python -m pytest`（数用例 / 算覆盖率）
 * 和 `git describe`（取版本），均可通过参数跳过。
 *
 * 用法：
 *   node scripts/validate-docs.mjs                      # 全量实测后校验（慢，~50s）
 *   node scripts/validate-docs.mjs --write              # 实测并重写 docs/facts.json
 *   node scripts/validate-docs.mjs --coverage-json=c.json  # 复用已生成的覆盖率（CI 用）
 *   node scripts/validate-docs.mjs --no-test-run        # 只按 facts.json 校验文档（最快）
 */

import { readFileSync, writeFileSync, existsSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = resolve(dirname(fileURLToPath(import.meta.url)), "..");
//: `--root=<dir>` 让门禁可以对一棵**临时树**跑（scripts/gates.selftest.mjs 用）。
//: 门禁自己也得能被测试 —— 否则规则写错时它只会安静地"通过"。
const rootArg = process.argv.find((a) => a.startsWith("--root="));
const ROOT = rootArg ? resolve(rootArg.slice("--root=".length)) : HERE;
const FACTS_PATH = resolve(ROOT, "docs/facts.json");

function argValue(name) {
  const hit = process.argv.find((a) => a.startsWith(`${name}=`));
  return hit ? hit.slice(name.length + 1) : undefined;
}

const WRITE = process.argv.includes("--write");
const NO_TEST_RUN = process.argv.includes("--no-test-run");
const COVERAGE_JSON = argValue("--coverage-json");

/**
 * 文档位置 → 数字的映射表。
 *
 * 加一个新位置 = 加一条规则。**不要**把"历史轮次的数字"加进来：
 * 那些值本来就该停在当时（历史轮次的数字记录在 git 历史与 CHANGELOG 里，保持原值）。
 *
 * capture 是正则的第几个捕获组（从 1 开始）；一个正则里可以有多个规则共享一次匹配。
 */
const RULES = [
  { key: "python_tests", file: "README.md", re: /tests\/\s+#\s+(\d+) 项 Python 测试/, group: 1 },
  { key: "python_tests", file: "docs/00-索引.md", re: /tests\/\s+(\d+) 项 Python 离线测试/, group: 1 },
  { key: "go_tests", file: "docs/00-索引.md", re: /项 Python 离线测试 \+ (\d+) 项 Go 测试/, group: 1 },
  { key: "python_tests", file: "test_ledger.md", re: /\| Python 用例 \| \*\*(\d+)\*\*/, group: 1 },
  { key: "python_coverage_percent", file: "test_ledger.md", re: /\| Python 覆盖率 \| \*\*(\d+)(?:\.\d+)?%/, group: 1 },
  { key: "findings", file: "docs/00-索引.md", re: /`docs\/findings\.json`（(\d+) 条，三态 verdict）/, group: 1 },
  { key: "version", file: "docker-compose.yml", re: /image:\s*cf-bypass-solver:(\d+\.\d+\.\d+)/, group: 1 },
  { key: "version", file: "Dockerfile", re: /cf-bypass-solver:(\d+\.\d+\.\d+)/, group: 1 },
  { key: "version", file: "CHANGELOG.md", re: /^## \[(\d+\.\d+\.\d+)\]/m, group: 1 },
  // 规格自称“冲突以我为准”，它自己的版本行必须跟事实源一致。
  { key: "version", file: "docs/项目规格.md", re: /^>\s*版本：v?(\d+\.\d+\.\d+)/m, group: 1 },
  // v6.4 A1：模块级指标进事实源。此前 `engines.py 覆盖率 57%` 与 `service.py 2073 行`
  // 这两个数字**没有任何东西守着**，实测分别是 100% 与 2994 行 —— 漂了一整轮没被发现。
  // 键名用点分路径（`module_coverage.<模块>`）指向嵌套对象，见 factAt()。
  { key: "module_coverage.solver/engines.py", file: "docs/项目规格.md",
    re: /\| L6 \| `engines\.py` 覆盖率 \*\*(\d+)%\*\*/, group: 1 },
  { key: "service_py_lines", file: "docs/项目规格.md",
    re: /\| L7 \| `service\.py` 仍 \*\*(\d+) 行\*\*/, group: 1 },
  { key: "module_coverage.solver/engines.py", file: "test_ledger.md",
    re: /\| `solver\/engines\.py` 覆盖率 \*\*(\d+)%\*\*/, group: 1 },
];

/** version 是字符串（"5.0.0" 转 Number 会变 NaN），其余键是数字。 */
const STRING_KEYS = new Set(["version"]);

/**
 * 允许的测量容差。
 *
 * 为什么覆盖率需要容差：**覆盖率是平台相关的**。同一个提交在 Windows 与 Linux 上实测不同
 * （`Server.allow_reuse_address = os.name != "nt"`、socket 错误类型、路径处理等分支只在一侧走到），
 * 2026-11-04 实测 Windows 86.x% / Linux 87.x%，四舍五入后就是 86 vs 87。
 * 拿它当跨平台硬等值门禁 → CI 必红，而且红得没有信息量。
 * 真正的覆盖率门禁在 `check-coverage.mjs`（逐模块下限 + 已留 floor 容差），这里只管"文档别写飞"。
 */
const TOLERANCE = {
  python_coverage_percent: 2,
  // 模块级覆盖率同样是平台相关的（同一个提交在 Windows 与 Linux 上实测差 1–2 点），
  // 按顶层键匹配，所以 `module_coverage.*` 全都吃这条容差。
  module_coverage: 2,
};

/**
 * 按"顶层键 + 可选子键"从事实源里取值：
 * `module_coverage.solver/engines.py` → facts.module_coverage["solver/engines.py"]。
 *
 * **只在第一个点处切一次** —— 子键本身可能含点（`engines.py`），按 `.` 全切会把它劈成
 * `["module_coverage","solver/engines","py"]`，然后报"缺键"（这个坑真踩过）。
 *
 * 为什么允许嵌套：`module_coverage` 是"模块 → 覆盖率"的字典，天然会长出新条目；
 * 拍平成 `engines_coverage` 这种键名的话，加第二个模块就要再发明一个键名。
 */
function factAt(facts, key) {
  const dot = key.indexOf(".");
  if (dot === -1) return facts[key];
  const root = facts[key.slice(0, dot)];
  return root == null ? undefined : root[key.slice(dot + 1)];
}

/** 容差按**顶层键**取（`module_coverage.*` 也该跟 `python_coverage_percent` 一样有容差）。 */
function toleranceFor(key) {
  const root = key.slice(0, key.indexOf(".") === -1 ? key.length : key.indexOf("."));
  return TOLERANCE[root] ?? TOLERANCE[key] ?? 0;
}

function same(key, a, b) {
  if (a === undefined || b === undefined) return false;
  if (STRING_KEYS.has(key)) return String(a) === String(b);
  return Math.abs(Number(a) - Number(b)) <= toleranceFor(key);
}

const problems = [];

/** 本次没能实测的键：必须打印出来，否则"跳过即通过"会变成假校验。 */
const unmeasured = [];

function problem(where, message) {
  problems.push({ where, message });
}

function run(cmd, args, cwd = ROOT) {
  return execFileSync(cmd, args, { cwd, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
}

function measure() {
  const measured = {};

  if (!NO_TEST_RUN) {
    // 一次 --collect-only 数用例：不跑函数体，比全跑快一个数量级。
    try {
      const out = run("python", ["-m", "pytest", "tests/", "-q", "--collect-only"]);
      const m = out.match(/(\d+)\s+tests?\s+collected/) ?? out.match(/collected\s+(\d+)/);
      if (m) measured.python_tests = Number(m[1]);
    } catch (e) {
      problem("measure", `pytest --collect-only 失败（无法核实用例数）：${(e.message ?? "").slice(0, 200)}`);
    }
  }

  if (COVERAGE_JSON) {
    const p = resolve(ROOT, COVERAGE_JSON);
    if (!existsSync(p)) {
      problem("measure", `--coverage-json 指向的文件不存在：${COVERAGE_JSON}`);
    } else {
      try {
        const cov = JSON.parse(readFileSync(p, "utf8"));
        // v6.3：`--cov` 同时覆盖 solver/ 与 captchas/，`totals` 是两者混算的结果
        // （会把 90% 的 solver 和 22% 的 captchas 平均成 64%）。文档里写的"Python 覆盖率"
        // 一直指 solver/ 整体，所以这里按范围取子树，与 check-coverage.mjs 的口径一致。
        let covered = 0;
        let statements = 0;
        for (const [file, info] of Object.entries(cov?.files ?? {})) {
          const path = String(file).split("\\").join("/");
          if (!path.startsWith("solver/")) continue;
          const summary = info?.summary ?? {};
          covered += Number(summary.covered_lines ?? 0);
          statements += Number(summary.num_statements ?? 0);
        }
        const pct = statements
          ? (covered / statements) * 100
          : (cov?.totals?.percent_covered ?? cov?.totals?.percent_covered_display);
        if (pct === undefined) problem("measure", `${COVERAGE_JSON} 里没有 totals.percent_covered`);
        else measured.python_coverage_percent = Math.round(Number(pct));

        // 模块级覆盖率（v6.4 A1）：只测**事实源里已登记**的模块。
        // `module_coverage` 的键就是"哪些模块的数字被文档引用"的清单 —— 把 60+ 个模块
        // 全写进 facts.json 只会让它每天漂一次，而没人看那些数字。
        const registry = Object.keys(readFacts().module_coverage ?? {});
        if (registry.length) {
          const byPath = new Map();
          for (const [file, info] of Object.entries(cov?.files ?? {})) {
            byPath.set(String(file).split("\\").join("/"), info);
          }
          const perModule = {};
          for (const modulePath of registry) {
            const hit = byPath.get(modulePath);
            if (!hit) {
              unmeasured.push(`module_coverage.${modulePath}`);
              continue;
            }
            perModule[modulePath] = Math.round(Number(hit.summary?.percent_covered));
          }
          if (Object.keys(perModule).length) measured.module_coverage = perModule;
        }
      } catch (e) {
        problem("measure", `${COVERAGE_JSON} 不是合法 JSON：${e.message}`);
      }
    }
  }

  if (!NO_TEST_RUN && !measured.python_tests) {
    // pytest --collect-only 没给出数字（输出格式变化/被替换成别的解释器）：必须显式记"未测量"。
    // 旧条件还要求 `!ranPytest`，于是"跑了但没解析出数字"会静默跳过 —— 门禁假绿。
    unmeasured.push("python_tests");
  }

  // 单文件规模（v6.4 A1）：`service.py` 的行数是文档里写死的已知限制（L7），
  // 必须能被核对 —— 否则"2329 → 2073"这种数字会一直停在很久以前的某一天。
  try {
    const py = readFileSync(resolve(ROOT, "solver/service.py"), "utf8");
    measured.service_py_lines = py.split("\n").length - (py.endsWith("\n") ? 1 : 0);
  } catch (e) {
    problem("measure", `读 solver/service.py 失败（无法核实 service_py_lines）：${e.message}`);
  }

  // Go 用例数：只在 go 可用时测量（CI 的 go job 负责编译与 -race，这里只数用例）。
  // 必须在 gocfsolve/ 里跑 —— go.mod 在那个子目录，从仓库根跑会直接失败，
  // 而"失败就跳过"会让 go_tests 这条规则永远不被真正测量（假校验）。
  try {
    const out = run("go", ["test", "./...", "-list", "^Test"], resolve(ROOT, "gocfsolve"));
    const n = out.split("\n").filter((l) => l.startsWith("Test")).length;
    if (n > 0) {
      measured.go_tests = n;
    } else {
      // 命令成功但一条都没数到：说明 `go test -list` 的输出格式变了。
      // 必须显式记成"未测量" —— 否则会静默沿用旧 facts.json 通过校验（正是本脚本自己警告的坑）。
      unmeasured.push("go_tests");
    }
  } catch {
    unmeasured.push("go_tests");
  }

  // 结论条数：读 findings.json 本身（与 validate-findings.mjs 同源）
  try {
    const doc = JSON.parse(readFileSync(resolve(ROOT, "docs/findings.json"), "utf8"));
    if (Array.isArray(doc.findings)) measured.findings = doc.findings.length;
  } catch (e) {
    problem("measure", `读 docs/findings.json 失败：${e.message}`);
  }

  // 版本：以最近一个 git tag 为准（未打 tag 的提交不产生新版本号）
  try {
    const tag = run("git", ["describe", "--tags", "--abbrev=0"]).trim();
    if (/^v?\d+\.\d+\.\d+$/.test(tag)) measured.version = tag.replace(/^v/, "");
  } catch {
    // 浅克隆/无 tag：沿用 facts.json 里的值
  }

  return measured;
}

function readFacts() {
  if (!existsSync(FACTS_PATH)) {
    problem("facts", "缺少 docs/facts.json（先跑一次 --write）");
    return {};
  }
  try {
    return JSON.parse(readFileSync(FACTS_PATH, "utf8"));
  } catch (e) {
    problem("facts", `docs/facts.json 不是合法 JSON：${e.message}`);
    return {};
  }
}

function checkDocs(facts) {
  let checked = 0;
  for (const rule of RULES) {
    const path = resolve(ROOT, rule.file);
    if (!existsSync(path)) {
      problem(rule.file, "文件不存在，规则失效（删掉这条规则或修路径）");
      continue;
    }
    const text = readFileSync(path, "utf8");
    const m = text.match(rule.re);
    if (!m) {
      problem(rule.file, `找不到 ${rule.key} 的锚点（正则 ${rule.re}）。文档被改写了就同步改规则`);
      continue;
    }
    const actual = STRING_KEYS.has(rule.key) ? m[rule.group] : Number(m[rule.group]);
    const expected = factAt(facts, rule.key);
    checked += 1;
    if (expected === undefined) {
      problem("facts", `facts.json 缺键 ${rule.key}`);
      continue;
    }
    if (!same(rule.key, actual, expected)) {
      const tol = toleranceFor(rule.key);
      const hint = tol ? `（容差 ±${tol}）` : "";
      problem(rule.file, `${rule.key} 写成 ${actual}，事实源是 ${expected}${hint}`);
    }
  }
  return checked;
}

function main() {
  const measured = measure();
  const facts = readFacts();

  if (WRITE) {
    const next = { ...facts, ...measured };
    // **version 不从测量值覆盖**：它是一次人为决定的发版动作，而 `git describe` 在
    // "已提交但还没打 tag"的窗口里必然给出**旧** tag。放它进来会导致一个很隐蔽的事故：
    // 刚把五处版本号改成 5.0.1、跑一次 --write 更新测试数 → version 被悄悄改回 5.0.0。
    // （2026-11-04 发版时真的踩到了。）所以：version 只保留 facts.json 里现有的值，
    // 需要升版就手动改，并由 check 模式的一致性校验兜住。
    next.version = facts.version ?? measured.version;
    next._measured_at = new Date().toISOString().slice(0, 10);
    writeFileSync(FACTS_PATH, `${JSON.stringify(next, null, 2)}\n`, "utf8");
    console.log(`已更新 docs/facts.json：${JSON.stringify(measured)}`);
    if (measured.version && measured.version !== next.version) {
      console.log(
        `（version 保留 ${next.version}：git describe 给的是 ${measured.version}，未打 tag 属正常）`,
      );
    }
  } else {
    // 事实源本身必须先与实测一致，否则"文档对得上事实源"毫无意义。
    // 但 version 例外：`git describe` 在"已提交但未打 tag"的提交上会给出**旧** tag，
    // 那不是漂移、是正常开发态。所以 version 不一致只警告，不判红（复审 MINOR-9）。
    for (const [k, v] of Object.entries(measured)) {
      if (k === "module_coverage") continue;   // 嵌套对象逐模块比，见下
      if (facts[k] === undefined || same(k, facts[k], v)) continue;
      if (k === "version") {
        console.log(
          `⚠ 版本提示：git describe 得到 ${v}，facts.json 写的是 ${facts[k]}（未打 tag 时属正常；` +
            `发版时再统一 --write）`,
        );
        continue;
      }
      problem("facts", `${k} 实测 ${v}，facts.json 写的是 ${facts[k]}（先跑 --write）`);
    }
    // 嵌套的模块级指标：逐模块比，才能指出"漂的是哪一个模块"（整体比较只会打印 [object Object]）。
    for (const [modulePath, value] of Object.entries(measured.module_coverage ?? {})) {
      const key = `module_coverage.${modulePath}`;
      const known = factAt(facts, key);
      if (!same(key, known, value)) {
        problem("facts", `${key} 实测 ${value}，facts.json 写的是 ${known}（先跑 --write）`);
      }
    }
  }

  if (NO_TEST_RUN) unmeasured.push("python_tests");
  if (!COVERAGE_JSON) {
    unmeasured.push("python_coverage_percent");
    // 模块级覆盖率的**唯一**来源也是那份 coverage json：没有它就不可能重测，
    // 必须显式列出来，否则"跳过即通过"会让这条新规则长期空转（正是本脚本自己警告的坑）。
    for (const modulePath of Object.keys(readFacts().module_coverage ?? {})) {
      unmeasured.push(`module_coverage.${modulePath}`);
    }
  }

  // WRITE 模式下校验用**刚写进去的那份**（next），而不是 {...facts, ...measured} ——
  // 后者会让 measured.version（来自 git describe 的旧 tag）覆盖掉要校验的 version。
  const effective = WRITE ? { ...facts, ...measured, version: facts.version ?? measured.version } : facts;
  const checked = checkDocs(effective);

  if (unmeasured.length > 0) {
    console.log(
      `⚠ 未实测（沿用 facts.json 原值，本次未核对现实）：${unmeasured.join(", ")}` +
        (unmeasured.includes("python_coverage_percent") ? " —— 传 --coverage-json=<pytest --cov-report=json 产物> 可实测" : ""),
    );
  }

  console.log(
    `事实源：version ${effective.version} / python_tests ${effective.python_tests} / ` +
      `覆盖率 ${effective.python_coverage_percent}% / go_tests ${effective.go_tests} / findings ${effective.findings}`,
  );

  if (problems.length === 0) {
    console.log(`✅ ${checked} 处文档数字与事实源一致`);
    process.exit(0);
  }
  console.error(`❌ 发现 ${problems.length} 个问题：`);
  for (const p of problems) console.error(`  [${p.where}] ${p.message}`);
  process.exit(1);
}

main();
