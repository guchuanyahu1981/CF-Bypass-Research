#!/usr/bin/env node
/**
 * 门禁自测（v6.3，B3）。
 *
 * 为什么需要：四道门禁（validate-docs / validate-openapi / validate-findings /
 * check-coverage）合计 900 行，**自己却没有一行自动化测试**。"它会红"这件事
 * 一直靠人工注入漂移再手动还原。项目已经吃过一次教训（v5.0.1 的 S3：`go test -list`
 * 从错误的 cwd 跑 → 被 catch 吞掉 → `go_tests` 从未被真正测量，而门禁一路是绿的）。
 * 门禁自己有静默降级，比没有门禁更糟。
 *
 * 做法：给每个门禁造一棵**最小临时树**，先证明"合法的输入会绿"，再逐个注入它声称
 * 会拦住的漂移，证明"每一种都会红"。四条门禁各 3–4 个负向样例。
 *
 * 用法：`node scripts/gates.selftest.mjs`；CI 里跑一步。
 * 零依赖、不联网、不碰仓库里的任何文件（全部写在 os.tmpdir 下的临时目录）。
 */

import { spawnSync } from "node:child_process";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const NODE = process.execPath;

let failures = 0;
let checks = 0;

function runGate(script, args) {
  const r = spawnSync(NODE, [resolve(HERE, script), ...args], { encoding: "utf8" });
  return { code: r.status, out: `${r.stdout ?? ""}${r.stderr ?? ""}` };
}

function expect(label, gate, expected) {
  checks += 1;
  const ok = gate.code === expected.code && (!expected.contains || gate.out.includes(expected.contains));
  if (!ok) {
    failures += 1;
    console.error(`✗ ${label}`);
    console.error(`  期望 exit=${expected.code}${expected.contains ? ` 且输出含「${expected.contains}」` : ""}`);
    console.error(`  实际 exit=${gate.code}，输出末尾：`);
    console.error(
      gate.out
        .trim()
        .split("\n")
        .slice(-6)
        .map((l) => `    ${l}`)
        .join("\n"),
    );
  } else {
    console.log(`✓ ${label}`);
  }
}

/** 在临时目录里铺一棵树；`files` 是 {相对路径: 文本}。 */
function makeTree(files) {
  const root = mkdtempSync(join(tmpdir(), "gate-selftest-"));
  for (const [rel, text] of Object.entries(files)) {
    const path = join(root, rel);
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, text, "utf8");
  }
  return root;
}

// ── validate-docs ────────────────────────────────────────
//
// 规则是"文档里的数字 == docs/facts.json"。所以最小树必须有：事实源、9 条锚点所在
// 的 5 个文件、以及被计数用的 findings.json。

const DOCS_FACTS = {
  version: "9.9.9",
  python_tests: 100,
  python_coverage_percent: 50,
  go_tests: 7,
  findings: 2,
  // v6.4 A1：模块级指标与单文件行数也进事实源，所以最小树里要有一份能被数出行的
  // `solver/service.py`、以及文档里那两条锚点（L6 覆盖率 / L7 行数）。
  module_coverage: { "solver/engines.py": 100 },
  service_py_lines: 40,
};

/** 造一个恰好 `lines` 行的源文件（`service_py_lines` 就是按换行数出来的）。 */
function sourceFileOfLines(lines) {
  return `${Array.from({ length: lines }, (_, i) => `x${i} = ${i}`).join("\n")}\n`;
}

function docsTree(overrides = {}, specVersion = null) {
  const facts = { ...DOCS_FACTS, ...overrides };
  const sv = specVersion ?? facts.version;
  return makeTree({
    "docs/facts.json": JSON.stringify(facts, null, 2),
    "docs/findings.json": JSON.stringify({ findings: [{ id: "F-001" }, { id: "F-002" }] }),
    "solver/service.py": sourceFileOfLines(facts.service_py_lines),
    "docs/项目规格.md":
      `> 版本：v${sv} · 更新：2026-01-01

` +
      "| L6 | `engines.py` 覆盖率 **100%**（x） | y | z |\n" +
      `| L7 | \`service.py\` 仍 **${facts.service_py_lines} 行**（x） | y | z |\n`,
    "README.md": "跑测试：pytest tests/    # 100 项 Python 测试\n",
    "docs/00-索引.md":
      "离线：pytest tests/   100 项 Python 离线测试 + 7 项 Go 测试。\n" +
      "结论见 `docs/findings.json`（2 条，三态 verdict）。\n",
    "test_ledger.md":
      "| Python 用例 | **100** | x |\n| Python 覆盖率 | **50%** | x |\n" +
      "| `solver/engines.py` 覆盖率 **100%** | ✅ | x |\n",
    "docker-compose.yml": "services:\n  s:\n    image: cf-bypass-solver:9.9.9\n",
    Dockerfile: "FROM python:3.12 # cf-bypass-solver:9.9.9\n",
    "CHANGELOG.md": "## [9.9.9] - 2026-01-01\n",
  });
}

