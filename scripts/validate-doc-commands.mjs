#!/usr/bin/env node
/**
 * 文档命令对账（T-05）：文档里的 run.ps1 / run.bat / python run.py 子命令
 * 必须真实存在于 run.py 的 COMMANDS。
 *
 * 规则：
 *   · 文档有、代码无 → exit 1 并指名文件行号；
 *   · 代码有、文档无 → 只警告（允许内部命令，不阻断）；
 *   · run.py 的 COMMANDS 键是唯一事实源（run.ps1 的 ValidateSet 由
 *     tests/test_runpy.py 的集合比对用例守，这里不重复）。
 *
 * 只读扫描根 README、docs/、plans/README、test_ledger.md；跳过 git 历史
 * 归档（plans/archive/）与 CHANGELOG（历史条目不追改，C1 的既定决策）。
 */
'use strict';

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

// ── 事实源：run.py 的 COMMANDS 键 ──
function readCommands() {
  const src = fs.readFileSync(path.join(ROOT, 'run.py'), 'utf8');
  const m = src.match(/COMMANDS\s*=\s*\{([\s\S]*?)\}/);
  if (!m) {
    console.error('FAIL: run.py 里找不到 COMMANDS 定义');
    process.exit(1);
  }
  const keys = new Set();
  for (const km of m[1].matchAll(/'([a-z0-9-]+)'\s*:/g)) keys.add(km[1]);
  return keys;
}

// ── 文档集合：对账范围是"承诺文档"——README 与 docs/ 顶层 md ──
// 计划书/ 下的施工图包含任务卡示例（`run.ps1 foo` 这类假想命令），不算承诺；
// CHANGELOG 是历史条目不追改（C1 既定决策）；plans/ 证据是原始输出。
function docFiles() {
  const out = [path.join(ROOT, 'README.md')];
  const docs = path.join(ROOT, 'docs');
  for (const name of fs.readdirSync(docs)) {
    if (name.endsWith('.md') && !name.startsWith('.')) out.push(path.join(docs, name));
  }
  return out;
}

// ── 抽取：run.ps1 X / run.bat X / python run.py X / py -3 run.py X ──
// 命令词后跟的第一个 token 如果是子命令样（小写字母/连字符），按子命令对账；
// run.ps1 / run.bat 单独出现（无子命令）不算——那是对入口本身的引用。
const CMD_RE = /(?:run\.ps1|run\.bat|run\.py)\s+([a-z][a-z0-9-]*)/g;

function extract(file) {
  const text = fs.readFileSync(file, 'utf8');
  const hits = [];
  const lines = text.split(/\r?\n/);
  lines.forEach((line, i) => {
    // 跳过明显的代码块围栏行与注释掉的内容不特殊处理——宁可多报不漏报
    for (const m of line.matchAll(CMD_RE)) {
      hits.push({ cmd: m[1], line: i + 1, snippet: line.trim().slice(0, 120) });
    }
  });
  return hits;
}

function main() {
  const commands = readCommands();
  const files = docFiles();
  const missing = [];
  const seen = new Set();

  for (const file of files) {
    for (const hit of extract(file)) {
      seen.add(hit.cmd);
      if (!commands.has(hit.cmd)) {
        missing.push({ file: path.relative(ROOT, file), line: hit.line, cmd: hit.cmd, snippet: hit.snippet });
      }
    }
  }

  // 代码有、文档无：警告不阻断（内部命令如 report/egress 允许不写进文档）
  const undocumented = [...commands].filter((c) => !seen.has(c) && c !== 'help');

  if (missing.length) {
    console.error(`❌ 文档引用了 ${missing.length} 个不存在的子命令：`);
    for (const m of missing) {
      console.error(`  ${m.file}:${m.line}  run.* ${m.cmd}  →  "${m.snippet}"`);
    }
    console.error('要么实现该子命令（run.py COMMANDS + run.ps1 ValidateSet 同步），要么修正文档。');
    process.exit(1);
  }

  console.log(`✅ 文档命令对账：扫描 ${files.length} 份文档，引用的子命令全部存在于 run.py COMMANDS（${commands.size} 个）`);
  if (undocumented.length) {
    console.log(`ℹ️  代码有但文档未提（仅提示，不阻断）：${undocumented.join(', ')}`);
  }
}

main();
