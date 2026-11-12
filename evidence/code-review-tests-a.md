# 独立代码复审

- generated_at: 2026-11-12T16:42:53
- duration_sec: 42.8
- diff_bytes: 20153
- untracked_included: tests/test_acceptance_report.py
- exit_code: 0

---

P0: none
P1: none
P2 | tests/test_detect_source.py:129-141 | Broken-source fixtures rely on exact literals (`"count": 34,`, `('cookie', '_abck', 100)`, `'id': 'detect-aws-waf'`, the shape regex text) copied from detect.py | Any benign edit to the signature table (adding/removing a signature changing count, reformatting tuples) silently makes the replace a no-op, so `_local_problems` gets unmodified source, finds no problems, and the "flags duplicate ids/confidence/count/regex" assertions fail misleadingly — or worse, pass vacuously if replacement happens but a different validator path is exercised | Have gen_detect_signatures expose the parse of the table, or build the broken table programmatically (mutate `detect.SIGNATURES` copies / generate the file from a small template) instead of string-replacing production source
P2 | tests/test_runpy.py:224-240 (test_runpy_node_script_paths_are_not_split) | AST guard only matches calls whose first arg is a literal `ast.List` with constant strings; node invocations built via variables, `[*base, path]`, or f-string paths bypass the check entirely | The exact regression it guards against can reappear in a refactored call site and the test still passes, giving false assurance on the "main verify chain first step" | Instead walk for `ast.Constant` values equal to `"node"` and validate every element of the enclosing argument list regardless of construction, or extract node-invoking commands into one helper and unit-test that helper's output
P2 | tests/test_runpy.py:test_runpy_mcp_stdout_is_protocol_only | Spawns the real MCP server with `input=""`; behavior depends on the server exiting on EOF immediately — if it waits for a peer before a clean shutdown the 30s timeout raises TimeoutExpired, failing the test as an error rather than an assertion | Slow CI machine or a slight startup delay (>30s under load) turns a passing protocol-purity check into a flaky red | Send a minimal initialize request then shutdown, or keep EOF-based exit but assert on stderr/returncode with a retry, and distinguish timeout from protocol pollution in the failure message
P2 | tests/test_runpy.py:74-77 (_SmokeResp / urlopen mock) | `cmd_smoke` is fully mocked at `subprocess.Popen` and `urlopen`, so the new tests verify bearer propagation and SSRF status handling only against the mock's contract; real behavior (e.g. token read from env, context-manager semantics, redirect handling of `urlopen`) is unexercised | If `cmd_smoke` switches to `requests` or reads the token elsewhere, tests keep passing while smoke breaks in production — a mock presented as integration | Keep these as unit tests but add at least one subprocess-level smoke run against a local throwaway server (already a pattern in test_detect.py) so the 400-with-error_code acceptance path is validated end to end

VERDICT: PASS