function selftestDocs() {
  console.log("\n── validate-docs ──");
  const clean = docsTree();
  expect("合法输入 → 绿", runGate("validate-docs.mjs", [`--root=${clean}`, "--no-test-run"]), {
    code: 0,
  });
  rmSync(clean, { recursive: true, force: true });

  const drift = docsTree({ python_tests: 999 });
  expect(
    "事实源写 999、文档写 100 → 红",
    runGate("validate-docs.mjs", [`--root=${drift}`, "--no-test-run"]),
    { code: 1, contains: "python_tests" },
  );
  rmSync(drift, { recursive: true, force: true });

  // v6.4 A1 的两条新规则（模块覆盖率 / 单文件行数）各自也要有一个负向样例 ——
  // "加了规则但门禁自测没覆盖它"正是这个脚本要防的那类盲区。
  const moduleDrift = docsTree({ module_coverage: { "solver/engines.py": 57 } });
  expect(
    "模块覆盖率写 57、文档写 100 → 红",
    runGate("validate-docs.mjs", [`--root=${moduleDrift}`, "--no-test-run"]),
    { code: 1, contains: "module_coverage" },
  );
  rmSync(moduleDrift, { recursive: true, force: true });

  // 行数漂移的方向是"源码长长了、事实源没跟上"，所以这里改**源文件**而不是改文档：
  // 只改文档的话，文档与事实源仍然一致，门禁本该绿（那种漂移由锚点规则自己抓）。
  const linesDrift = docsTree();
  writeFileSync(join(linesDrift, "solver", "service.py"), sourceFileOfLines(7), "utf8");
  expect(
    "源文件行数与事实源不一致 → 红",
    runGate("validate-docs.mjs", [`--root=${linesDrift}`, "--no-test-run"]),
    { code: 1, contains: "service_py_lines" },
  );
  rmSync(linesDrift, { recursive: true, force: true });

  const brokenAnchor = makeTree({
    "docs/facts.json": JSON.stringify(DOCS_FACTS),
    "docs/findings.json": JSON.stringify({ findings: [] }),
    "README.md": "这一行被改写了，锚点没了\n",
  });
  expect(
    "文档锚点被改写 → 红",
    runGate("validate-docs.mjs", [`--root=${brokenAnchor}`, "--no-test-run"]),
    { code: 1, contains: "锚点" },
  );
  rmSync(brokenAnchor, { recursive: true, force: true });

  const noFacts = makeTree({ "README.md": "空树\n" });
  expect(
    "没有 docs/facts.json → 红",
    runGate("validate-docs.mjs", [`--root=${noFacts}`, "--no-test-run"]),
    { code: 1, contains: "facts.json" },
  );
  rmSync(noFacts, { recursive: true, force: true });

  const specVersionDrift = docsTree({ version: "9.9.8" }, "9.9.9");
  expect(
    "规格版本行与事实源不一致 → 红（v6.4.3 新增锚点）",
    runGate("validate-docs.mjs", [`--root=${specVersionDrift}`, "--no-test-run"]),
    { code: 1, contains: "docs/项目规格.md" },
  );
  rmSync(specVersionDrift, { recursive: true, force: true });
}

// ── validate-openapi ─────────────────────────────────────
//
// 规则：规格的 path+method 必须与源码路由**双向完全一致**，除 /health 与 / 外
// 必须继承 Bearer，info.version 必须等于事实源。

const OPENAPI_SOURCE = `class Handler:
    def do_GET(self):
        path = self.path
        if path == "/health":
            pass
        elif path == "/":
            pass
        elif path == "/a":
            pass
        elif path == "/b":
            pass
        elif path == "/c":
            pass

    def do_POST(self):
        path = self.path
        if path == "/x":
            pass
        elif path == "/y":
            pass
        elif path == "/z":
            pass
`;

function openapiPaths() {
  return {
    "/health": { get: { security: [] } },
    "/": { get: { security: [] } },
    "/a": { get: {} },
    "/b": { get: {} },
    "/c": { get: {} },
    "/x": { post: {} },
    "/y": { post: {} },
    "/z": { post: {} },
  };
}

function goStruct(fields) {
  return "type Clearance struct {\n" + fields.map((f) => `  X string \`json:"${f}"\``).join("\n") + "\n}\n";
}

