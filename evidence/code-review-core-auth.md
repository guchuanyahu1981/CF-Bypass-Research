# 独立代码复审

- generated_at: 2026-11-12T13:23:59
- duration_sec: 125.1
- diff_bytes: 14029
- untracked_included: (none)
- exit_code: 0

---

P0: none

P1: none

P2 | solver/auth.py:~120 (append branch) | `_write_all(fd, prefix)` runs *before* the `try/finally: os.close(fd)` that guards the main token write | if the prefix write raises (ENOSPC/EPIPE), the fd opened for the append path is never closed; an embedder that retries `load_or_create_token` in a loop leaks one fd per failure | move the prefix write inside the same `try/finally` that closes `fd`, or open/close in a single guarded block.

P2 | solver/auth.py:_read_token_file | any non-empty value after `CF_SOLVE_TOKEN=` is accepted with no length/format check | a short/partial `os.write` (e.g. disk full mid-append) leaves a truncated token with no trailing newline; the writer exits 2 but later workers read and boot on the short, low-entropy token — a silently weakened credential | validate the value (e.g. `len >= 32` and urlsafe charset) before returning, and/or write via temp-file + `os.replace` so a partial line is never observable.

P2 | solver/bootstrap.py:~187 | the proxy conflict check fires on `--proxy` + `--cfsolve-bin` even when `--cfsolve-profile` is missing, i.e. when the Cfsolve backend won't actually be enabled | operator who forgot `--cfsolve-profile` gets a hard "配置冲突" exit 2 that points at the wrong cause, and the helpful "must give both" warning in the following `elif` is unreachable | compute backend enablement first (bin AND profile), then run the conflict check only against backends that will actually be appended.

P2 | solver/bootstrap.py:~230 | new `rate_per_host`/`rate_burst` kwargs are passed to `Solver(...)` but the diff contains no `Solver.__init__` change | if the constructor does not already accept these kwargs, every startup raises `TypeError: unexpected keyword argument 'rate_per_host'` and the process never boots | confirm the Solver signature already declares them (and their units/semantics), or include the constructor change in this diff.

P2 | tests (test_auth_token_race.py) | all lock behavior tests exercise only the POSIX `fcntl.flock` path; the Windows `msvcrt.locking` branch (including the lock-past-EOF dependency on the pre-written `"0"` byte) is never run on CI | a broken `msvcrt` path ships undetected and multi-worker first-boot splits tokens on Windows | add a Windows-guarded test (or a mock that simulates `LK_NBLCK` failing/succeeding) and a case asserting convergence when the lock file starts empty vs pre-populated.

P2 | solver/bootstrap.py:~230 | rate limiting is per-process (`rate_per_host` default 1/30), so on an N-worker deployment the effective per-host rate is ≈ N× the configured value | operators who rely on this to avoid hammering a host / bans get ~N× the intended request rate, silently, with the feature enabled by default | document/label it as per-worker in the flag name or make it opt-in, or share the bucket across workers; at minimum keep the "N×" caveat prominent (it is currently only in --help).

VERDICT: PASS
