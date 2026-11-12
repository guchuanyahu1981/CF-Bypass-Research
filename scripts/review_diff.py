#!/usr/bin/env python3
"""独立代码复审：把当前工作树 diff 喂给 `claude -p`，保存复审报告。

为什么不用子 Agent：本项目的记录里子 Agent 通道多次丢报告；真实复审通道是
`claude -p` 独立进程。脚本只负责"生成 diff + 约束输出格式 + 落证据"，不改代码。

复审规则写死在 PROMPT 里：只看正确性/安全/并发/错误处理/契约/测试/UI 逻辑；
每条发现必须带 file:line、失败场景、修法；结尾必须是 `VERDICT: PASS|FAIL`。
"""

from __future__ import annotations

import argparse
import difflib
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDE_DIRS = ("plans/", "evidence/", "site/out/", "site/.next/", "site/node_modules/", ".git/")
EXCLUDE_FILES = {"coverage.json", "local_solver.log", "local_tok.txt"}
TEXT_EXT = {".py", ".go", ".ts", ".tsx", ".js", ".mjs", ".json", ".yml", ".yaml",
            ".md", ".ps1", ".txt", ".toml", ".css"}
MAX_DIFF_BYTES = 2 * 1024 * 1024

PROMPT = """You are an independent code reviewer for a local Python/Go/TypeScript project.
You have NO tools. Never attempt tool calls. The diff below is the complete input;
do not ask to inspect the repository, do not output tool-call syntax.
This is a READ-ONLY review: do not modify files, do not run commands that change state.

Review the working-tree diff below. Focus on:
1. correctness and logic bugs;
2. security boundaries (SSRF, auth, secret leakage, injection);
3. concurrency, claims/cache races, lifecycle/resource leaks;
4. error handling and failure modes (timeouts, partial writes, corrupt data);
5. API contract consistency (docs/OpenAPI vs code, UI vs endpoints);
6. tests: missing cases, false assertions, mocks presented as real integration;
7. UI logic: buttons, state transitions, error copy, accessibility.

Severity:
- P0: data loss, security bypass, core path broken.
- P1: main-path regression, likely wrong behavior, or a failure that misleads operators.
- P2: maintainability/edge case, not blocking.
Do NOT report style-only nits. Do NOT invent issues. If a severity has no findings, write exactly `P0: none` / `P1: none` / `P2: none`.

Be concise: at most 8 findings total, no restatement of the diff.
If you believe a behavior is missing, check whether a helper already in the diff implements it before claiming a defect.
For every finding use:
`P1 | path/to/file.py:123 | what is wrong | concrete failure scenario | suggested fix`

End with exactly one line:
`VERDICT: PASS` if there are no P0/P1 findings, or `VERDICT: FAIL` if any P0/P1 exists.

=== DIFF START ===
"""


def _git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=ROOT)


def build_diff(paths: list[str] | None = None, args_max=None) -> tuple[str, list[str]]:
    paths = [p for p in (paths or []) if p]
    excludes = [f":(exclude){p}" for p in EXCLUDE_DIRS]
    git_paths = paths if paths else ["."]
    tracked = _git("diff", "HEAD", "--no-ext-diff", "--", *git_paths, *excludes,
                   *[f":(exclude){p}" for p in EXCLUDE_FILES]).decode("utf-8", "replace")
    parts = [tracked]
    included: list[str] = []
    for raw in _git("ls-files", "--others", "--exclude-standard", "-z").decode(
            "utf-8", "replace").split("\0"):
        rel = raw.strip()
        if not rel or any(rel.startswith(p) for p in EXCLUDE_DIRS) or rel in EXCLUDE_FILES:
            continue
        if paths and not any(rel == p or rel.startswith(p.rstrip("/") + "/") for p in paths):
            continue
        path = ROOT / rel
        if path.suffix.lower() not in TEXT_EXT or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        included.append(rel)
        diff = difflib.unified_diff([], text.splitlines(keepends=True),
                                    fromfile="/dev/null", tofile="b/" + rel,
                                    lineterm="")
        parts.append(f"diff --git a/{rel} b/{rel}\nnew file mode 100644\n"
                     f"--- /dev/null\n+++ b/{rel}\n" + "\n".join(diff))
    body = "\n".join(p for p in parts if p)
    max_bytes = args_max if args_max else MAX_DIFF_BYTES
    if len(body.encode("utf-8")) > max_bytes:
        body = body[:max_bytes] + f"\n\n[TRUNCATED: diff exceeded {max_bytes} bytes]\n"
    return body, included