function openapiTree({ version = "9.9.9", paths = openapiPaths(), security = [{ bearerAuth: [] }], goFields = ["cf_clearance"], schemaFields = ["cf_clearance"] } = {}) {
  const spec = {
    openapi: "3.0.3",
    info: { title: "t", version },
    components: {
      securitySchemes: { bearerAuth: { type: "http", scheme: "bearer" } },
      schemas: { SolveResponse: { type: "object", properties: Object.fromEntries(schemaFields.map((f) => [f, { type: "string" }])) } },
    },
    paths,
  };
  // null 表示“故意删掉顶层 security”，用来测门禁能不能发现。
  if (security !== null) spec.security = security;
  return makeTree({
    "gocfsolve/cfsolve.go": goStruct(goFields),
    "solver/service.py": OPENAPI_SOURCE,
    // v6.4 C4：门禁还会核对"规格里的非 2xx 响应码 ⊆ STATUS_BY_CODE"，
    // 所以最小树必须有一份能被提取的 errors.py（低于 8 个码会被判成"提取规则失效"）。
    "solver/errors.py": `STATUS_BY_CODE = {\n${[400, 401, 404, 405, 413, 429, 500, 502, 503, 504]
      .map((code) => `    CODE_${code}: ${code},`)
      .join("\n")}\n}\n`,
    "docs/facts.json": JSON.stringify({ version: "9.9.9" }),
    "docs/openapi.json": JSON.stringify(spec),
  });
}

function selftestOpenapi() {
  console.log("\n── validate-openapi ──");
  const clean = openapiTree();
  expect("合法输入 → 绿", runGate("validate-openapi.mjs", [`--root=${clean}`]), { code: 0 });
  rmSync(clean, { recursive: true, force: true });

  const missing = openapiPaths();
  delete missing["/c"];
  const t1 = openapiTree({ paths: missing });
  expect(
    "规格漏写一个源码路由 → 红",
    runGate("validate-openapi.mjs", [`--root=${t1}`]),
    { code: 1, contains: "/c" },
  );
  rmSync(t1, { recursive: true, force: true });

  const ghost = openapiPaths();
  ghost["/ghost"] = { get: {} };
  const t2 = openapiTree({ paths: ghost });
  expect(
    "规格多写一个幽灵路由 → 红",
    runGate("validate-openapi.mjs", [`--root=${t2}`]),
    { code: 1, contains: "/ghost" },
  );
  rmSync(t2, { recursive: true, force: true });

  const t3 = openapiTree({ version: "9.9.8" });
  expect(
    "规格版本与事实源不一致 → 红",
    runGate("validate-openapi.mjs", [`--root=${t3}`]),
    { code: 1, contains: "version" },
  );
  rmSync(t3, { recursive: true, force: true });

  // v6.4 C4 的新规则：声明了一个 errors.py 根本不会产生的响应码 → 红。
  // （对接方会为那条死分支写重试逻辑，比缺声明更难查。）
  const badCode = openapiPaths();
  badCode["/a"].get.responses = { 418: { description: "注入的幽灵响应码" } };
  const t4 = openapiTree({ paths: badCode });
  expect(
    "规格声明了 STATUS_BY_CODE 不会产生的响应码 → 红",
    runGate("validate-openapi.mjs", [`--root=${t4}`]),
    { code: 1, contains: "418" },
  );
  rmSync(t4, { recursive: true, force: true });

  // v6.4.3 的新规则：顶层 security 是“未显式覆盖的操作”的默认鉴权，
  // 删掉它后规则 2 的逐操作检查看不到任何异常，必须在顶层就判红。
  const t5 = openapiTree({ security: null });
  expect(
    "顶层 security 被删 → 红",
    runGate("validate-openapi.mjs", [`--root=${t5}`]),
    { code: 1, contains: "security" },
  );
  rmSync(t5, { recursive: true, force: true });

  const t6 = openapiTree({ goFields: ["cf_clearance", "missing_field"], schemaFields: ["cf_clearance"] });
  expect(
    "Go 客户端读取的字段未进 SolveResponse → 红",
    runGate("validate-openapi.mjs", [`--root=${t6}`]),
    { code: 1, contains: "missing_field" },
  );
  rmSync(t6, { recursive: true, force: true });
}

// ── validate-findings ────────────────────────────────────

function findingsTree(finding) {
  const f = finding ?? {
    id: "F-001",
    claim: "示例结论",
    verdict: "confirmed",
    severity: "high",
    evidence: [{ path: "README.md" }],
  };
  return makeTree({
    "docs/findings.json": JSON.stringify({ findings: [f] }),
    "README.md": "证据文件\n",
  });
}

