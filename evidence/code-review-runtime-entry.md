# 独立代码复审

- generated_at: 2026-11-12T23:28:14
- duration_sec: 99.7
- diff_bytes: 45578
- untracked_included: (none)
- exit_code: 0

---



Note: the diff is truncated mid-`cmd_accept` (at the `acceptance_report.py` invocation), so the new `build_parser` wiring (`--skip-smoke`, `--skip-review`, `--json-out`, `mcp`) and the `solver/` changes are not visible; findings below cover the inspectable code only.

P2 | run.py:~662 (cmd_accept, after run_steps) | acceptance_report runs only when all six tracks passed (`if rc: return rc`) | e.g. code-review verdict FAIL or realcalls failure → accept exits non-zero with no aggregated report, though acceptance_report exists precisely to classify pass/partial/fail outcomes for operators | run acceptance_report unconditionally (or also on failure) and return a combined non-zero exit code

P2 | run.py:~425 (cmd_verify: validate_docs/validate_openapi/gates_selftest) | the three new node-based gates invoke `node` without the `shutil.which("node")` guard that cmd_findings applies | on a node-less machine verify aborts mid-gate with `找不到可执行文件 node` (127) instead of the uniform WARN-and-skip used one function over | apply the same which("node") guard (skip with WARN) or state the hard node dependency uniformly across all gates

P2 | run.py:~648 (cmd_accept run_steps) vs run.py:~470 (cmd_verify step list) | accept runs the real-process E2E twice — once inside verify's steps and again as its own track — doubling the slowest track and rewriting evidence/e2e-local.json on the second pass | a several-minute E2E runs twice per accept; a flake in the first run is masked because only the second run's evidence reaches the report | drop the duplicate track from accept (verify already covers it) or skip verify's e2e step when invoked from accept

P2 | run.py:~207 (cmd_smoke readiness loop, carried over verbatim from the old file) | when /health responds with a non-ready state, the loop does not sleep (sleep only happens in the exception path), so it hot-spins hammering /health until the 120s deadline | a service reporting `state: starting` gets thousands of back-to-back health requests, potentially slowing its own startup on a single-threaded server | add `time.sleep(0.5)` on the success-but-not-ready path as well

P0: none
P1: none
P2: run.py acceptance-report gating; run.py node-guard inconsistency in new verify gates; run.py duplicate E2E in accept; run.py smoke readiness busy-loop (carried over)

VERDICT: PASS