def main() -> int:
    ap = argparse.ArgumentParser(description="独立代码复审（claude -p 读 diff）")
    ap.add_argument("--out", default="evidence/code-review.md")
    ap.add_argument("--timeout", type=int, default=1800, help="复审进程超时秒数")
    ap.add_argument("--max-bytes", type=int, default=200_000,
                    help="diff 超过该字节数时截断（防单次复审超时）")
    ap.add_argument("--effort", default="medium", choices=["low", "medium", "high"])
    ap.add_argument("--paths", nargs="*", default=[], help="只审这些路径（默认全部）")
    ap.add_argument("--context", default="", help="补充调用点/约束（纯文本，附在 diff 前）")
    args = ap.parse_args()

    diff, included = build_diff(args.paths, args.max_bytes)
    print(f"diff bytes={len(diff.encode('utf-8'))} untracked_included={len(included)}")
    context = f"\n=== CALL-SITE CONTEXT ===\n{args.context}\n" if args.context else ""
    prompt = PROMPT + context + diff
    started = time.time()
    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    prompt_file = None
    use_file_stdin = len(prompt.encode("utf-8")) > 12_000
    if use_file_stdin:
        prompt_file = out.with_name(out.name + ".prompt")
        prompt_file.write_text(prompt, encoding="utf-8")
        stdout_file = out.with_name(out.name + ".raw")
        # 大输入：文件重定向 stdin/stdout，不走 pipe（管道下 claude 会等满超时）。
        with open(prompt_file, "r", encoding="utf-8") as fin,                 open(stdout_file, "w", encoding="utf-8", errors="replace") as fout:
            proc = subprocess.run(
                ["claude", "-p", "--bare", "--effort", args.effort, "--tools", "",
                 "--output-format", "text"],
                stdin=fin, stdout=fout, stderr=subprocess.PIPE,
                timeout=args.timeout, cwd=str(ROOT),
            )
        proc.stdout = ""
        proc.stderr = proc.stderr.decode("utf-8", "replace") if isinstance(proc.stderr, bytes) else (proc.stderr or "")
        text = ""
        try:
            text = stdout_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
    else:
        proc = subprocess.run(
            ["claude", "-p", "--bare", "--effort", args.effort, "--tools", "",
             "--output-format", "text"],
            input=prompt, text=True, capture_output=True, timeout=args.timeout,
            cwd=str(ROOT), encoding="utf-8", errors="replace",
        )
        text = (proc.stdout or "") + (("\n[stderr]\n" + proc.stderr) if proc.stderr.strip() else "")
    if prompt_file is not None:
        try:
            prompt_file.unlink()
        except OSError:
            pass
    if use_file_stdin and "VERDICT:" not in text:
        # 文件模式下 stdout 写进了 .raw；若 text 丢失 verdict，从 raw 取最后一个补上。
        raw_path = out.with_name(out.name + ".raw")
        try:
            raw_text = raw_path.read_text(encoding="utf-8", errors="replace")
            matches = re.findall(r"VERDICT:\s*(PASS|FAIL)", raw_text, re.I)
            if matches:
                text = text.rstrip() + f"\n\nVERDICT: {matches[-1].upper()}\n"
        except OSError:
            pass
    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    header = (f"# 独立代码复审\n\n"
              f"- generated_at: {time.strftime('%Y-%m-%dT%H:%M:%S')}\n"
              f"- duration_sec: {time.time() - started:.1f}\n"
              f"- diff_bytes: {len(diff.encode('utf-8'))}\n"
              f"- untracked_included: {', '.join(included) if included else '(none)'}\n"
              f"- exit_code: {proc.returncode}\n\n---\n\n")
    out.write_text(header + text, encoding="utf-8")
    print(f"report: {out}")
    if proc.returncode != 0:
        print(f"FAIL: claude 退出码 {proc.returncode}（报告已保存）", file=sys.stderr)
        return 2
    m = re.search(r"VERDICT:\s*(PASS|FAIL)", text, re.I)
    if not m:
        print("FAIL: 复审报告缺少 `VERDICT: PASS|FAIL`，不能当作验收通过。", file=sys.stderr)
        return 2
    verdict = m.group(1).upper()
    print(f"VERDICT: {verdict}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())