function selftestFindings() {
  console.log("\n── validate-findings ──");
  const clean = findingsTree();
  expect("合法输入 → 绿", runGate("validate-findings.mjs", [`--root=${clean}`]), { code: 0 });
  rmSync(clean, { recursive: true, force: true });

  const badVerdict = findingsTree({
    id: "F-001",
    claim: "x",
    verdict: "maybe",
    evidence: [{ path: "README.md" }],
  });
  expect(
    "verdict 非法 → 红",
    runGate("validate-findings.mjs", [`--root=${badVerdict}`]),
    { code: 1, contains: "verdict" },
  );
  rmSync(badVerdict, { recursive: true, force: true });

  const missingEvidence = findingsTree({
    id: "F-001",
    claim: "x",
    verdict: "confirmed",
    severity: "high",
    evidence: [{ path: "does-not-exist.md" }],
  });
  expect(
    "证据文件不存在 → 红",
    runGate("validate-findings.mjs", [`--root=${missingEvidence}`]),
    { code: 1, contains: "证据文件不存在" },
  );
  rmSync(missingEvidence, { recursive: true, force: true });

  const noSeverity = findingsTree({
    id: "F-001",
    claim: "x",
    verdict: "confirmed",
    evidence: [{ path: "README.md" }],
  });
  expect(
    "confirmed 缺 severity → 红",
    runGate("validate-findings.mjs", [`--root=${noSeverity}`]),
    { code: 1, contains: "severity" },
  );
  rmSync(noSeverity, { recursive: true, force: true });
}

// ── check-coverage ───────────────────────────────────────

function coverageFiles(solverPct = 90, captchasPct = 25) {
  return {
    files: {
      "solver/a.py": {
        summary: { percent_covered: solverPct, covered_lines: solverPct, num_statements: 100 },
      },
      "captchas/c.py": {
        summary: { percent_covered: captchasPct, covered_lines: captchasPct, num_statements: 100 },
      },
    },
  };
}

function coverageTree({ cov = coverageFiles(), policy } = {}) {
  const pol = policy ?? {
    _scopes: { "solver/": { min: 80 }, "captchas/": { min: 20 } },
    modules: { "solver/a.py": { min: 85 }, "captchas/c.py": { min: 20 } },
  };
  return makeTree({
    "coverage.json": JSON.stringify(cov),
    "policy.json": JSON.stringify(pol),
  });
}

function runCoverage(root, extra = []) {
  return runGate("check-coverage.mjs", [
    `--coverage-json=${join(root, "coverage.json")}`,
    `--policy=${join(root, "policy.json")}`,
    ...extra,
  ]);
}

function selftestCoverage() {
  console.log("\n── check-coverage ──");
  const clean = coverageTree();
  expect("合法输入 → 绿", runCoverage(clean), { code: 0 });
  rmSync(clean, { recursive: true, force: true });

  const unregistered = coverageTree({
    policy: { _scopes: { "solver/": { min: 80 }, "captchas/": { min: 20 } }, modules: { "solver/a.py": { min: 85 } } },
  });
  expect("有模块未在策略里登记 → 红", runCoverage(unregistered), { code: 1, contains: "未在 docs/coverage-policy.json 登记" });
  rmSync(unregistered, { recursive: true, force: true });

  const ghost = coverageTree({
    policy: {
      _scopes: { "solver/": { min: 80 }, "captchas/": { min: 20 } },
      modules: { "solver/a.py": { min: 85 }, "captchas/c.py": { min: 20 }, "solver/ghost.py": { min: 10 } },
    },
  });
  expect("策略里登记了不存在的模块 → 红", runCoverage(ghost), { code: 1, contains: "报告里没有它" });
  rmSync(ghost, { recursive: true, force: true });

  const low = coverageTree({
    cov: coverageFiles(70, 25),
  });
  expect("模块覆盖率低于 min → 红", runCoverage(low), { code: 1, contains: "低于门禁" });
  rmSync(low, { recursive: true, force: true });

  const noCaptchas = coverageTree({
    cov: { files: { "solver/a.py": coverageFiles().files["solver/a.py"] } },
  });
  expect("报告里缺整个 captchas 范围 → 红", runCoverage(noCaptchas), { code: 1, contains: "captchas/" });
  rmSync(noCaptchas, { recursive: true, force: true });

  const scopeLow = coverageTree({ cov: coverageFiles(90, 5) });
  expect("captchas 整体跌破范围门槛 → 红", runCoverage(scopeLow), { code: 1, contains: "整体" });
  rmSync(scopeLow, { recursive: true, force: true });
}

// ── main ────────────────────────────────────────────────

selftestDocs();
selftestOpenapi();
selftestFindings();
selftestCoverage();

console.log("");
if (failures === 0) {
  console.log(`✅ 门禁自测：${checks} 条样例全部符合预期（该绿时绿、该红时红）`);
  process.exit(0);
}
console.error(`❌ 门禁自测：${failures}/${checks} 条样例未达预期 —— 门禁本身有问题，先修门禁`);
process.exit(1);
